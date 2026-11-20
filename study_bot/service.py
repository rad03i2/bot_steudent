"""منطق البوت الأساسي، منفصل عن تيليجرام حتى يسهل اختباره."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from typing import Callable

from .arabic import normalize
from .config import Config
from .dates import resolve_date_expression, week_bounds
from .db import Database
from .formatting import long_date, personal_list, schedule, task_card
from .llm import Analysis, AnalysisRequest, Analyzer, ExtractedTask
from .models import EXAM_KINDS, TASK_KINDS, Task, kind_of

log = logging.getLogger(__name__)


@dataclass
class Button:
    text: str
    data: str


@dataclass
class Reply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)


@dataclass
class CallbackResult:
    answer: str  # إشعار صغير يظهر للضاغط
    edit: Reply | None = None  # تعديل الرسالة التي فيها الزر
    new: Reply | None = None  # رسالة جديدة


@dataclass
class Sender:
    user_id: int
    name: str


# كلمات تدل على أن الرسالة قد تخص الدراسة؛ تُستخدم في القروبات لتقليل استدعاءات الذكاء الاصطناعي
_KEYWORDS = [
    "امتحان", "اختبار", "كويز", "quiz", "exam", "ميد", "mid", "فاينل", "final", "نهائي", "نصفي",
    "واجب", "homework", "assignment", "hw", "شيت", "مشروع", "project", "تقرير", "report",
    "تسليم", "deadline", "ديدلاين", "برزنت", "present", "عرض", "سمنار", "seminar",
    "مختبر", "لاب", "lab", "محاضره", "محاضرات", "تعويضي", "مادة", "ماده", "دكتور", "الدكتور", "الاستاذ", "ست ",
    "جدول", "مهام", "مهمه", "عدنا", "عندنا", "علينا", "باقي", "شكو", "شنو", "وش", "ايش", "متى", "امتى",
    "تاجل", "انلغى", "الغي", "انلغت", "تغير", "تقدم", "اتاجل",
    "بكره", "بكرا", "باجر", "غدا", "الاسبوع", "اسبوع",
    "الاحد", "الاثنين", "الثلاثاء", "الثلاثا", "الاربعاء", "الاربعا", "الخميس", "الجمعه", "السبت",
]
_DATE_RE = re.compile(r"\b\d{1,2}\s*[/\-.]\s*\d{1,2}\b")


def looks_relevant(text: str) -> bool:
    t = normalize(text)
    if len(t) < 4:
        return False
    return bool(_DATE_RE.search(t)) or any(k in t for k in _KEYWORDS)


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _clean_time(value: str | None) -> str | None:
    if not value:
        return None
    m = _TIME_RE.match(value.strip())
    return f"{int(m[1]):02d}:{m[2]}" if m else None


class StudyService:
    def __init__(
        self,
        db: Database,
        analyzer: Analyzer | None,
        config: Config,
        today_fn: Callable[[], date] | None = None,
    ):
        self.db = db
        self.analyzer = analyzer
        self.config = config
        self._today_fn = today_fn

    @property
    def style(self) -> str:
        return self.config.month_style

    def today(self) -> date:
        if self._today_fn:
            return self._today_fn()
        return datetime.now(self.config.tz).date()

    # ------------------------------------------------------------------
    # الجداول الجاهزة (أوامر لا تحتاج ذكاء اصطناعي)
    # ------------------------------------------------------------------

    def schedule_reply(self, period: str) -> Reply:
        today = self.today()
        kinds = None
        if period == "today":
            start = end = today
            title = "جدول اليوم"
        elif period == "tomorrow":
            start = end = today + timedelta(days=1)
            title = "جدول الغد"
        elif period == "week":
            start, end = today, week_bounds(today, self.config.week_start)[1]
            title = "بقية هذا الأسبوع"
        elif period == "nextweek":
            this_start, _ = week_bounds(today, self.config.week_start)
            start, end = this_start + timedelta(days=7), this_start + timedelta(days=13)
            title = "جدول الأسبوع القادم"
        elif period == "exams":
            start, end = today, today + timedelta(days=180)
            kinds = sorted(EXAM_KINDS)
            title = "الامتحانات القادمة"
        else:  # upcoming
            start, end = today, today + timedelta(days=30)
            title = "كل المهام القادمة (30 يوماً)"
        tasks = self.db.tasks_between(start, end, kinds=kinds)
        return Reply(schedule(tasks, title=title, start=start, end=end, today=today, style=self.style))

    # ------------------------------------------------------------------
    # الرسائل النصية
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        text: str,
        sender: Sender,
        *,
        chat_id: int | None,
        is_private: bool,
        addressed: bool = False,
        replied_to: str | None = None,
    ) -> list[Reply]:
        """addressed = الرسالة موجهة للبوت (منشن أو رد على رسالة البوت)."""
        text = text.strip()
        if not text:
            return []
        if not is_private and not addressed and self.config.group_prefilter and not looks_relevant(text):
            return []
        if self.analyzer is None:
            if is_private or addressed:
                return [Reply(
                    "⚠️ ميزة فهم الرسائل غير مفعّلة.\n"
                    "يمكنك استخدام الأوامر: /today /tomorrow /week /nextweek /upcoming /exams"
                )]
            return []

        today = self.today()
        upcoming = self.db.tasks_between(today - timedelta(days=3), today + timedelta(days=180))[:80]
        request = AnalysisRequest(
            message=text,
            today=today,
            week_start=self.config.week_start,
            sender_name=sender.name,
            known_subjects=self.db.known_subjects(),
            upcoming=upcoming,
            replied_to=replied_to,
            direct=is_private or addressed,
        )
        analysis = await self.analyzer.analyze(request)
        if analysis is None:
            if is_private or addressed:
                return [Reply("😕 صار خطأ مؤقت بالفهم، جرّب مرة ثانية بعد شوي.")]
            return []
        return self.apply_analysis(analysis, text, sender, chat_id=chat_id, is_private=is_private, addressed=addressed)

    def apply_analysis(
        self,
        analysis: Analysis,
        text: str,
        sender: Sender,
        *,
        chat_id: int | None,
        is_private: bool,
        addressed: bool = False,
    ) -> list[Reply]:
        intent = analysis.intent
        if intent == "add" and analysis.tasks:
            return [self._add_task(t, text, sender, chat_id) for t in analysis.tasks]
        if intent == "update":
            return [self._update_task(analysis)]
        if intent == "delete":
            return [self._ask_delete(analysis.target_task_id)]
        if intent == "done":
            done_reply = self._mark_done(analysis.target_task_id, sender, direct=is_private or addressed)
            return [done_reply] if done_reply else []
        if intent == "query" and analysis.query and analysis.query.only_mine:
            start = _parse_iso(analysis.query.start_date)
            end = _parse_iso(analysis.query.end_date)
            return [self.personal_reply(sender, start, end)]
        if intent == "query" and analysis.query:
            return [self._query(analysis)]
        if intent == "clarify" and analysis.reply:
            return [Reply("🤔 " + escape(analysis.reply))]
        if is_private or addressed:
            return [Reply(
                "👋 ما لقيت موعد دراسي بهالرسالة.\n"
                "اكتب مثلاً: <i>الخميس الجاي امتحان فيزياء الفصل الثالث</i>\n"
                "أو اسأل: <i>شنو عدنا الأسبوع الجاي؟</i>"
            )]
        return []

    # ---------- إضافة ----------

    def _resolve_due(self, expression: str | None, llm_date: str | None) -> date | None:
        """الكود يحسب التاريخ أولاً؛ وإن لم يفهم التعبير نعتمد على اختيار النموذج من التقويم."""
        resolved = resolve_date_expression(expression, self.today(), self.config.week_start)
        if resolved is not None:
            parsed = _parse_iso(llm_date)
            if parsed and parsed != resolved:
                log.info("date mismatch for %r: code=%s llm=%s (using code)", expression, resolved, parsed)
            return resolved
        return _parse_iso(llm_date)

    def _add_task(self, item: ExtractedTask, text: str, sender: Sender, chat_id: int | None) -> Reply:
        today = self.today()
        due = self._resolve_due(item.date_expression, item.date)
        label = f"{kind_of(item.kind).label} {item.subject}"
        if due is None:
            return Reply(f"🤔 فهمت إنه أكو <b>{escape(label)}</b>، بس متى بالضبط؟ اكتب اليوم أو التاريخ.")
        if due < today:
            return Reply(
                f"⚠️ موعد <b>{escape(label)}</b> طلع بتاريخ ماضي ({long_date(due, self.style)})، فما سجلته.\n"
                "إذا كان قصدك تاريخ قادم، اكتبه بشكل أوضح."
            )

        similar = self.db.find_similar(item.kind, item.subject, today)
        for existing in similar:
            if existing.due_date == due:
                task, is_new = self.db.confirm_task(existing.id, sender.user_id)
                assert task is not None
                header = "✅ هذا الموعد مسجّل مسبقاً، وتم توثيقه" if is_new else "ℹ️ هذا الموعد مسجّل مسبقاً"
                return Reply(task_card(task, today, header=header, style=self.style), self._card_buttons(task))

        task = self.db.add_task(
            kind=item.kind,
            subject=item.subject,
            title=item.title,
            due_date=due,
            due_time=_clean_time(item.time),
            notes=item.notes,
            chat_id=chat_id,
            user_id=sender.user_id,
            user_name=sender.name,
            source_text=text,
        )
        header = f"{task.info.icon} تمت إضافة {task.info.label} جديد إلى الجدول" if task.info.label not in ("مهمة", "محاضرة") else f"{task.info.icon} تمت إضافة {task.info.label} جديدة إلى الجدول"
        reply = Reply(task_card(task, today, header=header, style=self.style), self._card_buttons(task))

        conflicts = [t for t in similar if t.due_date != due and abs((t.due_date - due).days) <= 21]
        if conflicts:
            other = conflicts[0]
            reply.text += (
                f"\n\n⚠️ <b>انتبهوا:</b> يوجد {escape(other.info.label)} {escape(other.subject)} آخر مسجّل "
                f"يوم {long_date(other.due_date, self.style)} (#{other.id}).\n"
                "إذا الموعد تأجّل وهذا نفسه، احذفوا القديم 👇"
            )
            reply.buttons.append([Button(f"🗑 حذف القديم #{other.id}", f"d:{other.id}")])
        return reply

    @staticmethod
    def _card_buttons(task: Task) -> list[list[Button]]:
        return [
            [Button("✅ صحيح، أأكّد", f"c:{task.id}"), Button("✔️ خلصت", f"x:{task.id}")],
            [Button("🗑 حذف", f"d:{task.id}")],
        ]

    # ---------- الإنجاز والقائمة الشخصية ----------

    def personal_reply(self, sender: Sender, start: date | None = None, end: date | None = None) -> Reply:
        """قائمة "شنو باقي علي؟": المهام القادمة التي لم يعلّم عليها الطالب "خلصت"."""
        today = self.today()
        start = max(start or today, today)
        end = end if end and end >= start else today + timedelta(days=30)
        done = self.db.done_ids(sender.user_id)
        tasks = [t for t in self.db.tasks_between(start, end) if t.id not in done]
        text = personal_list(tasks, name=sender.name, start=start, end=end, today=today, style=self.style)
        buttons = [
            [Button(f"✔️ {t.info.label} {t.subject}"[:60], f"m:{t.id}:{sender.user_id}")]
            for t in sorted(tasks, key=lambda t: (t.due_date, t.priority, t.id))[:8]
        ]
        return Reply(text, buttons)

    def _mark_done(self, task_id: int | None, sender: Sender, *, direct: bool) -> Reply | None:
        task = self.db.get_task(task_id) if task_id else None
        if task is None:
            return Reply("🤔 ما عرفت أي مهمة خلصت. اعمل رد (Reply) على بطاقتها واكتب: خلصت") if direct else None
        self.db.mark_done(task.id, sender.user_id)
        remaining = self._remaining_count(sender)
        tail = f"باقي عليك {remaining} 📋 (اكتب: شنو باقي علي؟)" if remaining else "ما باقي عليك شي 🎉"
        return Reply(
            f"✔️ عاشت إيدك يا {escape(sender.name)}! سجّلت إنك خلصت "
            f"<b>{escape(task.info.label)} {escape(task.subject)}</b>.\n{tail}"
        )

    def _remaining_count(self, sender: Sender) -> int:
        today = self.today()
        done = self.db.done_ids(sender.user_id)
        return sum(1 for t in self.db.tasks_between(today, today + timedelta(days=30)) if t.id not in done)

    # ---------- تعديل ----------

    def _update_task(self, analysis: Analysis) -> Reply:
        task = self.db.get_task(analysis.target_task_id) if analysis.target_task_id else None
        if task is None:
            return Reply("🤔 ما عرفت أي موعد تقصد بالتعديل. اذكر المادة ونوع الموعد (مثلاً: كويز الرياضيات تأجل للأحد).")
        ch = analysis.changes
        if ch is None:
            return Reply("🤔 شنو التعديل المطلوب بالضبط؟")
        fields: dict = {}
        old_date = task.due_date
        if ch.date_expression or ch.date:
            new_due = self._resolve_due(ch.date_expression, ch.date)
            if new_due is None:
                return Reply("🤔 ما فهمت الموعد الجديد. اكتب اليوم أو التاريخ بوضوح.")
            fields["due_date"] = new_due
        if ch.time:
            fields["due_time"] = _clean_time(ch.time)
        if ch.title:
            fields["title"] = ch.title
        if ch.notes:
            fields["notes"] = ch.notes
        if ch.subject:
            fields["subject"] = ch.subject
        if ch.kind and ch.kind in TASK_KINDS:
            fields["kind"] = ch.kind
        if not fields:
            return Reply("🤔 شنو التعديل المطلوب بالضبط؟")
        updated = self.db.update_task(task.id, **fields)
        assert updated is not None
        text = task_card(updated, self.today(), header="✏️ تم تعديل الموعد", style=self.style)
        if "due_date" in fields and fields["due_date"] != old_date:
            text += f"\n\n🔁 كان سابقاً: {long_date(old_date, self.style)}"
        return Reply(text, self._card_buttons(updated))

    # ---------- حذف ----------

    def _ask_delete(self, task_id: int | None) -> Reply:
        task = self.db.get_task(task_id) if task_id else None
        if task is None:
            return Reply("🤔 ما عرفت أي موعد تقصد. اذكر المادة ونوع الموعد.")
        return Reply(
            f"🗑 تريد تحذف <b>{escape(task.info.label)} {escape(task.subject)}</b> "
            f"يوم {long_date(task.due_date, self.style)}؟",
            [[Button("نعم، احذف", f"dy:{task.id}"), Button("لا، خلّيه", "dn:0")]],
        )

    # ---------- استعلام ----------

    def _query(self, analysis: Analysis) -> Reply:
        q = analysis.query
        assert q is not None
        today = self.today()
        start = _parse_iso(q.start_date) or today
        end = _parse_iso(q.end_date) or start
        if end < start:
            start, end = end, start
        end = min(end, start + timedelta(days=366))
        kinds = [k for k in (q.kinds or []) if k in TASK_KINDS] or None
        tasks = self.db.tasks_between(start, end, subject=q.subject, kinds=kinds)
        if not tasks and q.subject:
            # ربما كُتب اسم المادة بشكل مختلف: نعرض بدون فلتر المادة
            tasks = [t for t in self.db.tasks_between(start, end, kinds=kinds)
                     if normalize(q.subject) in normalize(t.subject) or normalize(t.subject) in normalize(q.subject)]
        return Reply(schedule(tasks, title=q.title or "الجدول", start=start, end=end, today=today, style=self.style))

    # ------------------------------------------------------------------
    # الأزرار
    # ------------------------------------------------------------------

    def handle_callback(self, data: str, sender: Sender) -> CallbackResult:
        if data.startswith("m:"):
            return self._personal_callback(data, sender)
        action, _, raw_id = data.partition(":")
        try:
            task_id = int(raw_id)
        except ValueError:
            return CallbackResult("❌ زر غير صالح")
        today = self.today()

        if action == "c":
            task, is_new = self.db.confirm_task(task_id, sender.user_id)
            if task is None:
                return CallbackResult("هذا الموعد محذوف")
            card = Reply(
                task_card(task, today, header="✅ موعد مؤكد", style=self.style), self._card_buttons(task)
            )
            return CallbackResult("شكراً، تم تسجيل تأكيدك ✅" if is_new else "سبق وأكّدته 👍", edit=card)

        if action == "x":
            task = self.db.get_task(task_id)
            if task is None:
                return CallbackResult("هذا الموعد محذوف")
            now_done = self.db.toggle_done(task_id, sender.user_id)
            task = self.db.get_task(task_id)
            card = Reply(task_card(task, today, header="📌 موعد مسجّل", style=self.style), self._card_buttons(task))
            answer = "✔️ عاشت إيدك! انحسبت عليك خالصة" if now_done else "↩️ رجّعتها لقائمتك"
            return CallbackResult(answer, edit=card)

        if action == "d":
            return CallbackResult("", new=self._ask_delete(task_id))

        if action == "dy":
            task = self.db.get_task(task_id)
            if task is None or not self.db.delete_task(task_id):
                return CallbackResult("محذوف مسبقاً", edit=Reply("🗑 هذا الموعد محذوف."))
            return CallbackResult(
                "تم الحذف",
                edit=Reply(
                    f"🗑 تم حذف <b>{escape(task.info.label)} {escape(task.subject)}</b> "
                    f"({long_date(task.due_date, self.style)}) بواسطة {escape(sender.name)}."
                ),
            )

        if action == "dn":
            return CallbackResult("تم الإلغاء", edit=Reply("👌 تمام، ما حذفت شي."))

        return CallbackResult("❌ زر غير معروف")

    def _personal_callback(self, data: str, sender: Sender) -> CallbackResult:
        try:
            _, raw_task, raw_owner = data.split(":")
            task_id, owner = int(raw_task), int(raw_owner)
        except ValueError:
            return CallbackResult("❌ زر غير صالح")
        if sender.user_id != owner:
            return CallbackResult("هذي قائمة طالب ثاني 🙂 اكتب /mytasks حتى تشوف قائمتك")
        if self.db.get_task(task_id) is None:
            return CallbackResult("هذا الموعد محذوف", edit=self.personal_reply(sender))
        self.db.mark_done(task_id, sender.user_id)
        return CallbackResult("✔️ عاشت إيدك!", edit=self.personal_reply(sender))
