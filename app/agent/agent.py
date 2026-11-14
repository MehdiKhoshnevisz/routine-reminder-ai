import json
import logging
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

from ollama import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.actions import AgentOutput, execute
from app.core.config import settings
from app.models import User
from app.services import routine_service as rs

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Routine AI. You turn the user's message into a JSON list of actions on their routines.
Now: {now} ({weekday}).

The user's current routines:
{routines}

Every action has a "type" and a "title". Action types:
- create: title (the activity, e.g. "Brush teeth"), time (24h "HH:MM"), days
- update: title = name of the EXISTING routine to change, plus only the fields that change (time, days)
- delete / pause / resume: title = name of the existing routine
- list: when the user wants to see their routines

Rules:
- "add", "create", "remind me", "I want to" = create. One "create" action PER activity:
  "brush teeth at 7 and sport at 19" is TWO actions.
- Copy times exactly. "6:30" -> "06:30", "19" -> "19:00", "10 7 o'clock" / "10 past 7" -> "07:10".
  Only add 12 hours if the user says pm, evening, afternoon or night.
- days: "" (every day) unless the user names days; then e.g. "monday,wednesday", "weekdays" or "weekends".
- If the message is not about routines or is unclear, return no actions and put a short reply/question in "message".

Examples:
"first brush teeth in 10 7 oclock and do sport in 19 oclock" ->
{{"actions":[{{"type":"create","title":"Brush teeth","time":"07:10","days":""}},{{"type":"create","title":"Sport","time":"19:00","days":""}}],"message":""}}
"add meditation at 6:30 on weekends" ->
{{"actions":[{{"type":"create","title":"Meditation","time":"06:30","days":"weekends"}}],"message":""}}
"move sport to 8pm" ->
{{"actions":[{{"type":"update","title":"Sport","time":"20:00"}}],"message":""}}
"remove the reading routine" ->
{{"actions":[{{"type":"delete","title":"Reading"}}],"message":""}}
"hi" ->
{{"actions":[],"message":"Hi! Tell me a routine, e.g. 'sport at 19:00 every day'."}}
"""

HISTORY_LEN = 6
_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
_client = AsyncClient(host=settings.OLLAMA_HOST)


async def chat(db: AsyncSession, user: User, message: str) -> str:
    now = datetime.now(ZoneInfo(user.timezone))
    routines = await rs.list_routines(db, user.id)
    system = SYSTEM_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M"),
        weekday=now.strftime("%A"),
        routines="\n".join(rs.format_routine(r) for r in routines) or "(none)",
    )
    history = _history[user.id]
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]

    resp = await _client.chat(
        model=settings.OLLAMA_MODEL,
        messages=messages,
        format=AgentOutput.model_json_schema(),
        options={"temperature": 0},
    )
    raw = resp.message.content or "{}"
    try:
        out = AgentOutput.model_validate_json(raw)
    except ValidationError:
        log.warning("Bad LLM output: %s", raw)
        return "Sorry, I didn't get that. Try e.g. 'sport at 19:00 every day'."

    results = await execute(db, user.id, out.actions)
    reply = "\n".join(results) or out.message.strip() or "Tell me a routine, e.g. 'sport at 19:00'."

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": json.dumps(out.model_dump(exclude_none=True))})
    return reply
