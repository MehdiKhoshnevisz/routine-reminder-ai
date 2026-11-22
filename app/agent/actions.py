"""Executes the structured actions produced by the LLM against the database."""

import logging
import re
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.parsing import parse_days, parse_time
from app.models import Routine
from app.services import routine_service as rs

log = logging.getLogger(__name__)


class Action(BaseModel):
    type: Literal["create", "update", "delete", "pause", "resume", "list"]
    routine_id: int | None = None
    title: str | None = None
    time: str | None = None
    days: str | None = None


class AgentOutput(BaseModel):
    actions: list[Action] = []
    message: str = ""
    off_topic: bool = False  # message has nothing to do with routines


def _norm(s: str) -> str:
    return " ".join(w.rstrip("s") for w in s.lower().replace("-", " ").split())


def _candidates(routines: list[Routine], action: Action) -> list[Routine]:
    """Routines the action may refer to. Title first (LLMs copy names more reliably than ids);
    routine_id narrows it down when several routines share the name."""
    by_title: list[Routine] = []
    if action.title:
        t = _norm(action.title)
        by_title = [r for r in routines if _norm(r.title) == t] or [
            r for r in routines if t in _norm(r.title) or _norm(r.title) in t
        ]
    if action.routine_id is not None:
        by_id = [r for r in (by_title or routines) if r.id == action.routine_id]
        if by_id:
            return by_id
    return by_title


# Words that say whether "<existing routine> at <new time>" means change it or add another one.
_CHANGE_WORDS = re.compile(r"عوض|تغییر|ببر|جابه?\s?جا|اصلاح|به\s?جای|کنش|رو کن|بشه|بکن|change|move")
_HALF_ONLY = re.compile(r"صبح|ظهر|عصر|شب|بامداد")
_ADD_WORDS = re.compile(r"اضافه|جدید|بساز|یکی دیگه|یه دونه دیگه|دوتا|دو تا|\bهم\b|ست کن|بذار|تنظیم|یادم بنداز|یادآوری|add|new")


def _ask_change_or_add(routines: list[Routine], a: Action, message: str) -> str:
    """'ورزش ساعت ۱۹' while ورزش exists at 07:00 could mean either; the model guesses inconsistently, so ask."""
    if a.type not in ("create", "update") or not a.title or not a.time or not message:
        return ""
    if _CHANGE_WORDS.search(message) or _ADD_WORDS.search(message):
        return ""
    same = [r for r in routines if _norm(r.title) == _norm(a.title)]
    new = parse_time(a.time)
    if not same or any(r.time == new for r in same):
        return ""
    if _HALF_ONLY.search(message) and not re.search(r"[\d۰-۹]", message):
        return ""  # "نه صبح" / "عصر": AM/PM correction of the routine just set
    title, at = same[0].title, new.strftime("%H:%M")
    now = "، ".join(r.time.strftime("%H:%M") for r in same)
    return f"«{title}» الان ساعت {now} هست. ساعتش رو به {at} تغییر بدم، یا یه «{title}» جدید ساعت {at} هم اضافه کنم؟"


_ALL_WORDS = re.compile(r"همه|هر\s?دو|دوتاش|همشون|همه‌شون|all|both")


def _collapse_unasked_multi(actions: list[Action], message: str) -> list[Action]:
    """Safety net: "ورزش رو حذف کن" with two ورزش routines must not delete both. Several
    delete/update/pause/resume actions on the same name collapse to one without routine_id (-> "which one?")
    unless the user said "all" / "both"."""
    if _ALL_WORDS.search(message):
        return actions
    out: list[Action] = []
    seen: dict[tuple[str, str], Action] = {}
    for a in actions:
        if a.type in ("delete", "update", "pause", "resume") and a.title:
            key = (a.type, _norm(a.title))
            if key in seen:
                seen[key].routine_id = None
                continue
            seen[key] = a
        out.append(a)
    return out


