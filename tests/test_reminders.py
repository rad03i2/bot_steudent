from datetime import date, datetime, timedelta

from study_bot.config import Config
from study_bot.db import Database
from study_bot.reminders import due_messages, morning_text, reminder_text, weekly_text

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


MON = date(2026, 11, 23)  # يوم عمل
THU = date(2026, 11, 26)
SAT = date(2026, 11, 21)  # عطلة


def test_workday_reminders_are_sent_even_when_empty():
    text = morning_text(make_db(), THU)  # الخميس: محاضرات وماكو مهام
    assert "صباح الخير" in text and "تلوث التربة" in text and "ماكو مهام اليوم" in text
    day = reminder_text(make_db(), MON, 15)
    assert "ماكو مهام اليوم" in day and "ماكو مهام باجر" in day
    night = reminder_text(make_db(), MON, 21, final=True)
    assert "ماكو مهام باجر" in night and "محاضرات باجر" in night


def test_holiday_reminders_skip_when_empty_but_send_with_tasks():
    # الجمعة والسبت: ماكو شي = لا يُرسل
    for d in (FRI, SAT):
        assert morning_text(make_db(), d) is None
        assert reminder_text(make_db(), d, 15) is None
        assert reminder_text(make_db(), d, 18) is None
    assert reminder_text(make_db(), FRI, 21, final=True) is None
    # لكن إذا فيه مهمة اليوم أو باجر يُرسل
    db = make_db()
    add(db, "quiz", "إحصاء", SAT)
    assert "إحصاء" in reminder_text(db, FRI, 18)  # باجر السبت
    assert "إحصاء" in reminder_text(db, SAT, 15)
    assert "إحصاء" in morning_text(db, SAT)


def test_reminder_mentions_today_and_tomorrow_only():
    db = make_db()
    add(db, "exam", "فيزياء", FRI)
    add(db, "quiz", "إحصاء", FRI + timedelta(days=1))
    add(db, "quiz", "برمجة", FRI + timedelta(days=2))
    text = reminder_text(db, FRI, 15)
    assert "تذكير العصر" in text and "فيزياء" in text and "إحصاء" in text and "برمجة" not in text
    assert "تذكير المغرب" in reminder_text(db, FRI, 18)
    # يوم عمل وماكو مهام: التذكير يُرسل ويقول ذلك
    empty = reminder_text(make_db(), MON, 15)
    assert "ماكو مهام اليوم" in empty and "ماكو مهام باجر" in empty


def test_final_reminder_is_tomorrow_only_with_lectures():
    db = make_db()
    add(db, "exam", "فيزياء", FRI)  # اليوم: لا يظهر بتذكير الليل
    add(db, "quiz", "إحصاء", FRI + timedelta(days=1))
    sat = FRI + timedelta(days=1)
    text = reminder_text(db, FRI, 21, final=True)
    assert "تذكير الليل" in text and "إحصاء" in text and "فيزياء" not in text
    # الأحد فيه محاضرات: تظهر بتذكير ليلة السبت
    text = reminder_text(db, sat, 21, final=True)
    assert "محاضرات باجر" in text and "تصاميم انشائية" in text


def test_weekly_summary_covers_next_week():
    db = make_db()
    add(db, "exam", "فيزياء", date(2026, 11, 26))
    sat = date(2026, 11, 21)  # آخر يوم بالأسبوع (الأسبوع يبدأ الأحد)
    text = weekly_text(db, sat, week_start=6)
    assert "ملخص الأسبوع" in text and "22/11 – 28/11" in text and "فيزياء" in text and "محاضرات باجر" in text


def test_four_daily_slots_and_dedup():
    db = make_db()
    cfg = Config()
    assert cfg.reminder_times == ("06:00", "15:00", "18:00", "21:00")
    add(db, "homework", "رياضيات", FRI)
    add(db, "quiz", "إحصاء", FRI + timedelta(days=1))
    assert due_messages(db, at(FRI, 5, 59), cfg) == []
    kinds = {}
    for hh, mm in [(6, 3), (15, 1), (18, 2), (21, 4)]:
        msgs = due_messages(db, at(FRI, hh, mm), cfg)
        assert len(msgs) == 1 and msgs[0].text, (hh, mm)
        kinds[hh] = msgs[0].kind
        db.mark_sent(CHAT, msgs[0].kind, FRI)
        assert due_messages(db, at(FRI, hh, mm + 4), cfg) == []  # لا تكرار
    assert kinds == {6: "r0600", 15: "r1500", 18: "r1800", 21: "r2100"}
    # بعد انتهاء نافذة الإرسال (90 دقيقة) لا نرسل تذكيراً متأخراً
    assert due_messages(make_db(), at(FRI, 11, 0), cfg) == []
    assert due_messages(make_db(), at(FRI, 16, 45), cfg) == []
    # يوم عمل فاضي من المهام: التذكيرات تُرسل (نص غير فارغ)
    assert all(m.text for m in due_messages(make_db(), at(MON, 6, 2), cfg))
    # الجمعة فاضية: ملخص الصباح يُتجاهل (نص فارغ = لا يُرسل)
    assert [m.text for m in due_messages(make_db(), at(FRI, 6, 2), cfg)] == [""]


def test_last_slot_on_week_end_sends_weekly_with_image():
    db = make_db()
    sat = date(2026, 11, 21)
    msgs = due_messages(db, datetime(sat.year, sat.month, sat.day, 21, 5), Config())
    assert len(msgs) == 1 and "ملخص الأسبوع" in msgs[0].text
    assert msgs[0].image and msgs[0].image.startswith(b"\x89PNG")
    # يوم عادي وماكو مهام: التذكير يُرسل مع "ماكو مهام"
    msgs = due_messages(db, at(FRI - timedelta(days=1), 15, 5), Config())
    assert len(msgs) == 1 and "ماكو مهام" in msgs[0].text


def test_disabled_chat_and_config():
    db = make_db()
    add(db, "homework", "رياضيات", FRI)
    db.set_reminders(CHAT, False)
    assert due_messages(db, at(FRI, 7, 1), Config()) == []
    db.set_reminders(CHAT, True)
    assert due_messages(db, at(FRI, 7, 1), Config(reminders=False)) == []
    custom = Config(reminder_times=("06:30",))
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
            return datetime(2026, 11, 20, 6, 5, tzinfo=tz)

    monkeypatch.setattr(telegram_bot, "datetime", _FixedDatetime)
    bot = _FakeBot(fail_for={-200})
    await telegram_bot.reminders_job(_FakeContext(service, bot))
    assert [c for c, _ in bot.sent] == [CHAT]
    assert db.was_sent(CHAT, "r0600", FRI)
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
