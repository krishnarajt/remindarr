# Second Thought Backend

Backend API for the Second Thought timetable app with Telegram bot integration for notifications.

## Features

- 🔐 JWT Authentication (access + refresh tokens)
- 📅 Daily schedule management
- ⏰ Task time blocks with notifications
- 🤖 Telegram bot integration for reminders
- 🔔 Customizable notification settings:
  - Remind 10 minutes before activity
  - Remind on start of activity
  - Nudge during activity
  - Congratulate on finish
- 🐳 Docker + Kubernetes ready
- 🚀 ArgoCD deployment support

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL
- **ORM**: SQLAlchemy
- **Auth**: JWT (PyJWT)
- **Bot**: Telegram Bot API (httpx)

## Database Schema

```
users
├── id (PK)
├── username (unique)
├── password_hash
├── display_name
├── telegram_chat_id (unique)
├── telegram_username
├── remind_before_activity (bool)
├── remind_on_start (bool)
├── nudge_during_activity (bool)
├── congratulate_on_finish (bool)
├── default_slot_duration (int)
├── timezone
├── created_at
└── updated_at

schedules
├── id (PK)
├── user_id (FK -> users)
├── date (YYYY-MM-DD)
├── created_at
└── updated_at

tasks
├── id (PK)
├── task_uuid (unique)
├── user_id (FK -> users)
├── schedule_id (FK -> schedules)
├── start_time (HH:MM)
├── end_time (HH:MM)
├── task_description
├── is_completed
├── completed_at
├── reminded_before
├── reminded_on_start
├── nudged_during
├── congratulated
├── created_at
└── updated_at

refresh_tokens
├── id (PK)
├── user_id (FK -> users)
├── token
├── expires_at
└── created_at

telegram_link_codes
├── id (PK)
├── user_id (FK -> users)
├── code (6-digit)
├── expires_at
└── created_at
```

## API Endpoints

### Authentication
- `POST /api/auth/signup` - Create account
- `POST /api/auth/login` - Login
- `POST /api/auth/refresh` - Refresh access token
- `POST /api/auth/logout` - Logout

### User Settings
- `GET /api/user/settings` - Get settings
- `PUT /api/user/settings` - Update settings
- `POST /api/user/telegram/link` - Get Telegram link code
- `POST /api/user/telegram/unlink` - Unlink Telegram

### Schedule
- `POST /api/schedule/save` - Save daily schedule
- `GET /api/schedule/today` - Get today's schedule
- `GET /api/schedule/{date}` - Get schedule by date

### Webhook
- `POST /api/webhook/telegram` - Telegram webhook endpoint

## Local Development

### Using Docker Compose

```bash
# Copy env file
cp .env.example .env
# Edit .env with your values

# Start services
docker-compose up -d

# View logs
docker-compose logs -f
```

### Manual Setup

```bash
# Install dependencies (using uv)
uv sync

# Or using pip
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL=postgresql://second-thought-backend:second-thought-backend@localhost:5432/second-thought-backend
export SECRET_KEY=your-secret-key
export TELEGRAM_BOT_TOKEN=your-bot-token

# Run
uvicorn main:app --reload
```

## Telegram Bot Setup

1. Create a bot with [@BotFather](https://t.me/BotFather):
   - Send `/newbot`
   - Choose name: "Second Thought Backend"
   - Choose username: `second-thought-backend_bot` (or similar)
   - Save the token

2. Set the webhook after deploying:
```bash
curl -X POST \
  "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -d "url=https://second-thought-backend.yourdomain.com/api/webhook/telegram"
```

3. Verify webhook:
```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

### Bot Commands
- `/start` - Start the bot
- `/link [code]` - Link your account
- `/today` - View today's schedule
- `/settings` - View your settings
- `/unlink` - Unlink your account
- `/help` - Show help

## Kubernetes Deployment

### With ArgoCD

1. Push this repo to GitHub

2. Update `k8s/secrets.yaml` with your actual secrets

3. Update `k8s/ingress.yaml` with your domain

4. Update `k8s/deployment.yaml` with your Docker image

5. Apply the ArgoCD application:
```bash
kubectl apply -f k8s/argocd-application.yaml
```

### Manual

```bash
kubectl apply -k k8s/
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | - |
| `SECRET_KEY` | JWT signing key | - |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | - |
| `PORT` | Server port | 8000 |

## License

MIT
