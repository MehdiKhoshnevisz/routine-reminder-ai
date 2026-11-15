import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.actions import AgentOutput, execute
from app.core.config import settings
from app.models import User
from app.services import routine_service as rs
from app.services import state_service

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Routine AI, a Persian-speaking assistant. You turn the user's message into a JSON list of actions on their routines.
The user speaks Persian (Farsi). Always write "title" and "message" in Persian. Keep "type", "time" and "days" in the English format below.
Now: {now} ({weekday}).

The user's current routines:
{routines}

Every action has a "type" and a "title". Action types:
- create: title (the activity in Persian, e.g. "مسواک زدن"), time (24h "HH:MM" with English digits), days
- update: title = name of the EXISTING routine to change (copy it exactly as listed), plus only the fields that change (time, days)
- delete / pause / resume: title = name of the existing routine (copy it exactly as listed)
- list: when the user wants to see their routines

Rules:
- "اضافه کن", "بساز", "یادم بنداز", "می‌خوام" = create. One "create" action PER activity:
  "مسواک ساعت ۷ و ورزش ساعت ۱۹" is TWO actions.
- "عوض کن", "ببر ساعت", "تغییر بده" = update. "حذف کن", "پاک کن" = delete. "متوقف کن" = pause. "ادامه بده", "فعال کن" = resume.
- Copy times exactly and convert Persian digits: "۶:۳۰" -> "06:30", "۱۹" -> "19:00", "۷ و ده دقیقه" -> "07:10", "هفت و نیم" -> "07:30".
  Only add 12 hours if the user says عصر, بعدازظهر, شب or pm (e.g. "۸ شب" -> "20:00"). "صبح" means AM.
- days: "" (every day / "هر روز") unless the user names days. Use English day names, comma separated, e.g. "saturday,monday".
  The Iranian week starts on Saturday. "روزهای کاری" / "وسط هفته" -> "saturday,sunday,monday,tuesday,wednesday".
  "آخر هفته" -> "thursday,friday". شنبه=saturday, یکشنبه=sunday, دوشنبه=monday, سه‌شنبه=tuesday, چهارشنبه=wednesday, پنجشنبه=thursday, جمعه=friday.
- If the message has NOTHING to do with routines (greetings, small talk, jokes, general questions, other topics),
  return {{"actions":[],"message":"","off_topic":true}}. Do not answer it.
- If the message IS about routines but is unclear (e.g. missing time), return no actions and put a short question in Persian in "message".

Examples:
"اول ساعت ۷ و ده دقیقه مسواک بزنم و ساعت ۱۹ ورزش کنم" ->
{{"actions":[{{"type":"create","title":"مسواک زدن","time":"07:10","days":""}},{{"type":"create","title":"ورزش","time":"19:00","days":""}}],"message":""}}
"مدیتیشن ساعت ۶:۳۰ آخر هفته‌ها اضافه کن" ->
{{"actions":[{{"type":"create","title":"مدیتیشن","time":"06:30","days":"thursday,friday"}}],"message":""}}
"ورزش رو ببر ساعت ۸ شب" ->
{{"actions":[{{"type":"update","title":"ورزش","time":"20:00"}}],"message":""}}
"روتین کتاب خوندن رو حذف کن" ->
{{"actions":[{{"type":"delete","title":"کتاب خوندن"}}],"message":""}}
"سلام، خوبی؟" ->
{{"actions":[],"message":"","off_topic":true}}
"پایتخت فرانسه کجاست؟" ->
{{"actions":[],"message":"","off_topic":true}}
"یادم بنداز ورزش کنم" ->
{{"actions":[],"message":"چه ساعتی یادت بندازم؟"}}
"""

OFF_TOPIC_REPLY = (
    "من فقط درباره روتین‌هات می‌تونم کمکت کنم 🙂\n"
    "روتینت رو بگو، مثلاً «ورزش ساعت ۱۹ هر روز» یا «مسواک ساعت ۷:۱۰».\n\n"
    "دستورها:\n"
    "/routines - لیست روتین‌ها\n"
    "/update - ویرایش روتین\n"
    "/delete - حذف روتین\n"
    "/timezone - تنظیم منطقه زمانی\n"
    "/help - راهنما"
)

HISTORY_LEN = 6  # messages kept (stored in the DB, see AgentHistory)
_client: AsyncOpenAI | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> AsyncOpenAI:
    """OpenRouter client (OpenAI-compatible). Created lazily so the app can start before the key is set,
    and re-created if the event loop changed (serverless runtimes may use a new loop per request)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client_loop = loop
        _client = AsyncOpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=settings.OPENROUTER_TIMEOUT,
            max_retries=settings.OPENROUTER_MAX_RETRIES,
            default_headers={"X-Title": settings.APP_NAME},  # shows the app name in OpenRouter's dashboard
        )
    return _client


async def chat(db: AsyncSession, user: User, message: str) -> str:
    now = datetime.now(ZoneInfo(user.timezone))
    routines = await rs.list_routines(db, user.id)
    system = SYSTEM_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M"),
        weekday=now.strftime("%A"),
        routines="\n".join(rs.format_routine(r) for r in routines) or "(هیچ)",
    )
    history = await state_service.get_agent_history(db, user.id)
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]

    resp = await _get_client().chat.completions.create(
        model=settings.OPENROUTER_MODEL,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        out = AgentOutput.model_validate_json(raw)
    except ValidationError:
        log.warning("Bad LLM output: %s", raw)
        return "متأسفم، متوجه نشدم. مثلاً بنویس «ورزش ساعت ۱۹ هر روز»."

    if out.off_topic and not out.actions:
        return OFF_TOPIC_REPLY  # not stored in history so small talk doesn't steer later replies

    results = await execute(db, user.id, out.actions)
    reply = "\n\n".join(results) or out.message.strip() or OFF_TOPIC_REPLY

    history += [
        {"role": "user", "content": message},
        {"role": "assistant", "content": json.dumps(out.model_dump(exclude_none=True), ensure_ascii=False)},
    ]
    await state_service.save_agent_history(db, user.id, history[-HISTORY_LEN:])
    return reply
