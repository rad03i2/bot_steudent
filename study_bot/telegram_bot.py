"""ربط منطق البوت بتيليجرام."""

from __future__ import annotations

import io
import logging
from datetime import date, datetime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
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
from .reminders import due_messages
from .rules import RuleBasedAnalyzer
from .service import Reply, Sender, StudyService

log = logging.getLogger(__name__)

HELP_TEXT = """\
📚 <b>أهلاً! أنا مُذكِّر الدفعة</b>
المرحلة الرابعة – بولونيا – شعبة A

أقرأ رسائل القروب، وأي موعد دراسي ينذكر أسجّله وأرتّبه تلقائياً.
أعتمد مواد جدول الشعبة فقط، وأعرف المادة من اسمها أو اسم أستاذها (مثل <i>كويز د. عمار</i>).
مادة من خارج الجدول؟ اكتب معها <i>مادة خارجية</i>.

✍️ <b>للإضافة</b> اكتبوا بشكل طبيعي، مثلاً:
• <i>الخميس الجاي امتحان عند د. عمار الفصل الثالث</i>
• <i>تقرير النفايات الخطرة تسليمه باجر الساعة 11 بالليل</i>
• <i>واجب سمير بالمحاضرة الجاية</i>

✏️ <b>للتعديل أو الإلغاء:</b>
• <i>كويز الانشائية تأجل للأحد</i>
• <i>انلغى واجب الاقتصاد</i>

❓ <b>للسؤال:</b>
• <i>المهام</i> أو <i>مهام يوم الأربعاء</i> ← المهام فقط
• <i>شنو عدنا باجر؟</i> ← مهام باجر + محاضراته
• <i>جدول الأسبوع</i> ← صورة جدول الدروس (بدون مهام)
• <i>محاضرات باجر</i> أو <i>جدول الاثنين</i> ← صورة ذلك اليوم فقط
• <i>متى الفاينل مال تلوث التربة؟</i>

⚡️ <b>أوامر سريعة:</b>
/today – مهام اليوم
/tomorrow – مهام الغد
/week – مهام بقية هذا الأسبوع
/nextweek – مهام الأسبوع القادم
/upcoming – كل القادم (30 يوم)
/exams – الامتحانات القادمة
/timetable – صورة جدول الدروس كاملة
/timetable الاثنين – محاضرات يوم واحد فقط (أو اكتب: <i>محاضرات باجر</i>، <i>جدول الثلاثاء</i>)
/image – صورة جدول المهام للأسبوع (و <code>/image next</code> للأسبوع الجاي)
/calendar – ملف تضيف منه كل المواعيد لتقويم جوالك

📋 <b>شنو باقي علي:</b> اكتب <i>شنو باقي علي؟</i> أو /mytasks

🔔 <b>تذكيرات تلقائية</b> بالقروب: ملخص كل صباح، وتذكير مساءً بمواعيد باجر، وملخص أسبوعي.
/reminders – حالة التذكيرات (on / off للتشغيل والإيقاف)
"""

