"""CRUD + scheduling glue for reminders.

Persisting a reminder means: store the :class:`ScheduleSpec` as the JSON source
of truth, denormalise a few columns for convenience, and materialise the first
``next_trigger_at`` so the poller can pick it up.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.common.schemas import ScheduleSpec
from app.db.models import Reminders
from app.utils.scheduling import compute_next_trigger
from app.utils.time_utils import now_utc


def _local_to_utc(naive_local: Optional[datetime], tz_name: str) -> Optional[datetime]:
    if naive_local is None:
        return None
    return naive_local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC"))


def create_from_spec(
    db: Session,
    *,
    chat_id: str,
    name: str,
    content: str,
    spec: ScheduleSpec,
    source: str = "llm",
    nl_text: Optional[str] = None,
    notion_db_id: Optional[str] = None,
    notion_page_id: Optional[str] = None,
) -> Optional[Reminders]:
    """Create and persist a reminder from a validated spec.

    Returns ``None`` if the spec yields no future occurrence.
    """
    # Anchor recurring schedules to a stable dtstart so the cadence is stable.
    # Truncate to the minute so BYHOUR/BYMINUTE rules don't inherit stray
    # seconds from "now" and fire at e.g. 09:00:37.
    if spec.kind == "recurring" and spec.dtstart is None:
        spec.dtstart = (
            datetime.now(ZoneInfo(spec.timezone))
            .replace(tzinfo=None, second=0, microsecond=0)
        )

    next_trigger = compute_next_trigger(spec, now_utc())
    if next_trigger is None:
        return None

    reminder = Reminders(
        chat_id=str(chat_id),
        reminder_name=name,
        reminder_content=content,
        source=source,
        timezone=spec.timezone,
        schedule_kind=spec.kind,
        rrule=spec.rrule,
        dtstart=_local_to_utc(spec.dtstart, spec.timezone),
        schedule_spec=spec.model_dump(mode="json", exclude_none=True),
        nl_text=nl_text,
        max_occurrences=spec.count,
        until_at=_local_to_utc(spec.until, spec.timezone),
        next_trigger_at=next_trigger,
        notion_db_id=notion_db_id,
        notion_page_id=notion_page_id,
        active=True,
        paused=False,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def spec_of(reminder: Reminders) -> ScheduleSpec:
    """Rehydrate the ScheduleSpec from the stored JSON."""
    return ScheduleSpec.model_validate(reminder.schedule_spec)


def list_reminders(db: Session, chat_id: str, *, active_only: bool = True) -> List[Reminders]:
    stmt = select(Reminders).where(Reminders.chat_id == str(chat_id))
    if active_only:
        stmt = stmt.where(Reminders.active == True)  # noqa: E712
    stmt = stmt.order_by(Reminders.next_trigger_at.is_(None), Reminders.next_trigger_at)
    return list(db.exec(stmt).all())


def get_reminder(db: Session, reminder_id: str, chat_id: str) -> Optional[Reminders]:
    reminder = db.get(Reminders, reminder_id)
    if reminder and reminder.chat_id == str(chat_id):
        return reminder
    return None


def delete_reminder(db: Session, reminder_id: str, chat_id: str) -> bool:
    reminder = get_reminder(db, reminder_id, chat_id)
    if not reminder:
        return False
    db.delete(reminder)
    db.commit()
    return True


def set_paused(db: Session, reminder_id: str, chat_id: str, paused: bool) -> Optional[Reminders]:
    reminder = get_reminder(db, reminder_id, chat_id)
    if not reminder:
        return None
    reminder.paused = paused
    reminder.updated_at = now_utc()
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def mark_done(db: Session, reminder_id: str, chat_id: str) -> Optional[Reminders]:
    """Deactivate a reminder (user pressed 'Done')."""
    reminder = get_reminder(db, reminder_id, chat_id)
    if not reminder:
        return None
    reminder.active = False
    reminder.next_trigger_at = None
    reminder.updated_at = now_utc()
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder
