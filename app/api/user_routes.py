from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.database import get_db
from app.db.models import User
from app.api.schemas import UserSettings, UpdateSettingsRequest, ApiResponse, TelegramLinkResponse
from app.api.dependencies import get_current_user
from app.bot.telegram_bot import generate_link_code

router = APIRouter(prefix="/user", tags=["User"])


@router.get("/settings", response_model=UserSettings)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user settings"""
    return UserSettings(
        name=current_user.display_name or "",
        remindBeforeActivity=current_user.remind_before_activity,
        remindOnStart=current_user.remind_on_start,
        nudgeDuringActivity=current_user.nudge_during_activity,
        congratulateOnFinish=current_user.congratulate_on_finish,
        defaultSlotDuration=current_user.default_slot_duration,
        timezone=current_user.timezone,
        telegramLinked=current_user.telegram_chat_id is not None
    )


@router.put("/settings", response_model=ApiResponse)
def update_settings(
    request: UpdateSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user settings"""
    current_user.display_name = request.name
    current_user.remind_before_activity = request.remindBeforeActivity
    current_user.remind_on_start = request.remindOnStart
    current_user.nudge_during_activity = request.nudgeDuringActivity
    current_user.congratulate_on_finish = request.congratulateOnFinish
    current_user.default_slot_duration = request.defaultSlotDuration
    
    if request.timezone:
        current_user.timezone = request.timezone
    
    db.commit()
    
    return ApiResponse(success=True, message="Settings updated successfully")


@router.post("/telegram/link", response_model=TelegramLinkResponse)
def get_telegram_link_code(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generate a code to link Telegram account"""
    code = generate_link_code(db, current_user.id)
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    return TelegramLinkResponse(
        code=code,
        expiresAt=expires_at.isoformat(),
        message="Send this code to @second-thought-backend_bot on Telegram using /link command"
    )


@router.post("/telegram/unlink", response_model=ApiResponse)
def unlink_telegram_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Unlink Telegram account"""
    if not current_user.telegram_chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Telegram account linked"
        )
    
    current_user.telegram_chat_id = None
    current_user.telegram_username = None
    db.commit()
    
    return ApiResponse(success=True, message="Telegram unlinked successfully")
