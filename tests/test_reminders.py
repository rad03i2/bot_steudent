from datetime import date, datetime, timedelta

from study_bot.config import Config
from study_bot.db import Database
from study_bot.reminders import due_messages, evening_text, morning_text, weekly_text

FRI = date(2026, 11, 20)  # الجمعة
CHAT = -100


def make_db():
    db = Database(":memory:")
    db.register_chat(CHAT, "الدفعة")
    return db


def add(db, kind, subject, d, title=""):
    return db.add_task(kind=kind, subject=subject, title=title, due_date=d, due_time=None, notes=None,
                       chat_id=CHAT, user_id=1, user_name="أحمد")


def at(d, hh, mm=0):
    return datetime(d.year, d.month, d.day, hh, mm)


def test_morning_lists_today_and_heads_up():
    db = make_db()
    add(db, "homework", "رياضيات", FRI, "تمارين 5")
    add(db, "exam", "فيزياء", FRI + timedelta(days=3))       # ضمن 3 أيام
    add(db, "exam", "كيمياء", FRI + timedelta(days=5))       # بعيد
    add(db, "final", "أحياء", FRI + timedelta(days=10))      # فاينل: تنبيه قبل 14 يوماً
    text = morning_text(db, FRI)
    assert "صباح الخير" in text and "واجب رياضيات" in text
    assert "امتحان فيزياء" in text and "باقي 3 أيام" in text
    assert "كيمياء" not in text
    assert "امتحان فاينل أحياء" in text


def test_morning_empty_day_returns_none():
    assert morning_text(make_db(), FRI) is None


def test_evening_mentions_tomorrow_only():
    db = make_db()
    add(db, "quiz", "إحصاء", FRI + timedelta(days=1))
    add(db, "quiz", "برمجة", FRI + timedelta(days=2))
    text = evening_text(db, FRI)
    assert "عدكم باجر" in text and "إحصاء" in text and "برمجة" not in text
    assert evening_text(db, FRI + timedelta(days=5)) is None


def test_weekly_summary_covers_next_week():
    db = make_db()
    add(db, "exam", "فيزياء", date(2026, 11, 26))
    sat = date(2026, 11, 21)  # آخر يوم بالأسبوع (الأسبوع يبدأ الأحد)
    text = weekly_text(db, sat, week_start=6)
    assert "ملخص الأسبوع" in text and "22 – 28 تشرين الثاني" in text and "فيزياء" in text


def test_due_messages_timing_and_dedup():
    db = make_db()
    cfg = Config()
    add(db, "homework", "رياضيات", FRI)
    assert due_messages(db, at(FRI, 6, 59), cfg) == []
    msgs = due_messages(db, at(FRI, 7, 3), cfg)
    assert [(m.chat_id, m.kind) for m in msgs] == [(CHAT, "morning")] and msgs[0].text
    db.mark_sent(CHAT, "morning", FRI)
    assert due_messages(db, at(FRI, 7, 8), cfg) == []
    # بعد انتهاء نافذة الإرسال (3 ساعات) لا نرسل ملخص الصباح متأخراً
    db2 = make_db()
    add(db2, "homework", "رياضيات", FRI)
    assert due_messages(db2, at(FRI, 11, 0), cfg) == []


def test_evening_on_week_end_sends_weekly():
    db = make_db()
    sat = date(2026, 11, 21)
    msgs = due_messages(db, at(sat, 20, 1), Config())
    assert len(msgs) == 1 and "ملخص الأسبوع" in msgs[0].text
    # يوم عادي بدون مواعيد بالغد: رسالة فارغة (تُسجّل ولا تُرسل)
    msgs = due_messages(db, at(FRI, 20, 1), Config())
    assert len(msgs) == 1 and msgs[0].text == ""


def test_disabled_chat_and_config():
    db = make_db()
    add(db, "homework", "رياضيات", FRI)
    db.set_reminders(CHAT, False)
    assert due_messages(db, at(FRI, 7, 1), Config()) == []
    db.set_reminders(CHAT, True)
    assert due_messages(db, at(FRI, 7, 1), Config(reminders=False)) == []
    custom = Config(morning_time="06:30")
    assert due_messages(db, at(FRI, 6, 35), custom)


class _FakeBot:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    async def send_message(self, chat_id, text, **kw):
        from telegram.error import Forbidden

        if chat_id in self.fail_for:
            raise Forbidden("bot was kicked")
        self.sent.append((chat_id, text))


class _FakeContext:
    def __init__(self, service, bot):
        self.application = type("App", (), {"bot_data": {"service": service}})()
        self.bot = bot


async def test_reminders_job_sends_and_handles_kicked_bot(monkeypatch):
    from study_bot import telegram_bot
    from study_bot.service import StudyService

    db = make_db()
    db.register_chat(-200, "قروب قديم")
    add(db, "homework", "رياضيات", FRI)
    service = StudyService(db, None, Config())

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 20, 7, 5, tzinfo=tz)

    monkeypatch.setattr(telegram_bot, "datetime", _FixedDatetime)
    bot = _FakeBot(fail_for={-200})
    await telegram_bot.reminders_job(_FakeContext(service, bot))
    assert [c for c, _ in bot.sent] == [CHAT]
    assert db.was_sent(CHAT, "morning", FRI)
    assert -200 not in db.reminder_chats()  # التذكيرات توقفت للقروب الذي حذف البوت

    await telegram_bot.reminders_job(_FakeContext(service, bot))
    assert len(bot.sent) == 1  # لا تكرار


def test_groups_backfilled_from_existing_tasks(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    db.add_task(kind="quiz", subject="إحصاء", title="", due_date=FRI, due_time=None, notes=None,
                chat_id=-555, user_id=1, user_name="أحمد")
    db.add_task(kind="quiz", subject="إحصاء", title="", due_date=FRI, due_time=None, notes=None,
                chat_id=777, user_id=1, user_name="أحمد")  # محادثة خاصة: لا تُشترك تلقائياً
    db.conn.execute("DELETE FROM chats")
    db.conn.commit()
    reopened = Database(path)
    assert reopened.reminder_chats() == [-555]
