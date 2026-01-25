from app.api.auth_routes import router as auth_router
from app.api.user_routes import router as user_router
from app.api.schedule_routes import router as schedule_router
from app.api.webhook_routes import router as webhook_router

__all__ = ["auth_router", "user_router", "schedule_router", "webhook_router"]
