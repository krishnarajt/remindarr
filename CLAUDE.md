# Remindarr — developer guide

Telegram reminder bot with **natural-language scheduling** and **Notion sync**,
built on FastAPI + SQLModel + Postgres. Deployed to k3s alongside `scron` and
`LLMGateway` (shared Postgres, separate schema; secrets via Vault + ArgoCD).

## Run locally

```bash
cp .env.example .env        # fill in BOT_TOKEN and (optionally) LLM_GATEWAY_API_KEY
docker compose up           # app on :8000, Postgres on :5433
python -m pytest -v         # unit tests (no DB/network needed)
```

Point Telegram at the webhook (via a tunnel for local dev):

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://<host>/api/notifications/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"   # optional but recommended
```

## Layout

```
main.py                         FastAPI app, lifespan, /health, /ready
app/common/constants.py         Settings (pydantic-settings): DATABASE_URL + DB_SCHEMA, BOT_TOKEN, LLM_*
app/common/schemas.py           ScheduleSpec / TimeWindow / ParseResult — the schedule contract
app/db/models.py                SQLModel tables (Users, Reminders); all timestamps tz-aware UTC
app/db/config_db.py             engine + init_db + get_session
app/utils/scheduling.py         THE engine: RRULE + windows → next UTC trigger
app/utils/time_utils.py         unit parsing, tz formatting, guided-add spec builder
app/services/llm_gateway.py     client for LLMGateway POST /api/chat
app/services/nl_parser.py       natural language → ScheduleSpec (LLM, validated)
app/services/reminder_service.py create/list/delete/pause/done; spec ↔ row
app/services/notion.py          Notion API (token, db, query, extract) — bug-fixed
app/services/notion_sync.py     periodic Notion → reminders sync + deletion
app/services/notification_worker.py  60s poll loop: fire due, reschedule
app/api/state.py                in-memory conversational state (single replica)
app/api/telegram_webhook.py     webhook: messages + button callbacks, NL-first UX
app/api/settings_routes.py      REST settings for an external frontend
k8s/ , Dockerfile , .github/    deployment (kustomize base+overlay, GHCR CI)
```

## Scheduling model (read this before touching dates)

A reminder's schedule is a `ScheduleSpec` (in `schedule_spec` JSONB, the source
of truth). Recurrence is an **RFC 5545 RRULE** (no DTSTART/COUNT/UNTIL inside the
string — those are separate fields/columns). On top we layer **active/blackout
time-of-day windows**, all anchored to a per-reminder IANA `timezone`.

`compute_next_trigger(spec, after_utc)` is the only place date math happens:
RRULEs run on **naive local** datetimes (dateutil), then we localize → UTC.
The worker reschedules with `advance_past_backlog` so downtime never floods and
the cadence never drifts. `count`/`until` are enforced by the worker, not the
RRULE, so window-filtered occurrences don't miscount.

## Conventions

- Telegram messages are **HTML** (`parse_mode=HTML`); wrap dynamic text in
  `telegram.esc()`. Never hand-format Markdown.
- Business logic is **synchronous**; the async webhook route and worker loop
  offload to threads (`asyncio.to_thread`). Don't `await` inside services.
- Every datetime stored or compared is **tz-aware UTC**.
- New routers go under `app/api/` and are included in `main.py`; new services
  under `app/services/`. Config is centralised in `app/common/constants.py`.

## Deploy

CI builds `ghcr.io/krishnarajt/remindarr:git-<sha>` (Dockerfile `prod` target),
rewrites `k8s/base/deployment.yaml`, and commits. ArgoCD syncs
`k8s/overlays/prod` (namespace `remindarr`). Secrets come from Vault key
`apps/remindarr/remindarr/env` via ExternalSecret → `remindarr-env`. Set
`DB_SCHEMA=remindarr` against the shared Postgres.
```
