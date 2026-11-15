"""Endpoints used when running serverless (Vercel): Telegram webhook + reminder cron."""

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.telegram_bot import process_webhook_update
from app.core.config import settings
from app.db.session import init_db
from app.scheduler.reminders import run_once

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["serverless"])


def _check(secret: str, given: str | None) -> None:
    if not secret or not given or not secrets.compare_digest(secret, given):
        raise HTTPException(401, "Unauthorized")


@router.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    _check(settings.TELEGRAM_WEBHOOK_SECRET, x_telegram_bot_api_secret_token)
    await init_db()
    try:
        await process_webhook_update(await request.json())
    except Exception:
        # Still answer 200: otherwise Telegram re-sends the same update over and over.
        log.exception("Failed to process Telegram update")
    return {"ok": True}


@router.api_route("/cron/reminders", methods=["GET", "POST"], include_in_schema=False)
async def cron_reminders(authorization: str | None = Header(default=None)):
    """Call every minute (cron-job.org, Vercel Cron on Pro, ...) with `Authorization: Bearer <CRON_SECRET>`."""
    _check(settings.CRON_SECRET, (authorization or "").removeprefix("Bearer ").strip() or None)
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "TELEGRAM_BOT_TOKEN not set")
    await init_db()
    await run_once()
    return {"ok": True}
