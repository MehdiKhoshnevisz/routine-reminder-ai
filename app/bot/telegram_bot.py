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
from app.services import user_service
from app.services.routine_service import DAY_NAMES

log = logging.getLogger(__name__)

# ---------- keyboards ----------

BTN_LIST = "📋 My routines"
BTN_NEW = "➕ New routine"
BTN_UPDATE = "✏️ Update routine"
BTN_DELETE = "🗑 Delete routine"
BTN_TZ = "🌍 Timezone"
BTN_HELP = "❓ Help"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_LIST), KeyboardButton(BTN_NEW)],
        [KeyboardButton(BTN_UPDATE), KeyboardButton(BTN_DELETE)],
        [KeyboardButton(BTN_TZ), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
    input_field_placeholder="e.g. sport at 19:00 on weekdays",
)

COMMANDS = [
    BotCommand("start", "Start / show menu"),
    BotCommand("routines", "List your routines"),
    BotCommand("update", "Update a routine"),
    BotCommand("delete", "Delete a routine"),
    BotCommand("timezone", "Set your timezone"),
    BotCommand("cancel", "Cancel the current action"),
    BotCommand("help", "How to use the bot"),
]

HELP = (
    "Hi! I'm Routine AI. Just tell me your routines, for example:\n"
    "• Brush my teeth at 7:10 every day\n"
    "• Sport at 19:00 on weekdays\n"
    "• Move sport to 20:00\n\n"
    "I'll remind you here at the right time ⏰\n\n"
    "Commands:\n"
    "/routines - list your routines\n"
    "/update - change a routine (time, days, title, pause)\n"
    "/delete - delete a routine\n"
    "/timezone <Area/City> - set your timezone (e.g. Asia/Tehran)\n"
    "/cancel - cancel the current action"
)

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
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _routine_menu(r) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏰ Time", callback_data=f"e:{r.id}:time"),
            InlineKeyboardButton("📅 Days", callback_data=f"dp:{r.id}"),
            InlineKeyboardButton("✏️ Title", callback_data=f"e:{r.id}:title"),
        ],
        [
            InlineKeyboardButton("▶️ Resume" if not r.is_active else "⏸ Pause", callback_data=f"p:{r.id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"d:{r.id}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="ls:edit")],
    ])


