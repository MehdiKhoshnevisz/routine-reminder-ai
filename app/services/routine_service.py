from datetime import time

from sqlalchemy import delete, select
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
    run_date: date | None = None,
) -> Routine:
    routine = Routine(
        user_id=user_id,
        title=title,
        time=at.replace(second=0, microsecond=0),
        days=sorted(set(days or [])),
        description=description,
        is_active=True,
        run_date=run_date,
    )
    db.add(routine)
    await db.commit()
    await db.refresh(routine)
    return routine


async def update_routine(db: AsyncSession, routine: Routine, **fields) -> Routine:
    for k, v in fields.items():
        if v is not None:
            setattr(routine, k, v)
    if fields.get("time") is not None or fields.get("run_date") is not None:
        routine.last_reminded_on = None  # allow reminder again at the new time/date
    await db.commit()
    await db.refresh(routine)
    return routine


async def delete_routine(db: AsyncSession, routine: Routine) -> None:
    await db.delete(routine)
    await db.commit()


async def delete_all_routines(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(delete(Routine).where(Routine.user_id == user_id))
    await db.commit()
    return result.rowcount


def _days_text(r: Routine) -> str:
    if r.run_date:
        # Show Persian date
        from app.agent.parsing import _gregorian_to_persian
        jy, jm, jd = _gregorian_to_persian(r.run_date)
        persian_months = [
            "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
            "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"
        ]
        return f"{jd} {persian_months[jm-1]} {jy}"
    return "هر روز" if not r.days else "، ".join(DAY_NAMES[d] for d in DAY_ORDER if d in r.days)


# Left-to-right mark around "#12": in Persian (RTL) text Telegram would otherwise show it as "12#".
LRM = "\u200e"


def format_routine(r: Routine) -> str:
    status = "" if r.is_active else " (متوقف)"
    # first title and days, time last after "ساعت": reads right in RTL text
    return f"{r.title} - {_days_text(r)} - ساعت {r.time.strftime('%H:%M')}{status}"


def format_routine_card(r: Routine, header: str) -> str:
    """Multi-line version for confirmations, one field per line."""
    lines = [
        header,
        "",
        f"شناسه: {LRM}#{r.id}{LRM}",
        f"عنوان: {r.title}",
        f"ساعت: {r.time.strftime('%H:%M')}",
        f"روزها: {_days_text(r)}",
    ]
    if r.run_date:
        lines.append(f"تاریخ: {_days_text(r)}")  # _days_text handles run_date display
    if not r.is_active:
        lines.append("وضعیت: متوقف")
    return "\n".join(lines)
