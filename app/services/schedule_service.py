from datetime import datetime, date, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
import pytz

from app.db.models import User, Schedule, Task
from app.api.schemas import DailySchedule, TaskBlockJson


def get_or_create_schedule(db: Session, user_id: int, schedule_date: str) -> Schedule:
    """Get existing schedule or create new one for a date"""
    schedule = db.query(Schedule).filter(
        Schedule.user_id == user_id,
        Schedule.date == schedule_date
    ).first()
    
    if not schedule:
        schedule = Schedule(
            user_id=user_id,
            date=schedule_date
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
    
    return schedule


def save_schedule(db: Session, user_id: int, schedule_data: DailySchedule) -> Schedule:
    """Save or update a daily schedule with tasks"""
    # Get or create schedule
    schedule = get_or_create_schedule(db, user_id, schedule_data.date)
    
    # Delete existing tasks for this schedule
    db.query(Task).filter(Task.schedule_id == schedule.id).delete()
    
    # Add new tasks
    for task_json in schedule_data.tasks:
        task = Task(
            task_uuid=task_json.id,
            user_id=user_id,
            schedule_id=schedule.id,
            start_time=task_json.startTime,
            end_time=task_json.endTime,
            task_description=task_json.task
        )
        db.add(task)
    
    # Update schedule timestamp
    schedule.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(schedule)
    
    return schedule


def get_schedule_for_date(db: Session, user_id: int, schedule_date: str) -> Optional[DailySchedule]:
    """Get schedule for a specific date"""
    schedule = db.query(Schedule).filter(
        Schedule.user_id == user_id,
        Schedule.date == schedule_date
    ).first()
    
    if not schedule:
        return None
    
    tasks = db.query(Task).filter(Task.schedule_id == schedule.id).order_by(Task.start_time).all()
    
    task_list = [
        TaskBlockJson(
            id=task.task_uuid,
            startTime=task.start_time,
            endTime=task.end_time,
            task=task.task_description
        )
        for task in tasks
    ]
    
    return DailySchedule(
        date=schedule.date,
        createdAt=schedule.created_at.isoformat() if schedule.created_at else "",
        updatedAt=schedule.updated_at.isoformat() if schedule.updated_at else "",
        tasks=task_list
    )


def get_today_schedule(db: Session, user_id: int) -> Optional[DailySchedule]:
    """Get today's schedule"""
    today = date.today().isoformat()
    return get_schedule_for_date(db, user_id, today)


def get_upcoming_tasks(db: Session, user_id: int, minutes_ahead: int = 15) -> List[Task]:
    """Get tasks starting within the next N minutes"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return []
    
    try:
        tz = pytz.timezone(user.timezone or "Asia/Kolkata")
    except:
        tz = pytz.timezone("Asia/Kolkata")
    
    now = datetime.now(tz)
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")
    future_time = (now + timedelta(minutes=minutes_ahead)).strftime("%H:%M")
    
    tasks = db.query(Task).join(Schedule).filter(
        Task.user_id == user_id,
        Schedule.date == today,
        Task.start_time >= current_time,
        Task.start_time <= future_time,
        Task.is_completed == False
    ).all()
    
    return tasks


def get_tasks_for_notification(db: Session) -> dict:
    """Get all tasks that need notifications across all users"""
    result = {
        "remind_before": [],  # 10 min before start
        "remind_on_start": [],  # At start time
        "nudge_during": [],  # Middle of task
        "congratulate": []  # At end time
    }
    
    users = db.query(User).filter(User.telegram_chat_id.isnot(None)).all()
    
    for user in users:
        try:
            tz = pytz.timezone(user.timezone or "Asia/Kolkata")
        except:
            tz = pytz.timezone("Asia/Kolkata")
        
        now = datetime.now(tz)
        today = now.date().isoformat()
        
        # Get today's tasks
        tasks = db.query(Task).join(Schedule).filter(
            Task.user_id == user.id,
            Schedule.date == today,
            Task.is_completed == False
        ).all()
        
        for task in tasks:
            # Calculate times
            start_h, start_m = map(int, task.start_time.split(":"))
            end_h, end_m = map(int, task.end_time.split(":"))
            
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m
            current_minutes = now.hour * 60 + now.minute
            
            # 10 minutes before
            if user.remind_before_activity and not task.reminded_before:
                if start_minutes - 10 <= current_minutes < start_minutes - 9:
                    result["remind_before"].append((user, task))
            
            # At start
            if user.remind_on_start and not task.reminded_on_start:
                if start_minutes <= current_minutes < start_minutes + 1:
                    result["remind_on_start"].append((user, task))
            
            # Nudge during (middle of task)
            if user.nudge_during_activity and not task.nudged_during:
                middle_minutes = (start_minutes + end_minutes) // 2
                if middle_minutes <= current_minutes < middle_minutes + 1:
                    result["nudge_during"].append((user, task))
            
            # Congratulate at end
            if user.congratulate_on_finish and not task.congratulated:
                if end_minutes <= current_minutes < end_minutes + 1:
                    result["congratulate"].append((user, task))
    
    return result


def mark_task_completed(db: Session, user_id: int, task_uuid: str) -> bool:
    """Mark a task as completed"""
    task = db.query(Task).filter(
        Task.user_id == user_id,
        Task.task_uuid == task_uuid
    ).first()
    
    if task:
        task.is_completed = True
        task.completed_at = datetime.utcnow()
        db.commit()
        return True
    return False


def update_notification_flags(db: Session, task_id: int, flag_name: str) -> None:
    """Update notification tracking flags"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        setattr(task, flag_name, True)
        db.commit()
