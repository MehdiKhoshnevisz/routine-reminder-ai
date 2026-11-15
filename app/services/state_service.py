from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentHistory, TelegramState


async def get_telegram_state(db: AsyncSession, telegram_id: int) -> dict:
    row = await db.get(TelegramState, telegram_id)
    return dict(row.data) if row and row.data else {}


async def save_telegram_state(db: AsyncSession, telegram_id: int, data: dict) -> None:
    row = await db.get(TelegramState, telegram_id)
    if row:
        row.data = data
    else:
        db.add(TelegramState(telegram_id=telegram_id, data=data))
    await db.commit()


async def get_agent_history(db: AsyncSession, user_id: int) -> list[dict]:
    row = await db.get(AgentHistory, user_id)
    return list(row.messages) if row and row.messages else []


async def save_agent_history(db: AsyncSession, user_id: int, messages: list[dict]) -> None:
    row = await db.get(AgentHistory, user_id)
    if row:
        row.messages = messages
    else:
        db.add(AgentHistory(user_id=user_id, messages=messages))
    await db.commit()
