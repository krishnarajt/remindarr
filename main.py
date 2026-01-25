import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.db.database import init_db
from app.api.auth_routes import router as auth_router
from app.api.user_routes import router as user_router
from app.api.schedule_routes import router as schedule_router
from app.api.webhook_routes import router as webhook_router
from app.bot.telegram_bot import process_notifications


# Background task for notifications
async def notification_scheduler():
    """Run notification processing every minute"""
    while True:
        try:
            await process_notifications()
        except Exception as e:
            print(f"Error processing notifications: {e}")
        await asyncio.sleep(60)  # Check every minute


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    print("Starting Second Thought Backend...")
    init_db()
    print("Database initialized")
    
    # Start background notification task
    notification_task = asyncio.create_task(notification_scheduler())
    print("Notification scheduler started")
    
    yield
    
    # Shutdown
    notification_task.cancel()
    try:
        await notification_task
    except asyncio.CancelledError:
        pass
    print("Second Thought Backend shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Second Thought Backend",
    description="Backend API for Second Thought timetable app with Telegram notifications",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(schedule_router, prefix="/api")
app.include_router(webhook_router, prefix="/api")


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "name": "Second Thought Backend",
        "description": "Second Thought Backend API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
def health_check():
    """Health check endpoint for k8s"""
    return {"status": "healthy"}


@app.get("/ready")
def readiness_check():
    """Readiness check endpoint for k8s"""
    return {"status": "ready"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
