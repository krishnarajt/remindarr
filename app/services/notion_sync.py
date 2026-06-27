"""Periodic Notion → reminders synchronisation.

Previously the ``notion_check_frequency`` setting was dead: nothing ever
re-queried Notion. This module runs a background loop that, per user and at the
configured cadence, imports incomplete tasks due soon as one-time reminders,
de-duplicated by ``notion_page_id`` (so re-syncing updates rather than
duplicates). It also provides the deletion path that the audit flagged as
missing: removing a Notion DB deletes the reminders it created.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.common.schemas import ScheduleSpec
from app.db import config_db
from app.db.models import Reminders, Users
from app.services import notion
from app.services.reminder_service import create_from_spec
from app.utils.logging_utils import logger
from app.utils.scheduling import compute_next_trigger
from app.utils.time_utils import now_utc

SYNC_TICK_SECONDS = 600  # how often we look for users due a Notion refresh
DEFAULT_DUE_HOUR = 9  # date-only Notion due dates fire at 09:00 local


def _frequency_hours(user: Users) -> int:
    # Defensive: tolerate the legacy misspelled attribute if present.
    return getattr(user, "notion_check_frequency", None) or getattr(
        user, "notion_check_frequence", 12
    )


def _due_for_sync(user: Users, now: datetime) -> bool:
    if not (user.notion_enabled and user.notion_api_key):
        return False
    last = user.notion_last_synced_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=ZoneInfo("UTC"))
    return now - last >= timedelta(hours=_frequency_hours(user))


def _parse_due(time_val: str, tz_name: str) -> Optional[datetime]:
    """Parse a Notion date string into a naive local datetime."""
    try:
        raw = time_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # Date-only ("YYYY-MM-DD")
        try:
            d = datetime.fromisoformat(time_val[:10])
            return d.replace(hour=DEFAULT_DUE_HOUR, minute=0, second=0, microsecond=0)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        dt = dt.replace(hour=DEFAULT_DUE_HOUR)
    return dt


def import_database(db: Session, user: Users, mapping: dict) -> Tuple[int, int]:
    """Import incomplete due tasks for one mapped database. Returns (imported, skipped)."""
    db_id = mapping.get("db_id")
    name_prop = mapping.get("name_prop")
    time_prop = mapping.get("time_prop")
    status_prop = mapping.get("status_prop")
    status_prop_type = mapping.get("status_prop_type")
    complete_values = mapping.get("status_complete_values")
    tz_name = user.timezone or "UTC"

    ok, pages = notion.query_incomplete_due(
        user.notion_api_key,
        db_id,
        time_prop=time_prop,
        status_prop=status_prop,
        status_prop_type=status_prop_type,
        complete_values=complete_values,
    )
    if not ok:
        return 0, 0

    imported = skipped = 0
    for page in pages:
        try:
            props = page.get("properties", {})
            name_val = notion.extract_property_value(props.get(name_prop))
            if not name_val:
                skipped += 1
                continue

            status_val = notion.extract_property_value(props.get(status_prop)) if status_prop else None
            if notion.is_done(status_val, status_prop_type, complete_values):
                skipped += 1
                continue

            time_val = notion.extract_property_value(props.get(time_prop)) if time_prop else None
            at_local = _parse_due(time_val, tz_name) if time_val else None
            if at_local is None:
                skipped += 1
                continue

            page_id = page.get("id")
            spec = ScheduleSpec(kind="one_time", timezone=tz_name, at=at_local)
            next_trigger = compute_next_trigger(spec, now_utc())
            if next_trigger is None:
                # Due time already past — nothing to schedule.
                skipped += 1
                continue

            existing = db.exec(
                select(Reminders).where(
                    Reminders.chat_id == user.chat_id,
                    Reminders.notion_page_id == page_id,
                )
            ).first()

            if existing:
                existing.reminder_name = name_val
                existing.reminder_content = name_val
                existing.schedule_spec = spec.model_dump(mode="json", exclude_none=True)
                existing.next_trigger_at = next_trigger
                existing.active = True
                db.add(existing)
            else:
                create_from_spec(
                    db,
                    chat_id=user.chat_id,
                    name=name_val,
                    content=name_val,
                    spec=spec,
                    source="notion",
                    notion_db_id=db_id,
                    notion_page_id=page_id,
                )
            imported += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Notion import error for user %s: %s", user.chat_id, exc)
            skipped += 1

    db.commit()
    return imported, skipped


def sync_user(db: Session, user: Users) -> Tuple[int, int]:
    total_imported = total_skipped = 0
    for mapping in user.notion_db_mappings or []:
        imp, skp = import_database(db, user, mapping)
        total_imported += imp
        total_skipped += skp
    user.notion_last_synced_at = now_utc()
    db.add(user)
    db.commit()
    return total_imported, total_skipped


def delete_db_reminders(db: Session, chat_id: str, db_id: str) -> int:
    """Delete reminders created from a given Notion database. Returns count."""
    rows: List[Reminders] = list(
        db.exec(
            select(Reminders).where(
                Reminders.chat_id == str(chat_id), Reminders.notion_db_id == db_id
            )
        ).all()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def delete_all_notion_reminders(db: Session, chat_id: str) -> int:
    rows: List[Reminders] = list(
        db.exec(
            select(Reminders).where(
                Reminders.chat_id == str(chat_id), Reminders.source == "notion"
            )
        ).all()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def _run_due_syncs() -> None:
    """One synchronous pass: sync every user whose cadence is due."""
    now = now_utc()
    with Session(config_db.engine) as db:
        users = list(db.exec(select(Users).where(Users.notion_enabled == True)).all())  # noqa: E712
        for user in users:
            if _due_for_sync(user, now):
                try:
                    imp, skp = sync_user(db, user)
                    logger.info(
                        "Notion sync for %s: imported=%d skipped=%d", user.chat_id, imp, skp
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("Notion sync failed for %s: %s", user.chat_id, exc)


async def _sync_loop(app) -> None:
    stop_event: asyncio.Event = app.state._notion_stop
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_run_due_syncs)
        except Exception as exc:  # noqa: BLE001
            logger.error("Notion sync loop error: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=SYNC_TICK_SECONDS)
        except asyncio.TimeoutError:
            continue


def start_notion_sync(app) -> None:
    app.state._notion_stop = asyncio.Event()
    app.state._notion_task = asyncio.create_task(_sync_loop(app))


async def stop_notion_sync(app) -> None:
    if not hasattr(app.state, "_notion_stop"):
        return
    app.state._notion_stop.set()
    task = getattr(app.state, "_notion_task", None)
    if task:
        await task
