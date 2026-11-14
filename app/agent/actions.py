"""Executes the structured actions produced by the LLM against the database."""

import logging
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


def _norm(s: str) -> str:
    return " ".join(w.rstrip("s") for w in s.lower().replace("-", " ").split())


def _find(routines: list[Routine], action: Action) -> Routine | None:
    """Match by title first (small LLMs copy names reliably, ids not), then by id."""
    if action.title:
        t = _norm(action.title)
        exact = [r for r in routines if _norm(r.title) == t]
        partial = [r for r in routines if t in _norm(r.title) or _norm(r.title) in t]
        if exact or partial:
            return (exact or partial)[0]
    if action.routine_id is not None:
        return next((r for r in routines if r.id == action.routine_id), None)
    return None


async def execute(db: AsyncSession, user_id: int, actions: list[Action]) -> list[str]:
    """Returns a human readable line per action describing what really happened."""
    results: list[str] = []
    for a in actions:
        routines = await rs.list_routines(db, user_id)
        try:
            if a.type == "update" and a.title and a.time and not _find(routines, a):
                a.type = "create"  # model said "update" for a routine that doesn't exist yet
            if a.type == "create":
                if not a.title or not a.time:
                    results.append("⚠️ I need both an activity and a time to create a routine.")
                    continue
                r = await rs.create_routine(
                    db, user_id, title=a.title.strip(), at=parse_time(a.time), days=parse_days(a.days)
                )
                results.append(f"✅ Created {rs.format_routine(r)}")
            elif a.type == "list":
                body = "\n".join(rs.format_routine(r) for r in routines) or "You have no routines yet."
                results.append(f"📋 Your routines:\n{body}")
            else:
                r = _find(routines, a)
                if not r:
                    results.append(f"⚠️ Couldn't find the routine '{a.title or a.routine_id}'.")
                elif a.type == "delete":
                    await rs.delete_routine(db, r)
                    results.append(f"🗑 Deleted '{r.title}'")
                elif a.type in ("pause", "resume"):
                    r = await rs.update_routine(db, r, is_active=a.type == "resume")
                    results.append(f"{'▶️' if r.is_active else '⏸'} {rs.format_routine(r)}")
                else:  # update
                    r = await rs.update_routine(
                        db, r,
                        time=parse_time(a.time) if a.time else None,
                        days=parse_days(a.days) if a.days else None,
                    )
                    results.append(f"✏️ Updated {rs.format_routine(r)}")
        except ValueError as e:
            results.append(f"⚠️ {e}")
        log.info("action %s -> %s", a.model_dump(exclude_none=True), results[-1])
    return results
