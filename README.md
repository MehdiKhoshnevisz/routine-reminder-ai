# Routine AI – backend

FastAPI + PostgreSQL + OpenRouter (LLM) + Telegram bot.
Tell the agent your routines in plain language ("brush teeth at 7:10, sport at 19 o'clock on weekdays")
and it stores them; the Telegram bot then messages you at each routine's time.

## Structure

```
app/
  main.py                 FastAPI app; starts the Telegram bot + reminder loop on startup
  core/config.py          settings from .env
  db/                     SQLAlchemy engine/session, Base
  models/                 ORM models: User, Routine
  schemas/                Pydantic request/response models
  services/               business logic (shared by API, agent and bot)
  api/v1/endpoints/       REST routes: users, routines, agent
  agent/                  LLM agent: prompt -> structured JSON actions -> executed via services
  bot/telegram_bot.py     Telegram handlers (/start, /routines, /delete, /timezone, free text -> agent)
  scheduler/reminders.py  checks every 30s and sends due reminders on Telegram
```

## Run

```bash
docker compose up -d                 # PostgreSQL on :5432
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # then set OPENROUTER_API_KEY and TELEGRAM_BOT_TOKEN (from @BotFather)
.venv/bin/uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

Tables are created automatically on startup (add Alembic later when the schema starts changing).

## Deploy (Vercel)

On Vercel the app runs serverless (`TELEGRAM_MODE=webhook`, automatic when `VERCEL` is set):

- Telegram **pushes** updates to `POST /api/telegram/webhook` (checked against `TELEGRAM_WEBHOOK_SECRET`).
- Reminders: something must call `GET /api/cron/reminders` **every minute** with
  `Authorization: Bearer <CRON_SECRET>`, e.g. a free [cron-job.org](https://cron-job.org) job
  (Vercel Cron on the Hobby plan only runs once a day).
- Bot edit state and agent history are stored in Postgres (`telegram_states`, `agent_histories`).

Steps:

1. Hosted Postgres (e.g. Neon): set `DATABASE_URL` on Vercel. `postgres://` URLs are converted automatically;
   prefer the pooled URL.
2. Env vars on Vercel: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`,
   `DEFAULT_TIMEZONE`, `TELEGRAM_WEBHOOK_SECRET`, `CRON_SECRET`.
3. `vercel deploy --prod`
4. `.venv/bin/python -m scripts.set_webhook https://<your-app>.vercel.app` (uses the same secret from `.env`).
5. Create the per-minute cron job.

Running locally in polling mode removes the webhook. Re-run step 4 afterwards.

## API

| Method | Path | |
|---|---|---|
| POST | /api/v1/users | create user |
| GET/PATCH | /api/v1/users/{id} | get / update (e.g. timezone) |
| GET/POST | /api/v1/users/{id}/routines | list / create routine |
| GET/PATCH/DELETE | /api/v1/users/{id}/routines/{rid} | manage a routine |
| POST | /api/v1/agent/chat | `{"user_id": 1, "message": "sport at 19 every day"}` |

## Telegram

Open your bot and send `/start` (this registers you and shows the menu keyboard). Then just type routines,
or use the buttons:

| Button / command | |
|---|---|
| My routines · `/routines` | list routines |
| New routine | type it in your own words, the AI creates it |
| Update routine · `/update` or `/update <id>` | pick a routine, then change time, days (toggle buttons), title, pause/resume, delete |
| Delete routine · `/delete` or `/delete <id>` | delete with confirmation |
| Timezone · `/timezone Europe/Berlin` | reminders are sent in your timezone |
| `/cancel` | cancel a pending edit |

You can also update in plain language: "move sport to 20:00".

### Groups

Add the bot to a group. Routines created in the group belong to the group (a `users` row whose
`telegram_id` is the group chat id), have the group's timezone, and are reminded **in the group**.

- Only group admins (including anonymous admins) can create, edit or delete them, or set `/timezone`. Anyone can use `/routines` and `/help`.
- The bot only reacts to messages that mention it (`@bot sport at 19`) or reply to it. When it asks for a new time or title, reply to its message.
- If the group is upgraded to a supergroup and gets a new chat id, its routines move to the new id.

## Notes

- `days` are 0=Mon … 6=Sun; empty = every day.
- Chat history for the agent (last few messages) is stored in the `agent_histories` table.
- The agent calls OpenRouter (default model `openai/gpt-4o-mini`, set via `OPENROUTER_MODEL`) for JSON actions and builds the
  reply from what was actually saved, so confirmations are always truthful.
