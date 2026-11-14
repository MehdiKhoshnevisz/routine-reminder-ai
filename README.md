# Routine AI – backend

FastAPI + PostgreSQL + Ollama (local LLM) + Telegram bot.
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
ollama serve                         # if not already running (model: llama3.2)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # then set TELEGRAM_BOT_TOKEN (from @BotFather)
.venv/bin/uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

Tables are created automatically on startup (add Alembic later when the schema starts changing).

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
| 📋 My routines · `/routines` | list routines |
| ➕ New routine | type it in your own words, the AI creates it |
| ✏️ Update routine · `/update` or `/update <id>` | pick a routine, then change ⏰ time, 📅 days (toggle buttons), ✏️ title, ⏸ pause/resume, 🗑 delete |
| 🗑 Delete routine · `/delete` or `/delete <id>` | delete with confirmation |
| 🌍 Timezone · `/timezone Europe/Berlin` | reminders are sent in your timezone |
| `/cancel` | cancel a pending edit |

You can also update in plain language: "move sport to 20:00".

## Notes

- `days` are 0=Mon … 6=Sun; empty = every day.
- Chat history for the agent is in memory (last few messages) and resets on restart.
- llama3.2 is a small model; the agent asks it for strict JSON actions and builds the reply from what was
  actually saved, so confirmations are always truthful. A bigger model (e.g. `qwen2.5:7b`) will understand
  trickier phrasing — just change `OLLAMA_MODEL`.
