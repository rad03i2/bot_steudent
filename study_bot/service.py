"""منطق البوت الأساسي، منفصل عن تيليجرام حتى يسهل اختباره."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from typing import Callable

from .arabic import WEEKDAY_NAMES_AR, normalize
from .config import Config
from .dates import resolve_date_expression, week_bounds
from .db import Database
from .formatting import long_date, personal_list, schedule, task_card
from .llm import Analysis, AnalysisRequest, Analyzer, ExtractedTask
from .models import EXAM_KINDS, TASK_KINDS, Task, kind_of
from .timetable import Catalog, lecture_lines_html, lectures_on

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


@dataclass
class _Pending:
    """موعد ينتظر اختيار المادة من الأزرار."""

    item: ExtractedTask
    text: str
    sender: Sender
    chat_id: int | None
    created: datetime


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
# كلمات تدل على أن الطلب عن المهام (وليس عن جدول الدروس)
TASK_WORDS = {
    "مهام", "المهام", "مهمه", "واجب", "واجبات", "الواجبات", "امتحان", "امتحانات", "الامتحانات", "مواعيد",
    "المواعيد", "تسليم", "تسليمات", "كويز", "كويزات", "ميد", "فاينل", "مشروع", "تقرير",
}
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
        self._pending: dict[str, _Pending] = {}

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
            title = "مهام اليوم"
        elif period == "tomorrow":
            start = end = today + timedelta(days=1)
            title = "مهام الغد"
        elif period == "week":
            start, end = today, week_bounds(today, self.config.week_start)[1]
            title = "مهام بقية هذا الأسبوع"
        elif period == "nextweek":
            this_start, _ = week_bounds(today, self.config.week_start)
            start, end = this_start + timedelta(days=7), this_start + timedelta(days=13)
            title = "مهام الأسبوع القادم"
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
    # الصورة والتقويم
    # ------------------------------------------------------------------

    def week_image(self, next_week: bool = False) -> tuple[bytes, str]:
        from .image import render_week  # استيراد متأخر: Pillow يُحمّل فقط عند الحاجة

        today = self.today()
        start, _ = week_bounds(today, self.config.week_start)
        if next_week:
            start += timedelta(days=7)
        tasks = self.db.tasks_between(start, start + timedelta(days=6))
        title = "مهام الأسبوع القادم" if next_week else "مهام هذا الأسبوع"
        png = render_week(tasks, start=start, today=today, title=title, style=self.style)
        caption = f"🗓 {title}" + ("" if next_week else "\nللأسبوع الجاي: /image next")
        return png, caption

    def calendar_file(self) -> tuple[bytes, str]:
        from .calendar_export import build_ics, export_range

        start, end = export_range(self.today())
        tasks = self.db.tasks_between(start, end)
        caption = (
            f"📆 ملف التقويم ({len(tasks)} موعد)\n"
            "افتحه على جوالك وأضفه للتقويم، وراح يذكّرك تلقائياً قبل كل موعد.\n"
            "إذا انضافت مواعيد جديدة، اطلب الملف مرة ثانية وأضفه."
        )
        return build_ics(tasks, self.config.tz), caption

    def special_request(self, text: str) -> str | None:
        """طلبات لا تحتاج تحليلاً.

        - "جدول ..." أو "محاضرات ...": جدول الدروس فقط (بدون مهام):
          الأسبوع أو بدون يوم = الصورة الكاملة، ويوم واحد = بطاقة ذلك اليوم مقصوصة.
        - طلبات المهام ("المهام"، "مهام الاثنين"...) لا تُعالج هنا: تذهب للتحليل وترجع مهام فقط.
        - "صورة جدول المهام": صورة مهام الأسبوع. "تقويم": ملف التقويم.
        """
        from .arabic import WEEKDAY_ALIASES
        from .rules import _DELETE_WORDS, _PAST_WORDS, _UPDATE_WORDS, _date_word, find_dates, find_kind, tokenize

        t = normalize(text)
        words = set(t.split())
        lecture_words = {"محاضرات", "المحاضرات", "محاضره", "المحاضره", "دروس", "الدروس", "حصص", "الحصص",
                         "دوام", "الدوام", "lectures", "timetable"}
        table_words = {"جدول", "الجدول", "للجدول"}
        week_words = {"الاسبوع", "اسبوع", "الاسبوعي", "اسبوعي", "الاسبوعيه", "الاسبوعين"}
        has_task_words = bool(words & TASK_WORDS)
        asks_lectures = bool(words & lecture_words)
        asks_table = bool(words & table_words)

        if words & {"تقويم", "التقويم", "كاليندر", "calendar", "ics"} and len(words) <= 3:
            return "calendar"
        if words & {"صوره", "بالصوره"} and asks_table and has_task_words:
            nxt = bool(words & {"الجاي", "القادم", "الياي", "المقبل"})
            return "image_next" if nxt else "image"
        if not (asks_lectures or asks_table) or has_task_words:
            return None
        if words & {"تعويضيه", "تعويضي", "الجايه", "القادمه"} and asks_lectures:
            return None
        if words & (_UPDATE_WORDS | _DELETE_WORDS | _PAST_WORDS) or len(words) > 7:
            return None  # كلام عن محاضرة (تأجلت، انلغت...) أو جملة طويلة، وليس طلب جدول
        tokens = tokenize(text)
        kind, _ = find_kind(tokens)
        if kind not in (None, "lecture"):
            return None

        dates = find_dates(tokens, self.today(), self.config.week_start)
        names_a_weekday = any(_date_word(tk.norm) in WEEKDAY_ALIASES for tk in tokens)
        question = bool(words & {"شنو", "شكو", "وش", "ايش", "شو", "متى", "شني", "اشكو", "عدنا", "عندنا"}) or "؟" in text or "?" in text

        day: date | None = None
        if dates and (names_a_weekday or not words & week_words):
            day = dates[0].value
        elif asks_lectures and question and not words & (week_words | {"دروس", "الدروس", "timetable"}):
            day = self.today()  # "شنو عدنا محاضرات؟" بدون يوم = اليوم
        if day is not None:
            return f"timetable_day:{day.isoformat()}" if lectures_on(day) else f"lectures:{day.isoformat()}"
        return "timetable"  # "جدول الأسبوع"، "جدول الدروس": الصورة الكاملة

    def lectures_reply(self, day: date) -> Reply:
        lines = self._lecture_lines(day)
        rel = {0: "اليوم", 1: "باجر"}.get((day - self.today()).days)
        head = f"📚 <b>محاضرات {rel + ' – ' if rel else ''}{long_date(day, self.style)}</b>"
        if not lines:
            nxt = next(
                (day + timedelta(days=i) for i in range(1, 8) if lectures_on(day + timedelta(days=i))), None
            )
            tail = f"\nأول محاضرات بعدها: {long_date(nxt, self.style)}" if nxt else ""
            return Reply(f"{head}\n\n🎉 ماكو محاضرات هذا اليوم.{tail}")
        return Reply(head + "\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n\nالجدول كامل: /timetable")

    @staticmethod
    def _lecture_lines(day: date) -> list[str]:
        return lecture_lines_html(day)

    def _with_lectures(self, text: str, start: date, end: date) -> str:
        """سؤال عن يوم واحد ("شنو عدنا باجر؟"): نضيف محاضرات ذلك اليوم تحت المهام."""
        if start != end:
            return text
        lines = self._lecture_lines(start)
        if not lines:
            return text
        rel = {0: "اليوم", 1: "باجر"}.get((start - self.today()).days)
        label = f"محاضرات {rel or WEEKDAY_NAMES_AR[start.weekday()]}"
        return text + f"\n\n📚 <b>{label}:</b>\n" + "\n".join(lines)

    def timetable_image(self) -> tuple[bytes, str]:
        from .image import render_timetable

        return render_timetable(self.today()), "📚 جدول الدروس – المرحلة الرابعة بولونيا شعبة A"

    def timetable_day_image(self, day: date) -> tuple[bytes, str]:
        """صورة محاضرات يوم واحد فقط، مقصوصة من الجدول الكامل."""
        from .image import render_timetable

        png = render_timetable(self.today(), day=day.weekday())
        rel = {0: "اليوم", 1: "باجر"}.get((day - self.today()).days)
        return png, f"📚 محاضرات {long_date(day)}" + (f" ({rel})" if rel else "")

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
            known_subjects=self.catalog().names,
            catalog=self.catalog(),
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
            return [self._query(analysis, text)]
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

    def access(self, chat_id: int, *, is_private: bool, user_id: int, user_name: str, title: str = "") -> str:
        """من يحق له استخدام البوت. يرجع: ok / stranger (خاص من شخص ليس بالقروب) / leave (قروب آخر)."""
        allowed = self.config.allowed_chat_ids
        if is_private:
            if not allowed and not self.config.private_mode:
                return "ok"
            return "ok" if self.db.is_member(user_id) else "stranger"
        if allowed:
            if chat_id not in allowed:
                return "leave"
        elif self.config.private_mode:
            home = self.db.home_chat()
            if home is None:
                self.db.set_home_chat(chat_id)  # أول قروب يستخدم البوت يصير قروب الشعبة
            elif chat_id != home:
                return "leave"
        self.db.touch_member(user_id, user_name)
        self.db.register_chat(chat_id, title)
        return "ok"

    def catalog(self) -> Catalog:
        return Catalog(external=self.db.external_subjects())

    def _add_task(
        self, item: ExtractedTask, text: str, sender: Sender, chat_id: int | None, *, chosen: bool = False
    ) -> Reply:
        today = self.today()
        due = self._resolve_due(item.date_expression, item.date)
        if not chosen:
            # البوت يعتمد مواد جدول الشعبة فقط
            catalog = self.catalog()
            canonical = catalog.resolve(item.subject)
            if canonical is None and item.external and item.subject.strip():
                canonical = item.subject.strip()
                self.db.add_external_subject(canonical, sender.name)
            if canonical is None:
                return self._ask_subject(item, text, sender, chat_id, due)
            item = item.model_copy(update={"subject": canonical})
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

    def _ask_subject(
        self, item: ExtractedTask, text: str, sender: Sender, chat_id: int | None, due: date | None
    ) -> Reply:
        """المادة غير موجودة بجدول الشعبة (أو لم تُذكر): نسأل بأزرار بدل ما نحفظ."""
        token = secrets.token_hex(4)
        self._pending[token] = _Pending(item, text, sender, chat_id, datetime.now())
        self._cleanup_pending()
        kind_label = kind_of(item.kind).label
        when = f" يوم {long_date(due, self.style)}" if due else ""
        if item.subject.strip():
            head = (
                f"⚠️ «{escape(item.subject)}» مو من مواد جدول الشعبة، فما سجلت {escape(kind_label)}{when}.\n"
                "إذا قصدك مادة من الجدول اختارها 👇، وإذا هي مادة خارجية اضغط ➕"
            )
        else:
            head = f"📚 فهمت إنه أكو <b>{escape(kind_label)}</b>{when}، بس أي مادة؟ اختار 👇"
        buttons = [[Button(name, f"s:{token}:{i}")] for i, name in enumerate(self.catalog().names)]
        if item.subject.strip():
            buttons.append([Button(f"➕ «{item.subject}» مادة خارجية"[:60], f"s:{token}:x")])
        buttons.append([Button("❌ إلغاء", f"s:{token}:n")])
        return Reply(head, buttons)

    def _cleanup_pending(self) -> None:
        cutoff = datetime.now() - timedelta(hours=24)
        for key in [k for k, p in self._pending.items() if p.created < cutoff]:
            del self._pending[key]

    def _subject_callback(self, data: str, sender: Sender) -> CallbackResult:
        try:
            _, token, choice = data.split(":")
        except ValueError:
            return CallbackResult("❌ زر غير صالح")
        pending = self._pending.pop(token, None)
        if pending is None:
            return CallbackResult("انتهت صلاحية هذا السؤال، اكتب الموعد مرة ثانية", edit=Reply("⌛️ انتهت صلاحية هذا السؤال."))
        if choice == "n":
            return CallbackResult("تم الإلغاء", edit=Reply("👌 تمام، ما سجلت شي."))
        item = pending.item
        if choice == "x":
            name = item.subject.strip()
            self.db.add_external_subject(name, sender.name)
        else:
            names = self.catalog().names
            try:
                name = names[int(choice)]
            except (ValueError, IndexError):
                return CallbackResult("❌ اختيار غير صالح")
        item = item.model_copy(update={"subject": name})
        reply = self._add_task(item, pending.text, pending.sender, pending.chat_id, chosen=True)
        return CallbackResult("تم ✅", edit=reply)

    @staticmethod
    def _card_buttons(task: Task) -> list[list[Button]]:
        return [[Button("✅ صحيح، أأكّد", f"c:{task.id}"), Button("🗑 حذف", f"d:{task.id}")]]

    # ---------- الإنجاز والقائمة الشخصية ----------

    def personal_reply(self, sender: Sender, start: date | None = None, end: date | None = None) -> Reply:
        """قائمة "شنو باقي علي؟": المهام القادمة، للعرض فقط (بدون أزرار)."""
        today = self.today()
        start = max(start or today, today)
        end = end if end and end >= start else today + timedelta(days=30)
        done = self.db.done_ids(sender.user_id)
        tasks = [t for t in self.db.tasks_between(start, end) if t.id not in done]
        text = personal_list(tasks, name=sender.name, start=start, end=end, today=today, style=self.style)
        return Reply(text)

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

    def _query(self, analysis: Analysis, text: str = "") -> Reply:
        q = analysis.query
        assert q is not None
        today = self.today()
        start = _parse_iso(q.start_date) or today
        end = _parse_iso(q.end_date) or start
        if end < start:
            start, end = end, start
        end = min(end, start + timedelta(days=366))
        kinds = [k for k in (q.kinds or []) if k in TASK_KINDS] or None
        subject = self.catalog().resolve(q.subject) or q.subject
        tasks = self.db.tasks_between(start, end, subject=subject, kinds=kinds)
        if not tasks and q.subject:
            # ربما كُتب اسم المادة بشكل مختلف: نعرض بدون فلتر المادة
            tasks = [t for t in self.db.tasks_between(start, end, kinds=kinds)
                     if normalize(q.subject) in normalize(t.subject) or normalize(t.subject) in normalize(q.subject)]
        reply = schedule(tasks, title=q.title or "المواعيد", start=start, end=end, today=today, style=self.style)
        # "المهام" / "مهام الاثنين" = مهام فقط. "شنو عدنا باجر؟" = مهام + محاضرات ذلك اليوم
        tasks_only = bool(kinds) or bool(set(normalize(text).split()) & TASK_WORDS)
        return Reply(reply if tasks_only else self._with_lectures(reply, start, end))

    # ------------------------------------------------------------------
    # الأزرار
    # ------------------------------------------------------------------

    def handle_callback(self, data: str, sender: Sender) -> CallbackResult:
        if data.startswith(("m:", "x:")):
            # أزرار قديمة من نسخ سابقة (ميزة "خلصت" بالأزرار أُلغيت)
            return CallbackResult("هذا الزر انلغى، البطاقات الجديدة بدونه")
        if data.startswith("s:"):
            return self._subject_callback(data, sender)
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
