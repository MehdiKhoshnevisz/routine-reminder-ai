import functools
import logging
import re

from openai import APITimeoutError, AuthenticationError, RateLimitError
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode, ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agent import agent
from app.agent.parsing import parse_time
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import routine_service as rs
from app.services import state_service, user_service, stt_service
from app.services.routine_service import DAY_NAMES, DAY_ORDER

log = logging.getLogger(__name__)

# ---------- keyboards ----------

BTN_LIST = "کار‌های من"
BTN_NEW = "کار جدید"
BTN_SUPPORT = "پشتیبانی"
BTN_DONATE = "کمک"
BTN_TZ = "منطقه زمانی"
BTN_HELP = "راهنما"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_LIST), KeyboardButton(BTN_NEW)],
        [KeyboardButton(BTN_TZ), KeyboardButton(BTN_HELP)],
        [KeyboardButton(BTN_SUPPORT), KeyboardButton(BTN_DONATE)],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="مثلاً ورزش ساعت ۱۹ روزهای کاری",
)

# Groups get no bottom keyboard (it would pop up for every member), so their menu is inline.
GROUP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("کار‌های گروه", callback_data="m:list")],
    [InlineKeyboardButton("منطقه زمانی", callback_data="m:tz"), InlineKeyboardButton("پاک کردن همه", callback_data="m:reset")],
    [InlineKeyboardButton("پشتیبانی", callback_data="m:support"), InlineKeyboardButton("کمک", callback_data="m:donate")],
])

CANCEL_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("لغو", callback_data="cx")]])

# Timezone picker (callback data max 64 bytes)
TZ_CHOICES = [
    ("ایران - تهران", "Asia/Tehran"), ("امارات متحده - دبی", "Asia/Dubai"),
    ("آلمان - برلین", "Europe/Berlin"), ("ترکیه - استانبول", "Europe/Istanbul"),
    ("کانادا - تورنتو", "America/Toronto"), ("انگلیس - لندن", "Europe/London"),
    ("امریکا - ال اِی", "America/Los_Angeles"), ("آمریکا - نیویورک‌سیتی", "America/New_York"),
]
TZ_NAMES = {tz: name for name, tz in TZ_CHOICES}
TZ_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton(n, callback_data=f"tz:{tz}") for n, tz in TZ_CHOICES[i:i + 2]] for i in range(0, len(TZ_CHOICES), 2)]
    + [[InlineKeyboardButton("لغو", callback_data="cx")]]
)


SUPPORT_TEXT = (
    "ریما رو @metthewk ساخته =)\n\n"
    "اگه نقد، نظر یا پیشنهادی داری، یا یه جا باگ دیدی، حتماً بهش پیام بده.\n"
    "هر پیامی کمک می‌کنه ریما بهتر بشه :)"
)

# HTML; the card number is a code block (<pre>), which Telegram shows with a Copy button.
DONATE_TEXT = (
    "اگه ریما به دردت خورده، می‌تونی دونیت کنی :))\n"
    "هزینه‌اش صرف سرورهای بی‌سرپرست و بی‌خانواده می‌شه =)\n\n"
    "شماره کارت:\n"
    "<code>6216861906031157</code>"
)

HELP = (
    "سلام! من ریما هستم =)\n"
    "هر روز، کارایی که می‌خوای انجام بدی رو یادت می‌ندازم!\n\n"
    "کافیه مثل یه دوست بهم بگی چی رو کِی بهت یادآوری کنم. مثلاً:\n\n"
    "• هر روز ساعت پنج یادم بنداز کتاب بخونم.\n"
    "• هر روز ساعت سه و نیم یادم بنداز ورزش کنم.\n"
    "• روزهای کاری ساعت هفت شب یادم بنداز زبان بخونم.\n"
    "• ساعت ورزش رو از هفت به هشت تغییر بده.\n\n"
    # "🎙️ یا حتی پیام صوتی بفرست!\n\n"
    "همین!\n\n"
    "برای دیدن، تغییر یا حذف کار‌هات هم از دکمه‌های پایین استفاده کن :)"
)


