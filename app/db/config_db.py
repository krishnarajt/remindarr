# app/db/config_db.py
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.common.constants import settings

# Sync SQLAlchemy engine (psycopg2). echo off in normal operation.
engine = create_engine(settings.database_url, echo=False, future=True, pool_pre_ping=True)


def init_db() -> None:
    """Create the target schema (if needed) and all tables."""
    schema = settings.db_schema

    with engine.begin() as conn:
        if schema and schema != "public":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    # Import models so they register on SQLModel.metadata before create_all.
    import app.db.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: one Session per request."""
    with Session(engine) as session:
        yield session
