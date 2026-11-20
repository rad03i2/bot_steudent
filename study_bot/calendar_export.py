"""تصدير المواعيد إلى ملف تقويم (.ics) يفتحه iPhone و Android و Google Calendar.

- الموعد بدون وقت يُضاف كحدث ليوم كامل، مع تنبيه الساعة 8 مساء اليوم السابق.
- الموعد بوقت يُضاف كحدث مدته ساعة، مع تنبيه قبله بساعتين.
- لكل مهمة معرّف ثابت (UID)، فإعادة الاستيراد تحدّث المواعيد بدل تكرارها في أغلب التطبيقات.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Task

PRODID = "-//study-bot//مذكر الدفعة//AR"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545: السطر لا يتجاوز 75 بايت؛ الباقي يُكمل بسطر يبدأ بمسافة."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(current) + len(encoded) > limit:
            out.append(current.decode("utf-8"))
            current = b""
        current += encoded
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def build_ics(tasks: list[Task], tz: ZoneInfo, *, now: datetime | None = None, calendar_name: str = "مذكر الدفعة") -> bytes:
    stamp = (now or datetime.now(tz)).astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]
    for t in tasks:
        summary = f"{t.info.label} {t.subject}"
        if t.title:
            summary += f" – {t.title}"
        description = [f"النوع: {t.info.label}", f"المادة: {t.subject}"]
        if t.title:
            description.append(f"المحتوى: {t.title}")
        if t.notes:
            description.append(f"ملاحظات: {t.notes}")
        description.append(f"أضافه: {t.created_by_name}")

        lines += [
            "BEGIN:VEVENT",
            f"UID:task-{t.id}@study-bot",
            f"DTSTAMP:{stamp}",
        ]
        if t.due_time:
            hh, mm = (int(x) for x in t.due_time.split(":")[:2])
            start = datetime(t.due_date.year, t.due_date.month, t.due_date.day, hh, mm, tzinfo=tz)
            utc = ZoneInfo("UTC")
            lines += [
                f"DTSTART:{start.astimezone(utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{(start + timedelta(hours=1)).astimezone(utc).strftime('%Y%m%dT%H%M%SZ')}",
            ]
            trigger = "-PT2H"
        else:
            lines += [
                f"DTSTART;VALUE=DATE:{t.due_date.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(t.due_date + timedelta(days=1)).strftime('%Y%m%d')}",
            ]
            trigger = "-PT4H"  # الساعة 8 مساء اليوم السابق
        lines += [
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(chr(10).join(description))}",
            f"CATEGORIES:{_escape(t.info.label)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(summary)}",
            f"TRIGGER:{trigger}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8")


def export_range(today: date) -> tuple[date, date]:
    """نصدّر من اليوم ولمدة ستة أشهر."""
    return today, today + timedelta(days=180)
