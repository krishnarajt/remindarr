"""Pydantic schemas shared across the app.

The centrepiece is :class:`ScheduleSpec` — the structured, validated
representation of *any* reminder schedule. It is what the LLM emits from
natural language, what we persist (as JSON) as the source of truth, and what
the scheduling engine consumes to compute trigger times.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TimeWindow(BaseModel):
    """A local time-of-day range, ``"HH:MM"`` 24h.

    If ``end <= start`` the window is treated as crossing midnight
    (e.g. ``22:00``–``07:00``).
    """

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError(f"time must be 'HH:MM' (24h), got {v!r}")
        return v


class ScheduleSpec(BaseModel):
    """Structured schedule. Source of truth for a reminder.

    One-time reminders set ``kind='one_time'`` and ``at``. Recurring reminders
    set ``kind='recurring'`` and ``rrule`` (RFC 5545, *without* a DTSTART /
    COUNT / UNTIL line — those live in dedicated fields so window-filtered
    occurrences don't miscount).
    """

    kind: Literal["one_time", "recurring"]
    timezone: str = "UTC"

    # one-time
    at: Optional[datetime] = None

    # recurring
    rrule: Optional[str] = None
    dtstart: Optional[datetime] = None

    # constraints layered on top of the recurrence
    active_windows: Optional[List[TimeWindow]] = None
    blackout_windows: Optional[List[TimeWindow]] = None

    # bounds (tracked in DB, enforced by the worker — not embedded in rrule)
    until: Optional[datetime] = None
    count: Optional[int] = Field(default=None, ge=1)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ValueError(f"unknown IANA timezone: {v!r}")
        return v

    @field_validator("rrule")
    @classmethod
    def _valid_rrule(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        # Strip an accidental "RRULE:" prefix the model may emit.
        if v.upper().startswith("RRULE:"):
            v = v[len("RRULE:") :]
        if "FREQ=" not in v.upper():
            raise ValueError("rrule must contain FREQ=")
        # Reject the bits we intentionally keep out of the rule string.
        upper = v.upper()
        for banned in ("DTSTART", "COUNT=", "UNTIL="):
            if banned in upper:
                raise ValueError(f"rrule must not contain {banned!r}")
        # Final proof it parses.
        from dateutil.rrule import rrulestr

        try:
            rrulestr(v, dtstart=datetime(2000, 1, 1))
        except (ValueError, KeyError) as exc:
            raise ValueError(f"invalid rrule {v!r}: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "ScheduleSpec":
        if self.kind == "one_time":
            if self.at is None:
                raise ValueError("one_time schedule requires 'at'")
        else:  # recurring
            if not self.rrule:
                raise ValueError("recurring schedule requires 'rrule'")
        # Strip tzinfo from local wall-clock fields — they are interpreted in
        # `self.timezone`, never carry their own offset.
        for fld in ("at", "dtstart", "until"):
            val = getattr(self, fld)
            if val is not None and val.tzinfo is not None:
                setattr(self, fld, val.replace(tzinfo=None))
        return self


class ParseResult(BaseModel):
    """Outcome of converting natural language into a :class:`ScheduleSpec`."""

    ok: bool
    spec: Optional[ScheduleSpec] = None
    # Human-readable confirmation line, e.g. "Every day at 9:00 AM (UTC)".
    summary: Optional[str] = None
    # When the input is ambiguous/unsupported, a message to show the user.
    clarification: Optional[str] = None
    # Model-suggested title and message body for the reminder.
    name: Optional[str] = None
    content: Optional[str] = None


# --------------------------------------------------------------------------
# REST API payloads
# --------------------------------------------------------------------------


class SettingsPayload(BaseModel):
    chat_id: str
    notion_enabled: Optional[bool] = None
    notion_check_frequency: Optional[int] = None
    timezone: Optional[str] = None