def _days_keyboard(routine_id: int, selected: set[int]) -> InlineKeyboardMarkup:
    day_btns = [
        InlineKeyboardButton(f"{'✅' if d in selected else '▫️'} {DAY_NAMES[d]}", callback_data=f"dt:{routine_id}:{d}")
        for d in range(7)
    ]
    return InlineKeyboardMarkup([
        day_btns[:4],
        day_btns[4:],
        [
            InlineKeyboardButton("🔁 Every day", callback_data=f"de:{routine_id}"),
            InlineKeyboardButton("💾 Save", callback_data=f"ds:{routine_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"r:{routine_id}")],
    ])


def _routine_text(r) -> str:
    text = f"Routine {rs.format_routine(r)}"
    if r.description:
        text += f"\n{r.description}"
    return text + "\n\nWhat do you want to change?"


# ---------- helpers ----------

async def _user(db, update: Update):
    tg = update.effective_user
    return await user_service.get_or_create_telegram_user(db, tg.id, tg.full_name or tg.username or "friend")


async def _send_routine_picker(update: Update, mode: str):
    async with SessionLocal() as db:
        user = await _user(db, update)
        routines = await rs.list_routines(db, user.id)
    if not routines:
        await update.effective_message.reply_text("You have no routines yet. Tell me one, e.g. 'sport at 19:00'.")
        return
    title = "Which routine do you want to update?" if mode == "edit" else "Which routine do you want to delete?"
    await update.effective_message.reply_text(title, reply_markup=_routines_keyboard(routines, mode))


# ---------- commands ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    async with SessionLocal() as db:
        user = await _user(db, update)
    await update.message.reply_text(f"{HELP}\n\nYour timezone: {user.timezone}", reply_markup=MAIN_MENU)


async def routines_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as db:
        user = await _user(db, update)
        items = await rs.list_routines(db, user.id)
    if not items:
        await update.message.reply_text("You have no routines yet.", reply_markup=MAIN_MENU)
        return
    text = "📋 Your routines:\n" + "\n".join(rs.format_routine(r) for r in items)
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Edit a routine", callback_data="ls:edit")]]),
    )


async def update_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/update -> pick from buttons;  /update <id> -> open that routine directly."""
    ctx.user_data.pop("awaiting", None)
    if ctx.args and ctx.args[0].lstrip("#").isdigit():
        async with SessionLocal() as db:
            user = await _user(db, update)
            r = await rs.get_routine(db, user.id, int(ctx.args[0].lstrip("#")))
        if not r:
            await update.message.reply_text("Routine not found.")
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
            await update.message.reply_text("Routine not found.")
            return
        await update.message.reply_text(f"Delete {rs.format_routine(r)}?", reply_markup=_confirm_delete(r.id))
        return
    await _send_routine_picker(update, "del")


async def timezone_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        ctx.user_data["awaiting"] = {"field": "timezone"}
        async with SessionLocal() as db:
            user = await _user(db, update)
        await update.effective_message.reply_text(
            f"Your timezone is {user.timezone}.\nSend me a new one (e.g. Asia/Tehran, Europe/Berlin) or /cancel."
        )
        return
    await _set_timezone(update, ctx.args[0])


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_MENU)


async def help_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, reply_markup=MAIN_MENU)


async def new_routine_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(
        "Tell me your routine in your own words, e.g.\n"
        "• Brush teeth at 7:10 and sport at 19:00\n"
        "• Read a book at 22 on weekends"
    )


async def _set_timezone(update: Update, tz: str):
    async with SessionLocal() as db:
        user = await _user(db, update)
        try:
            await user_service.update_user(db, user, timezone=tz)
        except ValueError as e:
            await update.effective_message.reply_text(f"{e}\nTry e.g. Asia/Tehran or Europe/London.")
            return False
    await update.effective_message.reply_text(f"🌍 Timezone set to {tz}", reply_markup=MAIN_MENU)
    return True


def _confirm_delete(routine_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"dy:{routine_id}"),
        InlineKeyboardButton("✖️ No", callback_data="x"),
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
                await q.edit_message_text("You have no routines yet.")
                return
            mode = rest[0]
            title = "Which routine do you want to update?" if mode == "edit" else "Which routine do you want to delete?"
            await q.edit_message_text(title, reply_markup=_routines_keyboard(routines, mode))
            return

        r = await rs.get_routine(db, user.id, int(rest[0]))
        if not r:
            await q.edit_message_text("This routine no longer exists.")
            return

        if action == "r":
            ctx.user_data.pop("awaiting", None)
            await q.edit_message_text(_routine_text(r), reply_markup=_routine_menu(r))

        elif action == "e":
            field = rest[1]
            ctx.user_data["awaiting"] = {"field": field, "routine_id": r.id}
            prompt = (
                f"Send the new time for '{r.title}' (24h, e.g. 07:30 or 19:00), or /cancel."
                if field == "time"
                else f"Send the new title for '{r.title}', or /cancel."
            )
            await q.edit_message_text(prompt)

        elif action == "dp":
            ctx.user_data["days"] = set(r.days or [])
            await q.edit_message_text(
                f"Pick the days for '{r.title}' (none selected = every day):",
                reply_markup=_days_keyboard(r.id, ctx.user_data["days"]),
            )

        elif action == "dt":
            days: set[int] = ctx.user_data.setdefault("days", set(r.days or []))
            days.symmetric_difference_update({int(rest[1])})
            await q.edit_message_reply_markup(_days_keyboard(r.id, days))

        elif action in ("de", "ds"):
            days = set() if action == "de" else ctx.user_data.get("days", set(r.days or []))
            if days == set(range(7)):
                days = set()
            r = await rs.update_routine(db, r, days=sorted(days))
            ctx.user_data.pop("days", None)
            await q.edit_message_text(f"✅ Updated {rs.format_routine(r)}", reply_markup=_routine_menu(r))

        elif action == "p":
            r = await rs.update_routine(db, r, is_active=not r.is_active)
            state = "▶️ Resumed" if r.is_active else "⏸ Paused"
            await q.edit_message_text(f"{state} {rs.format_routine(r)}", reply_markup=_routine_menu(r))

        elif action == "d":
            await q.edit_message_text(f"Delete {rs.format_routine(r)}?", reply_markup=_confirm_delete(r.id))

        elif action == "dy":
            title = r.title
            await rs.delete_routine(db, r)
            await q.edit_message_text(f"🗑 Deleted '{title}'")


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
            await update.message.reply_text("This routine no longer exists.")
            return
        try:
            if field == "time":
                r = await rs.update_routine(db, r, time=parse_time(text))
            else:
                r = await rs.update_routine(db, r, title=text[:200])
        except ValueError as e:
            await update.message.reply_text(f"⚠️ {e} Try again or /cancel.")
            return
    ctx.user_data.pop("awaiting", None)
    await update.message.reply_text(f"✅ Updated {rs.format_routine(r)}", reply_markup=_routine_menu(r))


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
            reply = "I'm getting too many requests right now. Please try again in a minute."
        except APITimeoutError:
            log.warning("OpenRouter request timed out")
            reply = "That took too long to think about. Please try again."
        except AuthenticationError:
            log.error("OpenRouter auth failed, check OPENROUTER_API_KEY")
            reply = "Sorry, I'm not set up correctly right now. Please try again later."
        except Exception:
            log.exception("agent failed")
            reply = "Sorry, my brain (OpenRouter) isn't responding right now. Please try again in a moment."
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
    """Registers the '/' command menu shown in Telegram."""
    try:
        await app.bot.set_my_commands(COMMANDS)
    except Exception:
        log.exception("Could not set bot commands")
