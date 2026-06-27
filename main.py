from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.db.config_db as config_db
from app.api.settings_routes import router as settings_router
from app.api.telegram_webhook import router as webhook_router
from app.services.notification_worker import start_worker, stop_worker
from app.services.notion_sync import start_notion_sync, stop_notion_sync
from app.utils.logging_utils import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_db.init_db()
    # Background loops must be created inside the running event loop.
    start_worker(app)
    start_notion_sync(app)
    logger.info("Remindarr started")
    try:
        yield
    finally:
        await stop_worker(app)
        await stop_notion_sync(app)
        # Sync engine: dispose() is NOT awaitable (this used to crash on shutdown).
        config_db.engine.dispose()
        logger.info("Remindarr stopped")


app = FastAPI(title="Remindarr", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/ready")
async def readiness_check():
    """Readiness: verify the database is reachable."""
    try:
        with config_db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Readiness check failed: %s", exc)
        from fastapi.responses import JSONResponse

        return JSONResponse({"status": "not ready"}, status_code=503)
