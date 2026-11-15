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
from telegram.constants import ChatAction
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
from app.services import state_service, user_service
from app.services.routine_service import DAY_NAMES, DAY_ORDER

log = logging.getLogger(__name__)

# ---------- keyboards ----------

BTN_LIST = "📋 روتین‌های من"
BTN_NEW = "➕ روتین جدید"
BTN_UPDATE = "✏️ ویرایش روتین"
BTN_DELETE = "🗑 حذف روتین"
BTN_TZ = "🌍 منطقه زمانی"
BTN_HELP = "❓ راهنما"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_LIST), KeyboardButton(BTN_NEW)],
        [KeyboardButton(BTN_UPDATE), KeyboardButton(BTN_DELETE)],
        [KeyboardButton(BTN_TZ), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
    input_field_placeholder="مثلاً ورزش ساعت ۱۹ روزهای کاری",
)

COMMANDS = [
    BotCommand("start", "شروع / نمایش منو"),
    BotCommand("routines", "لیست روتین‌های شما"),
    BotCommand("update", "ویرایش یک روتین"),
    BotCommand("delete", "حذف یک روتین"),
    BotCommand("timezone", "تنظیم منطقه زمانی"),
    BotCommand("cancel", "لغو عملیات فعلی"),
    BotCommand("help", "راهنمای استفاده از ربات"),
]

HELP = (
    "سلام! من ناصرم =)\n"
    "پیگیر روتین‌های تو\n\n"
    "یک همچین چیزی بهم بگو تا یادآوری روتینت رو انجام بدم:\n"
    "• هر روز ساعت ۷:۱۰ مسواک بزنم\n"
    "• روزهای کاری ساعت ۱۹ ورزش کنم\n"
    "• ساعت ورزش رو از ۱۹ به ۲۰ تغییر بده\n\n"
    "هر روتینی که تعریف کنی، سر وقت بهت یادآوری می‌کنم (لایک عه باس)\n\n"
    "دستورها:\n"
    "/routines – دیدن روتین‌هات\n"
    "/update – ویرایش ساعت، روزها یا عنوان روتین\n"
    "/delete – حذف روتین\n"
    "/timezone – تنظیم منطقه زمانی\n"
    "/cancel – لغو عملیات فعلی"
)


# Telegram allows max 512 chars for the description and 120 for the short description.
DESCRIPTION = (
    "سلام! من ناصرم =)\n"
    "پیگیر روتین‌های تو\n\n"
    "روتین‌هات رو به زبان خودت بهم بگو، مثلاً:\n"
    "• هر روز ساعت ۷:۱۰ مسواک بزنم\n"
    "• روزهای کاری ساعت ۱۹ ورزش کنم\n\n"
    "سر وقت بهت یادآوری می‌کنم ⏰\n"
    "برای شروع، دکمه «شروع» رو بزن 👇"
)
SHORT_DESCRIPTION = "ناصرم =) روتین‌هات رو بگو، سر وقت بهت یادآوری می‌کنم ⏰"


def _help_text(user) -> str:
    return f"{HELP}\n\n🕒 منطقه زمانی فعلی: {user.timezone}"

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


def _routines_keyboard(routines, mode: str) -> InlineKeyboardMarkup:
    prefix = "r" if mode == "edit" else "d"
    rows = [
        [InlineKeyboardButton(
            f"{r.time.strftime('%H:%M')} · {r.title}{' ⏸' if not r.is_active else ''}",
            callback_data=f"{prefix}:{r.id}",
        )]
        for r in routines
    ]
    rows.append([InlineKeyboardButton("✖️ بستن", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _routine_menu(r) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏰ ساعت", callback_data=f"e:{r.id}:time"),
            InlineKeyboardButton("📅 روزها", callback_data=f"dp:{r.id}"),
            InlineKeyboardButton("✏️ عنوان", callback_data=f"e:{r.id}:title"),
        ],
        [
            InlineKeyboardButton("▶️ ادامه" if not r.is_active else "⏸ توقف", callback_data=f"p:{r.id}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"d:{r.id}"),
        ],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="ls:edit")],
    ])


def _days_keyboard(routine_id: int, selected: set[int]) -> InlineKeyboardMarkup:
    day_btns = [
        InlineKeyboardButton(f"{'✅' if d in selected else '▫️'} {DAY_NAMES[d]}", callback_data=f"dt:{routine_id}:{d}")
        for d in DAY_ORDER
    ]
    return InlineKeyboardMarkup([
        day_btns[:4],
        day_btns[4:],
        [
            InlineKeyboardButton("🔁 هر روز", callback_data=f"de:{routine_id}"),
            InlineKeyboardButton("💾 ذخیره", callback_data=f"ds:{routine_id}"),
        ],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data=f"r:{routine_id}")],
    ])


