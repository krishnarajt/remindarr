from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import date

from app.db.database import get_db
from app.db.models import User
from app.api.schemas import SaveScheduleRequest, DailySchedule, ApiResponse
from app.api.dependencies import get_current_user
from app.services.schedule_service import save_schedule, get_schedule_for_date, get_today_schedule

router = APIRouter(prefix="/schedule", tags=["Schedule"])


@router.post("/save", response_model=ApiResponse)
def save_daily_schedule(
    request: SaveScheduleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save or update a daily schedule"""
    try:
        save_schedule(db, current_user.id, request.schedule)
        return ApiResponse(success=True, message="Schedule saved successfully")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save schedule: {str(e)}"
        )


@router.get("/today", response_model=DailySchedule)
def get_todays_schedule(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get today's schedule"""
    schedule = get_today_schedule(db, current_user.id)
    
    if not schedule:
        # Return empty schedule for today
        today = date.today().isoformat()
        return DailySchedule(
            date=today,
            createdAt="",
            updatedAt="",
            tasks=[]
        )
    
    return schedule


@router.get("/{schedule_date}", response_model=DailySchedule)
def get_schedule_by_date(
    schedule_date: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get schedule for a specific date (YYYY-MM-DD format)"""
    # Validate date format
    try:
        date.fromisoformat(schedule_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD"
        )
    
    schedule = get_schedule_for_date(db, current_user.id, schedule_date)
    
    if not schedule:
        return DailySchedule(
            date=schedule_date,
            createdAt="",
            updatedAt="",
            tasks=[]
        )
    
    return schedule