# Telegram allows max 512 chars for the description and 120 for the short description.
DESCRIPTION = (
    "سلام! من ریما هستم =)\n"
    "هر روز، کارایی که می‌خوای انجام بدی رو یادت می‌ندازم!\n"
    "کافیه مثل یه دوست بهم بگی چی رو کِی بهت یادآوری کنم.\n"
    "همین. شروع کن :)"
)
SHORT_DESCRIPTION = "ریما هستم =) کار‌هات رو بگو، سر وقت بهت یادآوری می‌کنم"


GROUP_HELP = (
    "سلام! من ریما هستم =)\n"
    "کار‌های این گروه رو سر وقت توی همین گروه یادآوری می‌کنم.\n\n"
    "فقط ادمین‌های گروه می‌تونن کار بسازن، تغییر بدن یا حذف کنن.\n\n"
    "من رو منشن کن یا روی پیامم ریپلای کن، مثلاً:\n"
    "• @{username} جلسه ساعت ۱۰ روزهای کاری\n"
    "• @{username} ورزش گروهی ساعت ۱۹ آخر هفته‌ها\n"
    "• @{username} جلسه رو حذف کن\n"
    "• @{username} این کار رو نمیخوام\n\n"
    "برای دیدن لیست کارها هم همین‌طور بهم بگو:\n"
    "• @{username} لیست کارها"
)