def _routine_text(r) -> str:
    text = f"روتین {rs.format_routine(r)}"
    if r.description:
        text += f"\n{r.description}"
    return text + "\n\nچه چیزی رو می‌خوای تغییر بدی؟"


# ---------- helpers ----------

async def _user(db, update: Update):
    tg = update.effective_user
    return await user_service.get_or_create_telegram_user(db, tg.id, tg.full_name or tg.username or "دوست")


async def _send_routine_picker(update: Update, mode: str):
    async with SessionLocal() as db:
        user = await _user(db, update)
        routines = await rs.list_routines(db, user.id)
    if not routines:
        await update.effective_message.reply_text("هنوز هیچ روتینی نداری. یکی بهم بگو، مثلاً «ورزش ساعت ۱۹».")
        return
    title = "کدوم روتین رو می‌خوای ویرایش کنی؟" if mode == "edit" else "کدوم روتین رو می‌خوای حذف کنی؟"
    await update.effective_message.reply_text(title, reply_markup=_routines_keyboard(routines, mode))


# ---------- commands ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(_help_text(user), reply_markup=MAIN_MENU)


async def routines_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as db:
        user = await _user(db, update)
        items = await rs.list_routines(db, user.id)
    if not items:
        await update.message.reply_text("هنوز هیچ روتینی نداری.", reply_markup=MAIN_MENU)
        return
    text = "📋 روتین‌های شما:\n" + "\n".join(rs.format_routine(r) for r in items)
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ ویرایش روتین", callback_data="ls:edit")]]),
    )


async def update_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/update -> pick from buttons;  /update <id> -> open that routine directly."""
    ctx.user_data.pop("awaiting", None)
    if ctx.args and ctx.args[0].lstrip("#").isdigit():
        async with SessionLocal() as db:
            user = await _user(db, update)
            r = await rs.get_routine(db, user.id, int(ctx.args[0].lstrip("#")))
        if not r:
            await update.message.reply_text("روتین پیدا نشد.")
            return
        await update.message.reply_text(_routine_text(r), reply_markup=_routine_menu(r))
        return
    await _send_routine_picker(update, "edit")


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/delete -> pick from buttons;  /delete <id> -> ask confirmation for that routine."""
    if ctx.args and ctx.args[0].lstrip("#").isdigit():
        async with SessionLocal() as db:
            user = await _user(db, update)
            r = await rs.get_routine(db, user.id, int(ctx.args[0].lstrip("#")))
        if not r:
            await update.message.reply_text("روتین پیدا نشد.")
            return
        await update.message.reply_text(f"{rs.format_routine(r)} حذف بشه؟", reply_markup=_confirm_delete(r.id))
        return
    await _send_routine_picker(update, "del")


async def timezone_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        ctx.user_data["awaiting"] = {"field": "timezone"}
        async with SessionLocal() as db:
            user = await _user(db, update)
        await update.effective_message.reply_text(
            f"🕒 منطقه زمانی فعلی تو: {user.timezone}\n\n"
            "من یادآوری‌ها رو بر اساس ساعت منطقه زمانی تو می‌فرستم. "
            "پس اگه درست تنظیم نشده باشه، یادآوری‌ها زودتر یا دیرتر میان.\n\n"
            "برای تغییرش، اسم منطقه‌ات رو به شکل Area/City بفرست، مثلاً:\n"
            "• Asia/Tehran (ایران) - یا فقط بنویس «تهران»\n"
            "• Europe/Berlin (آلمان)\n"
            "• Europe/London (انگلیس)\n"
            "• America/New_York (نیویورک)\n"
            "• Asia/Dubai (دبی)\n\n"
            "می‌تونی مستقیم هم بنویسی:\n /timezone Asia/Tehran\n"
            "برای لغو: /cancel"
        )
        return
    await _set_timezone(update, ctx.args[0])


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("لغو شد.", reply_markup=MAIN_MENU)


async def help_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(_help_text(user), reply_markup=MAIN_MENU)


async def new_routine_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(
        "روتینت رو با زبان خودت بگو، مثلاً:\n"
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
    await update.effective_message.reply_text(f"🌍 منطقه زمانی روی {tz} تنظیم شد", reply_markup=MAIN_MENU)
    return True


def _confirm_delete(routine_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"dy:{routine_id}"),
        InlineKeyboardButton("✖️ نه", callback_data="x"),
    ]])


