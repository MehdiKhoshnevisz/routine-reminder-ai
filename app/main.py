import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.bot.telegram_bot import build_application, setup_bot_commands
from app.core.config import settings
from app.db.session import init_db
from app.scheduler.reminders import reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()

    tg_app = build_application()
    reminder_task = None
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        await setup_bot_commands(tg_app)
        await tg_app.updater.start_polling()
        reminder_task = asyncio.create_task(reminder_loop(tg_app.bot))

    yield

    if reminder_task:
        reminder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminder_task
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
