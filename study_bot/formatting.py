"""تنسيق رسائل البوت (بطاقات المهام والجداول) بصيغة HTML الخاصة بتيليجرام.

الجداول ذات الأعمدة تتلخبط في تيليجرام مع النص العربي، لذلك نستخدم قوائم
منظمة بالإيموجي، وهي أوضح بكثير على شاشة الجوال.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from .arabic import MONTHS_LEVANTINE, MONTHS_STANDARD, WEEKDAY_NAMES_AR
from .dates import days_left_phrase, relative_day_label
from .models import PRIORITY_DOTS, Task

SEP = "━━━━━━━━━━━━━━━━━━"


def month_name(month: int, style: str = "levantine") -> str:
    names = MONTHS_LEVANTINE if style == "levantine" else MONTHS_STANDARD
    return names[month - 1]


def long_date(d: date, style: str = "levantine", with_year: bool = False) -> str:
    text = f"{WEEKDAY_NAMES_AR[d.weekday()]} {d.day} {month_name(d.month, style)}"
    return f"{text} {d.year}" if with_year else text


def range_title(start: date, end: date, style: str = "levantine") -> str:
    if start == end:
        return long_date(start, style, with_year=True)
    if start.month == end.month and start.year == end.year:
        return f"{start.day} – {end.day} {month_name(end.month, style)} {end.year}"
    if start.year == end.year:
        return f"{start.day} {month_name(start.month, style)} – {end.day} {month_name(end.month, style)} {end.year}"
    return f"{start.day} {month_name(start.month, style)} {start.year} – {end.day} {month_name(end.month, style)} {end.year}"


def _time_text(t: str | None) -> str | None:
    if not t:
        return None
    try:
        hh, mm = (int(x) for x in t.split(":")[:2])
    except ValueError:
        return t
    period = "ص" if hh < 12 else "م"
    h12 = hh % 12 or 12
    return f"{h12}:{mm:02d} {period}"


def task_card(task: Task, today: date, *, header: str, style: str = "levantine") -> str:
    info = task.info
    lines = [f"<b>{escape(header)}</b>", SEP]
    lines.append(f"📘 المادة: <b>{escape(task.subject)}</b>")
    lines.append(f"{info.icon} النوع: {escape(info.label)}")
    date_line = f"📅 الموعد: <b>{long_date(task.due_date, style, with_year=True)}</b>"
    rel = relative_day_label(task.due_date, today)
    if rel:
        date_line += f" ({rel})"
    lines.append(date_line)
    if task.due_time:
        lines.append(f"⏰ الساعة: {_time_text(task.due_time)}")
    if task.title:
        lines.append(f"📖 المحتوى: {escape(task.title)}")
    if task.notes:
        lines.append(f"📝 ملاحظات: {escape(task.notes)}")
    lines.append(f"⏳ {days_left_phrase(task.due_date, today)}")
    confirm = "✅ مؤكد من " + _students(task.confirmations) if task.confirmations > 1 else ""
    lines.append(f"👤 أضافه: {escape(task.created_by_name or 'طالب')}" + (f"  •  {confirm}" if confirm else ""))
    if task.done_count:
        lines.append(f"✔️ أنجزها: {_students(task.done_count)}")
    lines.append(f"<i>#{task.id}</i>")
    return "\n".join(lines)


def _students(n: int) -> str:
    if n == 1:
        return "طالب واحد"
    if n == 2:
        return "طالبين"
    if 3 <= n <= 10:
        return f"{n} طلاب"
    return f"{n} طالب"


def task_line(task: Task) -> str:
    info = task.info
    main = f"{info.icon} <b>{escape(info.label)} {escape(task.subject)}</b>"
    if task.title:
        main += f" – {escape(task.title)}"
    extra = []
    if task.due_time:
        extra.append(f"⏰ {_time_text(task.due_time)}")
    if task.notes:
        extra.append(f"📝 {escape(task.notes)}")
    if task.confirmations > 1:
        extra.append(f"✅×{task.confirmations}")
    line = f"   {main}"
    if extra:
        line += "\n      " + "  •  ".join(extra)
    return line


def _is_heavy(day_tasks: list[Task]) -> bool:
    important = sum(1 for t in day_tasks if t.priority <= 2)
    return important >= 2 or len(day_tasks) >= 3


def sort_key(t: Task):
    return (t.priority, t.due_time or "99:99", t.id)


def schedule(
    tasks: list[Task],
    *,
    title: str,
    start: date,
    end: date,
    today: date,
    style: str = "levantine",
) -> str:
    lines = [f"🗓 <b>{escape(title)}</b>", f"   {range_title(start, end, style)}", SEP]

    if not tasks:
        lines.append("")
        lines.append("🎉 لا توجد أي مهام في هذه الفترة. استمتعوا!")
        return "\n".join(lines)

    by_day: dict[date, list[Task]] = defaultdict(list)
    for t in tasks:
        by_day[t.due_date].append(t)

    for d in sorted(by_day):
        day_tasks = sorted(by_day[d], key=sort_key)
        heavy = _is_heavy(day_tasks)
        top = min(t.priority for t in day_tasks)
        marker = "🔴" if heavy else PRIORITY_DOTS.get(top, "🔹") if top <= 1 else "🔹"
        header = f"{marker} <b>{WEEKDAY_NAMES_AR[d.weekday()]} {d.day}/{d.month}</b>"
        rel = relative_day_label(d, today)
        if rel:
            header += f" ({rel})"
        if heavy:
            header += "  ← يوم ثقيل! 💪"
        lines.append("")
        lines.append(header)
        lines.extend(task_line(t) for t in day_tasks)

    lines.append("")
    lines.append(SEP)
    most_important = min(tasks, key=lambda t: (t.priority, t.due_date, t.id))
    summary = f"📌 المجموع: {_count_tasks(len(tasks))}"
    if most_important.priority <= 2:
        summary += (
            f"\n🔥 الأهم: {escape(most_important.info.label)} {escape(most_important.subject)}"
            f" ({days_left_phrase(most_important.due_date, today)})"
        )
    lines.append(summary)
    return "\n".join(lines)


def _count_tasks(n: int) -> str:
    if n == 1:
        return "مهمة واحدة"
    if n == 2:
        return "مهمتان"
    if 3 <= n <= 10:
        return f"{n} مهام"
    return f"{n} مهمة"


def personal_list(
    tasks: list[Task],
    *,
    name: str,
    start: date,
    end: date,
    today: date,
    style: str = "levantine",
) -> str:
    """قائمة الطالب الشخصية: المهام التي لم يضغط عليها "خلصت" بعد."""
    if not tasks:
        return (
            f"📋 <b>مهام {escape(name)}</b>\n{SEP}\n\n"
            "🎉 ما باقي عليك شي! كلشي خالص.\nاستمر هيج 💪"
        )
    text = schedule(tasks, title=f"باقي عليك يا {name}", start=start, end=end, today=today, style=style)
    return text + "\n\n👇 اضغط على المهمة لما تخلصها"
