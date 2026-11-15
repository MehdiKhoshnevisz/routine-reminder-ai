import asyncio
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.actions import AgentOutput, execute
from app.agent.parsing import parse_time
from app.core.config import settings
from app.core.text import strip_emoji
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
  If several routines have that name, also set "routine_id" (the number after #) of the one the user means.
- delete / pause / resume: title = name of the existing routine (copy it exactly as listed)
- Several routines with the same name: if the user said WHICH one (time, number, صبح/عصر, e.g. answering "which one?"),
  ALSO set "routine_id" (the number after #) of that one. If they didn't say which, return ONE action without routine_id
  (the app will ask them). Only act on all of them if they say "همه" / "هر دو".
- list: when the user wants to see their routines

Output: {{"actions":[...],"message":""}}
- "message": only when there are NO actions: a short, friendly Persian question or reply.

Be conversational. When something needed is missing or unclear, DON'T guess and DON'T give up: return no actions and ask
ONE short friendly question in "message" about exactly the missing part:
- activity missing ("ساعت ۷ یادم بنداز") -> "ساعت ۷ چی رو یادت بندازم؟"
- time missing ("یادم بنداز ورزش کنم") -> "ورزش رو چه ساعتی یادت بندازم؟"
- which routine is unclear (update/delete and several routines could match, or none matches) -> ask which one, naming the candidates
- the message is about routines but vague, garbled or contradictory ("یه چیزی ساعت یه چیزی") -> ask them to say it another way, with a short example
- they name an EXISTING routine with a different time but no change/add word ("ورزش ساعت ۱۹" while "ورزش" is at 07:00)
  -> ask: "ساعت ورزش رو از ۰۷:۰۰ به ۱۹:۰۰ تغییر بدم، یا یه ورزش جدید ساعت ۱۹:۰۰ هم اضافه کنم؟"
Do NOT ask about things with a sensible default: no days = every day; hour without صبح/عصر = pick the likely half (see below).
If your previous message asked a question, the user's message is the ANSWER: combine it with their earlier request and act
(e.g. you asked "ورزش رو چه ساعتی یادت بندازم؟" and they say "۷ عصر" -> create ورزش at 19:00). An answer is never off_topic.
Greetings or thanks ("سلام", "مرسی") -> no actions, a warm one-line "message" that invites them to tell a routine.
Style of "message": casual and friendly Persian. NEVER use emoji. If you want a face, use text emoticons only: :) :)) :( =)

Rules:
- The user talks casually. Any wording that asks to remember / set / add something at a time is create:
  "اضافه کن", "بساز", "ست کن", "تنظیم کن", "بذار", "یادم بنداز", "یادآوری کن", "می‌خوام", "باید ... کنم", or just "<activity> ساعت <time>".
- One "create" action PER activity: "مسواک ساعت ۷ و ورزش ساعت ۱۹" is TWO actions.
  One activity with one time is exactly ONE action. Never split one time into two routines.
- "عوض کن", "ببر ساعت", "کن ساعت", "تغییر بده" = update. "حذف کن", "پاک کن" = delete. "متوقف کن" = pause. "ادامه بده", "فعال کن" = resume.

Times ("time" is 24h "HH:MM" with English digits):
- Persian digits: "۶:۳۰" -> 06:30, "۱۹" -> 19:00, "15:00" -> 15:00.
- Number words: یک=1 دو=2 سه=3 چهار=4 پنج=5 شش=6 هفت=7 هشت=8 نه=9 ده=10 یازده=11 دوازده=12,
  سیزده=13 ... نوزده=19 بیست=20 سی=30 چهل=40 پنجاه=50; "و" joins them: "پنجاه و نه" = 59, "بیست و پنج" = 25.
- "<h> و <m> دقیقه" = h:m ("سه و پنجاه و نه دقیقه" -> 3:59, "۷ و ده دقیقه" -> 7:10), "<h> و نیم" = h:30, "<h> و ربع" = h:15,
  "<m> دقیقه به <h>" = (h-1):(60-m) ("ده دقیقه به هشت" -> 7:50), "یه ربع به <h>" = (h-1):45.
- AM/PM: صبح / بامداد = AM. ظهر: "۱۲ ظهر" -> 12:00, "۱ ظهر" -> 13:00. بعدازظهر / عصر / شب = PM ("۸ شب" -> 20:00, "۱۲ شب" -> 00:00).
  Hours 0 and 13-23 are already 24h: use them as they are.
- Hour 1-11 with NO صبح/ظهر/عصر/شب is ambiguous. Pick the likely one for the activity
  (waking up, breakfast, morning prayer -> AM; dinner, sleep -> PM; otherwise 1-6 -> PM, 7-11 -> AM).
