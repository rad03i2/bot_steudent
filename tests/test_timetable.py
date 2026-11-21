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
    svc, _ = make_service()  # اليوم السبت 21/11
    assert svc.special_request("ابعث جدول الدروس") == "timetable"
    # يوم واحد: صورة مقصوصة لذلك اليوم فقط
    assert svc.special_request("شنو عدنا محاضرات باجر؟") == "timetable_day:2026-11-22"
    assert svc.special_request("محاضرات الخميس") == "timetable_day:2026-11-26"
    assert svc.special_request("جدول الاثنين") == "timetable_day:2026-11-23"
    assert svc.special_request("جدول الدروس الثلاثاء") == "timetable_day:2026-11-24"
    # يوم عطلة: رد نصي
    assert svc.special_request("محاضرات الجمعة") == "lectures:2026-11-27"
    # ليست طلب جدول دروس
    assert svc.special_request("الواجب بالمحاضرة الجاية") is None
    assert svc.special_request("محاضرة تعويضية الخميس") is None
    assert svc.special_request("جدول الواجبات الخميس") is None
    assert svc.special_request("مهام الاثنين") is None
    assert svc.special_request("جدول اليوم") == "lectures:2026-11-21"  # اليوم سبت: عطلة
    # الأسبوع = صورة الجدول كاملة (بدون مهام)، وليس كتابة
    assert svc.special_request("جدول الأسبوع") == "timetable"
    assert svc.special_request("جدول الاسبوع الجاي") == "timetable"
    assert svc.special_request("شنو عندنا محاضرات باكر") == "timetable_day:2026-11-22"
    assert svc.special_request("جدول باكر") == "timetable_day:2026-11-22"
    # كلام عن محاضرة وليس طلب جدول
    assert svc.special_request("المحاضرة تأجلت للخميس") is None
    assert svc.special_request("المحاضرات الجاية كلها مراجعة ومحد يفهم شي والدكتور يشرح بسرعة كلش") is None


def test_day_timetable_image_is_cropped_to_one_day():
    svc, _ = make_service()
    full, _ = svc.timetable_image()
    day_png, caption = svc.timetable_day_image(date(2026, 11, 23))
    full_img, day_img = Image.open(io.BytesIO(full)), Image.open(io.BytesIO(day_png))
    assert day_img.width < full_img.width and day_img.height < full_img.height // 3
    assert "الاثنين 23/11" in caption and "2026" not in caption
    # الارتفاع يتبع عدد محاضرات اليوم: الأحد والأربعاء ثلاث محاضرات، الثلاثاء محاضرتان
    h = {d: Image.open(io.BytesIO(svc.timetable_day_image(d)[0])).height for d in
         (date(2026, 11, 22), date(2026, 11, 24), date(2026, 11, 25))}
    assert h[date(2026, 11, 22)] == h[date(2026, 11, 25)] > h[date(2026, 11, 24)]


def test_lectures_reply_and_holiday():
    svc, _ = make_service()
    r = svc.lectures_reply(date(2026, 11, 22))
    assert "باجر" in r.text and "8:00 – 10:00" in r.text and "د. سمير سعدي شماس" in r.text
    r = svc.lectures_reply(SAT)
    assert "ماكو محاضرات" in r.text and "الأحد" in r.text


async def test_tomorrow_question_gives_tasks_plus_lectures_without_notes():
    svc, db = make_service()  # اليوم السبت، باجر الأحد
    await say(svc, "الأحد امتحان عند د. عمار الفصل 3")
    r = await say(svc, "شنو عدنا باكر", private=True)
    text = r[0].text
    assert "مهام الغد" in text and "الأحد 22/11" in text and "امتحان تصاميم محطات" in text
    assert "محاضرات باجر" in text and "8:00 – 10:00" in text and "د. سمير سعدي شماس" in text
    # الملاحظات (القاعة المدرجة، مقررات) لا تظهر
    assert "القاعة المدرجة" not in text and "مقررات" not in text


async def test_tasks_requests_send_tasks_only():
    svc, db = make_service()
    await say(svc, "الأحد امتحان عند د. عمار")
    for q in ("مهام باكر", "المهام", "شنو المهام باكر", "واجبات باكر"):
        assert svc.special_request(q) is None, q
    r = await say(svc, "مهام باكر", private=True)
    assert "امتحان تصاميم محطات" in r[0].text and "محاضرات" not in r[0].text
    assert "محاضرات" not in svc.schedule_reply("tomorrow").text  # /tomorrow = مهام فقط
    r = await say(svc, "المهام", private=True)
    assert "المهام القادمة" in r[0].text and "محاضرات" not in r[0].text


def test_lecture_notes_are_not_shown_anywhere():
    from study_bot.timetable import lecture_lines_html

    for d in (date(2026, 11, 22), date(2026, 11, 23), date(2026, 11, 25)):
        text = "\n".join(lecture_lines_html(d))
        assert "القاعة المدرجة" not in text and "مقررات" not in text
    # المحاضرات والأساتذة تبقى
    assert "راكان فاروق" in "\n".join(lecture_lines_html(date(2026, 11, 23)))


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
