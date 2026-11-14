"""Background loop that sends Telegram reminders when a routine's time arrives."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from telegram import Bot

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Routine, User

log = logging.getLogger(__name__)


def is_due(routine: Routine, now_local: datetime) -> bool:
    if routine.last_reminded_on == now_local.date():
        return False
    if routine.days and now_local.weekday() not in routine.days:
        return False
    scheduled = now_local.replace(
        hour=routine.time.hour, minute=routine.time.minute, second=0, microsecond=0
    )
    return scheduled <= now_local < scheduled + timedelta(minutes=settings.REMINDER_GRACE_MINUTES)


async def check_and_send(bot: Bot) -> None:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(Routine)
            .join(Routine.user)
            .where(Routine.is_active.is_(True), User.telegram_id.is_not(None))
            .options(joinedload(Routine.user))
        )
        for routine in rows.all():
            now_local = datetime.now(ZoneInfo(routine.user.timezone))
            if not is_due(routine, now_local):
                continue
            text = f"⏰ Reminder: {routine.title} ({routine.time.strftime('%H:%M')})"
            if routine.description:
                text += f"\n{routine.description}"
            try:
                await bot.send_message(chat_id=routine.user.telegram_id, text=text)
                routine.last_reminded_on = now_local.date()
                log.info("Reminded user %s about routine %s", routine.user_id, routine.id)
            except Exception:
                log.exception("Failed to send reminder for routine %s", routine.id)
        await db.commit()


async def reminder_loop(bot: Bot) -> None:
    log.info("Reminder loop started (every %ss)", settings.REMINDER_CHECK_SECONDS)
    while True:
        try:
            await check_and_send(bot)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(settings.REMINDER_CHECK_SECONDS)
