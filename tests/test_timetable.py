"""جدول الشعبة: المواد المعتمدة، المواد الخارجية، المحاضرات، وصورة الجدول."""

import io
from datetime import date

from PIL import Image

from study_bot.config import Config
from study_bot.db import Database
from study_bot.rules import RuleBasedAnalyzer
from study_bot.service import Sender, StudyService
from study_bot.timetable import Catalog, lectures_on, next_lecture_date

SAT = date(2026, 11, 21)  # السبت
AHMED = Sender(1, "أحمد")


def make_service(today=SAT):
    db = Database(":memory:")
    return StudyService(db, RuleBasedAnalyzer(), Config(), today_fn=lambda: today), db


async def say(svc, text, private=False):
    return await svc.handle_message(text, AHMED, chat_id=-1, is_private=private)


def all_tasks(db):
    return db.tasks_between(date(2026, 1, 1), date(2027, 12, 31))


def test_teacher_names_map_to_subjects():
    c = Catalog()
    cases = {
        "امتحان عند د عمار": "تصاميم محطات معالجة مياه الفضلات",
        "واجب سمير": "تصاميم انشائية",
        "الدكتور حامد": "اساسيات النفايات الخطرة",
        "د انس": "اساسيات النفايات الخطرة",
        "راكان قال": "ادارة المشاريع والاقتصاد الهندسي",
        "ست وعد": "تلوث التربة والمياه الجوفية",
        "طه": "تقييم الأثر البيئي",
    }
    for text, subject in cases.items():
        assert c.find(text.split()).subject == subject, text
    assert c.find("انا وعدت اخوي".split()) is None  # "وعد" بدون لقب ليست اسم أستاذ


def test_foundations_part_is_same_subject():
    m = Catalog().find("كويز اسس".split())
    assert m.subject == "تصاميم انشائية" and m.part == "أسس"


async def test_subject_outside_timetable_is_not_saved():
    svc, db = make_service()
    r = await say(svc, "الخميس الجاي امتحان فيزياء")
    assert "مو من مواد جدول الشعبة" in r[0].text and all_tasks(db) == []
    data = [b.data for row in r[0].buttons for b in row]
    assert any(d.endswith(":x") for d in data)  # خيار "مادة خارجية"

    # يختار الطالب مادة من الجدول بدلها
    pick = next(b.data for row in r[0].buttons for b in row if b.text == "تلوث التربة والمياه الجوفية")
    res = svc.handle_callback(pick, AHMED)
    assert "تمت إضافة" in res.edit.text
    assert [t.subject for t in all_tasks(db)] == ["تلوث التربة والمياه الجوفية"]


async def test_external_subject_is_saved_and_remembered():
    svc, db = make_service()
    r = await say(svc, "الخميس الجاي امتحان فيزياء مادة خارجية")
    assert "تمت إضافة" in r[0].text
    assert db.external_subjects() == ["فيزياء"]
    r = await say(svc, "الأحد كويز فيزياء")  # صارت معروفة
    assert "تمت إضافة" in r[0].text and len(all_tasks(db)) == 2


async def test_external_via_button():
    svc, db = make_service()
    r = await say(svc, "الخميس امتحان احصاء")
    ext = next(b.data for row in r[0].buttons for b in row if b.data.endswith(":x"))
    svc.handle_callback(ext, AHMED)
    assert db.external_subjects() == ["احصاء"] and len(all_tasks(db)) == 1


async def test_missing_subject_asks_with_buttons():
    svc, db = make_service()
    r = await say(svc, "باجر عدنا كويز")
    assert "أي مادة" in r[0].text and len(r[0].buttons) >= 8 and all_tasks(db) == []
    cancel = r[0].buttons[-1][0].data
    assert "ما سجلت" in svc.handle_callback(cancel, AHMED).edit.text


async def test_next_lecture_date():
    # السبت: أقرب محاضرة انشائية الأحد، وأقرب محاضرة للتربة الخميس
    assert next_lecture_date("تصاميم انشائية", SAT) == date(2026, 11, 22)
    assert next_lecture_date("تلوث التربة والمياه الجوفية", SAT) == date(2026, 11, 26)
    svc, db = make_service()
    await say(svc, "واجب ست وعد بالمحاضرة الجاية")
    assert all_tasks(db)[0].due_date == date(2026, 11, 26)


def test_lectures_per_day():
    assert [lec.subject for lec in lectures_on(date(2026, 11, 22))] == [
        "تصاميم انشائية", "تصاميم محطات معالجة مياه الفضلات", "المشروع الهندسي 1",
    ]
    assert lectures_on(date(2026, 11, 20)) == []  # الجمعة


def test_special_requests_for_lectures():
    svc, _ = make_service()
    assert svc.special_request("ابعث جدول الدروس") == "timetable"
    assert svc.special_request("شنو عدنا محاضرات باجر؟") == "lectures:2026-11-22"
    assert svc.special_request("محاضرات الخميس") == "lectures:2026-11-26"
    assert svc.special_request("الواجب بالمحاضرة الجاية") is None
    assert svc.special_request("محاضرة تعويضية الخميس") is None


def test_lectures_reply_and_holiday():
    svc, _ = make_service()
    r = svc.lectures_reply(date(2026, 11, 22))
    assert "باجر" in r.text and "8:00 – 10:00" in r.text and "د. سمير سعدي شماس" in r.text
    r = svc.lectures_reply(SAT)
    assert "ماكو محاضرات" in r.text and "الأحد" in r.text


def test_single_day_schedule_includes_lectures():
    svc, _ = make_service(today=date(2026, 11, 23))  # الاثنين
    text = svc.schedule_reply("today").text
    assert "محاضرات اليوم نفسه" in text and "تقييم الأثر البيئي" in text


def test_timetable_image():
    svc, _ = make_service()
    png, caption = svc.timetable_image()
    img = Image.open(io.BytesIO(png))
    assert img.width == 1080 and img.height > 1500 and "شعبة A" in caption


def test_bot_is_private_to_the_section_group():
    svc, db = make_service()
    # أول قروب يستخدم البوت يصير قروب الشعبة
    assert svc.access(-100, is_private=False, user_id=1, user_name="أحمد") == "ok"
    assert svc.access(-200, is_private=False, user_id=9, user_name="غريب") == "leave"
    # على الخاص: فقط من ظهر في قروب الشعبة
    assert svc.access(1, is_private=True, user_id=1, user_name="أحمد") == "ok"
    assert svc.access(9, is_private=True, user_id=9, user_name="غريب") == "stranger"


def test_home_group_inferred_from_existing_tasks():
    svc, db = make_service()
    for _ in range(3):
        db.add_task(kind="quiz", subject="تصاميم انشائية", title="", due_date=SAT, due_time=None, notes=None,
                    chat_id=-555, user_id=1, user_name="أحمد")
    assert db.home_chat() == -555
    assert svc.access(-100, is_private=False, user_id=1, user_name="أحمد") == "leave"
    assert svc.access(-555, is_private=False, user_id=1, user_name="أحمد") == "ok"


def test_allowed_chat_ids_override():
    db = Database(":memory:")
    svc = StudyService(db, None, Config(allowed_chat_ids=frozenset({-7})), today_fn=lambda: SAT)
    assert svc.access(-7, is_private=False, user_id=1, user_name="x") == "ok"
    assert svc.access(-8, is_private=False, user_id=1, user_name="x") == "leave"
