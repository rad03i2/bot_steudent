"""ربط منطق البوت بتيليجرام."""

from __future__ import annotations

import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Config
from .db import Database
from .llm import ClaudeAnalyzer
from .service import Reply, Sender, StudyService

log = logging.getLogger(__name__)

HELP_TEXT = """\
📚 <b>أهلاً! أنا مُذكِّر الدفعة</b>

أقرأ رسائل القروب، وأي موعد دراسي ينذكر أسجّله وأرتّبه تلقائياً.

✍️ <b>للإضافة</b> اكتبوا بشكل طبيعي، مثلاً:
• <i>الخميس الجاي امتحان فيزياء الفصل الثالث والرابع</i>
• <i>واجب رياضيات تمارين 4.1 لـ 4.5 تسليمه باجر الساعة 11 بالليل</i>
• <i>الميد مال البرمجة 3/12</i>

✏️ <b>للتعديل أو الإلغاء:</b>
• <i>كويز الكيمياء تأجل للأحد</i>
• <i>انلغى واجب الإحصاء</i>

❓ <b>للسؤال:</b>
• <i>شنو عدنا الأسبوع الجاي؟</i>
• <i>مهام يوم الأربعاء</i>
• <i>متى الفاينل مال الفيزياء؟</i>

⚡️ <b>أوامر سريعة:</b>
/today – جدول اليوم
/tomorrow – جدول الغد
/week – بقية هذا الأسبوع
/nextweek – الأسبوع القادم
/upcoming – كل القادم (30 يوم)
/exams – الامتحانات القادمة
"""

COMMANDS = [
    ("today", "جدول اليوم"),
    ("tomorrow", "جدول الغد"),
    ("week", "بقية هذا الأسبوع"),
    ("nextweek", "جدول الأسبوع القادم"),
    ("upcoming", "كل المهام القادمة"),
    ("exams", "الامتحانات القادمة"),
    ("help", "طريقة الاستخدام"),
]


def _markup(reply: Reply) -> InlineKeyboardMarkup | None:
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in reply.buttons]
    )


def _sender(update: Update) -> Sender:
    user = update.effective_user
    assert user is not None
    return Sender(user_id=user.id, name=user.full_name or user.username or "طالب")


def _service(context: ContextTypes.DEFAULT_TYPE) -> StudyService:
    return context.application.bot_data["service"]


def _allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يقيّد البوت على قروب الدفعة إذا تم ضبط ALLOWED_CHAT_IDS."""
    service = _service(context)
    allowed = service.config.allowed_chat_ids
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    if chat.type == ChatType.PRIVATE:
        if not allowed:
            return True
        return service.db.is_member(user.id)
    if allowed and chat.id not in allowed:
        return False
    service.db.touch_member(user.id, user.full_name or "")
    return True


async def _send(update: Update, reply: Reply) -> None:
    message = update.effective_message
    assert message is not None
    await message.reply_text(
        reply.text,
        parse_mode=ParseMode.HTML,
        reply_markup=_markup(reply),
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    await _send(update, Reply(HELP_TEXT))


async def cmd_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    assert chat is not None
    await _send(update, Reply(f"🆔 رقم هذه المحادثة: <code>{chat.id}</code>"))


def _schedule_command(period: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update, context):
            return
        await _send(update, _service(context).schedule_reply(period))

    return handler


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return
    if not _allowed(update, context):
        return

    bot = context.bot
    is_private = chat.type == ChatType.PRIVATE
    text = message.text
    mention = f"@{bot.username}" if bot.username else None
    addressed = False
    if mention and mention.lower() in text.lower():
        addressed = True
        text = text.replace(mention, "").replace(mention.lower(), "").strip()
    replied_to = None
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.from_user and replied.from_user.id == bot.id:
            addressed = True
        replied_to = replied.text or replied.caption

    service = _service(context)
    if service.analyzer is not None and (is_private or addressed):
        await chat.send_action(ChatAction.TYPING)

    replies = await service.handle_message(
        text,
        _sender(update),
        chat_id=chat.id,
        is_private=is_private,
        addressed=addressed,
        replied_to=replied_to,
    )
    for reply in replies:
        await _send(update, reply)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    if not _allowed(update, context):
        await query.answer("غير مسموح")
        return
    result = _service(context).handle_callback(query.data, _sender(update))
    await query.answer(result.answer or None)
    if result.edit is not None:
        await query.edit_message_text(
            result.edit.text, parse_mode=ParseMode.HTML, reply_markup=_markup(result.edit)
        )
    if result.new is not None and query.message is not None:
        await query.message.reply_text(
            result.new.text, parse_mode=ParseMode.HTML, reply_markup=_markup(result.new)
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error", exc_info=context.error)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])


def build_application(config: Config) -> Application:
    db = Database(config.db_path)
    analyzer = (
        ClaudeAnalyzer(config.anthropic_api_key, config.claude_model, config.claude_effort)
        if config.ai_enabled
        else None
    )
    service = StudyService(db, analyzer, config)

    app = Application.builder().token(config.telegram_token).post_init(_post_init).build()
    app.bot_data["service"] = service

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("chatid", cmd_chat_id))
    for name in ("today", "tomorrow", "week", "nextweek", "upcoming", "exams"):
        app.add_handler(CommandHandler(name, _schedule_command(name)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    return app
