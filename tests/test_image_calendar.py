"""المرحلة الرابعة: صورة الجدول وملف التقويم."""

import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import icalendar
from PIL import Image

from study_bot.calendar_export import build_ics
from study_bot.config import Config
from study_bot.db import Database
from study_bot.image import fit_text, render_week, text_width
from study_bot.reminders import due_messages
from study_bot.service import StudyService

TODAY = date(2026, 11, 20)
TZ = ZoneInfo("Asia/Baghdad")


def make_db():
    db = Database(":memory:")
    add = lambda k, s, d, t="", tm=None: db.add_task(  # noqa: E731
        kind=k, subject=s, title=t, due_date=d, due_time=tm, notes=None, chat_id=-1, user_id=1, user_name="أحمد"
    )
    add("homework", "رياضيات", date(2026, 11, 22), "تمارين 4.1 إلى 4.5", "23:59")
    add("exam", "فيزياء", date(2026, 11, 26), "الفصل 3, و 4; مع المراجعة", "10:00")
    add("quiz", "Physics", date(2026, 11, 25), "chapter 3")
    add("final", "هندسة برمجيات", date(2026, 11, 27), "كل المنهج " * 20)
    return db


def test_render_week_png():
    db = make_db()
    png = render_week(db.tasks_between(date(2026, 11, 22), date(2026, 11, 28)), start=date(2026, 11, 22), today=TODAY)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.width == 1080 and img.height > 1000


def test_render_empty_week():
    png = render_week([], start=date(2026, 11, 22), today=TODAY)
    assert Image.open(io.BytesIO(png)).width == 1080


def test_fit_text_truncates():
    long = "كل المنهج من الفصل الأول إلى الفصل العاشر " * 3
    short = fit_text(long, 300, 24)
    assert short.endswith("…") and text_width(short, 24) <= 300


def test_service_week_image_and_special_requests():
    svc = StudyService(make_db(), None, Config(), today_fn=lambda: TODAY)
    png, caption = svc.week_image(next_week=True)
    assert png.startswith(b"\x89PNG") and "الأسبوع القادم" in caption
    assert svc.special_request("صورة جدول الأسبوع الجاي") == "image_next"
    assert svc.special_request("ابعث صورة الجدول") == "image"
    assert svc.special_request("تقويم") == "calendar"
    assert svc.special_request("التقويم الجامعي تغير هالسنة كلش") is None
    assert svc.special_request("الخميس امتحان فيزياء") is None


def test_ics_is_valid_and_correct():
    db = make_db()
    data = build_ics(db.tasks_between(TODAY, date(2027, 1, 1)), TZ, now=datetime(2026, 11, 20, 12, tzinfo=TZ))
    cal = icalendar.Calendar.from_ical(data)
    events = {str(e["UID"]): e for e in cal.walk("VEVENT")}
    assert len(events) == 4
    exam = next(e for e in events.values() if "فيزياء" in str(e["SUMMARY"]))
    assert exam["DTSTART"].dt == datetime(2026, 11, 26, 7, 0, tzinfo=ZoneInfo("UTC"))  # 10:00 بغداد
    assert "الفصل 3, و 4; مع المراجعة" in str(exam["SUMMARY"])
    hw = next(e for e in events.values() if "رياضيات" in str(e["SUMMARY"]))
    assert hw["DTSTART"].dt.hour == 20 and hw["DTSTART"].dt.minute == 59  # 23:59 بغداد = 20:59 UTC
    final = next(e for e in events.values() if "فاينل" in str(e["SUMMARY"]))
    assert final["DTSTART"].dt == date(2026, 11, 27)  # يوم كامل
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))


def test_service_calendar_file():
    svc = StudyService(make_db(), None, Config(), today_fn=lambda: TODAY)
    data, caption = svc.calendar_file()
    assert data.startswith(b"BEGIN:VCALENDAR") and "4 موعد" in caption


def test_weekly_reminder_has_image():
    db = make_db()
    db.register_chat(-1, "الدفعة")
    sat = date(2026, 11, 21)
    msgs = due_messages(db, datetime(sat.year, sat.month, sat.day, 20, 5), Config())
    assert len(msgs) == 1 and msgs[0].image and msgs[0].image.startswith(b"\x89PNG")
    # يوم عادي: بدون صورة
    msgs = due_messages(db, datetime(2026, 11, 21 - 1, 20, 5), Config())
    assert msgs[0].image is None
