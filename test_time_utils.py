"""Unit tests for the scheduling engine and time helpers.

Run with: python -m pytest test_time_utils.py -v
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.common.schemas import ScheduleSpec, TimeWindow
from app.utils.scheduling import (
    advance_past_backlog,
    compute_next_trigger,
    in_window,
    passes_windows,
)
from app.utils.time_utils import build_interval_spec, parse_time_unit

UTC = timezone.utc


# --------------------------------------------------------------------------
# parse_time_unit
# --------------------------------------------------------------------------


def test_parse_units():
    assert parse_time_unit("minutes") == (1, "minutes")
    assert parse_time_unit("hours") == (60, "hours")
    assert parse_time_unit("days") == (60 * 24, "days")
    assert parse_time_unit("invalid") == (None, None)


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------


def test_in_window_basic():
    w = TimeWindow(start="09:00", end="18:00")
    assert in_window(time(9, 0), w)
    assert in_window(time(12, 0), w)
    assert not in_window(time(18, 0), w)  # end is exclusive
    assert not in_window(time(8, 59), w)


def test_in_window_crosses_midnight():
    w = TimeWindow(start="22:00", end="07:00")
    assert in_window(time(23, 0), w)
    assert in_window(time(2, 0), w)
    assert in_window(time(22, 0), w)
    assert not in_window(time(7, 0), w)
    assert not in_window(time(12, 0), w)


def test_passes_windows_active_and_blackout():
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=HOURLY",
        active_windows=[TimeWindow(start="09:00", end="18:00")],
        blackout_windows=[TimeWindow(start="13:00", end="14:00")],
    )
    assert passes_windows(datetime(2026, 1, 1, 10, 0), spec)
    assert not passes_windows(datetime(2026, 1, 1, 13, 30), spec)  # lunch blackout
    assert not passes_windows(datetime(2026, 1, 1, 20, 0), spec)  # outside active


# --------------------------------------------------------------------------
# compute_next_trigger — one-time
# --------------------------------------------------------------------------


def test_one_time_future():
    spec = ScheduleSpec(kind="one_time", timezone="UTC", at=datetime(2026, 6, 22, 15, 0))
    after = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    nxt = compute_next_trigger(spec, after)
    assert nxt == datetime(2026, 6, 22, 15, 0, tzinfo=UTC)


def test_one_time_past_returns_none():
    spec = ScheduleSpec(kind="one_time", timezone="UTC", at=datetime(2026, 6, 22, 10, 0))
    after = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    assert compute_next_trigger(spec, after) is None


def test_one_time_timezone_conversion():
    # 09:00 in Asia/Kolkata (UTC+5:30) == 03:30 UTC
    spec = ScheduleSpec(kind="one_time", timezone="Asia/Kolkata", at=datetime(2026, 6, 22, 9, 0))
    after = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
    nxt = compute_next_trigger(spec, after)
    assert nxt == datetime(2026, 6, 22, 3, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# compute_next_trigger — recurring (the headline examples)
# --------------------------------------------------------------------------


def test_daily_at_9am():
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        dtstart=datetime(2026, 6, 1, 0, 0),
    )
    after = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)  # past 9am today
    nxt = compute_next_trigger(spec, after)
    assert nxt == datetime(2026, 6, 23, 9, 0, tzinfo=UTC)  # tomorrow 9am


def test_odd_saturdays_even_months():
    # 1st/3rd/5th Saturday of even months at 9am.
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=MONTHLY;BYMONTH=2,4,6,8,10,12;BYDAY=SA;BYSETPOS=1,3,5;BYHOUR=9;BYMINUTE=0",
        dtstart=datetime(2026, 1, 1, 0, 0),
    )
    after = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    nxt = compute_next_trigger(spec, after)
    # June 2026: Saturdays are 6,13,20,27 → 1st=6th, 3rd=20th. First after Jun 1 = Jun 6 09:00.
    assert nxt == datetime(2026, 6, 6, 9, 0, tzinfo=UTC)
    # Month must be even.
    assert nxt.month % 2 == 0


def test_water_during_work_hours_skips_night():
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=HOURLY;BYMINUTE=0",
        active_windows=[TimeWindow(start="09:00", end="18:00")],
        blackout_windows=[TimeWindow(start="22:00", end="07:00")],
        dtstart=datetime(2026, 6, 22, 0, 0),
    )
    # At 19:00 the next fire should jump to 09:00 next day (outside active window).
    after = datetime(2026, 6, 22, 19, 0, tzinfo=UTC)
    nxt = compute_next_trigger(spec, after)
    assert nxt == datetime(2026, 6, 23, 9, 0, tzinfo=UTC)


def test_until_exhaustion():
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        dtstart=datetime(2026, 6, 1, 0, 0),
        until=datetime(2026, 6, 3, 0, 0),
    )
    # After the until bound there are no more occurrences.
    after = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    assert compute_next_trigger(spec, after) is None


# --------------------------------------------------------------------------
# backlog skipping
# --------------------------------------------------------------------------


def test_advance_past_backlog_no_flood():
    spec = ScheduleSpec(
        kind="recurring",
        timezone="UTC",
        rrule="FREQ=HOURLY;BYMINUTE=0",
        dtstart=datetime(2026, 6, 22, 0, 0),
    )
    # Reminder last scheduled at 10:00 but the worker was down until 15:00.
    last_scheduled = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    now = datetime(2026, 6, 22, 15, 5, tzinfo=UTC)
    nxt = advance_past_backlog(spec, last_scheduled, now)
    # Should be the first occurrence strictly after now, i.e. 16:00 — not 11:00.
    assert nxt == datetime(2026, 6, 22, 16, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# build_interval_spec (guided /add)
# --------------------------------------------------------------------------


def test_build_interval_spec_one_time():
    spec = build_interval_spec(30, 1, is_recurring=False, tz_name="UTC")
    assert spec.kind == "one_time"
    assert spec.at is not None


def test_build_interval_spec_recurring_hours():
    spec = build_interval_spec(2, 60, is_recurring=True, tz_name="UTC")
    assert spec.kind == "recurring"
    assert "FREQ=HOURLY;INTERVAL=2" in spec.rrule


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
