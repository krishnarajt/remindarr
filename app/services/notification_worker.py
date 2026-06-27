"""Background reminder worker.

Polls for due reminders, sends them, and reschedules using the stored
:class:`ScheduleSpec`. Key properties:

- **No drift**: the next occurrence is derived from the RRULE relative to the
  scheduled time, not wall-clock ``now``.
- **No backlog floods**: occurrences missed while the worker was down are
  skipped (advanced past) rather than fired N times.
- **Non-blocking**: the whole poll-and-send batch runs in a worker thread via
  ``asyncio.to_thread`` so the event loop stays responsive; Telegram I/O is
  synchronous there, which is fine.
"""

from __future__ import annotations

import asyncio

from sqlmodel import Session, select

from app.db import config_db
from app.db.models import Reminders
from app.services import reminder_service
from app.services.telegram import esc, send_message
from app.utils.logging_utils import logger
from app.utils.scheduling import advance_past_backlog
from app.utils.time_utils import format_datetime_for_user, now_utc

CHECK_INTERVAL_SECONDS = 60


def _fired_buttons(reminder: Reminders):
    if reminder.schedule_kind == "recurring":
        return [[("✅ Done", f"rem:done:{reminder.id}"), ("⏸ Pause", f"rem:pause:{reminder.id}")]]
    return None


def _format_message(reminder: Reminders) -> str:
    local = format_datetime_for_user(now_utc(), reminder.timezone)
    return (
        f"🔔 <b>{esc(reminder.reminder_name)}</b>\n\n"
        f"{esc(reminder.reminder_content)}\n\n"
        f"⏰ {esc(local)}"
    )


def _reschedule(reminder: Reminders, now) -> None:
    """Mutate the reminder in place to its next state after firing."""
    reminder.last_triggered_at = now
    reminder.occurrences_sent += 1

    # Count bound reached?
    if reminder.max_occurrences and reminder.occurrences_sent >= reminder.max_occurrences:
        reminder.active = False
        reminder.next_trigger_at = None
        return

    if reminder.schedule_kind == "one_time":
        reminder.active = False
        reminder.next_trigger_at = None
        return

    spec = reminder_service.spec_of(reminder)
    last_scheduled = reminder.next_trigger_at or now
    next_trigger = advance_past_backlog(spec, last_scheduled, now)
    if next_trigger is None:
        reminder.active = False
        reminder.next_trigger_at = None
    else:
        reminder.next_trigger_at = next_trigger


def process_due_reminders() -> None:
    """One synchronous poll-and-send pass."""
    now = now_utc()
    with Session(config_db.engine) as db:
        stmt = (
            select(Reminders)
            .where(Reminders.active == True)  # noqa: E712
            .where(Reminders.paused == False)  # noqa: E712
            .where(Reminders.next_trigger_at != None)  # noqa: E711
            .where(Reminders.next_trigger_at <= now)
            .with_for_update(skip_locked=True)
        )
        due = list(db.exec(stmt).all())

        for reminder in due:
            try:
                sent = send_message(
                    reminder.chat_id,
                    _format_message(reminder),
                    buttons=_fired_buttons(reminder),
                )
                if not sent:
                    # Transient send failure — leave it due and retry next tick.
                    logger.warning("Send failed for reminder %s; will retry", reminder.id)
                    continue
                _reschedule(reminder, now)
                db.add(reminder)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to process reminder %s: %s", reminder.id, exc)

        db.commit()


async def _reminder_loop(app) -> None:
    stop_event: asyncio.Event = app.state._reminder_stop
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(process_due_reminders)
        except Exception as exc:  # noqa: BLE001
            logger.error("Reminder worker error: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


def start_worker(app) -> None:
    app.state._reminder_stop = asyncio.Event()
    app.state._reminder_task = asyncio.create_task(_reminder_loop(app))


async def stop_worker(app) -> None:
    if not hasattr(app.state, "_reminder_stop"):
        return
    app.state._reminder_stop.set()
    task = getattr(app.state, "_reminder_task", None)
    if task:
        await task
