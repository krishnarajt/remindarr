"""Pytest bootstrap.

Provide dummy environment so importing app modules (which construct a global
``Settings()``) doesn't require a real .env during unit tests.
"""

import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("DB_SCHEMA", "remindarr")
