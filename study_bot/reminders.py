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

# بعد الوقت المحدد بكم ساعة نتوقف عن محاولة الإرسال (حتى لا يصل "صباح الخير" بالليل)
SEND_WINDOW = timedelta(hours=3)


@dataclass
class Outgoing:
    chat_id: int
    kind: str  # morning / evening / weekly
    text: str


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
    """ملخص الصباح: مواعيد اليوم + ما يقترب. يرجع None إذا لا يوجد شيء."""
    today_tasks = sorted(db.tasks_between(today, today), key=sort_key)
    soon = sorted(_heads_up(db, today), key=lambda t: (t.due_date, t.priority, t.id))
    if not today_tasks and not soon:
        return None

    lines = [f"☀️ <b>صباح الخير! جدول اليوم</b>", f"   {long_date(today, style)}", SEP]
    if today_tasks:
        lines.extend(task_line(t) for t in today_tasks)
    else:
        lines.append("   اليوم ماكو مواعيد 🎉")

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


def evening_text(db: Database, today: date, style: str = "levantine") -> str | None:
    """تذكير المساء بمواعيد الغد. يرجع None إذا الغد فارغ."""
    tomorrow = today + timedelta(days=1)
    tasks = sorted(db.tasks_between(tomorrow, tomorrow), key=sort_key)
    if not tasks:
        return None
    lines = [f"🌙 <b>تذكير: عدكم باجر</b>", f"   {long_date(tomorrow, style)}", SEP]
    lines.extend(task_line(t) for t in tasks)
    lines.append(SEP)
    lines.append("حضّروا زين وناموا بدري 😴")
    return "\n".join(lines)


def weekly_text(db: Database, today: date, week_start: int, style: str = "levantine") -> str:
    """ملخص الأسبوع القادم (يُرسل مساء اليوم الأخير من الأسبوع)."""
    start, _ = week_bounds(today + timedelta(days=1), week_start)
    end = start + timedelta(days=6)
    tasks = db.tasks_between(start, end)
    return "📅 <b>ملخص الأسبوع</b>\n\n" + schedule(
        tasks, title="جدول الأسبوع القادم", start=start, end=end, today=today, style=style
    )


def due_messages(db: Database, now: datetime, config: Config) -> list[Outgoing]:
    """الرسائل التي حان وقتها ولم تُرسل اليوم. now بالتوقيت المحلي."""
    if not config.reminders:
        return []
    today = now.date()
    morning_at = _parse_hhmm(config.morning_time, time(7, 0))
    evening_at = _parse_hhmm(config.evening_time, time(20, 0))
    # اليوم الأخير من الأسبوع: غداً يبدأ أسبوع جديد
    is_week_end = (today + timedelta(days=1)).weekday() == config.week_start

    out: list[Outgoing] = []
    texts: dict[str, str | None] = {}
    for chat_id in db.reminder_chats():
        if _in_window(now, morning_at) and not db.was_sent(chat_id, "morning", today):
            if "morning" not in texts:
                texts["morning"] = morning_text(db, today, config.month_style)
            out.append(Outgoing(chat_id, "morning", texts["morning"] or ""))
        if _in_window(now, evening_at) and not db.was_sent(chat_id, "evening", today):
            if "evening" not in texts:
                texts["evening"] = (
                    weekly_text(db, today, config.week_start, config.month_style)
                    if is_week_end
                    else evening_text(db, today, config.month_style)
                )
            out.append(Outgoing(chat_id, "evening", texts["evening"] or ""))
    return out