# ---------- inline button callbacks ----------

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, *rest = q.data.split(":")

    if action == "x":
        ctx.user_data.pop("awaiting", None)
        await q.edit_message_reply_markup(None)
        return

    async with SessionLocal() as db:
        user = await _user(db, update)

        if action == "ls":
            routines = await rs.list_routines(db, user.id)
            if not routines:
                await q.edit_message_text("هنوز هیچ روتینی نداری.")
                return
            mode = rest[0]
            title = "کدوم روتین رو می‌خوای ویرایش کنی؟" if mode == "edit" else "کدوم روتین رو می‌خوای حذف کنی؟"
            await q.edit_message_text(title, reply_markup=_routines_keyboard(routines, mode))
            return

        r = await rs.get_routine(db, user.id, int(rest[0]))
        if not r:
            await q.edit_message_text("این روتین دیگه وجود نداره.")
            return

        if action == "r":
            ctx.user_data.pop("awaiting", None)
            await q.edit_message_text(_routine_text(r), reply_markup=_routine_menu(r))

        elif action == "e":
            field = rest[1]
            ctx.user_data["awaiting"] = {"field": field, "routine_id": r.id}
            prompt = (
                f"ساعت جدید «{r.title}» رو بفرست (۲۴ ساعته، مثلاً 07:30 یا 19:00)، یا /cancel."
                if field == "time"
                else f"عنوان جدید «{r.title}» رو بفرست، یا /cancel."
            )
            await q.edit_message_text(prompt)

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
            await q.edit_message_text(rs.format_routine_card(r, "✅ روتین به‌روزرسانی شد"), reply_markup=_routine_menu(r))

        elif action == "p":
            r = await rs.update_routine(db, r, is_active=not r.is_active)
            state = "▶️ ادامه داده شد:" if r.is_active else "⏸ متوقف شد:"
            await q.edit_message_text(f"{state} {rs.format_routine(r)}", reply_markup=_routine_menu(r))

        elif action == "d":
            await q.edit_message_text(f"{rs.format_routine(r)} حذف بشه؟", reply_markup=_confirm_delete(r.id))

        elif action == "dy":
            title = r.title
            await rs.delete_routine(db, r)
            await q.edit_message_text(f"🗑 «{title}» حذف شد")


# ---------- free text ----------

async def _handle_awaiting(update: Update, ctx: ContextTypes.DEFAULT_TYPE, awaiting: dict) -> None:
    text = update.message.text.strip()
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
            await update.message.reply_text("این روتین دیگه وجود نداره.")
            return
        try:
            if field == "time":
                r = await rs.update_routine(db, r, time=parse_time(text))
            else:
                r = await rs.update_routine(db, r, title=text[:200])
        except ValueError as e:
            await update.message.reply_text(f"⚠️ {e} دوباره امتحان کن یا /cancel.")
            return
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(rs.format_routine_card(r, "✅ روتین به‌روزرسانی شد"), reply_markup=_routine_menu(r))


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    awaiting = ctx.user_data.get("awaiting")
    if awaiting:
        await _handle_awaiting(update, ctx, awaiting)
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    async with SessionLocal() as db:
        user = await _user(db, update)
        try:
            reply = await agent.chat(db, user, update.message.text)
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
    await update.message.reply_text(reply, reply_markup=MAIN_MENU)


# ---------- setup ----------

def _button(text: str):
    return filters.TEXT & filters.Regex(f"^{re.escape(text)}$")


def build_application() -> Application | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set - Telegram bot disabled")
        return None
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("routines", routines_cmd))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # main menu (reply keyboard) buttons
    app.add_handler(MessageHandler(_button(BTN_LIST), routines_cmd))
    app.add_handler(MessageHandler(_button(BTN_NEW), new_routine_btn))
    app.add_handler(MessageHandler(_button(BTN_UPDATE), update_cmd))
    app.add_handler(MessageHandler(_button(BTN_DELETE), delete_cmd))
    app.add_handler(MessageHandler(_button(BTN_TZ), timezone_cmd))
    app.add_handler(MessageHandler(_button(BTN_HELP), help_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


async def setup_bot_commands(app: Application) -> None:
    """Registers the '/' command menu and the texts shown in an empty chat / bot profile."""
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception:
        log.exception("Could not set bot commands")
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
        if tg_user:
            async with SessionLocal() as db:
                app.user_data[tg_user.id].update(await state_service.get_telegram_state(db, tg_user.id))
        await app.process_update(update)
        if tg_user:
            async with SessionLocal() as db:
                await state_service.save_telegram_state(db, tg_user.id, dict(app.user_data[tg_user.id]))
