from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import User


def validate_timezone(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"Unknown timezone: {tz}") from e
    return tz


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def list_users(db: AsyncSession) -> list[User]:
    return list((await db.scalars(select(User).order_by(User.id))).all())


async def create_user(
    db: AsyncSession, name: str, telegram_id: int | None = None, timezone: str | None = None
) -> User:
    user = User(
        name=name,
        telegram_id=telegram_id,
        timezone=validate_timezone(timezone or settings.DEFAULT_TIMEZONE),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_or_create_telegram_user(db: AsyncSession, telegram_id: int, name: str) -> User:
    user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user:
        return user
    return await create_user(db, name=name, telegram_id=telegram_id)


async def update_user(db: AsyncSession, user: User, **fields) -> User:
    if fields.get("timezone"):
        validate_timezone(fields["timezone"])
    for k, v in fields.items():
        if v is not None:
            setattr(user, k, v)
    await db.commit()
    await db.refresh(user)
    return user
