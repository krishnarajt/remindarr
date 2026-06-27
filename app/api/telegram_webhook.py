"""Telegram webhook: routes messages and button callbacks.

UX model:
- **Natural language first.** Any free text (no active flow) is sent to the LLM
  to become a structured schedule; the user confirms with a button.
- **Buttons everywhere.** Menus, the reminder list (with per-item Done/Pause/
  Delete), Notion setup, and settings are all inline keyboards.
- A guided ``/add`` flow remains as a no-LLM fallback.

The HTTP handler is async but offloads all work to a thread (``asyncio.to_thread``)
so the event loop never blocks on Telegram / DB / LLM I/O.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.api import state as st
from app.api.state import FlowType, UserState, clear_state, get_state
from app.common.constants import settings
from app.common.schemas import ScheduleSpec
from app.db import config_db
from app.db.models import Users
from app.services import notion, notion_sync, reminder_service
from app.services.nl_parser import parse_reminder
from app.services.telegram import (
    answer_callback_query,
    edit_message_text,
    esc,
    menu_keyboard,
    send_message,
)
from app.utils.logging_utils import logger
from app.utils.time_utils import build_interval_spec, format_datetime_for_user, now_utc, parse_time_unit

router = APIRouter(prefix="/notifications", tags=["notifications"])

WELCOME = (
    "👋 <b>Welcome to Remindarr!</b>\n\n"
    "Just tell me what to remind you about — in plain English. For example:\n"
    "• <i>remind me to call mom in 2 hours</i>\n"
    "• <i>drink water every hour during work hours, not at night</i>\n"
    "• <i>taxes on the 1st &amp; 3rd Saturday of even months at 9am</i>\n\n"
    "Or use the buttons below."
)

HELP = (
    "📚 <b>Remindarr Help</b>\n\n"
    "<b>Natural language</b> — just type what you want:\n"
    "  <i>“water every hour 9–6 except lunch”</i>\n"
    "I'll show you what I understood and you confirm with a tap.\n\n"
    "<b>Commands</b>\n"
    "/add — guided reminder setup\n"
    "/list — view &amp; manage reminders\n"
    "/notion — connect a Notion database\n"
    "/settings — preferences\n"
    "/tz &lt;Area/City&gt; — set your timezone (e.g. <code>/tz Asia/Kolkata</code>)\n"
    "/cancel — abort the current step\n"
)


# ==========================================================================
# HTTP entry point
# ==========================================================================


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    # Verify Telegram's secret token if one is configured (anti-spoofing).
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            return JSONResponse({"status": "forbidden"}, status_code=403)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "error", "reason": "invalid json"}, status_code=400)

    try:
        await asyncio.to_thread(handle_update, data)
    except Exception as exc:  # noqa: BLE001
        logger.error("Webhook handling error: %s", exc, exc_info=True)
    return {"status": "ok"}


def handle_update(data: dict) -> None:
    with Session(config_db.engine) as db:
        if "callback_query" in data:
            handle_callback(db, data["callback_query"])
        elif "message" in data and data["message"].get("text"):
            handle_message(db, data["message"])


# ==========================================================================
# User helpers
# ==========================================================================


def get_or_create_user(db: Session, chat: dict, from_user: dict) -> Users:
    chat_id = str(chat["id"])
    user = db.get(Users, chat_id)
    if not user:
        user = Users(
            chat_id=chat_id,
            username=from_user.get("username"),
            first_name=from_user.get("first_name"),
            language_code=from_user.get("language_code"),
            is_bot=from_user.get("is_bot", False),
            timezone=settings.default_timezone,
        )
        db.add(user)
        logger.info("Created user %s", chat_id)
    else:
        user.username = from_user.get("username", user.username)
        user.first_name = from_user.get("first_name", user.first_name)
        user.last_active_at = now_utc()
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _user_tz(user: Users) -> str:
    return user.timezone or settings.default_timezone


# ==========================================================================
# Message handling
# ==========================================================================


def handle_message(db: Session, message: dict) -> None:
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    user = get_or_create_user(db, message["chat"], message.get("from", {}))
    state = get_state(chat_id)

    if text.startswith("/"):
        handle_command(db, chat_id, text, user, state)
        return

    if state.flow == FlowType.ADD:
        add_flow_text(db, chat_id, text, user, state)
        return
    if state.flow == FlowType.NOTION:
        notion_flow_text(db, chat_id, text, user, state)
        return

    # No active flow → treat as a natural-language reminder.
    nl_create(db, chat_id, text, user, state)


def handle_command(db: Session, chat_id, text: str, user: Users, state: UserState) -> None:
    cmd = text.split()[0].lower()
    clear_state(chat_id)

    if cmd == "/start":
        send_message(chat_id, WELCOME, buttons=menu_keyboard())
    elif cmd == "/help":
        send_message(chat_id, HELP, buttons=[[("🏠 Menu", "menu:home")]])
    elif cmd == "/list":
        body, kb = render_list(db, str(chat_id))
        send_message(chat_id, body, buttons=kb)
    elif cmd == "/add":
        start_add(db, chat_id, state)
    elif cmd == "/notion":
        open_notion(db, chat_id, user, state)
    elif cmd == "/settings":
        body, kb = render_settings(user)
        send_message(chat_id, body, buttons=kb)
    elif cmd == "/tz":
        set_timezone(db, chat_id, text, user)
    elif cmd in ("/cancel", "/stop"):
        send_message(chat_id, "❌ Cancelled.", buttons=menu_keyboard())
    else:
        send_message(chat_id, "Unknown command. Try /help.", buttons=menu_keyboard())


def set_timezone(db: Session, chat_id, text: str, user: Users) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        send_message(chat_id, "Usage: <code>/tz Area/City</code> e.g. <code>/tz Asia/Kolkata</code>")
        return
    tz_name = parts[1].strip()
    try:
        ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        send_message(chat_id, f"❌ Unknown timezone: <code>{esc(tz_name)}</code>")
        return
    user.timezone = tz_name
    db.add(user)
    db.commit()
    send_message(chat_id, f"✅ Timezone set to <b>{esc(tz_name)}</b>.")


# ==========================================================================
# Natural-language reminder creation
# ==========================================================================


def nl_create(db: Session, chat_id, text: str, user: Users, state: UserState) -> None:
    tz = _user_tz(user)
    now_local = datetime.now(ZoneInfo(tz))
    result = parse_reminder(text, now_local=now_local, user_tz=tz)

    if not result.ok or result.spec is None:
        send_message(
            chat_id,
            "🤔 " + esc(result.clarification or "I couldn't understand that."),
            buttons=[[("➕ Guided add", "menu:add")], [("🏠 Menu", "menu:home")]],
        )
        return

    state.set(FlowType.CONFIRM)
    state.data = {
        "spec": result.spec.model_dump(mode="json", exclude_none=True),
        "name": (result.name or text)[:120],
        "content": result.content or text,
        "nl": text,
    }
    body = (
        "🧠 Here's what I understood:\n\n"
        f"📝 <b>{esc(state.data['name'])}</b>\n"
        f"🗓 {esc(result.summary or '')}\n\n"
        "Create this reminder?"
    )
    send_message(
        chat_id,
        body,
        buttons=[[("✅ Confirm", "confirm:save"), ("❌ Cancel", "confirm:cancel")]],
    )


# ==========================================================================
# Reminder list rendering
# ==========================================================================


def render_list(db: Session, chat_id: str):
    reminders = reminder_service.list_reminders(db, chat_id, active_only=True)
    if not reminders:
        return (
            "📭 No active reminders.\n\nType a reminder in plain English, or use /add.",
            [[("➕ Add", "menu:add")], [("🏠 Menu", "menu:home")]],
        )

    lines = ["📋 <b>Your reminders</b>\n"]
    kb = []
    for i, r in enumerate(reminders[:10], 1):
        nxt = format_datetime_for_user(r.next_trigger_at, r.timezone) if r.next_trigger_at else "—"
        kind = "🔁" if r.schedule_kind == "recurring" else "1️⃣"
        flags = " ⏸" if r.paused else ""
        src = " 📓" if r.source == "notion" else ""
        lines.append(f"{i}. {kind} <b>{esc(r.reminder_name)}</b>{flags}{src}\n    ⏰ {esc(nxt)}")
        toggle = (
            (f"▶️ {i}", f"rem:resume:{r.id}") if r.paused else (f"⏸ {i}", f"rem:pause:{r.id}")
        )
        kb.append([(f"✅ {i}", f"rem:done:{r.id}"), toggle, (f"🗑 {i}", f"rem:del:{r.id}")])
    kb.append([("🔄 Refresh", "menu:list"), ("🏠 Menu", "menu:home")])
    return "\n".join(lines), kb


# ==========================================================================
# Guided /add flow
# ==========================================================================


def start_add(db: Session, chat_id, state: UserState) -> None:
    state.set(FlowType.ADD, st.ADD_NAME)
    send_message(chat_id, "✨ <b>New reminder</b>\n\n📝 What should I call it?")


def add_flow_text(db: Session, chat_id, text: str, user: Users, state: UserState) -> None:
    if state.step == st.ADD_NAME:
        state.data["name"] = text
        state.step = st.ADD_TYPE
        send_message(
            chat_id,
            "🔁 One-time or recurring?",
            buttons=[[("1️⃣ One-time", "add:type:once"), ("🔁 Recurring", "add:type:recur")]],
        )
    elif state.step == st.ADD_AMOUNT:
        try:
            amount = int(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "❌ Enter a positive whole number.")
            return
        state.data["amount"] = amount
        state.step = st.ADD_CONTENT
        send_message(chat_id, "💬 What message should I send you?")
    elif state.step == st.ADD_CONTENT:
        finalize_add(db, chat_id, text, user, state)
    else:
        # Waiting on a button; nudge the user.
        send_message(chat_id, "Please tap one of the buttons above, or /cancel.")


def add_button(db: Session, chat_id, message_id, parts, user: Users, state: UserState) -> None:
    if state.flow != FlowType.ADD:
        return
    field = parts[1]
    if field == "type":
        state.data["is_recurring"] = parts[2] == "recur"
        state.step = st.ADD_UNIT
        edit_message_text(
            chat_id,
            message_id,
            "⏱ Choose a unit:",
            buttons=[[("Minutes", "add:unit:minutes"), ("Hours", "add:unit:hours"), ("Days", "add:unit:days")]],
        )
    elif field == "unit":
        multiplier, unit = parse_time_unit(parts[2])
        if not multiplier:
            return
        state.data["multiplier"] = multiplier
        state.data["unit"] = unit
        state.step = st.ADD_AMOUNT
        edit_message_text(chat_id, message_id, f"🔢 How many {unit}? (reply with a number)")


def finalize_add(db: Session, chat_id, content: str, user: Users, state: UserState) -> None:
    try:
        spec = build_interval_spec(
            amount=state.data["amount"],
            multiplier=state.data["multiplier"],
            is_recurring=state.data["is_recurring"],
            tz_name=_user_tz(user),
        )
        reminder = reminder_service.create_from_spec(
            db,
            chat_id=str(chat_id),
            name=state.data["name"],
            content=content,
            spec=spec,
            source="user",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Guided add failed for %s: %s", chat_id, exc)
        reminder = None

    clear_state(chat_id)
    if not reminder:
        send_message(chat_id, "❌ Couldn't create that reminder. Try again.")
        return
    nxt = format_datetime_for_user(reminder.next_trigger_at, reminder.timezone)
    send_message(
        chat_id,
        f"✅ <b>Reminder created!</b>\n\n📝 {esc(reminder.reminder_name)}\n🔔 Next: {esc(nxt)}",
        buttons=[[("📋 My reminders", "menu:list")]],
    )


# ==========================================================================
# Settings
# ==========================================================================


def render_settings(user: Users):
    notion_status = "✅ On" if user.notion_enabled else "❌ Off"
    freq = user.notion_check_frequency or 12
    dbs = len(user.notion_db_pages or [])
    body = (
        "⚙️ <b>Settings</b>\n\n"
        f"🌐 Timezone: <b>{esc(user.timezone or 'UTC')}</b> (change with /tz)\n"
        f"🔗 Notion: <b>{notion_status}</b>\n"
        f"⏱ Notion refresh: <b>{freq}h</b>\n"
        f"📚 Connected databases: <b>{dbs}</b>"
    )
    kb = [
        [("🔁 Toggle Notion", "set:toggle")],
        [("⏱ 12h", "set:freq:12"), ("⏱ 24h", "set:freq:24")],
        [("🗑 Reset Notion", "set:reset")],
        [("🏠 Menu", "menu:home")],
    ]
    return body, kb


def handle_set(db: Session, chat_id, message_id, parts, user: Users) -> None:
    action = parts[1]
    if action == "toggle":
        user.notion_enabled = not bool(user.notion_enabled)
    elif action == "freq" and len(parts) == 3 and parts[2] in ("12", "24"):
        user.notion_check_frequency = int(parts[2])
    elif action == "reset":
        deleted = notion_sync.delete_all_notion_reminders(db, str(chat_id))
        user.notion_api_key = None
        user.notion_enabled = False
        user.notion_db_pages = []
        user.notion_db_mappings = []
        logger.info("Reset Notion for %s (deleted %d reminders)", chat_id, deleted)
    db.add(user)
    db.commit()
    db.refresh(user)
    body, kb = render_settings(user)
    edit_message_text(chat_id, message_id, body, buttons=kb)


# ==========================================================================
# Notion flow
# ==========================================================================


def open_notion(db: Session, chat_id, user: Users, state: UserState) -> None:
    if not user.notion_api_key:
        state.set(FlowType.NOTION, st.N_TOKEN)
        send_message(
            chat_id,
            "🔗 <b>Connect Notion</b>\n\n"
            "1. Open https://www.notion.so/my-integrations\n"
            "2. Create an integration and copy its token\n"
            "3. Share your database with the integration\n\n"
            "Send me the token (starts with <code>secret_</code> or <code>ntn_</code>):",
        )
        return
    body, kb = render_notion_menu(user)
    send_message(chat_id, body, buttons=kb)


def render_notion_menu(user: Users):
    status = "✅ On" if user.notion_enabled else "❌ Off"
    dbs = len(user.notion_db_pages or [])
    body = f"🔗 <b>Notion</b>\n\nStatus: <b>{status}</b>\nConnected databases: <b>{dbs}</b>"
    kb = [
        [("➕ Add database", "notion:add"), ("➖ Remove", "notion:remove")],
        [("📄 List", "notion:list"), ("🔑 Change token", "notion:token")],
        [("✅ Done", "notion:done")],
    ]
    return body, kb


def handle_notion_button(db: Session, chat_id, message_id, parts, user: Users, state: UserState) -> None:
    action = parts[1]
    if action == "add":
        state.set(FlowType.NOTION, st.N_DB_ID)
        edit_message_text(
            chat_id,
            message_id,
            "📎 Send the Notion database ID or URL (or type <code>done</code> to finish):",
        )
    elif action == "token":
        state.set(FlowType.NOTION, st.N_TOKEN)
        edit_message_text(chat_id, message_id, "🔑 Send your new Notion token:")
    elif action == "list":
        pages = user.notion_db_pages or []
        if not pages:
            edit_message_text(chat_id, message_id, "No databases connected.", buttons=render_notion_menu(user)[1])
        else:
            listing = "\n".join(f"{i + 1}. <code>{esc(p)}</code>" for i, p in enumerate(pages))
            edit_message_text(chat_id, message_id, f"<b>Connected databases:</b>\n{listing}", buttons=render_notion_menu(user)[1])
    elif action == "remove":
        pages = user.notion_db_pages or []
        if not pages:
            edit_message_text(chat_id, message_id, "No databases to remove.", buttons=render_notion_menu(user)[1])
            return
        kb = [[(f"🗑 {i + 1}. {p[:8]}…", f"nrm:{i}")] for i, p in enumerate(pages)]
        kb.append([("⬅️ Back", "notion:menu")])
        edit_message_text(chat_id, message_id, "Select a database to remove:", buttons=kb)
    elif action == "menu":
        body, kb = render_notion_menu(user)
        edit_message_text(chat_id, message_id, body, buttons=kb)
    elif action == "import":
        run_notion_import(db, chat_id, message_id, user, state)
    elif action == "skip":
        clear_state(chat_id)
        body, kb = render_notion_menu(user)
        edit_message_text(chat_id, message_id, "✅ Mapping saved (not imported).", buttons=kb)
    elif action == "done":
        clear_state(chat_id)
        edit_message_text(chat_id, message_id, "✅ Notion setup complete.", buttons=menu_keyboard())


def notion_remove_index(db: Session, chat_id, message_id, idx: int, user: Users) -> None:
    pages = list(user.notion_db_pages or [])
    if idx < 0 or idx >= len(pages):
        edit_message_text(chat_id, message_id, "❌ Invalid selection.")
        return
    removed = pages.pop(idx)
    mappings = [m for m in (user.notion_db_mappings or []) if m.get("db_id") != removed]
    user.notion_db_pages = pages
    user.notion_db_mappings = mappings
    db.add(user)
    db.commit()
    db.refresh(user)
    deleted = notion_sync.delete_db_reminders(db, str(chat_id), removed)
    body, kb = render_notion_menu(user)
    edit_message_text(
        chat_id,
        message_id,
        f"✅ Removed database and {deleted} associated reminder(s).\n\n{body}",
        buttons=kb,
    )


def notion_flow_text(db: Session, chat_id, text: str, user: Users, state: UserState) -> None:
    step = state.step

    if step == st.N_TOKEN:
        if not text.startswith(("secret_", "ntn_")):
            send_message(chat_id, "❌ Token should start with <code>secret_</code> or <code>ntn_</code>. Try again:")
            return
        ok, info = notion.validate_token(text)
        if not ok:
            send_message(chat_id, "❌ Token validation failed. Check it and try again:")
            return
        user.notion_api_key = text
        user.notion_enabled = True
        user.notion_workspace_name = (info or {}).get("name")
        db.add(user)
        db.commit()
        db.refresh(user)
        state.step = st.N_DB_ID
        send_message(chat_id, "✅ Connected! Now send a database ID/URL (or <code>done</code>):")

    elif step == st.N_DB_ID:
        if text.strip().lower() == "done":
            clear_state(chat_id)
            body, kb = render_notion_menu(user)
            send_message(chat_id, body, buttons=kb)
            return
        match = re.search(r"[0-9a-fA-F\-]{32,36}", text)
        db_id = (match.group(0) if match else text.strip()).replace("-", "")
        ok, info = notion.get_database(user.notion_api_key, db_id)
        if not ok or not info:
            send_message(chat_id, "❌ Couldn't access that database. Ensure the integration has access. Try again or <code>done</code>:")
            return
        properties = info.get("properties", {})
        if not properties:
            send_message(chat_id, "❌ That database has no properties.")
            return
        state.data["db_id"] = db_id
        state.data["prop_names"] = list(properties.keys())
        state.data["prop_types"] = {n: p.get("type") for n, p in properties.items()}
        state.step = st.N_NAME_PROP
        props_text = "\n".join(f"• {esc(p)}" for p in properties.keys())
        send_message(chat_id, f"Found {len(properties)} properties:\n\n{props_text}\n\n📝 Which holds the <b>task name</b>?")

    elif step == st.N_NAME_PROP:
        if not _check_prop(chat_id, text, state):
            return
        state.data["name_prop"] = text.strip()
        state.step = st.N_TIME_PROP
        send_message(chat_id, "⏰ Which property holds the <b>due date</b>? (a Date property)")

    elif step == st.N_TIME_PROP:
        if not _check_prop(chat_id, text, state):
            return
        state.data["time_prop"] = text.strip()
        state.step = st.N_STATUS_PROP
        send_message(chat_id, "✅ Which property marks a task <b>done</b>? (Checkbox or Status)")

    elif step == st.N_STATUS_PROP:
        if not _check_prop(chat_id, text, state):
            return
        state.data["status_prop"] = text.strip()
        _save_notion_mapping(db, user, state)
        state.step = st.N_IMPORT
        send_message(
            chat_id,
            "🎯 Mapping saved! Import incomplete tasks due in the next 24h now?",
            buttons=[[("✅ Import", "notion:import"), ("⏭ Skip", "notion:skip")]],
        )


def _check_prop(chat_id, text: str, state: UserState) -> bool:
    if text.strip() not in state.data.get("prop_names", []):
        send_message(chat_id, "❌ No property with that exact name. Try again:")
        return False
    return True


def _save_notion_mapping(db: Session, user: Users, state: UserState) -> None:
    db_id = state.data["db_id"]
    status_prop = state.data["status_prop"]
    mapping = {
        "db_id": db_id,
        "name_prop": state.data["name_prop"],
        "time_prop": state.data["time_prop"],
        "status_prop": status_prop,
        "status_prop_type": state.data["prop_types"].get(status_prop, "checkbox"),
        "status_complete_values": None,
    }
    pages = list(user.notion_db_pages or [])
    if db_id not in pages:
        pages.append(db_id)
    mappings = [m for m in (user.notion_db_mappings or []) if m.get("db_id") != db_id]
    mappings.append(mapping)
    user.notion_db_pages = pages
    user.notion_db_mappings = mappings
    db.add(user)
    db.commit()
    db.refresh(user)


def run_notion_import(db: Session, chat_id, message_id, user: Users, state: UserState) -> None:
    db_id = state.data.get("db_id")
    mapping = next((m for m in (user.notion_db_mappings or []) if m.get("db_id") == db_id), None)
    clear_state(chat_id)
    if not mapping:
        edit_message_text(chat_id, message_id, "❌ Mapping not found.")
        return
    imported, skipped = notion_sync.import_database(db, user, mapping)
    user.notion_last_synced_at = now_utc()
    db.add(user)
    db.commit()
    body, kb = render_notion_menu(user)
    edit_message_text(
        chat_id,
        message_id,
        f"✅ Import complete — imported {imported}, skipped {skipped}.\n\n{body}",
        buttons=kb,
    )


# ==========================================================================
# Callback routing
# ==========================================================================


def handle_callback(db: Session, cq: dict) -> None:
    data = cq.get("data", "")
    cq_id = cq.get("id")
    msg = cq.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    if chat_id is None:
        answer_callback_query(cq_id)
        return

    user = get_or_create_user(db, chat, cq.get("from", {}))
    state = get_state(chat_id)
    answer_callback_query(cq_id)  # acknowledge (stops the spinner)

    parts = data.split(":")
    domain = parts[0]

    try:
        if domain == "menu":
            handle_menu(db, chat_id, message_id, parts[1], user, state)
        elif domain == "confirm":
            handle_confirm(db, chat_id, message_id, parts[1], state)
        elif domain == "rem":
            handle_rem(db, chat_id, message_id, parts[1], parts[2])
        elif domain == "add":
            add_button(db, chat_id, message_id, parts, user, state)
        elif domain == "set":
            handle_set(db, chat_id, message_id, parts, user)
        elif domain == "notion":
            handle_notion_button(db, chat_id, message_id, parts, user, state)
        elif domain == "nrm":
            notion_remove_index(db, chat_id, message_id, int(parts[1]), user)
    except Exception as exc:  # noqa: BLE001
        logger.error("Callback %r failed: %s", data, exc, exc_info=True)


def handle_menu(db: Session, chat_id, message_id, which: str, user: Users, state: UserState) -> None:
    clear_state(chat_id)
    if which in ("home", "start"):
        edit_message_text(chat_id, message_id, WELCOME, buttons=menu_keyboard())
    elif which == "help":
        edit_message_text(chat_id, message_id, HELP, buttons=[[("🏠 Menu", "menu:home")]])
    elif which == "list":
        body, kb = render_list(db, str(chat_id))
        edit_message_text(chat_id, message_id, body, buttons=kb)
    elif which == "add":
        start_add(db, chat_id, state)
    elif which == "notion":
        if not user.notion_api_key:
            open_notion(db, chat_id, user, state)
        else:
            body, kb = render_notion_menu(user)
            edit_message_text(chat_id, message_id, body, buttons=kb)
    elif which == "settings":
        body, kb = render_settings(user)
        edit_message_text(chat_id, message_id, body, buttons=kb)


def handle_confirm(db: Session, chat_id, message_id, action: str, state: UserState) -> None:
    if action == "cancel" or state.flow != FlowType.CONFIRM:
        clear_state(chat_id)
        edit_message_text(chat_id, message_id, "❌ Cancelled.", buttons=[[("🏠 Menu", "menu:home")]])
        return
    try:
        spec = ScheduleSpec.model_validate(state.data["spec"])
        reminder = reminder_service.create_from_spec(
            db,
            chat_id=str(chat_id),
            name=state.data["name"],
            content=state.data["content"],
            spec=spec,
            source="llm",
            nl_text=state.data.get("nl"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Confirm save failed for %s: %s", chat_id, exc)
        reminder = None

    clear_state(chat_id)
    if not reminder:
        edit_message_text(chat_id, message_id, "⚠️ Couldn't schedule that reminder.")
        return
    nxt = format_datetime_for_user(reminder.next_trigger_at, reminder.timezone)
    edit_message_text(
        chat_id,
        message_id,
        f"✅ <b>Reminder set!</b>\n\n📝 {esc(reminder.reminder_name)}\n🔔 Next: {esc(nxt)}",
        buttons=[[("📋 My reminders", "menu:list")]],
    )


def handle_rem(db: Session, chat_id, message_id, action: str, rid: str) -> None:
    if action == "done":
        reminder_service.mark_done(db, rid, str(chat_id))
    elif action == "del":
        reminder_service.delete_reminder(db, rid, str(chat_id))
    elif action == "pause":
        reminder_service.set_paused(db, rid, str(chat_id), True)
    elif action == "resume":
        reminder_service.set_paused(db, rid, str(chat_id), False)
    body, kb = render_list(db, str(chat_id))
    edit_message_text(chat_id, message_id, body, buttons=kb)
