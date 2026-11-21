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


async def test_done_button_removed_from_cards_and_old_buttons_are_harmless():
    svc, db = make_service()
    r = await say(svc, "باجر واجب انشائية تمارين 5")
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    buttons = _buttons(r[0])
    assert buttons == [f"c:{task_id}", f"d:{task_id}"]  # تأكيد وحذف فقط، بدون "خلصت"
    res = svc.handle_callback(f"x:{task_id}", AHMED)  # زر قديم من نسخة سابقة
    assert "انلغى" in res.answer and not db.is_done(task_id, AHMED.user_id)


async def test_personal_list_hides_done_tasks():
    svc, db = make_service()
    await say(svc, "باجر واجب انشائية")
    await say(svc, "الخميس الجاي امتحان النفايات")
    hw = [t for t in db.tasks_between(TODAY, date(2027, 1, 1)) if t.kind == "homework"][0]

    r = await say(svc, "خلصت واجب الانشائية")
    assert "سجّلت إنك خلصت" in r[0].text and "باقي عليك 1" in r[0].text

    r = await say(svc, "شنو باقي علي؟")
    assert "باقي عليك يا أحمد" in r[0].text and "النفايات" in r[0].text and "انشائية" not in r[0].text

    # سارة لم تنجز شيئاً: قائمتها فيها الاثنين
    r = await say(svc, "شنو باقي علي؟", SARA)
    assert "انشائية" in r[0].text and "النفايات" in r[0].text
    assert _buttons(r[0]) == []
    assert hw.id


async def test_done_by_replying_to_card():
    svc, db = make_service()
    r = await say(svc, "الأحد تقرير تقييم الأثر")
    card = r[0].text
    r = await say(svc, "خلصته", SARA, replied_to=card)
    task_id = db.tasks_between(TODAY, date(2027, 1, 1))[0].id
    assert "عاشت إيدك" in r[0].text and db.is_done(task_id, SARA.user_id)


async def test_personal_list_has_no_buttons():
    svc, db = make_service()
    await say(svc, "باجر واجب انشائية")
    r = svc.personal_reply(AHMED)
    assert r.buttons == [] and "اضغط" not in r.text and "انشائية" in r.text


async def test_all_done_message_and_past_chatter_not_marked():
    svc, db = make_service()
    assert "ما باقي عليك شي" in svc.personal_reply(AHMED).text
    await say(svc, "الخميس الجاي امتحان النفايات")
    r = await say(svc, "خلصت امتحان النفايات كان سهل")
    assert r == [] and db.done_ids(AHMED.user_id) == set()
