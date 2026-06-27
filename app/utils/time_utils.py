"""Time helpers: unit parsing, timezone conversion, and display formatting.

Scheduling math (RRULE + windows) lives in :mod:`app.utils.scheduling`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.common.schemas import ScheduleSpec


def parse_time_unit(unit_text: str) -> Tuple[Optional[int], Optional[str]]:
    """Map a unit word to (minutes_per_unit, normalized_name), or (None, None)."""
    unit = unit_text.strip().lower()
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return 1, "minutes"
    if unit in ("h", "hr", "hrs", "hour", "hours"):
        return 60, "hours"
    if unit in ("d", "day", "days"):
        return 60 * 24, "days"
    return None, None


def get_user_timezone(tz_name: Optional[str]) -> ZoneInfo:
    """Return a ZoneInfo for ``tz_name``, falling back to UTC."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime_for_user(dt: datetime, tz_name: Optional[str]) -> str:
    """Format a datetime in the user's timezone for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(get_user_timezone(tz_name))
    return f"{local.strftime('%Y-%m-%d %H:%M')} {local.tzname() or 'UTC'}"


def build_interval_spec(
    amount: int,
    multiplier: int,
    is_recurring: bool,
    tz_name: str = "UTC",
) -> ScheduleSpec:
    """Build a ScheduleSpec for the guided /add flow.

    A one-time reminder ``amount * multiplier`` minutes from now, or a recurring
    one on that interval. ``multiplier`` is minutes-per-unit (see
    :func:`parse_time_unit`).
    """
    if amount <= 0 or multiplier <= 0:
        raise ValueError("amount and multiplier must be positive")

    total_minutes = amount * multiplier
    tz = get_user_timezone(tz_name)
    fire_local = (
        (datetime.now(tz) + timedelta(minutes=total_minutes))
        .replace(tzinfo=None, second=0, microsecond=0)
    )

    if not is_recurring:
        return ScheduleSpec(kind="one_time", timezone=tz_name, at=fire_local)

    # Recurring: express the interval as an RRULE so the engine handles it.
    if total_minutes % (60 * 24) == 0:
        rrule = f"FREQ=DAILY;INTERVAL={total_minutes // (60 * 24)}"
    elif total_minutes % 60 == 0:
        rrule = f"FREQ=HOURLY;INTERVAL={total_minutes // 60}"
    else:
        rrule = f"FREQ=MINUTELY;INTERVAL={total_minutes}"
    return ScheduleSpec(
        kind="recurring", timezone=tz_name, rrule=rrule, dtstart=fire_local
    )