COMMANDS = [
    ("today", "مهام اليوم"),
    ("tomorrow", "مهام الغد"),
    ("week", "مهام بقية هذا الأسبوع"),
    ("nextweek", "مهام الأسبوع القادم"),
    ("upcoming", "كل المهام القادمة"),
    ("exams", "الامتحانات القادمة"),
    ("mytasks", "شنو باقي علي؟ (قائمتي الشخصية)"),
    ("timetable", "صورة جدول الدروس"),
    ("image", "صورة جدول المهام للأسبوع (next للأسبوع الجاي)"),
    ("calendar", "ملف تقويم لإضافة المواعيد لجوالك"),
    ("reminders", "التذكيرات التلقائية (on/off)"),
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


async def _allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """البوت خاص بقروب الشعبة: يغادر أي قروب آخر، وعلى الخاص يرد فقط على أعضاء القروب."""
    service = _service(context)
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    verdict = service.access(
        chat.id,
        is_private=chat.type == ChatType.PRIVATE,
        user_id=user.id,
        user_name=user.full_name or "",
        title=chat.title or "",
    )
    if verdict == "ok":
        return True
    try:
        if verdict == "leave":
            await context.bot.send_message(chat.id, "🔒 هذا البوت خاص بشعبة A – المرحلة الرابعة بولونيا. مع السلامة 👋")
            await context.bot.leave_chat(chat.id)
        elif verdict == "stranger" and update.message is not None:
            await update.message.reply_text(
                "🔒 هذا البوت خاص بطلاب شعبة A. إذا أنت من الشعبة، اكتب أي رسالة بقروب الشعبة أول حتى أتعرف عليك."
            )
    except TelegramError:
        log.warning("access response failed", exc_info=True)
    return False


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
    if not await _allowed(update, context):
        return
    await _send(update, Reply(HELP_TEXT))


async def cmd_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    assert chat is not None
    await _send(update, Reply(f"🆔 رقم هذه المحادثة: <code>{chat.id}</code>"))


def _schedule_command(period: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _allowed(update, context):
            return
        await _send(update, _service(context).schedule_reply(period))

    return handler


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return
    if not await _allowed(update, context):
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
    special = service.special_request(text)
    if special:
        await _send_special(update, service, special)
        return
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
    if not await _allowed(update, context):
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


async def _send_special(update: Update, service: StudyService, kind: str) -> None:
    message = update.effective_message
    chat = update.effective_chat
    assert message is not None and chat is not None
    if kind in ("image", "image_next"):
        await chat.send_action(ChatAction.UPLOAD_PHOTO)
        png, caption = service.week_image(next_week=kind == "image_next")
        await message.reply_photo(InputFile(io.BytesIO(png), filename="week.png"), caption=caption)
    elif kind == "timetable":
        await chat.send_action(ChatAction.UPLOAD_PHOTO)
        png, caption = service.timetable_image()
        await message.reply_photo(InputFile(io.BytesIO(png), filename="timetable.png"), caption=caption)
    elif kind.startswith("timetable_day:"):
        await chat.send_action(ChatAction.UPLOAD_PHOTO)
        day = date.fromisoformat(kind.split(":", 1)[1])
        png, caption = service.timetable_day_image(day)
        await message.reply_photo(InputFile(io.BytesIO(png), filename="day.png"), caption=caption)
    elif kind.startswith("lectures:"):
        day = date.fromisoformat(kind.split(":", 1)[1])
        await _send(update, service.lectures_reply(day))
    elif kind == "calendar":
        await chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        data, caption = service.calendar_file()
        await message.reply_document(
            InputFile(io.BytesIO(data), filename="study_schedule.ics"), caption=caption
        )


async def cmd_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _allowed(update, context):
        return
    nxt = bool(context.args) and context.args[0].lower() in ("next", "الجاي", "القادم")
    await _send_special(update, _service(context), "image_next" if nxt else "image")


async def cmd_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/timetable: الجدول كاملاً. /timetable الاثنين: محاضرات ذلك اليوم فقط."""
    if not await _allowed(update, context):
        return
    service = _service(context)
    kind = "timetable"
    if context.args:
        kind = service.special_request("جدول الدروس " + " ".join(context.args)) or "timetable"
    await _send_special(update, service, kind)


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _allowed(update, context):
        return
    await _send_special(update, _service(context), "calendar")


async def cmd_mytasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _allowed(update, context):
        return
    await _send(update, _service(context).personal_reply(_sender(update)))


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reminders on|off: تشغيل أو إيقاف التذكيرات التلقائية لهذه المحادثة."""
    if not await _allowed(update, context):
        return
    chat = update.effective_chat
    assert chat is not None
    service = _service(context)
    cfg = service.config
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "تشغيل", "شغل"):
        service.db.set_reminders(chat.id, True)
    elif arg in ("off", "ايقاف", "إيقاف", "طفي"):
        service.db.set_reminders(chat.id, False)
    enabled = service.db.reminders_enabled(chat.id) and chat.id in service.db.reminder_chats()
    if enabled:
        text = (
            "🔔 <b>التذكيرات مفعّلة</b>\n"
            f"⏰ الأوقات: {' • '.join(cfg.reminder_times)}\n"
            "☀️ أول تذكير: محاضرات اليوم ومواعيده وما يقترب\n"
            "🕒🌆 بالنهار والمغرب: مواعيد اليوم وباجر\n"
            "🌙 آخر تذكير: مواعيد ومحاضرات باجر (ومساء السبت ملخص الأسبوع بالصورة)\n\n"
            "للإيقاف: <code>/reminders off</code>"
        )
    else:
        text = "🔕 <b>التذكيرات متوقفة</b>\nللتشغيل: <code>/reminders on</code>"
    await _send(update, Reply(text))


async def reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    service: StudyService = context.application.bot_data["service"]
    now = datetime.now(service.config.tz)
    for msg in due_messages(service.db, now, service.config):
        if msg.text:
            try:
                if msg.image:
                    await context.bot.send_photo(
                        msg.chat_id, InputFile(io.BytesIO(msg.image), filename="week.png")
                    )
                await context.bot.send_message(
                    msg.chat_id, msg.text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except Forbidden:
                # البوت انحذف من القروب
                log.info("Chat %s removed the bot; disabling reminders", msg.chat_id)
                service.db.set_reminders(msg.chat_id, False)
                continue
            except TelegramError:
                log.warning("Failed to send %s reminder to %s", msg.kind, msg.chat_id, exc_info=True)
                continue
        # حتى الأيام الفارغة تُسجّل، حتى لا نعيد الفحص كل 5 دقائق
        service.db.mark_sent(msg.chat_id, msg.kind, now.date())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error", exc_info=context.error)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
    try:
        await app.bot.set_my_short_description("مُذكِّر الدفعة 📚 ينظم امتحاناتكم وواجباتكم ومشاريعكم تلقائياً")
        await app.bot.set_my_description(
            "📚 مُذكِّر الدفعة\n\n"
            "أضيفوني لقروب الدفعة، وأي موعد دراسي ينذكر (امتحان، كويز، ميد، فاينل، واجب، مشروع...) "
            "أسجّله وأحسب تاريخه وأرتّبه بجداول حسب الأيام والأسابيع.\n\n"
            "اسألوني: شنو عدنا الأسبوع الجاي؟"
        )
    except Exception:  # الوصف تجميلي فقط، لا نوقف البوت بسببه
        log.warning("تعذر تحديث وصف البوت", exc_info=True)


def build_application(config: Config) -> Application:
    db = Database(config.db_path)
    # مجاني افتراضياً: المحلّل المحلي. Claude يُستخدم فقط إذا وُضع مفتاحه.
    analyzer = (
        ClaudeAnalyzer(config.anthropic_api_key, config.claude_model, config.claude_effort)
        if config.ai_enabled
        else RuleBasedAnalyzer()
    )
    service = StudyService(db, analyzer, config)

    app = Application.builder().token(config.telegram_token).post_init(_post_init).build()
    app.bot_data["service"] = service

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("chatid", cmd_chat_id))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("mytasks", cmd_mytasks))
    app.add_handler(CommandHandler("image", cmd_image))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("timetable", cmd_timetable))
    for name in ("today", "tomorrow", "week", "nextweek", "upcoming", "exams"):
        app.add_handler(CommandHandler(name, _schedule_command(name)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    if config.reminders and app.job_queue is not None:
        app.job_queue.run_repeating(reminders_job, interval=300, first=20, name="reminders")
    return app
