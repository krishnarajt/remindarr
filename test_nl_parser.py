"""Offline tests for natural-language → ScheduleSpec parsing.

The LLM gateway is stubbed with canned JSON so these run without network/keys.
Run with: python -m pytest test_nl_parser.py -v
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo


def _stub(monkeypatch, payload: dict):
    from app.services import nl_parser

    monkeypatch.setattr(nl_parser.gateway, "api_key", "test-key")
    monkeypatch.setattr(nl_parser.gateway, "chat", lambda *a, **k: json.dumps(payload))
    return nl_parser


def test_parse_recurring(monkeypatch):
    nl_parser = _stub(
        monkeypatch,
        {
            "ok": True,
            "summary": "Every day at 9:00 (UTC)",
            "spec": {"kind": "recurring", "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"},
            "reminder_name": "Drink coffee",
            "reminder_content": "Time for coffee ☕",
        },
    )
    res = nl_parser.parse_reminder(
        "every day at 9am coffee", now_local=datetime.now(ZoneInfo("UTC")), user_tz="UTC"
    )
    assert res.ok
    assert res.spec.kind == "recurring"
    assert res.name == "Drink coffee"
    assert res.content == "Time for coffee ☕"


def test_parse_water_windows(monkeypatch):
    nl_parser = _stub(
        monkeypatch,
        {
            "ok": True,
            "summary": "Every hour during work hours (UTC)",
            "spec": {
                "kind": "recurring",
                "timezone": "UTC",
                "rrule": "FREQ=HOURLY;BYMINUTE=0",
                "active_windows": [{"start": "09:00", "end": "18:00"}],
                "blackout_windows": [{"start": "22:00", "end": "07:00"}],
            },
            "reminder_name": "Drink water",
            "reminder_content": "💧 Hydrate!",
        },
    )
    res = nl_parser.parse_reminder(
        "drink water every hour during work hours but not at night",
        now_local=datetime.now(ZoneInfo("UTC")),
        user_tz="UTC",
    )
    assert res.ok
    assert res.spec.active_windows[0].start == "09:00"
    assert res.spec.blackout_windows[0].end == "07:00"


def test_parse_clarification(monkeypatch):
    nl_parser = _stub(
        monkeypatch,
        {"ok": False, "clarification": "When should I remind you?"},
    )
    res = nl_parser.parse_reminder(
        "remind me to do stuff", now_local=datetime.now(ZoneInfo("UTC")), user_tz="UTC"
    )
    assert not res.ok
    assert "When" in res.clarification


def test_parse_does_not_send_provider_specific_response_format(monkeypatch):
    from app.services import nl_parser

    payload = {
        "ok": True,
        "summary": "Every day at 9:00 (UTC)",
        "spec": {"kind": "recurring", "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"},
    }
    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return json.dumps(payload)

    monkeypatch.setattr(nl_parser.gateway, "api_key", "test-key")
    monkeypatch.setattr(nl_parser.gateway, "chat", fake_chat)

    res = nl_parser.parse_reminder(
        "every day at 9am", now_local=datetime.now(ZoneInfo("UTC")), user_tz="UTC"
    )

    assert res.ok
    assert calls
    assert "extra" not in calls[0]


def test_parse_invalid_json_then_gives_up(monkeypatch):
    from app.services import nl_parser

    monkeypatch.setattr(nl_parser.gateway, "api_key", "test-key")
    monkeypatch.setattr(nl_parser.gateway, "chat", lambda *a, **k: "not json at all")
    res = nl_parser.parse_reminder(
        "blah", now_local=datetime.now(ZoneInfo("UTC")), user_tz="UTC"
    )
    assert not res.ok
    assert res.clarification


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