- If the previous turn created or updated a routine and the user now only answers "صبح", "عصر", "شب", "نه صبح", "منظورم شب بود", ...,
  UPDATE that routine's time to the same hour in the other half of the day.
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
{{"actions":[],"message":"سلام! خوبم مرسی :) بگو چه روتینی رو برات یادآوری کنم؟"}}
"ساعت ۷ یادم بنداز" ->
{{"actions":[],"message":"ساعت ۷ چی رو یادت بندازم؟"}}
(routines: "#4 07:00 - ورزش", "#9 19:00 - ورزش"; you asked which one to delete) "اونی که عصره" ->
{{"actions":[{{"type":"delete","title":"ورزش","routine_id":9}}],"message":""}}
(you asked "ورزش رو چه ساعتی یادت بندازم؟") "۷ عصر" ->
{{"actions":[{{"type":"create","title":"ورزش","time":"19:00","days":""}}],"message":""}}
"پایتخت فرانسه کجاست؟" ->
{{"actions":[],"message":"","off_topic":true}}
"یادم بنداز ورزش کنم" ->
{{"actions":[],"message":"ورزش رو چه ساعتی یادت بندازم؟"}}
"برام سه و پنجاه و نه دقیقه ورزش کردن رو ست کن" ->
{{"actions":[{{"type":"create","title":"ورزش","time":"15:59","days":""}}],"message":""}}
"ساعت ۷ بیدار شدن" ->
{{"actions":[{{"type":"create","title":"بیدار شدن","time":"07:00","days":""}}],"message":""}}
(after the routine "ورزش" was created at 15:00) "نه صبح" ->
{{"actions":[{{"type":"update","title":"ورزش","time":"03:00"}}],"message":""}}
"""

OFF_TOPIC_REPLY = (
    "من فقط درباره روتین‌هات می‌تونم کمکت کنم :)\n"
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


_HALF_WORDS = ("صبح", "بامداد", "ظهر", "عصر", "شب", "am", "pm")
# Spelled-out hours 13-23 ("ساعت هفده", "بیست و یک") are already unambiguous.
_24H_WORDS = ("سیزده", "چهارده", "پانزده", "پونزده", "شانزده", "شونزده", "هفده", "هیفده", "هجده", "هیجده", "نوزده", "بیست")
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_HOUR_WORDS = ("یک", "دو", "سه", "چهار", "پنج", "شش", "شیش", "هفت", "هشت", "نه", "ده", "یازده", "دوازده")


def _ampm_note(message: str, actions) -> str:
    """When the user gave an hour like "3" without صبح/عصر/..., say which half of the day was picked
    (decided in code from the user's own words, so the note only shows up when it was really a guess)."""
    text = message.lower().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if any(w in text for w in _HALF_WORDS + _24H_WORDS):
        return ""
    if not re.search(r"\d|ساعت|" + "|".join(_HOUR_WORDS), text):
        return ""  # no time in this message ("عوضش کن"): the time came from an earlier turn
    if any(int(h) >= 13 or h.startswith("0") for h in re.findall(r"\d{1,2}(?=\s*[:٫.]|\b|$)", text) if int(h) <= 23):
        return ""  # "15:00", "19", "07:30"
    for a in actions:
        if a.type in ("create", "update") and a.time:
            try:
                t = parse_time(a.time)
            except ValueError:
                continue
            if t.hour in (0, 12):
                continue
            h12 = f"{t.hour % 12}" if t.minute == 0 else f"{t.hour % 12}:{t.minute:02d}"
            picked, other = ("صبح", "عصر") if t.hour < 12 else ("بعدازظهر", "صبح")
            return f"ساعت {h12.translate(_FA)} رو {picked} در نظر گرفتم. اگه {other} منظورت بود، بگو «{other}» =)"
    return ""


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
        return "ببخشید، درست متوجه نشدم :( می‌تونی یه جور دیگه بگی؟ مثلاً «ورزش ساعت ۱۹ هر روز»."

    out.message = strip_emoji(out.message)
    for a in out.actions:
        if a.title:
            a.title = strip_emoji(a.title) or a.title
    if out.off_topic and not out.actions:
        return OFF_TOPIC_REPLY  # not stored in history so small talk doesn't steer later replies

    results = await execute(db, user.id, out.actions, message)
    if results and (note := _ampm_note(message, out.actions)):
        results.append(note)
    reply = "\n\n".join(results) or out.message.strip() or OFF_TOPIC_REPLY

    # Store what the user actually saw (incl. questions the code asked, e.g. "which routine?"), so the
    # next message can be read as the answer to it. Capped so long lists don't bloat the history.
    history += [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply[:600]},
    ]
    await state_service.save_agent_history(db, user.id, history[-HISTORY_LEN:])
    return reply
