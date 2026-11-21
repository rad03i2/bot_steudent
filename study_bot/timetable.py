"""جدول الدروس ومواد الشعبة.

المصدر: جدول الدروس الرسمي - جامعة الموصل / كلية الهندسة / قسم هندسة البيئة،
المستوى الرابع - بولونيا - شعبة (A) - الفصل الخريفي - قاعة 5 رقم (2825 ط2).

البوت يعتمد مواد هذا الجدول فقط. أي مادة أخرى لا تُحفظ إلا إذا قال الطالب
صراحةً إنها "مادة خارجية"، فتُضاف لقائمة المواد الخارجية في قاعدة البيانات.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .arabic import normalize

SECTION_TITLE = "المرحلة الرابعة – بولونيا – شعبة A"
SECTION_DETAILS = "قسم هندسة البيئة • الفصل الخريفي • قاعة 5 (2825 ط2)"

# datetime.weekday(): الاثنين = 0 ... الأحد = 6
SUN, MON, TUE, WED, THU = 6, 0, 1, 2, 3


@dataclass(frozen=True)
class Subject:
    name: str  # الاسم الرسمي كما في الجدول
    code: str
    units: int
    teachers: tuple[str, ...]
    aliases: tuple[str, ...] = ()  # أسماء مختصرة يكتبها الطلاب
    teacher_keys: tuple[str, ...] = ()  # أسماء الأساتذة التي تدل على المادة
    color: tuple[int, int, int] = (90, 100, 120)


@dataclass(frozen=True)
class Lecture:
    weekday: int
    start: str  # HH:MM بنظام 24 ساعة
    end: str
    subject: str  # اسم المادة الرسمي
    part: str = ""  # مثل "أسس"
    teachers: tuple[str, ...] = ()
    note: str = ""


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        "تصاميم انشائية", "ENEV412", 6,
        ("د. سمير سعدي شماس", "د. ايمن وليد", "د. محمد ظافر"),
        aliases=("تصاميم انشائيه", "انشائيه", "الانشائيه", "التصاميم الانشائيه", "اسس", "الاسس",
                 "انشائي", "structural", "structures"),
        teacher_keys=("سمير", "سمير شماس", "شماس", "ايمن", "ايمن وليد", "محمد ظافر", "ظافر"),
        color=(30, 136, 229),
    ),
    Subject(
        "تصاميم محطات معالجة مياه الفضلات", "ENV411", 6,
        ("أ.م. د. عمار ثامر حمد",),
        aliases=("محطات", "المحطات", "تصاميم المحطات", "محطات المعالجه", "معالجه مياه الفضلات",
                 "مياه الفضلات", "الفضلات", "wastewater", "treatment"),
        teacher_keys=("عمار", "عمار ثامر"),
        color=(0, 137, 123),
    ),
    Subject(
        "المشروع الهندسي 1", "ENV417", 3, (),
        aliases=("المشروع الهندسي", "مشروع هندسي", "مشروع التخرج", "graduation project"),
        color=(142, 36, 170),
    ),
    Subject(
        "تقييم الأثر البيئي", "ENV414", 3,
        ("أ.م. طه احمد", "م. احمد ياسين"),
        aliases=("تقييم الاثر", "الاثر البيئي", "الاثر", "تقييم", "eia"),
        teacher_keys=("طه", "طه احمد", "احمد ياسين", "ياسين"),
        color=(67, 160, 71),
    ),
    Subject(
        "ادارة المشاريع والاقتصاد الهندسي", "ENEV413", 4,
        ("راكان فاروق",),
        aliases=("ادارة المشاريع", "اداره المشاريع", "الاداره", "اداره", "الاقتصاد الهندسي",
                 "الاقتصاد", "اقتصاد", "management", "economics"),
        teacher_keys=("راكان", "راكان فاروق"),
        color=(244, 81, 30),
    ),
    Subject(
        "اساسيات النفايات الخطرة", "ENV416", 4,
        ("د. انس فخري", "د. حامد ادريس"),
        aliases=("النفايات الخطره", "النفايات", "نفايات", "الخطره", "نفايات خطره", "hazardous"),
        teacher_keys=("انس", "انس فخري", "حامد", "حامد ادريس"),
        color=(229, 57, 53),
    ),
    Subject(
        "تلوث التربة والمياه الجوفية", "ENEV415", 4,
        ("د. وعد محمد علي",),
        aliases=("تلوث التربه", "التربه", "تربه", "المياه الجوفيه", "الجوفيه", "groundwater", "soil"),
        # "وعد" كلمة عادية أيضاً، فنقبلها فقط مع لقب
        teacher_keys=("د وعد", "دكتوره وعد", "الدكتوره وعد", "ست وعد", "وعد محمد"),
        color=(121, 85, 72),
    ),
)

LECTURES: tuple[Lecture, ...] = (
    Lecture(SUN, "08:00", "10:00", "تصاميم انشائية", teachers=("د. سمير سعدي شماس",), note="القاعة المدرجة"),
    Lecture(SUN, "10:00", "12:00", "تصاميم محطات معالجة مياه الفضلات", teachers=("أ.م. د. عمار ثامر حمد",), note="مقررات"),
    Lecture(SUN, "12:00", "14:00", "المشروع الهندسي 1"),
    Lecture(MON, "08:00", "10:00", "تقييم الأثر البيئي", teachers=("أ.م. طه احمد", "م. احمد ياسين")),
    Lecture(MON, "10:00", "12:00", "ادارة المشاريع والاقتصاد الهندسي", teachers=("راكان فاروق",), note="بالقاعة المدرجة"),
    Lecture(MON, "12:00", "14:00", "اساسيات النفايات الخطرة", teachers=("د. انس فخري", "د. حامد ادريس")),
    Lecture(TUE, "10:00", "12:00", "تصاميم محطات معالجة مياه الفضلات", teachers=("أ.م. د. عمار ثامر حمد",), note="مقررات"),
    Lecture(TUE, "12:00", "14:00", "اساسيات النفايات الخطرة", teachers=("د. انس فخري", "د. حامد ادريس")),
    Lecture(WED, "09:00", "10:00", "تصاميم محطات معالجة مياه الفضلات", teachers=("أ.م. د. عمار ثامر حمد",), note="مقررات"),
    Lecture(WED, "10:00", "12:00", "تصاميم انشائية", part="أسس", teachers=("د. ايمن وليد", "د. محمد ظافر"), note="مقررات ثالث"),
    Lecture(WED, "12:00", "14:00", "ادارة المشاريع والاقتصاد الهندسي", teachers=("راكان فاروق",), note="بالقاعة المدرجة"),
    Lecture(THU, "08:00", "10:00", "تلوث التربة والمياه الجوفية", teachers=("د. وعد محمد علي",)),
    Lecture(THU, "10:00", "11:00", "تصاميم انشائية", teachers=("د. سمير سعدي شماس",)),
    Lecture(THU, "12:00", "13:00", "تصاميم انشائية", part="أسس", teachers=("د. ايمن وليد", "د. محمد ظافر"), note="مقررات ثالث"),
)

SUBJECT_BY_NAME = {s.name: s for s in SUBJECTS}


# ---------------------------------------------------------------------------
# مطابقة النص مع المواد
# ---------------------------------------------------------------------------

def _key(word: str) -> str:
    w = normalize(word)
    if w.startswith("ال") and len(w) > 4:
        w = w[2:]
    return w


def _variants(word: str) -> set[str]:
    """"بالنفايات" -> {"بالنفايات", "نفايات"}، "للمحطات" -> {"محطات"} ..."""
    w = normalize(word)
    out = {_key(w)}
    for prefix in ("بال", "وال", "فال", "كال", "لل"):
        if w.startswith(prefix) and len(w) > len(prefix) + 2:
            out.add(_key("ال" + w[len(prefix):]))
    for prefix in ("ب", "ل", "و"):
        if w.startswith(prefix) and len(w) > 3:
            out.add(_key(w[1:]))
    return out


def _phrase_keys(phrase: str) -> list[str]:
    return [_key(w) for w in normalize(phrase).split()]


@dataclass
class _Entry:
    keys: list[str]
    subject: str
    part: str = ""
    by_teacher: bool = False


@dataclass
class Match:
    subject: str
    part: str
    start: int  # موضع أول كلمة في الرسالة
    end: int
    by_teacher: bool


@dataclass
class Catalog:
    """مواد الشعبة + المواد الخارجية التي أضافها الطلاب."""

    external: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [s.name for s in SUBJECTS] + list(self.external)

    def _entries(self) -> list[_Entry]:
        entries: list[_Entry] = []
        for s in SUBJECTS:
            entries.append(_Entry(_phrase_keys(s.name), s.name))
            for a in s.aliases:
                part = "أسس" if normalize(a) in ("اسس", "الاسس") else ""
                entries.append(_Entry(_phrase_keys(a), s.name, part))
            for t in s.teacher_keys:
                part = "أسس" if normalize(t) in ("ايمن", "ايمن وليد", "محمد ظافر", "ظافر") else ""
                entries.append(_Entry(_phrase_keys(t), s.name, part, by_teacher=True))
        for name in self.external:
            entries.append(_Entry(_phrase_keys(name), name))
        # الأطول أولاً حتى تغلب "تصاميم محطات..." على "تصاميم"
        entries.sort(key=lambda e: len(e.keys), reverse=True)
        return entries

    def find(self, words: list[str]) -> Match | None:
        """يبحث عن مادة في كلمات الرسالة (أطول تطابق، وأول ظهور)."""
        variants = [_variants(w) for w in words]
        best: Match | None = None
        for entry in self._entries():
            n = len(entry.keys)
            for i in range(len(words) - n + 1):
                if all(entry.keys[k] in variants[i + k] for k in range(n)):
                    candidate = Match(entry.subject, entry.part, i, i + n, entry.by_teacher)
                    if best is None or (n > best.end - best.start) or (
                        n == best.end - best.start and i < best.start
                    ):
                        best = candidate
                    break
        return best

    def resolve(self, name: str | None) -> str | None:
        """يحوّل اسماً حراً (من الذكاء الاصطناعي مثلاً) إلى اسم مادة معتمد، أو None."""
        if not name:
            return None
        if name in SUBJECT_BY_NAME or name in self.external:
            return name
        match = self.find(name.split())
        return match.subject if match else None

    def is_external(self, name: str) -> bool:
        return name in self.external


# ---------------------------------------------------------------------------
# المحاضرات
# ---------------------------------------------------------------------------

def lectures_on(d: date) -> list[Lecture]:
    return sorted((lec for lec in LECTURES if lec.weekday == d.weekday()), key=lambda lec: lec.start)


def next_lecture_date(subject: str, today: date, *, include_today: bool = False) -> date | None:
    """تاريخ المحاضرة القادمة للمادة ("الواجب بالمحاضرة الجاية")."""
    days = {lec.weekday for lec in LECTURES if lec.subject == subject}
    if not days:
        return None
    for offset in range(0 if include_today else 1, 8):
        d = today + timedelta(days=offset)
        if d.weekday() in days:
            return d
    return None


def time_range_text(lec: Lecture) -> str:
    def fmt(t: str) -> str:
        hh, mm = (int(x) for x in t.split(":"))
        return f"{hh % 12 or 12}:{mm:02d}"

    return f"{fmt(lec.start)} – {fmt(lec.end)}"


def lecture_lines_html(day: date) -> list[str]:
    """سطور محاضرات يوم معيّن بصيغة HTML لتيليجرام."""
    from html import escape

    lines = []
    for lec in lectures_on(day):
        name = lec.subject + (f" ({lec.part})" if lec.part else "")
        line = f"🕘 <b>{time_range_text(lec)}</b>  {escape(name)}"
        # الملاحظات (القاعة المدرجة، مقررات...) لا تُعرض: الأساتذة فقط
        extra = " + ".join(lec.teachers)
        if extra:
            line += f"\n      <i>{escape(extra)}</i>"
        lines.append(line)
    return lines
