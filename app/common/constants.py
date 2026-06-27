"""Application settings.

Reads configuration from environment variables (and a local `.env` in dev).
Mirrors the deployment convention of the sibling `scron` service: a single
``DATABASE_URL`` plus a ``DB_SCHEMA`` so multiple apps can share one Postgres
instance while living in separate schemas. The legacy ``db_*`` parts are still
honoured as a fallback so local `.env` files keep working.
"""

from typing import Optional

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    # ----- Database -----------------------------------------------------
    # Preferred: a full SQLAlchemy URL. If absent we assemble one from parts.
    database_url: Optional[str] = None
    db_schema: str = "remindarr"

    # Legacy parts (fallback when database_url is not provided)
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_name: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None

    # ----- Telegram -----------------------------------------------------
    bot_token: str
    # Optional fallback chat id for reminders missing one (legacy behaviour)
    chat_id: Optional[str] = None
    # If set, the webhook verifies Telegram's X-Telegram-Bot-Api-Secret-Token.
    telegram_webhook_secret: Optional[str] = None

    # ----- LLM gateway --------------------------------------------------
    llm_gateway_url: str = "http://llmgateway:8000"
    llm_gateway_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"

    # ----- Misc ---------------------------------------------------------
    # Default timezone for users who never set one. IANA name.
    default_timezone: str = "UTC"

    model_config = SettingsConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        """Assemble database_url from parts if it wasn't supplied directly."""
        if not self.database_url:
            required = (
                self.db_user,
                self.db_password,
                self.db_name,
                self.db_host,
                self.db_port,
            )
            if all(v is not None for v in required):
                self.database_url = (
                    f"postgresql+psycopg2://"
                    f"{self.db_user}:{self.db_password}@"
                    f"{self.db_host}:{self.db_port}/{self.db_name}"
                )
            else:
                raise ValueError(
                    "Database not configured: set DATABASE_URL, or all of "
                    "DB_USER/DB_PASSWORD/DB_NAME/DB_HOST/DB_PORT."
                )
        # Normalise a bare postgresql:// URL to the psycopg2 driver we depend on.
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg2://", 1
            )
        return self


settings = Settings()
