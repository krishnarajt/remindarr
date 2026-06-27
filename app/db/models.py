"""Database models (greenfield schema).

Design notes:
- Every timestamp is ``TIMESTAMP WITH TIME ZONE`` (timestamptz) and stored in
  UTC. The previous schema mixed naive and tz-aware columns, which caused
  trigger-time drift; here everything is tz-aware UTC, end to end.
- A reminder's schedule lives in ``schedule_spec`` (JSONB) as the source of
  truth (a serialised :class:`app.common.schemas.ScheduleSpec`). Convenience
  columns (``rrule``, ``timezone``, ``dtstart``, ``until_at``,
  ``max_occurrences``) are denormalised for the worker, and ``next_trigger_at``
  is materialised so the poller can index/filter efficiently.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.common.constants import settings


def _tz_now_col(**kwargs) -> Column:
    return Column(DateTime(timezone=True), nullable=kwargs.pop("nullable", True), **kwargs)


class Base(SQLModel):
    __table_args__ = {"schema": settings.db_schema}


class Users(Base, table=True):
    """Telegram user + integration settings."""

    __tablename__ = "users"

    chat_id: str = Field(primary_key=True, description="Telegram chat/user ID")
    username: Optional[str] = Field(default=None)
    first_name: Optional[str] = Field(default=None)
    language_code: Optional[str] = Field(default=None)
    is_bot: bool = Field(default=False)

    # Notion integration
    notion_api_key: Optional[str] = Field(default=None)
    notion_workspace_name: Optional[str] = Field(default=None)
    notion_enabled: bool = Field(default=False)
    notion_db_pages: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    # Per-DB mapping: {db_id, name_prop, time_prop, status_prop,
    #                  status_prop_type, status_complete_values: [str]}
    notion_db_mappings: Optional[List[Dict[str, Any]]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    # Refresh cadence in hours (12 or 24). Note: prior code misspelled this as
    # "frequence"; we read both keys defensively elsewhere.
    notion_check_frequency: int = Field(default=12)
    notion_last_synced_at: Optional[datetime] = Field(
        default=None, sa_column=_tz_now_col()
    )

    # Preferences
    timezone: Optional[str] = Field(default="UTC")
    notifications_enabled: bool = Field(default=True)

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
    )
    last_active_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class Reminders(Base, table=True):
    __tablename__ = "reminders"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True, index=True
    )
    chat_id: str = Field(index=True, description="Telegram chat id to notify")

    reminder_name: str = Field(sa_column=Column(Text, nullable=False))
    reminder_content: str = Field(sa_column=Column(Text, nullable=False))

    # 'user' (guided /add), 'llm' (natural language), or 'notion'
    source: str = Field(default="user", sa_column=Column(String(16), nullable=False))

    active: bool = Field(default=True, nullable=False, index=True)
    paused: bool = Field(default=False, nullable=False)

    # ----- schedule -----------------------------------------------------
    timezone: str = Field(default="UTC", sa_column=Column(String(64), nullable=False))
    schedule_kind: str = Field(default="one_time", sa_column=Column(String(16), nullable=False))
    rrule: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    dtstart: Optional[datetime] = Field(default=None, sa_column=_tz_now_col())
    schedule_spec: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    nl_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # bounds / bookkeeping
    occurrences_sent: int = Field(default=0, nullable=False)
    max_occurrences: Optional[int] = Field(default=None)
    until_at: Optional[datetime] = Field(default=None, sa_column=_tz_now_col())

    next_trigger_at: Optional[datetime] = Field(default=None, sa_column=_tz_now_col(index=True))
    last_triggered_at: Optional[datetime] = Field(default=None, sa_column=_tz_now_col())

    # ----- notion provenance -------------------------------------------
    notion_db_id: Optional[str] = Field(default=None, index=True)
    notion_page_id: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
    )
