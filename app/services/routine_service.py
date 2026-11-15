from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Routine

# Indexed by Python weekday (0 = Monday). Displayed in Persian week order (Saturday first).
DAY_NAMES = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
DAY_ORDER = [5, 6, 0, 1, 2, 3, 4]


async def list_routines(db: AsyncSession, user_id: int, only_active: bool = False) -> list[Routine]:
    q = select(Routine).where(Routine.user_id == user_id)
    if only_active:
        q = q.where(Routine.is_active.is_(True))
    return list((await db.scalars(q.order_by(Routine.time))).all())


async def get_routine(db: AsyncSession, user_id: int, routine_id: int) -> Routine | None:
    r = await db.get(Routine, routine_id)
    return r if r and r.user_id == user_id else None


async def create_routine(
    db: AsyncSession,
    user_id: int,
    title: str,
    at: time,
    days: list[int] | None = None,
    description: str | None = None,
) -> Routine:
    routine = Routine(
        user_id=user_id,
        title=title,
        time=at.replace(second=0, microsecond=0),
        days=sorted(set(days or [])),
        description=description,
        is_active=True,
    )
    db.add(routine)
    await db.commit()
    await db.refresh(routine)
    return routine


async def update_routine(db: AsyncSession, routine: Routine, **fields) -> Routine:
    for k, v in fields.items():
        if v is not None:
            setattr(routine, k, v)
    if fields.get("time") is not None:
        routine.last_reminded_on = None  # allow reminder again today at the new time
    await db.commit()
    await db.refresh(routine)
    return routine


async def delete_routine(db: AsyncSession, routine: Routine) -> None:
    await db.delete(routine)
    await db.commit()


def _days_text(r: Routine) -> str:
    return "هر روز" if not r.days else "، ".join(DAY_NAMES[d] for d in DAY_ORDER if d in r.days)


def format_routine(r: Routine) -> str:
    status = "" if r.is_active else " (متوقف)"
    return f"#{r.id} {r.time.strftime('%H:%M')} - {r.title} [{_days_text(r)}]{status}"


def format_routine_card(r: Routine, header: str) -> str:
    """Multi-line version for confirmations, one field per line."""
    lines = [
        header,
        "",
        f"شناسه: #{r.id}",
        f"عنوان: {r.title}",
        f"ساعت: {r.time.strftime('%H:%M')}",
        f"روزها: {_days_text(r)}",
    ]
    if not r.is_active:
        lines.append("وضعیت: متوقف")
    return "\n".join(lines)
