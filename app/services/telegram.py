"""Telegram Bot API client.

Synchronous on purpose: all business logic in this app runs in worker threads
(FastAPI routes and the reminder loop offload via ``asyncio.to_thread``), so a
plain blocking client is the simplest correct choice.

Messages are sent as **HTML** (``parse_mode=HTML``). The previous code formatted
text with Markdown but never set ``parse_mode``, so users saw literal ``*`` and
backslash-escaped characters — that bug is fixed here. Always wrap dynamic text
in :func:`esc`.
"""

from __future__ import annotations

import html
from typing import Iterable, List, Optional, Sequence, Tuple

import httpx

from app.common.constants import settings
from app.utils.logging_utils import logger

_API = "https://api.telegram.org"
_client = httpx.Client(timeout=httpx.Timeout(15.0, connect=10.0))

# A keyboard is a list of rows; each row is a list of (label, callback_data).
Button = Tuple[str, str]
Keyboard = Sequence[Sequence[Button]]


def esc(text: Optional[str]) -> str:
    """HTML-escape user/dynamic content for safe inclusion in messages."""
    return html.escape(text or "", quote=False)


def _markup(buttons: Optional[Keyboard]) -> Optional[dict]:
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for (label, data) in row]
            for row in buttons
        ]
    }


def _post(method: str, payload: dict) -> Optional[dict]:
    url = f"{_API}/bot{settings.bot_token}/{method}"
    try:
        resp = _client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Telegram %s failed (%s): %s",
            method,
            exc.response.status_code,
            exc.response.text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram %s error: %s", method, exc)
    return None


def send_message(
    chat_id, text: str, buttons: Optional[Keyboard] = None, parse_mode: str = "HTML"
) -> Optional[dict]:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    markup = _markup(buttons)
    if markup:
        payload["reply_markup"] = markup
    return _post("sendMessage", payload)


def edit_message_text(
    chat_id, message_id: int, text: str, buttons: Optional[Keyboard] = None
) -> Optional[dict]:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    markup = _markup(buttons)
    if markup:
        payload["reply_markup"] = markup
    return _post("editMessageText", payload)


def answer_callback_query(
    callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
) -> Optional[dict]:
    payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    return _post("answerCallbackQuery", payload)


def menu_keyboard() -> List[List[Button]]:
    """The persistent main-menu inline keyboard."""
    return [
        [("➕ Add reminder", "menu:add"), ("📋 My reminders", "menu:list")],
        [("🔗 Notion", "menu:notion"), ("⚙️ Settings", "menu:settings")],
        [("❓ Help", "menu:help")],
    ]
