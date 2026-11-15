from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AgentHistory, TelegramState


async def get_telegram_state(db: AsyncSession, telegram_id: int) -> dict:
    row = await db.get(TelegramState, telegram_id)
    return dict(row.data) if row and row.data else {}


async def save_telegram_state(db: AsyncSession, telegram_id: int, data: dict, previous: dict) -> None:
    """Writes only when the state changed, and deletes the row instead of storing an empty state
    (most users have no pending edit, so most users need no row at all)."""
    if data == previous:
        return
    if data:
        await db.merge(TelegramState(telegram_id=telegram_id, data=data))
    else:
        await db.execute(delete(TelegramState).where(TelegramState.telegram_id == telegram_id))
    await db.commit()


def _history_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=settings.AGENT_HISTORY_TTL_MINUTES)


async def get_agent_history(db: AsyncSession, user_id: int) -> list[dict]:
    row = await db.get(AgentHistory, user_id)
    if not row or not row.messages or row.updated_at < _history_cutoff():
        return []  # stale history is useless for follow-ups
    return list(row.messages)


async def save_agent_history(db: AsyncSession, user_id: int, messages: list[dict]) -> None:
    await db.merge(AgentHistory(user_id=user_id, messages=messages, updated_at=datetime.now(timezone.utc)))
    await db.commit()


async def delete_stale_agent_histories(db: AsyncSession) -> int:
    result = await db.execute(delete(AgentHistory).where(AgentHistory.updated_at < _history_cutoff()))
    await db.commit()
    return result.rowcount
