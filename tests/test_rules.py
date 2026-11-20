"""اختبارات المحلّل المجاني (بدون أي API)."""

from datetime import date

import pytest

from study_bot.config import Config
from study_bot.db import Database
from study_bot.llm import AnalysisRequest
from study_bot.rules import RuleBasedAnalyzer, analyze_message
from study_bot.service import Sender, StudyService

TODAY = date(2026, 11, 20)  # الجمعة


def analyze(message, known=(), upcoming=(), direct=False, replied_to=None):
    return analyze_message(AnalysisRequest(
        message=message, today=TODAY, week_start=6, sender_name="أحمد",
        known_subjects=list(known), upcoming=list(upcoming), replied_to=replied_to, direct=direct,
    ))


def tasks_of(message, **kw):
    a = analyze(message, **kw)
    assert a.intent == "add", (message, a)
    return [(t.kind, t.subject, t.title, t.date, t.time) for t in a.tasks]


@pytest.mark.parametrize(
    "message, expected",
    [
        ("يا شباب الخميس الجاي امتحان فيزياء على الفصل الثالث والرابع",
         [("exam", "فيزياء", "الفصل الثالث والرابع", "2026-11-26", None)]),
        ("واجب رياضيات تمارين 4.1 لـ 4.5 تسليمه باجر الساعة 11 بالليل",
         [("homework", "رياضيات", "تمارين 4.1 ل 4.5", "2026-11-21", "23:00")]),
        ("الميد مال البرمجة 3/12", [("midterm", "برمجة", "", "2026-12-03", None)]),
        ("ترى باجر عدنا كويز الكيمياء الساعة 9 الصبح", [("quiz", "كيمياء", "", "2026-11-21", "09:00")]),
        ("امتحان الفاينل للفيزياء يوم 10 كانون الثاني", [("final", "فيزياء", "", "2027-01-10", None)]),
        ("باجر كويز Physics chapter 3", [("quiz", "Physics", "chapter 3", "2026-11-21", None)]),
        ("الخميس الجاي امتحان فيزياء وكويز رياضيات",
         [("exam", "فيزياء", "", "2026-11-26", None), ("quiz", "رياضيات", "", "2026-11-26", None)]),
        ("الأحد: تقرير مختبر الأحياء، والثلاثاء برزنتيشن هندسة برمجيات",
         [("report", "أحياء", "", "2026-11-22", None),
          ("presentation", "هندسة برمجيات", "", "2026-11-24", None)]),
        ("الدكتور قال الميد يوم 7/12 الساعة 10", [("midterm", "غير محددة", "", "2026-12-07", "10:00")]),
        ("كويز بالفيزياء الأربعاء الساعة 2", [("quiz", "فيزياء", "", "2026-11-25", "14:00")]),
    ],
)
def test_add_messages(message, expected):
    assert tasks_of(message) == expected


def test_known_subject_is_reused():
    assert tasks_of("كويز physics الأحد", known=["physics"])[0][1] == "physics"
    assert tasks_of("باجر واجب بالرياضيات", known=["رياضيات"])[0][1] == "رياضيات"


@pytest.mark.parametrize(
    "message, start, end, kinds",
    [
        ("شنو عدنا الأسبوع الجاي؟", "2026-11-22", "2026-11-28", None),
        ("شكو باجر", "2026-11-21", "2026-11-21", None),
        ("مهام يوم الأربعاء", "2026-11-25", "2026-11-25", None),
        ("وش علينا هالأسبوع", "2026-11-20", "2026-11-21", None),
        ("شنو عدنا", "2026-11-20", "2026-12-04", None),
        ("متى الفاينل؟", "2026-11-20", "2027-05-19", ["final"]),
        ("شنو الامتحانات الجاية؟", "2026-11-20", "2026-12-04", ["exam", "final", "midterm", "quiz"]),
    ],
)
def test_queries(message, start, end, kinds):
    a = analyze(message)
    assert a.intent == "query"
    assert (a.query.start_date, a.query.end_date, a.query.kinds) == (start, end, kinds)


@pytest.mark.parametrize(
    "message",
    ["امتحان الخميس كان صعب", "هههه صدك", "صباح الخير", "عدنا امتحان فيزياء", "الكويز الماضي جان سهل"],
)
def test_ignored_in_group(message):
    assert analyze(message).intent == "ignore"


def test_missing_date_asks_in_private():
    a = analyze("عدنا امتحان فيزياء", direct=True)
    assert a.intent == "clarify" and "متى" in a.reply


# ---------------------------------------------------------------------------
# سيناريو كامل عبر الخدمة
# ---------------------------------------------------------------------------

AHMED = Sender(1, "أحمد")
SARA = Sender(2, "سارة")


def make_service():
    db = Database(":memory:")
    return StudyService(db, RuleBasedAnalyzer(), Config(), today_fn=lambda: TODAY), db


async def say(svc, text, who=AHMED, private=False, replied_to=None):
    return await svc.handle_message(text, who, chat_id=1, is_private=private, replied_to=replied_to)


@pytest.mark.asyncio
async def test_full_flow_free_mode():
    svc, db = make_service()

    r = await say(svc, "الخميس الجاي امتحان فيزياء الفصل 3 و 4")
    assert "تمت إضافة امتحان" in r[0].text and "26 تشرين الثاني" in r[0].text

    # طالبة ثانية تذكر نفس الموعد بصيغة مختلفة -> توثيق بدون تكرار
    r = await say(svc, "ترى يوم الخميس عدنا امتحان الفيزياء", SARA)
    assert "مسجّل مسبقاً" in r[0].text and "مؤكد من طالبين" in r[0].text

    await say(svc, "باجر واجب رياضيات تمارين 5")
    assert len(db.tasks_between(TODAY, date(2027, 1, 1))) == 2

    # تأجيل
    r = await say(svc, "امتحان الفيزياء تأجل للأحد الجاي")
    assert "تم تعديل" in r[0].text and "كان سابقاً" in r[0].text
    physics = [t for t in db.tasks_between(TODAY, date(2027, 1, 1)) if t.subject == "فيزياء"][0]
    assert physics.due_date == date(2026, 11, 22)

    # سؤال
    r = await say(svc, "شنو عدنا الأسبوع الجاي؟", SARA)
    assert "جدول الأسبوع القادم" in r[0].text and "فيزياء" in r[0].text and "رياضيات" not in r[0].text

    # إلغاء (يطلب تأكيد بالأزرار)
    r = await say(svc, "انلغى واجب الرياضيات")
    assert "تريد تحذف" in r[0].text
    data = r[0].buttons[0][0].data
    svc.handle_callback(data, AHMED)
    assert [t.subject for t in db.tasks_between(TODAY, date(2027, 1, 1))] == ["فيزياء"]


@pytest.mark.asyncio
async def test_update_by_replying_to_card():
    svc, db = make_service()
    r = await say(svc, "الأربعاء كويز كيمياء")
    card = r[0].text
    r = await say(svc, "تأجل للخميس الساعة 10", replied_to=card)
    task = db.tasks_between(TODAY, date(2027, 1, 1))[0]
    assert task.due_date == date(2026, 11, 26) and task.due_time == "10:00"


@pytest.mark.asyncio
async def test_ambiguous_update_asks_which():
    svc, db = make_service()
    await say(svc, "الأحد كويز كيمياء")
    await say(svc, "الأربعاء كويز كيمياء")
    r = await say(svc, "كويز الكيمياء تأجل للخميس", private=True)
    assert "أكثر من موعد" in r[0].text
