"""Background loop that sends Telegram reminders when a routine's time arrives."""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload
from telegram import Bot
from telegram.error import ChatMigrated, Forbidden

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Routine, User
from app.services import state_service

log = logging.getLogger(__name__)


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa_time(t) -> str:
    """07:00 -> '۷', 07:10 -> '۷:۱۰'."""
    s = str(t.hour) if t.minute == 0 else f"{t.hour}:{t.minute:02d}"
    return s.translate(_FA_DIGITS)


def _is_suffix(title: str) -> str:
    """Persian 'is' ending: ورزش -> ورزشه, مطالعه -> مطالعه‌ست, یوگا -> یوگاست."""
    title = title.strip()
    if title.endswith("ه"):
        return f"{title}‌ست"
    if title.endswith(("ا", "و")):
        return f"{title}ست"
    return f"{title}ه"


def reminder_text(routine: Routine) -> str:
    return (
        f"ساعت {_fa_time(routine.time)} شد\n"
        f"وقت {_is_suffix(routine.title)}\n\n"
    )


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
        unreachable: set[int] = set()  # users/groups that blocked or removed the bot
        for routine in rows.all():
            if routine.user_id in unreachable:
                continue
            now_local = datetime.now(ZoneInfo(routine.user.timezone))
            if not is_due(routine, now_local):
                continue
            text = reminder_text(routine)
            if routine.description:
                text += f"\n{routine.description}"
            try:
                # telegram_id is a person (private chat) or a group (routine created in the group).
                try:
                    await bot.send_message(chat_id=routine.user.telegram_id, text=text)
                except ChatMigrated as e:  # group became a supergroup
                    routine.user.telegram_id = e.new_chat_id
                    await bot.send_message(chat_id=e.new_chat_id, text=text)
                routine.last_reminded_on = now_local.date()
                log.info("Reminded user %s about routine %s", routine.user_id, routine.id)
            except Forbidden:
                # Bot was blocked (private chat) or removed from the group: stop retrying every day.
                # The routines stay, paused; the user can resume them from /routines.
                unreachable.add(routine.user_id)
                await db.execute(
                    update(Routine).where(Routine.user_id == routine.user_id).values(is_active=False)
                )
                log.info("User %s unreachable, paused their routines", routine.user_id)
            except Exception:
                log.exception("Failed to send reminder for routine %s", routine.id)
        await db.commit()


async def run_once() -> None:
    """One reminder check with its own Bot instance (serverless cron endpoint)."""
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
        await check_and_send(bot)
    if datetime.now().minute == 0:  # hourly housekeeping
        await cleanup()


async def cleanup() -> None:
    async with SessionLocal() as db:
        n = await state_service.delete_stale_agent_histories(db)
    if n:
        log.info("Deleted %s stale agent histories", n)


async def reminder_loop(bot: Bot) -> None:
    log.info("Reminder loop started (every %ss)", settings.REMINDER_CHECK_SECONDS)
    while True:
        try:
            await check_and_send(bot)
            if datetime.now().minute == 0 and datetime.now().second < settings.REMINDER_CHECK_SECONDS:
                await cleanup()
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(settings.REMINDER_CHECK_SECONDS)