async def execute(db: AsyncSession, user_id: int, actions: list[Action], message: str = "") -> list[str]:
    """Returns a human readable line per action describing what really happened (or a question)."""
    actions = _collapse_unasked_multi(actions, message)
    results: list[str] = []
    created: set[str] = set()
    for a in actions:
        routines = await rs.list_routines(db, user_id)
        try:
            if question := _ask_change_or_add(routines, a, message):
                results.append(question)
                continue
            if a.type == "create" and a.title:
                key = _norm(a.title)
                if key in created:
                    continue  # model split one activity into several routines
                created.add(key)
                same = next((r for r in routines if _norm(r.title) == key), None)
                if same and a.time and same.time == parse_time(a.time):
                    results.append(f"اینو از قبل داری :) {rs.format_routine(same)}")
                    continue
            if a.type == "update" and a.title and a.time and not _candidates(routines, a):
                a.type = "create"  # model said "update" for a routine that doesn't exist yet
            if a.type == "create":
                if not a.title:
                    when = f"ساعت {a.time} " if a.time else ""
                    results.append(f"{when}چی رو یادت بندازم؟")
                    continue
                if not a.time:
                    results.append(f"«{a.title.strip()}» رو چه ساعتی یادت بندازم؟")
                    continue

                # Parse days - handle special "once" marker ([-1])
                parsed_days = parse_days(a.days)
                is_once = parsed_days == [-1]

                # Also check message for "فردا" (tomorrow) or "امروز" (today) as fallback
                msg_lower = message.lower()
                wants_tomorrow = "فردا" in msg_lower or "farda" in msg_lower
                wants_today = "امروز" in msg_lower or "emrooz" in msg_lower

                if is_once or wants_tomorrow or wants_today:
                    # For one-time or specific day routines, calculate the target day
                    from app.models import User
                    from datetime import datetime, timedelta
                    from zoneinfo import ZoneInfo
                    user = await db.get(User, user_id)
                    if user:
                        now = datetime.now(ZoneInfo(user.timezone))
                        if wants_tomorrow:
                            target_day = (now + timedelta(days=1)).weekday()  # 0=Monday
                            parsed_days = [target_day]
                        elif wants_today:
                            parsed_days = [now.weekday()]
                        elif is_once:
                            # Default to today if time hasn't passed, else tomorrow
                            routine_time = parse_time(a.time)
                            today_at_time = now.replace(hour=routine_time.hour, minute=routine_time.minute, second=0, microsecond=0)
                            if now > today_at_time:
                                target_day = (now + timedelta(days=1)).weekday()
                            else:
                                target_day = now.weekday()
                            parsed_days = [target_day]
                    else:
                        parsed_days = []  # fallback to every day

                r = await rs.create_routine(
                    db, user_id, title=a.title.strip()[:200], at=parse_time(a.time), days=parsed_days
                )
                msg = f"ساخته شد :) \n {rs.format_routine(r)}"
                if is_once or wants_tomorrow or wants_today:
                    msg += "\n\n⚠️ این کار فقط برای یک باره تنظیم شده. بعد از اجرا یادت باشه پاکش کنی (از منوی «کار‌های من»)."
                results.append(msg)
            elif a.type == "list":
                body = "\n".join(rs.format_routine(r) for r in routines) or "هنوز هیچ کاری نداری."
                results.append(f"کار‌هات:\n{body}")
            else:
                cands = _candidates(routines, a)
                r = cands[0] if len(cands) == 1 else None
                if not cands:
                    name = a.title or f"#{a.routine_id}"
                    if routines:
                        names = "، ".join(f"«{x.title}»" for x in routines)
                        results.append(f"کاری به اسم «{name}» پیدا نکردم :/ کار‌هات: {names}. منظورت کدومه؟")
                    else:
                        results.append("هنوز هیچ کاری نداری. می‌خوای یکی بسازیم؟ مثلاً «ورزش ساعت ۱۹».")
                elif len(cands) > 1:
                    lines = "\n".join(f"• {rs.format_routine(x)}" for x in cands)
                    results.append(f"چندتا کار «{a.title}» داری، منظورت کدومه؟ (ساعت یا شماره‌اش رو بگو)\n{lines}")
                elif a.type == "delete":
                    label = rs.format_routine(r)
                    await rs.delete_routine(db, r)
                    results.append(f"حذف شد: {label}")
                elif a.type in ("pause", "resume"):
                    r = await rs.update_routine(db, r, is_active=a.type == "resume")
                    results.append(f"{'دوباره فعال شد :)' if r.is_active else 'متوقف شد:'} {rs.format_routine(r)}")
                else:  # update
                    r = await rs.update_routine(
                        db, r,
                        time=parse_time(a.time) if a.time else None,
                        days=parse_days(a.days) if a.days else None,
                    )
                    results.append(rs.format_routine_card(r, "آپدیت شد :)"))
        except ValueError as e:
            results.append(f"{e} :(")
        log.info("action %s -> %s", a.model_dump(exclude_none=True), results[-1])
    return results
