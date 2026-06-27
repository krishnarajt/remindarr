"""Natural language → :class:`ScheduleSpec` via the LLM gateway.

The user types something like *"remind me to drink water every hour during work
hours but not at night"*; we ask the model to emit a strict JSON object, then
validate it into a ScheduleSpec and prove it yields a real next occurrence.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from app.common.schemas import ParseResult, ScheduleSpec
from app.services.llm_gateway import LLMGatewayError, gateway
from app.utils.logging_utils import logger
from app.utils.scheduling import compute_next_trigger, describe_spec
from app.utils.time_utils import now_utc

_SYSTEM_PROMPT = """\
You convert a person's natural-language reminder into a strict JSON schedule.

Output ONLY a JSON object (no prose, no markdown fences) with this shape:
{
  "ok": true,
  "summary": "<short human-readable description of WHAT and WHEN>",
  "spec": {
    "kind": "one_time" | "recurring",
    "timezone": "<IANA tz>",                      // default to the user's tz
    "at": "YYYY-MM-DDTHH:MM:SS",                  // local wall-clock, one_time only
    "rrule": "FREQ=...;...",                      // RFC5545, recurring only
    "dtstart": "YYYY-MM-DDTHH:MM:SS",             // optional anchor, local
    "active_windows": [{"start":"HH:MM","end":"HH:MM"}],   // fire only inside
    "blackout_windows": [{"start":"HH:MM","end":"HH:MM"}], // never fire inside
    "until": "YYYY-MM-DDTHH:MM:SS",               // optional hard stop, local
    "count": <int>                                // optional max number of fires
  },
  "reminder_name": "<3-6 word title>",
  "reminder_content": "<the message to send the user when it fires>"
}

RULES:
- All datetimes are LOCAL wall-clock in the spec's timezone. Never include a
  timezone offset or 'Z'.
- Put time-of-day in the RRULE via BYHOUR/BYMINUTE (e.g. 9am => BYHOUR=9;BYMINUTE=0).
- Do NOT put DTSTART, COUNT or UNTIL inside the rrule string; use the dedicated
  fields.
- Use active_windows for "only during X" and blackout_windows for "not during X".
  A window whose end is earlier than its start crosses midnight (e.g. 22:00-07:00).
- If the request is too vague to schedule (e.g. no time at all for a one-time
  reminder), return {"ok": false, "clarification": "<one short question>"}.

EXAMPLES (assume user tz = Asia/Kolkata):
- "remind me to call mom in 2 hours"
  => one_time, at = now + 2h.
- "every day at 9am drink coffee"
  => recurring, rrule "FREQ=DAILY;BYHOUR=9;BYMINUTE=0".
- "remind me to do taxes on odd-weekend saturdays in even months at 9am"
  => recurring, rrule "FREQ=MONTHLY;BYMONTH=2,4,6,8,10,12;BYDAY=SA;BYSETPOS=1,3,5;BYHOUR=9;BYMINUTE=0".
- "drink water every hour during work hours but not at night 10pm to 7am"
  => recurring, rrule "FREQ=HOURLY;BYHOUR=9,10,11,12,13,14,15,16,17;BYMINUTE=0",
     active_windows [{"start":"09:00","end":"18:00"}],
     blackout_windows [{"start":"22:00","end":"07:00"}].
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json\n...\n```  or  ```\n...\n```
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def _build_user_prompt(text: str, now_local: datetime, user_tz: str) -> str:
    return (
        f"User timezone: {user_tz}\n"
        f"Current local datetime: {now_local.strftime('%Y-%m-%dT%H:%M:%S')} "
        f"({now_local.strftime('%A')})\n\n"
        f"Reminder request:\n{text}"
    )


def _try_parse(raw: str) -> ParseResult:
    """Parse one raw LLM response into a ParseResult. Raises on unusable JSON."""
    data = json.loads(_strip_fences(raw))

    if not data.get("ok", False):
        return ParseResult(
            ok=False,
            clarification=data.get("clarification")
            or "I couldn't understand that schedule. Could you rephrase it?",
        )

    spec = ScheduleSpec.model_validate(data["spec"])

    # Prove the schedule actually produces a future occurrence.
    if compute_next_trigger(spec, now_utc()) is None:
        return ParseResult(
            ok=False,
            clarification="That schedule has no upcoming occurrences. "
            "Try a different time?",
        )

    return ParseResult(
        ok=True,
        spec=spec,
        summary=data.get("summary") or describe_spec(spec),
    )


def parse_reminder(text: str, *, now_local: datetime, user_tz: str) -> ParseResult:
    """Convert ``text`` into a ScheduleSpec, with one repair retry.

    Also attaches the model-suggested name/content onto the result's spec via a
    side channel: callers read ``result._name`` / ``result._content`` when set.
    """
    if not gateway.configured:
        return ParseResult(
            ok=False,
            clarification="Natural-language reminders aren't configured yet. "
            "Use /add to create one step by step.",
        )

    user_prompt = _build_user_prompt(text, now_local, user_tz)
    extra = {"response_format": {"type": "json_object"}}

    last_err: Optional[str] = None
    for attempt in range(2):
        try:
            prompt = user_prompt
            if attempt == 1 and last_err:
                prompt = (
                    f"{user_prompt}\n\nYour previous reply was invalid: {last_err}\n"
                    "Return corrected JSON only."
                )
            raw = gateway.chat(
                prompt,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0,
                max_output_tokens=800,
                extra=extra,
            )
            result = _try_parse(raw)
            if result.ok and result.spec is not None:
                _attach_text(result, raw)
            return result
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_err = str(exc)
            logger.warning("NL parse attempt %d failed: %s", attempt + 1, exc)
        except LLMGatewayError as exc:
            logger.error("NL parse gateway error: %s", exc)
            return ParseResult(
                ok=False,
                clarification="The language model is unavailable right now. "
                "Try /add instead.",
            )

    return ParseResult(
        ok=False,
        clarification="I couldn't turn that into a schedule. Try rephrasing, "
        "or use /add for a guided setup.",
    )


def _attach_text(result: ParseResult, raw: str) -> None:
    """Pull reminder_name/content out of the raw JSON onto the result object."""
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        data = {}
    result.name = data.get("reminder_name")
    result.content = data.get("reminder_content")
