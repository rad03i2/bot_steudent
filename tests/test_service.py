from datetime import date

import pytest

from study_bot.config import Config
from study_bot.db import Database, subject_key
from study_bot.llm import Analysis, ExtractedTask, QuerySpec, TaskChanges
from study_bot.service import Sender, StudyService, looks_relevant

TODAY = date(2026, 11, 20)  # الجمعة
AHMED = Sender(1, "أحمد")
SARA = Sender(2, "سارة")


class FakeAnalyzer:
    def __init__(self):
        self.next: Analysis | None = None
        self.requests: list = []

    async def analyze(self, req) -> Analysis | None:
        self.requests.append(req)
        return self.next


def make_service(analyzer=None):
    db = Database(":memory:")
    svc = StudyService(db, analyzer, Config(), today_fn=lambda: TODAY)
    return svc, db


def add_analysis(**kw) -> Analysis:
    task = dict(kind="exam", subject="اساسيات النفايات الخطرة", title="الفصل 3 و 4",
                date_expression="الخميس الجاي", date="2026-11-26", time=None, notes=None, external=False)
    task.update(kw)
    return Analysis(intent="add", tasks=[ExtractedTask(**task)], target_task_id=None, changes=None,
                    query=None, reply=None)


@pytest.mark.asyncio
async def test_add_task_uses_code_date_over_llm():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    # النموذج أخطأ في التاريخ، والكود يصححه
    fake.next = add_analysis(date="2026-11-27")
    replies = await svc.handle_message("الخميس الجاي امتحان فيزياء الفصل 3 و 4", AHMED,
                                       chat_id=-100, is_private=False)
    assert len(replies) == 1
    assert "الخميس 26/11" in replies[0].text and "تشرين" not in replies[0].text
    assert "باقي 6 أيام" in replies[0].text
    tasks = db.tasks_between(TODAY, date(2026, 12, 31))
    assert [(t.subject, t.due_date) for t in tasks] == [("اساسيات النفايات الخطرة", date(2026, 11, 26))]
    assert fake.requests[0].today == TODAY


@pytest.mark.asyncio
async def test_duplicate_is_confirmed_not_duplicated():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    fake.next = add_analysis()
    await svc.handle_message("الخميس امتحان فيزياء", AHMED, chat_id=1, is_private=False)
    fake.next = add_analysis(subject="النفايات الخطرة")
    replies = await svc.handle_message("ترى الخميس امتحان الفيزياء", SARA, chat_id=1, is_private=False)
    assert "مسجّل مسبقاً" in replies[0].text
    assert "مؤكد من طالبين" in replies[0].text
    assert len(db.tasks_between(TODAY, date(2026, 12, 31))) == 1


@pytest.mark.asyncio
async def test_conflict_warning():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    fake.next = add_analysis()
    await svc.handle_message("الخميس امتحان فيزياء", AHMED, chat_id=1, is_private=False)
    fake.next = add_analysis(date_expression="الأربعاء", date="2026-11-25")
    replies = await svc.handle_message("لا الامتحان الأربعاء", SARA, chat_id=1, is_private=False)
    assert "انتبهوا" in replies[0].text
    assert any(b.data.startswith("d:") for row in replies[0].buttons for b in row)


@pytest.mark.asyncio
async def test_past_date_rejected():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    fake.next = add_analysis(date_expression="15/11", date="2026-11-15")
    replies = await svc.handle_message("15/11 امتحان", AHMED, chat_id=1, is_private=False)
    assert "ماضي" in replies[0].text
    assert db.tasks_between(date(2026, 1, 1), date(2027, 1, 1)) == []


@pytest.mark.asyncio
async def test_update_postpones_task():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    fake.next = add_analysis()
    await svc.handle_message("الخميس امتحان فيزياء", AHMED, chat_id=1, is_private=False)
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    fake.next = Analysis(
        intent="update", tasks=[], target_task_id=task_id,
        changes=TaskChanges(kind=None, subject=None, title=None, date_expression="الأحد الجاي",
                            date=None, time="10:00", notes=None),
        query=None, reply=None,
    )
    replies = await svc.handle_message("امتحان الفيزياء تأجل للأحد الجاي الساعة 10", AHMED,
                                       chat_id=1, is_private=False)
    assert "تم تعديل" in replies[0].text
    assert "كان سابقاً" in replies[0].text
    task = db.get_task(task_id)
    assert task.due_date == date(2026, 11, 22) and task.due_time == "10:00"


