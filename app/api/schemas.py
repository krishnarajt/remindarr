from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ============ Auth Schemas ============

class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)


class AuthResponse(BaseModel):
    accessToken: str
    refreshToken: str
    message: Optional[str] = None


class RefreshRequest(BaseModel):
    refreshToken: str


class RefreshResponse(BaseModel):
    accessToken: str


# ============ User/Settings Schemas ============

class UserSettings(BaseModel):
    name: str
    remindBeforeActivity: bool = True
    remindOnStart: bool = True
    nudgeDuringActivity: bool = True
    congratulateOnFinish: bool = True
    defaultSlotDuration: int = 60
    timezone: Optional[str] = "Asia/Kolkata"
    telegramLinked: Optional[bool] = False


class UpdateSettingsRequest(BaseModel):
    name: str
    remindBeforeActivity: bool
    remindOnStart: bool
    nudgeDuringActivity: bool
    congratulateOnFinish: bool
    defaultSlotDuration: int
    timezone: Optional[str] = None


class ApiResponse(BaseModel):
    success: bool
    message: Optional[str] = None


# ============ Task Schemas ============

class TaskBlockJson(BaseModel):
    id: str
    startTime: str  # HH:MM format
    endTime: str    # HH:MM format
    task: str


class DailySchedule(BaseModel):
    date: str  # YYYY-MM-DD format
    createdAt: str
    updatedAt: str
    tasks: List[TaskBlockJson]


class SaveScheduleRequest(BaseModel):
    schedule: DailySchedule


class TaskResponse(BaseModel):
    id: str
    startTime: str
    endTime: str
    task: str
    isCompleted: bool = False
    
    class Config:
        from_attributes = True


class ScheduleResponse(BaseModel):
    date: str
    tasks: List[TaskResponse]
    
    class Config:
        from_attributes = True


# ============ Telegram Schemas ============

class TelegramLinkResponse(BaseModel):
    code: str
    expiresAt: str
    message: str


class TelegramWebhookUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None
    callback_query: Optional[dict] = None
