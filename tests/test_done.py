"""المرحلة الثالثة: زر "خلصت" والقائمة الشخصية."""

from datetime import date

from study_bot.config import Config
from study_bot.db import Database
from study_bot.rules import RuleBasedAnalyzer
from study_bot.service import Sender, StudyService

TODAY = date(2026, 11, 20)
AHMED = Sender(1, "أحمد")
SARA = Sender(2, "سارة")


def make_service():
    db = Database(":memory:")
    return StudyService(db, RuleBasedAnalyzer(), Config(), today_fn=lambda: TODAY), db


async def say(svc, text, who=AHMED, replied_to=None, private=False):
    return await svc.handle_message(text, who, chat_id=-1, is_private=private, replied_to=replied_to,
                                    addressed=replied_to is not None)


def _buttons(reply):
    return [b.data for row in reply.buttons for b in row]


async def test_done_button_is_per_student():
    svc, db = make_service()
    r = await say(svc, "باجر واجب رياضيات تمارين 5")
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    assert f"x:{task_id}" in _buttons(r[0])

    res = svc.handle_callback(f"x:{task_id}", AHMED)
    assert "عاشت إيدك" in res.answer and "أنجزها: طالب واحد" in res.edit.text
    assert db.is_done(task_id, AHMED.user_id) and not db.is_done(task_id, SARA.user_id)

    res = svc.handle_callback(f"x:{task_id}", AHMED)  # ضغطة ثانية ترجعها
    assert "رجّعتها" in res.answer and not db.is_done(task_id, AHMED.user_id)


async def test_personal_list_hides_done_tasks():
    svc, db = make_service()
    await say(svc, "باجر واجب رياضيات")
    await say(svc, "الخميس الجاي امتحان فيزياء")
    hw = [t for t in db.tasks_between(TODAY, date(2027, 1, 1)) if t.kind == "homework"][0]

    r = await say(svc, "خلصت واجب الرياضيات")
    assert "سجّلت إنك خلصت" in r[0].text and "باقي عليك 1" in r[0].text

    r = await say(svc, "شنو باقي علي؟")
    assert "باقي عليك يا أحمد" in r[0].text and "فيزياء" in r[0].text and "رياضيات" not in r[0].text

    # سارة لم تنجز شيئاً: قائمتها فيها الاثنين
    r = await say(svc, "شنو باقي علي؟", SARA)
    assert "رياضيات" in r[0].text and "فيزياء" in r[0].text
    assert all(d.endswith(f":{SARA.user_id}") for d in _buttons(r[0]))
    assert hw.id


async def test_done_by_replying_to_card():
    svc, db = make_service()
    r = await say(svc, "الأحد تقرير كيمياء")
    card = r[0].text
    r = await say(svc, "خلصته", SARA, replied_to=card)
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    assert "عاشت إيدك" in r[0].text and db.is_done(task_id, SARA.user_id)


async def test_personal_list_buttons_only_for_owner():
    svc, db = make_service()
    await say(svc, "باجر واجب رياضيات")
    r = svc.personal_reply(AHMED)
    data = _buttons(r)[0]
    assert "طالب ثاني" in svc.handle_callback(data, SARA).answer
    res = svc.handle_callback(data, AHMED)
    assert "ما باقي عليك شي" in res.edit.text and res.edit.buttons == []


async def test_all_done_message_and_past_chatter_not_marked():
    svc, db = make_service()
    assert "ما باقي عليك شي" in svc.personal_reply(AHMED).text
    await say(svc, "الخميس الجاي امتحان فيزياء")
    r = await say(svc, "خلصت امتحان الفيزياء كان سهل")
    assert r == [] and db.done_ids(AHMED.user_id) == set()
