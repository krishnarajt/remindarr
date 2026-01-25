import os
import random
import string
from datetime import datetime, timedelta
from typing import Optional
import httpx
from sqlalchemy.orm import Session

from app.db.models import User, TelegramLinkCode, Task
from app.db.database import SessionLocal

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to a Telegram chat"""
    if not TELEGRAM_BOT_TOKEN:
        print("Warning: TELEGRAM_BOT_TOKEN not set")
        return False
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode
                },
                timeout=10.0
            )
            return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False


def generate_link_code(db: Session, user_id: int) -> str:
    """Generate a 6-digit code for linking Telegram account"""
    # Delete any existing codes for this user
    db.query(TelegramLinkCode).filter(TelegramLinkCode.user_id == user_id).delete()
    
    # Generate new code
    code = ''.join(random.choices(string.digits, k=6))
    
    # Store code with 10 minute expiry
    link_code = TelegramLinkCode(
        user_id=user_id,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=10)
    )
    db.add(link_code)
    db.commit()
    
    return code


def verify_link_code(db: Session, code: str, telegram_chat_id: str, telegram_username: str = None) -> Optional[User]:
    """Verify a link code and connect Telegram account"""
    link_code = db.query(TelegramLinkCode).filter(
        TelegramLinkCode.code == code,
        TelegramLinkCode.expires_at > datetime.utcnow()
    ).first()
    
    if not link_code:
        return None
    
    # Get user and update Telegram info
    user = db.query(User).filter(User.id == link_code.user_id).first()
    if user:
        user.telegram_chat_id = telegram_chat_id
        user.telegram_username = telegram_username
        
        # Delete used code
        db.delete(link_code)
        db.commit()
        
        return user
    
    return None


def unlink_telegram(db: Session, user_id: int) -> bool:
    """Unlink Telegram account from user"""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.telegram_chat_id = None
        user.telegram_username = None
        db.commit()
        return True
    return False


async def handle_telegram_webhook(update: dict) -> str:
    """Handle incoming Telegram webhook updates"""
    db = SessionLocal()
    try:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        username = message.get("from", {}).get("username", "")
        
        if not chat_id or not text:
            return "OK"
        
        # Check if user is already linked
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        
        # Handle commands
        if text.startswith("/start"):
            if user:
                await send_message(chat_id, f"👋 Welcome back, <b>{user.display_name or user.username}</b>!\n\nYour Telegram is already linked to your Second Thought account.\n\nI'll send you reminders for your scheduled tasks.\n\nType /help to see available commands.")
            else:
                await send_message(chat_id, "👋 Welcome to <b>Second Thought</b> - your schedule assistant!\n\nTo link your account, get a code from the app settings and send it here.\n\nJust type /link followed by your 6-digit code:\n<code>/link 123456</code>")
        
        elif text.startswith("/link"):
            parts = text.split()
            if len(parts) != 2:
                await send_message(chat_id, "Please provide your link code:\n<code>/link 123456</code>")
            else:
                code = parts[1]
                linked_user = verify_link_code(db, code, chat_id, username)
                if linked_user:
                    await send_message(chat_id, f"✅ Success! Your Telegram is now linked to <b>{linked_user.username}</b>.\n\nI'll send you reminders for your scheduled tasks based on your notification settings.")
                else:
                    await send_message(chat_id, "❌ Invalid or expired code. Please get a new code from the app settings.")
        
        elif text.startswith("/today"):
            if not user:
                await send_message(chat_id, "Please link your account first using /link")
            else:
                from app.services.schedule_service import get_today_schedule
                schedule = get_today_schedule(db, user.id)
                if schedule and schedule.tasks:
                    task_list = "\n".join([
                        f"• <b>{t.startTime}-{t.endTime}</b>: {t.task}"
                        for t in schedule.tasks
                    ])
                    await send_message(chat_id, f"📅 <b>Today's Schedule</b>\n\n{task_list}")
                else:
                    await send_message(chat_id, "📅 No tasks scheduled for today.\n\nAdd tasks in the Second Thought app!")
        
        elif text.startswith("/settings"):
            if not user:
                await send_message(chat_id, "Please link your account first using /link")
            else:
                settings_msg = f"""⚙️ <b>Your Settings</b>

🔔 <b>Notifications:</b>
• Remind 10 min before: {'✅' if user.remind_before_activity else '❌'}
• Remind on start: {'✅' if user.remind_on_start else '❌'}
• Nudge during activity: {'✅' if user.nudge_during_activity else '❌'}
• Congratulate on finish: {'✅' if user.congratulate_on_finish else '❌'}

⏱ Default slot duration: {user.default_slot_duration} min
🌍 Timezone: {user.timezone}

<i>Change these settings in the app!</i>"""
                await send_message(chat_id, settings_msg)
        
        elif text.startswith("/help"):
            help_text = """🤖 <b>Second Thought Bot Commands</b>

/start - Start the bot
/link [code] - Link your account
/today - View today's schedule
/settings - View your settings
/unlink - Unlink your account
/help - Show this help

Your notification settings in the app control when I'll message you about your tasks."""
            await send_message(chat_id, help_text)
        
        elif text.startswith("/unlink"):
            if user:
                unlink_telegram(db, user.id)
                await send_message(chat_id, "✅ Your Telegram has been unlinked from your account.")
            else:
                await send_message(chat_id, "Your Telegram isn't linked to any account.")
        
        else:
            # Unknown message
            if user:
                await send_message(chat_id, "I didn't understand that. Type /help for available commands.")
            else:
                await send_message(chat_id, "Please link your account first. Use /link [code] to connect your Second Thought account.")
        
        return "OK"
    
    finally:
        db.close()


async def send_task_reminder(user: User, task: Task, reminder_type: str) -> bool:
    """Send a task reminder notification"""
    if not user.telegram_chat_id:
        return False
    
    if reminder_type == "remind_before":
        message = f"⏰ <b>Coming up in 10 minutes!</b>\n\n📋 {task.task_description}\n🕐 {task.start_time} - {task.end_time}"
    
    elif reminder_type == "remind_on_start":
        message = f"🚀 <b>Time to start!</b>\n\n📋 {task.task_description}\n🕐 Now until {task.end_time}"
    
    elif reminder_type == "nudge_during":
        message = f"💪 <b>Keep going!</b>\n\n📋 {task.task_description}\n\nYou're doing great, stay focused!"
    
    elif reminder_type == "congratulate":
        message = f"🎉 <b>Time's up!</b>\n\n📋 {task.task_description}\n\nGreat job completing this task!"
    
    else:
        return False
    
    return await send_message(user.telegram_chat_id, message)


async def process_notifications():
    """Process and send all pending notifications"""
    from app.services.schedule_service import get_tasks_for_notification, update_notification_flags
    
    db = SessionLocal()
    try:
        notifications = get_tasks_for_notification(db)
        
        for reminder_type, tasks in notifications.items():
            for user, task in tasks:
                success = await send_task_reminder(user, task, reminder_type)
                if success:
                    flag_map = {
                        "remind_before": "reminded_before",
                        "remind_on_start": "reminded_on_start",
                        "nudge_during": "nudged_during",
                        "congratulate": "congratulated"
                    }
                    update_notification_flags(db, task.id, flag_map[reminder_type])
    
    finally:
        db.close()
