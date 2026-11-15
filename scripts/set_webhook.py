"""Points the Telegram bot at the deployed webhook and registers the command menu.

Usage:  .venv/bin/python -m scripts.set_webhook https://your-app.vercel.app
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from .env (must match the values set on Vercel).
Note: running the app locally in polling mode deletes the webhook again, so re-run this afterwards.
"""

import asyncio
import sys

from telegram import Bot

from app.bot.telegram_bot import COMMANDS, DESCRIPTION, SHORT_DESCRIPTION
from app.core.config import settings


async def main(base_url: str) -> None:
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        sys.exit("Set TELEGRAM_WEBHOOK_SECRET in .env first (same value as on Vercel).")
    url = base_url.rstrip("/") + "/api/telegram/webhook"
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
        await bot.set_webhook(
            url,
            secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        await bot.set_my_commands(COMMANDS)
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
        info = await bot.get_webhook_info()
        print(f"Webhook: {info.url}  pending={info.pending_update_count}  last_error={info.last_error_message}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
