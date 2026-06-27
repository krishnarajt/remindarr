"""The scheduling engine.

Turns a :class:`~app.common.schemas.ScheduleSpec` into concrete UTC trigger
times. Recurrence is delegated to RFC 5545 RRULEs (via ``dateutil``); on top of
that we layer time-of-day *windows* (fire only inside active windows, never
inside blackout windows). Everything is anchored to the reminder's IANA
timezone, because "10pm" and "work hours" are inherently local.

dateutil's ``rrule`` works on *naive* datetimes. The correct dance is therefore:
feed it naive local time, get naive local occurrences back, then attach the
timezone and convert to UTC. This sidesteps the well-known tz-in-rrule pitfalls.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from app.common.schemas import ScheduleSpec, TimeWindow

# Safety cap: how many occurrences to scan while skipping window-filtered or
# backlogged times before giving up (prevents an infinite loop on a spec whose
# windows can never be satisfied).
_MAX_SCAN = 5000


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def in_window(t: time, w: TimeWindow) -> bool:
    """Is ``t`` inside window ``w``? ``end <= start`` means it crosses midnight."""
    start = _parse_hhmm(w.start)
    end = _parse_hhmm(w.end)
    if start <= end:
        return start <= t < end
    # crosses midnight, e.g. 22:00–07:00
    return t >= start or t < end


def passes_windows(local_dt: datetime, spec: ScheduleSpec) -> bool:
    """A candidate (naive local) time is allowed iff it is inside *some* active
    window (when any are defined) and outside *all* blackout windows."""
    t = local_dt.time()
    if spec.active_windows:
        if not any(in_window(t, w) for w in spec.active_windows):
            return False
    if spec.blackout_windows:
        if any(in_window(t, w) for w in spec.blackout_windows):
            return False
    return True


def _as_utc(dt: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC (assume naive == UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local_to_utc(naive_local: datetime, tz: ZoneInfo) -> datetime:
    return naive_local.replace(tzinfo=tz).astimezone(timezone.utc)


def compute_next_trigger(spec: ScheduleSpec, after_utc: datetime) -> Optional[datetime]:
    """Return the next UTC trigger strictly after ``after_utc``, or ``None``.

    ``None`` means the schedule has no further occurrences (one-time already
    past, RRULE exhausted by ``until``, or no window-satisfying time within the
    scan cap).
    """
    after_utc = _as_utc(after_utc)
    tz = ZoneInfo(spec.timezone)

    if spec.kind == "one_time":
        if spec.at is None:
            return None
        fire_utc = _local_to_utc(spec.at, tz)
        return fire_utc if fire_utc > after_utc else None

    # ----- recurring -----------------------------------------------------
    after_local = after_utc.astimezone(tz).replace(tzinfo=None)
    dtstart_local = spec.dtstart or after_local
    until_local = spec.until

    rule = rrulestr(spec.rrule, dtstart=dtstart_local)

    cursor = after_local
    for _ in range(_MAX_SCAN):
        nxt = rule.after(cursor, inc=False)
        if nxt is None:
            return None
        if until_local is not None and nxt > until_local:
            return None
        if passes_windows(nxt, spec):
            return _local_to_utc(nxt, tz)
        cursor = nxt
    # Scan cap hit: schedule's windows are effectively unsatisfiable.
    return None


def advance_past_backlog(
    spec: ScheduleSpec, last_scheduled_utc: datetime, now_utc: datetime
) -> Optional[datetime]:
    """Next trigger after the one that just fired, skipping any missed
    occurrences (e.g. while the worker was down) *without* firing them.

    Walking occurrence-by-occurrence from the scheduled time keeps the RRULE
    cadence (no drift); we just don't emit the backlog.
    """
    now_utc = _as_utc(now_utc)
    nxt = compute_next_trigger(spec, last_scheduled_utc)
    scanned = 0
    while nxt is not None and nxt <= now_utc and scanned < _MAX_SCAN:
        nxt = compute_next_trigger(spec, nxt)
        scanned += 1
    return nxt


# --------------------------------------------------------------------------
# Human-readable description (fallback when the LLM doesn't supply a summary)
# --------------------------------------------------------------------------

_FREQ_WORD = {
    "MINUTELY": "minute",
    "HOURLY": "hour",
    "DAILY": "day",
    "WEEKLY": "week",
    "MONTHLY": "month",
    "YEARLY": "year",
}


def describe_spec(spec: ScheduleSpec) -> str:
    """A best-effort plain-English summary. The LLM normally provides a nicer
    one; this is the deterministic fallback used by the guided flow and tests."""
    if spec.kind == "one_time" and spec.at is not None:
        return f"Once on {spec.at.strftime('%Y-%m-%d %H:%M')} ({spec.timezone})"

    parts = []
    rule = (spec.rrule or "").upper()
    freq = next((f for f in _FREQ_WORD if f"FREQ={f}" in rule), None)
    if freq:
        parts.append(f"Every {_FREQ_WORD[freq]}")
    else:
        parts.append("Recurring")

    if spec.active_windows:
        wins = ", ".join(f"{w.start}–{w.end}" for w in spec.active_windows)
        parts.append(f"only during {wins}")
    if spec.blackout_windows:
        wins = ", ".join(f"{w.start}–{w.end}" for w in spec.blackout_windows)
        parts.append(f"except {wins}")
    parts.append(f"({spec.timezone})")
    return " ".join(parts)
