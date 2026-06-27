"""Notion API client.

Fixes carried over from the audit:
- The "incomplete tasks" filter for select/status properties used an ``or`` of
  two ``does_not_equal`` clauses, which is *always true* (a value can't equal
  both "Done" and "Completed"). It now ANDs a ``does_not_equal`` per
  completed-value, so completed tasks are actually excluded.
- ``extract_notion_property_value`` now handles the ``status`` property type
  (Notion's most common "done" field), which was previously ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import httpx

from app.utils.logging_utils import logger

_NOTION_VERSION = "2022-06-28"
_client = httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))

# Default values that mean "this task is finished" for select/status properties.
DEFAULT_COMPLETE_VALUES = ("done", "complete", "completed", "finished", "archived")


def _headers(token: str, json_body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def validate_token(token: str) -> Tuple[bool, Optional[dict]]:
    try:
        resp = _client.get("https://api.notion.com/v1/users/me", headers=_headers(token))
        if resp.status_code == 200:
            return True, resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Notion token validation error: %s", exc)
    return False, None


def get_database(token: str, db_id: str) -> Tuple[bool, Optional[dict]]:
    try:
        resp = _client.get(
            f"https://api.notion.com/v1/databases/{db_id}", headers=_headers(token)
        )
        if resp.status_code == 200:
            return True, resp.json()
        logger.error("Failed to fetch Notion DB %s: %s", db_id, resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.error("Notion database fetch error: %s", exc)
    return False, None


def query_incomplete_due(
    token: str,
    db_id: str,
    *,
    time_prop: Optional[str] = None,
    status_prop: Optional[str] = None,
    status_prop_type: Optional[str] = None,
    complete_values: Optional[List[str]] = None,
    horizon_hours: int = 24,
) -> Tuple[bool, list]:
    """Query incomplete tasks due within ``horizon_hours``."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"

    filters: list = []
    if time_prop:
        filters.append(
            {
                "property": time_prop,
                "date": {"on_or_after": now.strftime(fmt), "on_or_before": horizon.strftime(fmt)},
            }
        )

    if status_prop:
        values = [v for v in (complete_values or DEFAULT_COMPLETE_VALUES)]
        if status_prop_type == "checkbox":
            filters.append({"property": status_prop, "checkbox": {"equals": False}})
        elif status_prop_type in ("select", "status"):
            # AND a does_not_equal per completed value (the previous OR was a no-op).
            for val in values:
                filters.append(
                    {"property": status_prop, status_prop_type: {"does_not_equal": val}}
                )

    body: dict = {}
    if len(filters) == 1:
        body["filter"] = filters[0]
    elif filters:
        body["filter"] = {"and": filters}

    try:
        resp = _client.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=_headers(token, json_body=True),
            json=body,
        )
        if resp.status_code == 200:
            return True, resp.json().get("results", [])
        logger.error("Failed to query Notion DB %s: %s", db_id, resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.error("Notion database query error: %s", exc)
    return False, []


def extract_property_value(prop_data: Optional[dict]):
    """Extract a usable value from a Notion property cell, by its type."""
    if not prop_data:
        return None
    ptype = prop_data.get("type")

    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop_data.get("title", [])).strip() or None
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop_data.get("rich_text", [])).strip() or None
    if ptype == "date":
        dt = prop_data.get("date")
        return dt.get("start") if dt else None
    if ptype == "checkbox":
        return prop_data.get("checkbox")
    if ptype == "select":
        sel = prop_data.get("select")
        return sel.get("name") if sel else None
    if ptype == "status":  # previously unhandled
        st = prop_data.get("status")
        return st.get("name") if st else None
    return None


def is_done(value, prop_type: Optional[str], complete_values: Optional[List[str]] = None) -> bool:
    """Decide whether an extracted status value means 'completed'."""
    if prop_type == "checkbox":
        return value is True
    if value is None:
        return False
    finished = {v.lower() for v in (complete_values or DEFAULT_COMPLETE_VALUES)}
    return str(value).strip().lower() in finished
