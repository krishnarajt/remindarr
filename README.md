# instructions for this bot, and how to make stuff like this again

1. register your bot with botfather.
Step 1. Get your bot token (if you don’t have it yet)

Open Telegram → search for @BotFather.

Send /newbot.

Follow the prompts → choose a name and username (ending in _bot).

You’ll receive a token like:

1234567890:ABCDefGhIjkLmNoPQRstuVWxyz

Save it — we’ll call it BOT_TOKEN.

1.

Step 2: Get your chat ID (3 easy options)
🟢 Option 1: Quickest (Use a simple URL)

Open this in your browser — replacing <BOT_TOKEN> with your real token:

<https://api.telegram.org/bot><BOT_TOKEN>/getUpdates

Example:

<https://api.telegram.org/bot1234567890:ABCdefGhijKLmnopQRstuVWxyz/getUpdates>

Then send a message to your bot (like “hi”) and refresh that URL.

You’ll see a JSON response like:

{
  "ok": true,
  "result": [
    {
      "update_id": 123456789,
      "message": {
        "message_id": 1,
        "from": {
          "id": 987654321,
          "is_bot": false,
          "first_name": "Krishnaraj"
        },
        "chat": {
          "id": 987654321,
          "first_name": "Krishnaraj",
          "type": "private"
        },
        "date": 1731229324,
        "text": "hi"
      }
    }
  ]
}

👉 Your chat ID is:

987654321

That’s the value inside "chat": {"id": ...}

1. if you wanna communiate 2 way like from user to your bot, then ull need to use webhooks after hosting your app.

`
curl -X POST \
  "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -d "url=https://remindarr.krishnarajthadesar.in/api/notifications/webhook"
`

`curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"`

# usage

Talk to the bot in plain English — it uses an LLM (via the internal LLMGateway)
to turn free text into a structured schedule, then asks you to confirm:

- "remind me to call mom in 2 hours"
- "drink water every hour during work hours, not at night 10pm–7am"
- "taxes on the 1st & 3rd Saturday of even months at 9am"

Buttons drive everything else. Commands: `/add` (guided), `/list` (manage with
Done/Pause/Delete buttons), `/notion`, `/settings`, `/tz Area/City`, `/cancel`.

See `CLAUDE.md` for architecture and the scheduling model.

# overhaul status (the old "things to fix")

1. ✅ Commands fixed — `parse_mode=HTML` set (Markdown previously rendered as
   literal `*` / backslashes).
2. ✅ Modularised into `app/{common,db,utils,services,api}` (scron-style).
3. — MCP integration: not done (out of scope for this pass).
4. ✅ Notion query bug fixed (`or`→`and` completion filter; `status` property
   type now handled) + periodic background sync added.
5. ✅ Recurring reminders work via the RRULE engine (drift-free, backlog-safe).
6. ✅ Times always shown in the user's timezone.
7. ✅ Schema restructured; Notion reminders carry `notion_db_id`/`notion_page_id`.
8. ✅ Deleting a Notion DB now also deletes its reminders.
9. ✅ Reminders (incl. recurring) can be deleted/paused from `/list`.
10. ✅ `/list` works with interactive per-item controls.
