from fastapi import APIRouter, Request

from app.bot.telegram_bot import handle_telegram_webhook

router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.post("/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram webhook updates"""
    try:
        update = await request.json()
        result = await handle_telegram_webhook(update)
        return {"status": result}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/telegram")
async def telegram_webhook_verify():
    """Verify webhook endpoint is working"""
    return {"status": "ok", "message": "Telegram webhook is active"}