@pytest.mark.asyncio
async def test_delete_flow_via_buttons():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    fake.next = add_analysis()
    await svc.handle_message("الخميس امتحان فيزياء", AHMED, chat_id=1, is_private=False)
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    fake.next = Analysis(intent="delete", tasks=[], target_task_id=task_id, changes=None, query=None, reply=None)
    replies = await svc.handle_message("انلغى امتحان الفيزياء", AHMED, chat_id=1, is_private=False)
    assert "تريد تحذف" in replies[0].text
    result = svc.handle_callback(f"dy:{task_id}", SARA)
    assert "تم حذف" in result.edit.text
    assert db.get_task(task_id) is None


@pytest.mark.asyncio
async def test_query_schedule():
    fake = FakeAnalyzer()
    svc, db = make_service(fake)
    for kw in [
        dict(),
        dict(kind="homework", subject="تقييم الأثر البيئي", title="تمارين 4.1", date_expression="الأحد", date=None),
        dict(kind="project", subject="المشروع الهندسي 1", title="المرحلة الأولى", date_expression="26/11", date=None),
        dict(kind="final", subject="تلوث التربة والمياه الجوفية", title="", date_expression="10/1", date=None),
    ]:
        fake.next = add_analysis(**kw)
        await svc.handle_message("x امتحان", AHMED, chat_id=1, is_private=False)

    fake.next = Analysis(
        intent="query", tasks=[], target_task_id=None, changes=None, reply=None,
        query=QuerySpec(start_date="2026-11-22", end_date="2026-11-28", subject=None, kinds=None,
                        title="جدول الأسبوع القادم", only_mine=False),
    )
    replies = await svc.handle_message("شنو عدنا الأسبوع الجاي؟", SARA, chat_id=1, is_private=False)
    text = replies[0].text
    assert "جدول الأسبوع القادم" in text
    assert "22/11 – 28/11" in text and "تشرين" not in text
    assert "الأحد 22/11" in text and "الخميس 26/11" in text
    assert "يوم ثقيل" in text  # امتحان + مشروع بنفس اليوم
    assert "تلوث التربة" not in text  # الفاينل خارج النطاق
    assert "المجموع: 3 مهام" in text
    # الامتحان يظهر قبل المشروع في نفس اليوم (أولوية أعلى)
    assert text.index("امتحان اساسيات النفايات") < text.index("مشروع المشروع الهندسي")


def test_schedule_commands_and_empty():
    svc, db = make_service()
    assert "لا توجد أي مهام" in svc.schedule_reply("today").text
    db.add_task(kind="final", subject="كيمياء", title="", due_date=date(2027, 1, 10), due_time=None,
                notes=None, chat_id=1, user_id=1, user_name="أحمد")
    assert "كيمياء" in svc.schedule_reply("exams").text
    assert "كيمياء" not in svc.schedule_reply("nextweek").text


@pytest.mark.asyncio
async def test_group_prefilter_skips_chitchat():
    fake = FakeAnalyzer()
    svc, _ = make_service(fake)
    replies = await svc.handle_message("هههههه صدك", AHMED, chat_id=1, is_private=False)
    assert replies == [] and fake.requests == []


@pytest.mark.asyncio
async def test_no_ai_private_message_explains():
    svc, _ = make_service(None)
    replies = await svc.handle_message("الخميس امتحان", AHMED, chat_id=1, is_private=True)
    assert "/today" in replies[0].text


def test_confirm_button_counts_once():
    svc, db = make_service()
    t = db.add_task(kind="quiz", subject="إحصاء", title="", due_date=date(2026, 11, 24), due_time="09:30",
                    notes=None, chat_id=1, user_id=1, user_name="أحمد")
    r1 = svc.handle_callback(f"c:{t.id}", SARA)
    r2 = svc.handle_callback(f"c:{t.id}", SARA)
    assert "تم تسجيل" in r1.answer and "سبق" in r2.answer
    assert db.get_task(t.id).confirmations == 2
    assert "9:30 ص" in r1.edit.text


def test_subject_key_and_relevance():
    assert subject_key("الفيزياء") == subject_key("فيزياء")
    assert looks_relevant("باجر عدنا كويز")
    assert looks_relevant("الامتحان 26/11")
    assert not looks_relevant("صباح الخير شباب")
