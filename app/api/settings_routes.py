"""REST endpoints for an external frontend to read/manage user settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.common.schemas import SettingsPayload
from app.db.config_db import get_session
from app.db.models import Users
from app.services import notion_sync
from app.utils.logging_utils import logger

router = APIRouter(prefix="/notifications", tags=["settings"])


@router.get("/settings/{chat_id}")
def get_settings(chat_id: str, db: Session = Depends(get_session)):
    user = db.get(Users, str(chat_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "chat_id": user.chat_id,
        "username": user.username,
        "first_name": user.first_name,
        "timezone": user.timezone,
        "notion_enabled": bool(user.notion_enabled),
        "notion_db_pages": user.notion_db_pages or [],
        "notion_db_mappings": user.notion_db_mappings or [],
        "notion_check_frequency": user.notion_check_frequency,
        "has_notion_token": bool(user.notion_api_key),
        "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
    }


@router.post("/settings")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_session)):
    user = db.get(Users, str(payload.chat_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.notion_enabled is not None:
        user.notion_enabled = bool(payload.notion_enabled)
    if payload.notion_check_frequency is not None:
        if payload.notion_check_frequency not in (12, 24):
            raise HTTPException(status_code=400, detail="frequency must be 12 or 24")
        user.notion_check_frequency = int(payload.notion_check_frequency)
    if payload.timezone is not None:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(payload.timezone)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid timezone")
        user.timezone = payload.timezone

    db.add(user)
    db.commit()
    db.refresh(user)
    return {"status": "ok"}


@router.delete("/settings/{chat_id}/notion")
def reset_notion(chat_id: str, db: Session = Depends(get_session)):
    user = db.get(Users, str(chat_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    deleted = notion_sync.delete_all_notion_reminders(db, str(chat_id))
    user.notion_api_key = None
    user.notion_enabled = False
    user.notion_db_pages = []
    user.notion_db_mappings = []
    db.add(user)
    db.commit()
    logger.info("Reset Notion for %s via API (deleted %d reminders)", chat_id, deleted)
    return {"status": "ok", "deleted_reminders": deleted}