def _help_text(user, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return GROUP_HELP.format(username=ctx.bot.username) if _is_group(update) else HELP

# Callback data (max 64 bytes):
#   ls:<mode>          list routines as buttons (mode: edit|del)
#   r:<id>             open routine edit menu
#   e:<id>:<field>     ask for a new value (time|title)
#   dp:<id>            open days picker
#   dt:<id>:<d>        toggle day d in picker
#   de:<id>            picker: every day
#   ds:<id>            picker: save
#   p:<id>             pause / resume
#   d:<id>             ask delete confirmation
#   dy:<id>            delete confirmed
#   x                  close


LIST_TITLE = "کار‌هات :)\nروی هر کدوم بزن تا ببینی، ویرایشش کنی یا حذفش کنی."


def _routines_keyboard(routines, mode: str = "edit") -> InlineKeyboardMarkup:
    """One button per routine (opens it, or asks to delete it in "del" mode) + delete all / close."""
    prefix = "r" if mode == "edit" else "d"
    rows = [
        [InlineKeyboardButton(
            f"{r.title} - ساعت {r.time.strftime('%H:%M')}{' (متوقف)' if not r.is_active else ''}",
            callback_data=f"{prefix}:{r.id}",
        )]
        for r in routines
    ]
    rows.append([InlineKeyboardButton("پاک کردن همه", callback_data="m:reset"), InlineKeyboardButton("بستن", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _routine_menu(r) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ساعت", callback_data=f"e:{r.id}:time"),
            InlineKeyboardButton("روزها", callback_data=f"dp:{r.id}"),
            InlineKeyboardButton("عنوان", callback_data=f"e:{r.id}:title"),
        ],
        [
            InlineKeyboardButton("ادامه" if not r.is_active else "توقف", callback_data=f"p:{r.id}"),
            InlineKeyboardButton("حذف", callback_data=f"d:{r.id}"),
        ],
        [InlineKeyboardButton("بازگشت", callback_data="ls:edit")],
    ])


def _days_keyboard(routine_id: int, selected: set[int]) -> InlineKeyboardMarkup:
    day_btns = [
        InlineKeyboardButton(f"{'●' if d in selected else '○'} {DAY_NAMES[d]}", callback_data=f"dt:{routine_id}:{d}")
        for d in DAY_ORDER
    ]
    return InlineKeyboardMarkup([
        day_btns[:4],
        day_btns[4:],
        [
            InlineKeyboardButton("هر روز", callback_data=f"de:{routine_id}"),
            InlineKeyboardButton("ذخیره", callback_data=f"ds:{routine_id}"),
        ],
        [InlineKeyboardButton("بازگشت", callback_data=f"r:{routine_id}")],
    ])


def _routine_text(r) -> str:
    text = rs.format_routine(r)
    if r.description:
        text += f"\n{r.description}"
    return text + "\n\nباهاش چیکار کنم؟ می‌خوای ویرایشش کنی یا حذفش کنم؟"


# ---------- helpers ----------

def _is_group(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def _menu(update: Update):
    """The reply-keyboard menu only in private chats (in a group it would pop up for every member)."""
    return None if _is_group(update) else MAIN_MENU


def _help_markup(update: Update):
    return None if _is_group(update) else MAIN_MENU


def _reply_hint(update: Update) -> str:
    # With privacy mode on, the bot only sees group messages that reply to it or mention it.
    return "\n(روی همین پیام ریپلای کن)" if _is_group(update) else ""


async def _user(db, update: Update):
    """Owner of the routines in this chat. In a group that's the group itself (its own User row with
    telegram_id = group chat id), so its routines, timezone and reminders belong to the group."""
    if _is_group(update):
        chat = update.effective_chat
        return await user_service.get_or_create_telegram_user(db, chat.id, chat.title or "گروه")
    tg = update.effective_user
    return await user_service.get_or_create_telegram_user(db, tg.id, tg.full_name or tg.username or "دوست")


async def _is_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_group(update):
        return True
    chat = update.effective_chat
    msg = update.message
    if msg and msg.sender_chat and msg.sender_chat.id == chat.id:
        return True  # anonymous group admin
    if not update.effective_user:
        return False
    try:
        member = await ctx.bot.get_chat_member(chat.id, update.effective_user.id)
    except Exception:
        log.exception("get_chat_member failed")
        return False
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


NOT_ADMIN = "توی گروه فقط ادمین‌ها می‌تونن کار‌ها رو بسازن یا تغییر بدن."


def admin_only(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await _is_admin(update, ctx):
            return await handler(update, ctx)
        if update.callback_query:
            await update.callback_query.answer(NOT_ADMIN, show_alert=True)
        else:
            await update.effective_message.reply_text(NOT_ADMIN)

    return wrapper


def _addressed_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Text meant for the bot. In groups only messages that mention the bot or reply to it count
    (an admin bot receives every group message); the mention is stripped."""
    msg = update.message
    if not _is_group(update):
        return msg.text
    mention = f"@{ctx.bot.username}"
    if mention.lower() in msg.text.lower():
        return re.sub(re.escape(mention), "", msg.text, flags=re.IGNORECASE).strip() or None
    reply = msg.reply_to_message
    if reply and reply.from_user and reply.from_user.id == ctx.bot.id:
        return msg.text
    return None


_ROUTINE_REF = re.compile(r"\s*(?:#|شارپ|شماره)\s*([0-9۰-۹]+)\s*")


async def _show_routine(update: Update, routine_id: int) -> None:
    """Routine card with edit / delete buttons ("#4" typed, or its number tapped in the list)."""
    async with SessionLocal() as db:
        user = await _user(db, update)
        r = await rs.get_routine(db, user.id, routine_id)
    if not r:
        await update.effective_message.reply_text(f"کار {rs.LRM}#{routine_id}{rs.LRM} رو پیدا نکردم :/ از «کار‌های من» لیستشون رو ببین.")
        return
    await update.effective_message.reply_text(_routine_text(r), reply_markup=_routine_menu(r))


# ---------- commands ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    # deep link from a routine number in the list: t.me/<bot>?start=r12 -> open routine 12
    if ctx.args and (m := re.fullmatch(r"r(\d+)", ctx.args[0])):
        await _show_routine(update, int(m.group(1)))
        return
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(_help_text(user, update, ctx), reply_markup=_help_markup(update))


async def routines_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as db:
        user = await _user(db, update)
        items = await rs.list_routines(db, user.id)
    if not items:
        await update.effective_message.reply_text(
            "هنوز هیچ کاری نداری. یکی بهم بگو، مثلاً «ورزش ساعت ۱۹» :)", reply_markup=_menu(update)
        )
        return
    await update.effective_message.reply_text(LIST_TITLE, reply_markup=_routines_keyboard(items))


@admin_only
async def timezone_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        ctx.user_data["awaiting"] = {"field": "timezone", "chat": update.effective_chat.id}
        async with SessionLocal() as db:
            user = await _user(db, update)
        current = TZ_NAMES.get(user.timezone, user.timezone)
        await update.effective_message.reply_text(
            f"منطقه زمانی فعلی: {current}\n\n"
            "یادآوری‌ها رو با ساعت منطقه زمانی تو می‌فرستم، پس اگه درست نباشه زودتر یا دیرتر میان.\n\n"
            "شهرت رو انتخاب کن. اگه توی لیست نیست، اسم منطقه‌ات رو به شکل Area/City بفرست (مثلاً Europe/Paris)."
            + _reply_hint(update),
            reply_markup=TZ_KEYBOARD,
        )
        return
    await _set_timezone(update, ctx.args[0])


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("لغو شد.", reply_markup=_menu(update))


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(_help_text(user, update, ctx), reply_markup=_help_markup(update))


@admin_only
async def new_routine_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(
        "کارت رو با زبان خودت بگو، مثلاً:\n"
        "• مسواک ساعت ۷:۱۰ و ورزش ساعت ۱۹\n"
        "• کتاب خوندن ساعت ۲۲ آخر هفته‌ها"
    )


_TZ_ALIASES = {"تهران": "Asia/Tehran", "ایران": "Asia/Tehran", "tehran": "Asia/Tehran", "iran": "Asia/Tehran"}


async def _set_timezone(update: Update, tz: str):
    tz = _TZ_ALIASES.get(tz.strip().lower(), tz.strip())
    async with SessionLocal() as db:
        user = await _user(db, update)
        try:
            await user_service.update_user(db, user, timezone=tz)
        except ValueError as e:
            await update.effective_message.reply_text(f"{e}\nمثلاً Asia/Tehran یا Europe/London رو امتحان کن.")
            return False
    await update.effective_message.reply_text(f"اوکی، منطقه زمانی روی {tz} تنظیم شد :)", reply_markup=_menu(update))
    return True


def _confirm_delete(routine_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("آره، حذفش کن", callback_data=f"dy:{routine_id}"),
        InlineKeyboardButton("نه", callback_data="x"),
    ]])


# ---------- inline button callbacks ----------

_PUBLIC_CALLBACKS = {"m:list", "m:support", "m:donate"}  # anyone in a group may view these


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.data not in _PUBLIC_CALLBACKS and not await _is_admin(update, ctx):
        await q.answer(NOT_ADMIN, show_alert=True)
        return
    await q.answer()
    action, *rest = q.data.split(":")

    if action == "x":
        ctx.user_data.pop("awaiting", None)
        await q.edit_message_reply_markup(None)
        return

    if action == "cx":  # cancel a prompt ("send the new time ...")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("days", None)
        await q.edit_message_text("لغو شد :)")
        return

    if action == "m":  # inline menu (groups) / "delete all" under the list
        handler = {"list": routines_cmd, "tz": timezone_cmd, "reset": reset_cmd,
                   "support": support_cmd, "donate": donate_cmd}.get(rest[0])
        if handler:
            await handler(update, ctx)
        return

    if action == "tz":
        tz = ":".join(rest)
        async with SessionLocal() as db:
            user = await _user(db, update)
            await user_service.update_user(db, user, timezone=tz)
        ctx.user_data.pop("awaiting", None)
        await q.edit_message_text(f"اوکی، منطقه زمانی روی {TZ_NAMES.get(tz, tz)} تنظیم شد :)")
        return

    async with SessionLocal() as db:
        user = await _user(db, update)

        if action == "rs":  # /reset confirmed
            n = await rs.delete_all_routines(db, user.id)
            await state_service.delete_agent_history(db, user.id)
            ctx.user_data.clear()
            await q.edit_message_text(f"همه چی پاک شد ({n} کار). از اول شروع کنیم :)")
            return

        if action == "ls":
            routines = await rs.list_routines(db, user.id)
            if not routines:
                await q.edit_message_text("هنوز هیچ کاری نداری.")
                return
            mode = rest[0]
            title = LIST_TITLE if mode == "edit" else "کدوم کار رو می‌خوای حذف کنی؟"
            await q.edit_message_text(title, reply_markup=_routines_keyboard(routines, mode))
            return

        r = await rs.get_routine(db, user.id, int(rest[0]))
        if not r:
            await q.edit_message_text("این کار دیگه وجود نداره.")
            return

        if action == "r":
            ctx.user_data.pop("awaiting", None)
            await q.edit_message_text(_routine_text(r), reply_markup=_routine_menu(r))

        elif action == "e":
            field = rest[1]
            ctx.user_data["awaiting"] = {"field": field, "routine_id": r.id, "chat": update.effective_chat.id}
            prompt = (
                f"ساعت جدید «{r.title}» رو بفرست (مثلاً 07:30 یا 19:00)."
                if field == "time"
                else f"عنوان جدید «{r.title}» رو بفرست."
            )
            await q.edit_message_text(prompt + _reply_hint(update), reply_markup=CANCEL_BUTTON)

        elif action == "dp":
            # user_data is stored as JSON in webhook mode, so keep lists (not sets) in it
            ctx.user_data["days"] = sorted(r.days or [])
            await q.edit_message_text(
                f"روزهای «{r.title}» رو انتخاب کن (هیچ‌کدوم = هر روز):",
                reply_markup=_days_keyboard(r.id, set(ctx.user_data["days"])),
            )

        elif action == "dt":
            days = set(ctx.user_data.get("days", r.days or [])) ^ {int(rest[1])}
            ctx.user_data["days"] = sorted(days)
            await q.edit_message_reply_markup(_days_keyboard(r.id, days))

        elif action in ("de", "ds"):
            days = set() if action == "de" else set(ctx.user_data.get("days", r.days or []))
            if days == set(range(7)):
                days = set()
            r = await rs.update_routine(db, r, days=sorted(days))
            ctx.user_data.pop("days", None)
            await q.edit_message_text(rs.format_routine_card(r, "آپدیت شد :)"), reply_markup=_routine_menu(r))

        elif action == "p":
            r = await rs.update_routine(db, r, is_active=not r.is_active)
            state = "دوباره فعال شد :)" if r.is_active else "متوقف شد:"
            await q.edit_message_text(f"{state} {rs.format_routine(r)}", reply_markup=_routine_menu(r))

        elif action == "d":
            await q.edit_message_text(f"{rs.format_routine(r)} حذف بشه؟", reply_markup=_confirm_delete(r.id))

        elif action == "dy":
            title = r.title
            await rs.delete_routine(db, r)
            await q.edit_message_text(f"«{title}» حذف شد")


# ---------- free text ----------

async def _handle_awaiting(update: Update, ctx: ContextTypes.DEFAULT_TYPE, awaiting: dict, text: str) -> None:
    text = text.strip()
    field = awaiting["field"]

    if field == "timezone":
        if await _set_timezone(update, text):
            ctx.user_data.pop("awaiting", None)
        return

    async with SessionLocal() as db:
        user = await _user(db, update)
        r = await rs.get_routine(db, user.id, awaiting["routine_id"])
        if not r:
            ctx.user_data.pop("awaiting", None)
            await update.message.reply_text("این کار دیگه وجود نداره.")
            return
        try:
            if field == "time":
                r = await rs.update_routine(db, r, time=parse_time(text))
            else:
                r = await rs.update_routine(db, r, title=text[:200])
        except ValueError as e:
            await update.message.reply_text(f"{e} :( دوباره امتحان کن.", reply_markup=CANCEL_BUTTON)
            return
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(rs.format_routine_card(r, "آپدیت شد :)"), reply_markup=_routine_menu(r))


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = _addressed_text(update, ctx)
    if not text:
        return  # group chatter not meant for the bot
    if not await _is_admin(update, ctx):
        await update.message.reply_text(NOT_ADMIN)
        return

    if m := _ROUTINE_REF.fullmatch(text):  # "#4", "#۴", "شارپ ۴"
        ctx.user_data.pop("awaiting", None)
        await _show_routine(update, int(m.group(1).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))))
        return

    awaiting = ctx.user_data.get("awaiting")
    if awaiting and awaiting.get("chat", update.effective_chat.id) == update.effective_chat.id:
        await _handle_awaiting(update, ctx, awaiting, text)
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    async with SessionLocal() as db:
        user = await _user(db, update)
        try:
            reply = await agent.chat(db, user, text)
        except RateLimitError:
            log.warning("OpenRouter rate limited")
            reply = "الان درخواست‌ها خیلی زیاده. لطفاً یک دقیقه دیگه دوباره امتحان کن."
        except APITimeoutError:
            log.warning("OpenRouter request timed out")
            reply = "فکر کردن بهش خیلی طول کشید. لطفاً دوباره امتحان کن."
        except AuthenticationError:
            log.error("OpenRouter auth failed, check OPENROUTER_API_KEY")
            reply = "متأسفم، الان درست تنظیم نشدم. لطفاً بعداً دوباره امتحان کن."
        except Exception:
            log.exception("agent failed")
            reply = "متأسفم، مغزم (OpenRouter) الان جواب نمی‌ده. لطفاً چند لحظه دیگه دوباره امتحان کن."
    await update.message.reply_text(reply, reply_markup=_menu(update))


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Voice message: transcribe it, then treat the text like a normal message."""
    msg = update.message
    voice = msg.voice
    if not voice:
        return

    # Group addressing rules apply to voice too
    if _is_group(update):
        text_check = msg.caption or ""
        mention = f"@{ctx.bot.username}"
        reply = msg.reply_to_message
        is_addressed = (
            mention.lower() in text_check.lower()
            or (reply and reply.from_user and reply.from_user.id == ctx.bot.id)
        )
        if not is_addressed:
            return  # voice in group not meant for the bot

    if not await _is_admin(update, ctx):
        await msg.reply_text(NOT_ADMIN)
        return

    # Duration guard
    if voice.duration and voice.duration > stt_service.MAX_VOICE_SECONDS:
        await msg.reply_text("پیام صوتی خیلی طولانیه! لطفاً زیر ۵ دقیقه بفرست :)")
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    # Download the voice file
    try:
        file = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
        audio_bytes = bytes(audio_bytes)
    except Exception:
        log.exception("Failed to download voice message")
        await msg.reply_text("متأسفم، نتونستم پیام صوتی رو دانلود کنم. لطفاً دوباره امتحان کن.")
        return

    # Transcribe
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    text, err = await stt_service.transcribe_voice(audio_bytes, filename=voice.file_unique_id + ".ogg")

    if not text:
        if err == "credits":
            await msg.reply_text(
                "قابلیت پیام صوتی فعلاً غیرفعاله چون OpenRouter برای پردازش صدا حداقل $0.50 اعتبار لازم داره.\n"
                "لطفاً اعتبار اضافه کن یا پیام متنی بفرست :)"
            )
        else:
            await msg.reply_text("متأسفم، نتونستم صدات رو بشناسم :( لطفاً دوباره امتحان کن یا بنویس.")
        return

    # Confirm what we heard
    await msg.reply_text(f"شنیدم: «{text}»", reply_markup=_menu(update))

    # Process as normal text message
    async with SessionLocal() as db:
        user = await _user(db, update)
        try:
            reply = await agent.chat(db, user, text)
        except RateLimitError:
            log.warning("OpenRouter rate limited")
            reply = "الان درخواست‌ها خیلی زیاده. لطفاً یک دقیقه دیگه دوباره امتحان کن."
        except APITimeoutError:
            log.warning("OpenRouter request timed out")
            reply = "فکر کردن بهش خیلی طول کشید. لطفاً دوباره امتحان کن."
        except AuthenticationError:
            log.error("OpenRouter auth failed, check OPENROUTER_API_KEY")
            reply = "متأسفم، الان درست تنظیم نشدم. لطفاً بعداً دوباره امتحان کن."
        except Exception:
            log.exception("agent failed")
            reply = "متأسفم، مغزم (OpenRouter) الان جواب نمی‌ده. لطفاً چند لحظه دیگه دوباره امتحان کن."
    await msg.reply_text(reply, reply_markup=_menu(update))


async def unknown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Any other /command: the bot has no command menu, so just show the help with the keyboard."""
    if _is_group(update) and f"@{ctx.bot.username}".lower() not in update.message.text.lower():
        return  # a command meant for another bot in the group
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(_help_text(user, update, ctx), reply_markup=_help_markup(update))


# ---------- support / donate / reset ----------

async def support_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(SUPPORT_TEXT, reply_markup=_menu(update))


async def donate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(DONATE_TEXT, parse_mode=ParseMode.HTML, reply_markup=_menu(update))


@admin_only
async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    who = "همه‌ی کار‌های این گروه" if _is_group(update) else "همه‌ی کار‌هات"
    await update.effective_message.reply_text(
        f"مطمئنی؟ {who} و تاریخچه‌ی گفتگو پاک می‌شه و دیگه برنمی‌گرده :(",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("آره، همه رو پاک کن", callback_data="rs"),
            InlineKeyboardButton("نه", callback_data="x"),
        ]]),
    )


# ---------- group events ----------

async def on_new_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not any(m.id == ctx.bot.id for m in update.message.new_chat_members):
        return
    async with SessionLocal() as db:
        group = await _user(db, update)
    # Send welcome message WITHOUT inline keyboard in groups
    await update.message.reply_text(_help_text(group, update, ctx))


async def on_migrate(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """A group became a supergroup and got a new chat id: move its routines along."""
    msg = update.message
    if msg.migrate_to_chat_id:
        async with SessionLocal() as db:
            await user_service.migrate_telegram_id(db, msg.chat.id, msg.migrate_to_chat_id)


# ---------- setup ----------

# Labels of the previous (emoji) menu: users keep that keyboard until they get a new MAIN_MENU.
_OLD_LABELS = {
    BTN_LIST: "📋 کار‌های من", BTN_NEW: "➕ کار جدید", BTN_TZ: "🌍 منطقه زمانی", BTN_HELP: "❓ راهنما",
}
_REMOVED_LABELS = ("ویرایش کار", "✏️ ویرایش کار", "حذف کار", "🗑 حذف کار")


def _button(*texts: str):
    labels = [t for text in texts for t in (text, _OLD_LABELS.get(text)) if t]
    return filters.TEXT & filters.Regex("^(" + "|".join(re.escape(t) for t in labels) + ")$")


def build_application() -> Application | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set - Telegram bot disabled")
        return None
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, on_migrate))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("routines", routines_cmd))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("donate", donate_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # main menu (reply keyboard) buttons
    app.add_handler(MessageHandler(_button(BTN_LIST), routines_cmd))
    app.add_handler(MessageHandler(_button(BTN_NEW), new_routine_btn))
    app.add_handler(MessageHandler(_button(BTN_SUPPORT), support_cmd))
    app.add_handler(MessageHandler(_button(BTN_DONATE), donate_cmd))
    # removed edit/delete menu buttons (still on some users' keyboards) -> the routines list, where both are possible
    app.add_handler(MessageHandler(_button(*_REMOVED_LABELS), routines_cmd))
    app.add_handler(MessageHandler(_button(BTN_TZ), timezone_cmd))
    app.add_handler(MessageHandler(_button(BTN_HELP), help_cmd))

    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


async def setup_bot_commands(app: Application) -> None:
    """No '/' command menu (the bot is keyboard-driven; typed commands still work),
    plus the texts shown in an empty chat / bot profile."""
    try:
        await app.bot.delete_my_commands()
    except Exception:
        log.exception("Could not delete bot commands")
    try:
        # Shown in an empty chat before the user presses Start (max 512 chars).
        await app.bot.set_my_description(DESCRIPTION)
        # Shown on the bot's profile page and when the bot is shared (max 120 chars).
        await app.bot.set_my_short_description(SHORT_DESCRIPTION)
    except Exception:
        log.exception("Could not set bot description")


async def process_webhook_update(data: dict) -> None:
    """Handles one update pushed by Telegram (serverless mode).

    A fresh Application per request: serverless instances may switch event loops between requests,
    and the bot's HTTP client is bound to the loop. The user's `user_data` is loaded from / saved to the DB.
    """
    app = build_application()
    if not app:
        return
    async with app:  # initialize() / shutdown()
        update = Update.de_json(data, app.bot)
        tg_user = update.effective_user
        previous: dict = {}
        if tg_user:
            async with SessionLocal() as db:
                previous = await state_service.get_telegram_state(db, tg_user.id)
            app.user_data[tg_user.id].update(previous)
        await app.process_update(update)
        if tg_user:
            async with SessionLocal() as db:
                await state_service.save_telegram_state(
                    db, tg_user.id, dict(app.user_data[tg_user.id]), previous
                )
