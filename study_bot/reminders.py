"""التذكيرات التلقائية: ملخص الصباح، تذكير المساء، والملخص الأسبوعي.

البوت يفحص كل بضع دقائق هل حان وقت تذكير ولم يُرسل اليوم بعد. هذا الأسلوب
يتحمّل إعادة تشغيل البوت (مثل التشغيل على GitHub Actions): إذا صادف وقت
التذكير لحظة إعادة التشغيل، يُرسل بعدها مباشرة، ولا يتكرر لأن الإرسال مسجّل.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape

from .arabic import WEEKDAY_NAMES_AR
from .config import Config
from .dates import days_left_phrase, week_bounds
from .db import Database
from .formatting import SEP, long_date, schedule, sort_key, task_line
from .models import Task

# كم يوماً قبل الموعد نبدأ التنبيه في ملخص الصباح، حسب النوع
HEADS_UP_DAYS = {
    "final": 14,
    "midterm": 7,
    "exam": 3,
    "quiz": 2,
    "project": 3,
    "presentation": 3,
    "report": 1,
    "homework": 1,
    "lab": 1,
    "lecture": 1,
    "other": 1,
}

# بعد الوقت المحدد بكم نتوقف عن محاولة الإرسال (حتى لا يصل تذكير متأخراً جداً بعد انقطاع)
SEND_WINDOW = timedelta(minutes=90)


@dataclass
class Outgoing:
    chat_id: int
    kind: str  # morning / evening
    text: str
    image: bytes | None = None  # صورة جدول الأسبوع (مع الملخص الأسبوعي)


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return default


def _in_window(now: datetime, at: time) -> bool:
    start = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return start <= now < start + SEND_WINDOW


def _heads_up(db: Database, today: date) -> list[Task]:
    horizon = today + timedelta(days=max(HEADS_UP_DAYS.values()))
    upcoming = db.tasks_between(today + timedelta(days=1), horizon)
    return [t for t in upcoming if (t.due_date - today).days <= HEADS_UP_DAYS.get(t.kind, 1)]


def morning_text(db: Database, today: date, style: str = "levantine") -> str | None:
    """ملخص الصباح: محاضرات اليوم + مواعيد اليوم + ما يقترب. يرجع None إذا لا يوجد شيء."""
    from .timetable import lecture_lines_html

    today_tasks = sorted(db.tasks_between(today, today), key=sort_key)
    soon = sorted(_heads_up(db, today), key=lambda t: (t.due_date, t.priority, t.id))
    lectures = lecture_lines_html(today)
    if not today_tasks and not soon and not lectures:
        return None

    lines = [f"☀️ <b>صباح الخير!</b>", f"   {long_date(today, style)}", SEP]
    if lectures:
        lines.append("📚 <b>محاضرات اليوم:</b>")
        lines.extend(lectures)
        lines.append("")
    lines.append("📝 <b>مواعيد اليوم:</b>")
    if today_tasks:
        lines.extend(task_line(t) for t in today_tasks)
    else:
        lines.append("   ماكو امتحانات أو تسليمات اليوم 🎉")

    if soon:
        lines.append("")
        lines.append("📢 <b>انتبهوا، قريباً:</b>")
        for t in soon:
            day = f"{WEEKDAY_NAMES_AR[t.due_date.weekday()]} {t.due_date.day}/{t.due_date.month}"
            lines.append(
                f"   {t.info.icon} {escape(t.info.label)} {escape(t.subject)} – "
                f"<b>{days_left_phrase(t.due_date, today)}</b> ({day})"
            )
    lines.append(SEP)
    lines.append("بالتوفيق للجميع 💪")
    return "\n".join(lines)


def _slot_title(hour: int) -> str:
    if hour < 12:
        return "🌅 تذكير الصباح"
    if hour < 17:
        return "🕒 تذكير العصر"
    if hour < 20:
        return "🌆 تذكير المغرب"
    return "🌙 تذكير الليل"


def reminder_text(db: Database, today: date, hour: int, style: str = "levantine", *, final: bool = False) -> str | None:
    """تذكير نهاري/مسائي: مواعيد اليوم وباجر. وفي آخر تذكير بالليل تُضاف محاضرات باجر.
    يرجع None إذا ماكو شي يستحق التذكير."""
    from .timetable import lecture_lines_html

    tomorrow = today + timedelta(days=1)
    # آخر تذكير بالليل: اليوم انتهى، فنذكّر بباجر فقط
    today_tasks = [] if final else sorted(db.tasks_between(today, today), key=sort_key)
    tomorrow_tasks = sorted(db.tasks_between(tomorrow, tomorrow), key=sort_key)
    lectures = lecture_lines_html(tomorrow) if final else []
    if not today_tasks and not tomorrow_tasks and not lectures:
        return None

    lines = [f"<b>{_slot_title(hour)}</b>", SEP]
    if today_tasks:
        lines.append(f"📌 <b>اليوم ({long_date(today, style)}):</b>")
        lines.extend(task_line(t) for t in today_tasks)
    if tomorrow_tasks:
        if today_tasks:
            lines.append("")
        lines.append(f"📅 <b>باجر ({long_date(tomorrow, style)}):</b>")
        lines.extend(task_line(t) for t in tomorrow_tasks)
    if lectures:
        if today_tasks or tomorrow_tasks:
            lines.append("")
        lines.append(f"📚 <b>محاضرات باجر ({long_date(tomorrow, style)}):</b>")
        lines.extend(lectures)
    lines.append(SEP)
    lines.append("حضّروا زين وناموا بدري 😴" if final else "لا تنسون! 💪")
    return "\n".join(lines)


def weekly_text(db: Database, today: date, week_start: int, style: str = "levantine") -> str:
    """ملخص الأسبوع القادم (يُرسل مساء اليوم الأخير من الأسبوع)."""
    start, _ = week_bounds(today + timedelta(days=1), week_start)
    end = start + timedelta(days=6)
    from .timetable import lecture_lines_html

    tasks = db.tasks_between(start, end)
    text = "📅 <b>ملخص الأسبوع</b>\n\n" + schedule(
        tasks, title="جدول الأسبوع القادم", start=start, end=end, today=today, style=style
    )
    lectures = lecture_lines_html(today + timedelta(days=1))
    if lectures:
        text += f"\n\n📚 <b>محاضرات باجر ({long_date(today + timedelta(days=1), style)}):</b>\n" + "\n".join(lectures)
    return text


def _slot_kind(t: time) -> str:
    return f"r{t.hour:02d}{t.minute:02d}"


def due_messages(db: Database, now: datetime, config: Config) -> list[Outgoing]:
    """الرسائل التي حان وقتها ولم تُرسل اليوم. now بالتوقيت المحلي.

    أول وقت باليوم = ملخص الصباح، وآخر وقت = تذكير الليل (وفي آخر يوم بالأسبوع ملخص الأسبوع
    القادم بالصورة)، وما بينهما تذكيرات بمواعيد اليوم وباجر.
    """
    if not config.reminders:
        return []
    today = now.date()
    times = sorted({_parse_hhmm(t, time(0, 0)) for t in config.reminder_times})
    # اليوم الأخير من الأسبوع: غداً يبدأ أسبوع جديد
    is_week_end = (today + timedelta(days=1)).weekday() == config.week_start

    out: list[Outgoing] = []
    cache: dict[str, tuple[str | None, bytes | None]] = {}
    chats = db.reminder_chats()
    for index, at in enumerate(times):
        if not _in_window(now, at):
            continue
        kind = _slot_kind(at)
        is_first, is_last = index == 0, index == len(times) - 1 and len(times) > 1
        for chat_id in chats:
            if db.was_sent(chat_id, kind, today):
                continue
            if kind not in cache:
                image = None
                if is_first:
                    text = morning_text(db, today, config.month_style)
                elif is_last and is_week_end:
                    text = weekly_text(db, today, config.week_start, config.month_style)
                    image = _weekly_image(db, today, config)
                else:
                    text = reminder_text(db, today, at.hour, config.month_style, final=is_last)
                cache[kind] = (text, image)
            text, image = cache[kind]
            out.append(Outgoing(chat_id, kind, text or "", image))
    return out


def _weekly_image(db: Database, today: date, config: Config) -> bytes | None:
    try:
        from .image import render_week

        start, _ = week_bounds(today + timedelta(days=1), config.week_start)
        tasks = db.tasks_between(start, start + timedelta(days=6))
        return render_week(tasks, start=start, today=today, title="جدول الأسبوع القادم", style=config.month_style)
    except Exception:  # الصورة إضافة؛ إذا فشلت يكفي النص
        import logging

        logging.getLogger(__name__).warning("weekly image failed", exc_info=True)
        return None
