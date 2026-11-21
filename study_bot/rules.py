"""محلّل مجاني بالكامل لرسائل الطلاب، يعمل على الجهاز نفسه.

لا يحتاج أي مفتاح API، ولا يرسل أي بيانات لأي جهة خارجية. يعتمد على:
- قوائم كلمات للأنواع (امتحان، كويز، ميد، فاينل، واجب...) بلهجات مختلفة،
- البحث عن التعابير الزمنية داخل الرسالة وحسابها بـ dates.py،
- استخراج اسم المادة (ويتعلم أسماء المواد من المهام المسجلة سابقاً)،
- تمييز الإضافة من السؤال من التعديل من الإلغاء.

يرجع النتيجة بنفس شكل Analysis الذي يرجعه Claude، فباقي البوت لا يفرّق بينهما.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .arabic import NUMBER_WORDS, WEEKDAY_ALIASES, WEEKDAY_NAMES_AR, normalize
from .dates import (
    _AFTER_TOMORROW,
    _NEXT_WEEK,
    _THIS_WEEK,
    _TODAY,
    _TOMORROW,
    resolve_date_expression,
    week_bounds,
)
from .db import subject_key
from .llm import Analysis, AnalysisRequest, ExtractedTask, QuerySpec, TaskChanges
from .models import EXAM_KINDS, Task, kind_of

# ---------------------------------------------------------------------------
# القواميس
# ---------------------------------------------------------------------------

# (النوع، الكلمات بعد التطبيع). الترتيب مهم: الأكثر تحديداً أولاً
KIND_KEYWORDS: list[tuple[str, set[str]]] = [
    ("final", {"فاينل", "فاينال", "الفاينل", "الفاينال", "final", "finals", "نهائي", "النهائي"}),
    ("midterm", {"ميد", "الميد", "mid", "midterm", "ميدترم", "نصفي", "النصفي"}),
    ("quiz", {"كويز", "الكويز", "كوز", "quiz", "كويزات"}),
    ("presentation", {"برزنتيشن", "البرزنتيشن", "برزنتيشين", "presentation", "سمنار", "السمنار",
                      "seminar", "سيمينار", "عرض", "العرض", "برزنت"}),
    ("project", {"مشروع", "المشروع", "مشاريع", "project", "بروجكت", "البروجكت", "بروجيكت"}),
    ("report", {"تقرير", "التقرير", "تقارير", "report", "ريبورت", "الريبورت"}),
    ("lab", {"مختبر", "المختبر", "لاب", "اللاب", "lab", "عملي", "العملي"}),
    ("homework", {"واجب", "الواجب", "واجبات", "homework", "hw", "assignment", "اسايمنت", "الاسايمنت",
                  "شيت", "الشيت", "sheet", "تكليف", "التكليف"}),
    ("exam", {"امتحان", "الامتحان", "امتحانات", "اختبار", "الاختبار", "exam", "test", "تست", "الامتحانات"}),
    ("lecture", {"محاضره", "المحاضره", "lecture", "تعويضيه", "تعويضي"}),
]
_DEADLINE_WORDS = {"تسليم", "التسليم", "تسليمه", "تسليمها", "deadline", "ديدلاين", "سلموا", "نسلم", "نسلمه"}

_QUESTION_WORDS = {
    "شنو", "شكو", "وش", "ايش", "شو", "ماذا", "متى", "امتى", "هل", "شلون", "كم", "وين", "اشكو",
    "شني", "ليش", "منو",
}
_QUERY_WORDS = {"جدول", "الجدول", "مهام", "المهام", "مواعيد", "المواعيد", "وريني", "ورينا", "اعرض", "ذكرني", "ذكرنا"}
_PAST_WORDS = {
    "كان", "كانت", "جان", "جانت", "خلص", "خلصت", "انتهى", "انتهت", "الماضي", "الماضيه", "الفات",
    "فات", "طاف", "اللي طاف", "مر", "مرت", "سوينا", "قدمنا", "امس", "البارحه",
}
_UPDATE_WORDS = {
    "تاجل", "اتاجل", "تاجلت", "اتاجلت", "تاجيل", "التاجيل", "اجلوه", "اجلوها", "اجل", "انتقل", "انتقلت",
    "تغير", "تغيرت", "اتغير", "غيروه", "غيروها", "تحول", "تحولت", "صار", "صارت", "تقدم", "تقدمت",
    "اتقدم", "قدموه", "قدموها", "نقلوه", "نقلوها", "بدل", "تعديل", "عدل", "عدلوه",
}
_DELETE_WORDS = {
    "انلغى", "انلغت", "الغي", "الغيت", "الغوا", "الغوه", "الغوها", "ملغي", "ملغيه", "تلغى", "كنسل",
    "انكنسل", "cancel", "cancelled", "canceled", "احذف", "احذفه", "احذفها", "امسح", "امسحه", "امسحها",
    "شيله", "شيلها", "شيلوه", "ماكو", "مافي",
}
_DONE_WORDS = {
    "خلصت", "خلصته", "خلصتها", "خلصتهن", "سويته", "سويتها", "سويت", "انجزت", "انجزته", "انجزتها",
    "حليته", "حليتها", "حليت", "سلمت", "سلمته", "سلمتها", "كملته", "كملتها", "done", "finished",
}
_PERSONAL_WORDS = {"مهامي", "واجباتي", "امتحاناتي", "جدولي", "مواعيدي", "تكاليفي", "مشاريعي"}
_STOP_WORDS = {
    "مال", "ماله", "مالت", "مالة", "ماده", "الماده", "بماده", "لماده", "حق", "حقت", "تبع", "تبعت", "في",
    "ب", "على", "عن", "ل", "من", "عدنا", "عندنا", "عدكم", "عندكم", "علينا", "عليكم", "ترى", "تره",
    "شباب", "يا", "جماعه", "اكو", "فيه", "راح", "رح", "يكون", "يصير", "هو", "هي", "و", "او", "اليوم",
    "الساعه", "ساعه", "يوم", "بيوم", "تاريخ", "بتاريخ", "موعد", "موعده", "عدهم", "الدكتور", "دكتور",
    "الاستاذ", "استاذ", "الست", "ست", "قال", "قالت", "قلنا", "يقول", "تقول", "انتبهوا", "تذكير",
    "مهم", "جدا", "هم", "هذا", "هذه", "هاي", "هذي", "ذا", "اول", "ثاني", "يوجد", "لدينا", "نسلم",
    "الجاي", "القادم", "الياي", "الجايه", "القادمه", "الاسبوع", "اسبوع", "بعد", "قبل", "ان", "انه",
    "الصبح", "بالليل", "الظهر", "العصر", "مساء", "صباحا", "ص", "م", "صعب", "سهل", "كلش", "اخي",
    "ابو", "حبايب", "يمعود", "تسليم", "التسليم", "deadline", "ديدلاين", "لا", "نعم", "اي", "ايه",
    "خارجيه", "خارجي", "الخارجيه", "عند", "د", "دكتوره", "الدكتوره", "استاذه", "الاستاذه", "ست",
}
_CONTENT_MARKERS = {
    "الفصل", "فصل", "الفصول", "فصول", "شابتر", "الشابتر", "شابترات", "chapter", "chapters", "ch",
    "صفحه", "صفحات", "الصفحات", "تمارين", "التمارين", "سؤال", "اسئله", "الاسئله", "المحاضره",
    "المحاضرات", "محاضرات", "lecture", "lectures", "lec", "الوحده", "وحده", "unit", "المنهج",
    "منهج", "جزء", "الجزء", "المرحله", "مرحله", "part", "topic", "موضوع", "الموضوع", "كل", "اول",
    "تجربه", "التجربه", "slides", "سلايدات", "السلايدات", "ملزمه", "الملزمه",
}
# كلمات تبدأ بها أسماء مواد مركّبة من كلمتين ("هندسة برمجيات"، "قواعد بيانات")
_COMPOUND_SUBJECT_HEADS = {
    "هندسه", "مبادي", "مبادئ", "اساسيات", "قواعد", "تحليل", "نظم", "نظريه", "شبكات", "برمجه",
    "رياضيات", "لغه", "تصميم", "هياكل", "تراكيب", "معالجه", "امن", "اداره", "كيمياء", "فيزياء",
    "احياء", "حقوق", "تاريخ", "علم", "علوم", "ذكاء", "انظمه", "تقنيات", "طرق", "مختبر", "اقتصاد",
    "محاسبه", "تشريح", "فسلجه", "طب", "مقدمه", "اخلاقيات", "ثقافه", "تربيه", "ميكانيك", "دوائر",
}
_PM_WORDS = {"م", "مساء", "مساءا", "بالليل", "الليل", "ليلا", "العصر", "عصرا", "الظهر", "ظهرا", "pm", "المسا", "بليل"}
_AM_WORDS = {"ص", "صباحا", "الصبح", "صباح", "الصباح", "الفجر", "am", "صبحا"}
_EXAM_FAMILY_WORDS = {"امتحانات", "الامتحانات", "اختبارات", "الاختبارات", "exams"}



def _normalized(words: set[str]) -> set[str]:
    return {normalize(w) or w for w in words}


KIND_KEYWORDS = [(k, _normalized(ws)) for k, ws in KIND_KEYWORDS]
_DEADLINE_WORDS = _normalized(_DEADLINE_WORDS)
_QUESTION_WORDS = _normalized(_QUESTION_WORDS)
_QUERY_WORDS = _normalized(_QUERY_WORDS)
_PAST_WORDS = _normalized(_PAST_WORDS)
_UPDATE_WORDS = _normalized(_UPDATE_WORDS)
_DELETE_WORDS = _normalized(_DELETE_WORDS)
_STOP_WORDS = _normalized(_STOP_WORDS)
_DONE_WORDS = _normalized(_DONE_WORDS)
_PERSONAL_WORDS = _normalized(_PERSONAL_WORDS)
_CONTENT_MARKERS = _normalized(_CONTENT_MARKERS)
_COMPOUND_SUBJECT_HEADS = _normalized(_COMPOUND_SUBJECT_HEADS)
_PM_WORDS = _normalized(_PM_WORDS)
_AM_WORDS = _normalized(_AM_WORDS)
_EXAM_FAMILY_WORDS = _normalized(_EXAM_FAMILY_WORDS)
_KIND_OF_WORD = {w: k for k, words in KIND_KEYWORDS for w in words}

_PUNCT = re.compile(r"[،,؛;؟?!.:\"'()\[\]{}«»…\-–—_*~|/\\]")
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMERIC_DATE = re.compile(r"^\d{1,4}[/\-]\d{1,2}(?:[/\-]\d{2,4})?$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
_DECIMAL = re.compile(r"^\d+\.\d+$")
_TASK_REF = re.compile(r"#(\d+)")


# ---------------------------------------------------------------------------
# تقطيع النص
# ---------------------------------------------------------------------------

@dataclass
class Token:
    raw: str  # كما كُتب (بعد إزالة التشكيل وتحويل الأرقام)
    norm: str  # بعد التطبيع


def tokenize(text: str) -> list[Token]:
    text = _DIACRITICS.sub("", text.translate(_ARABIC_DIGITS))
    # نحافظ على 26/11 و 10:30 و 4.1 كما هي، ونفصل باقي الرموز
    protected = re.sub(r"(\d)([/:.\-])(\d)", lambda m: f"{m[1]}\u0001{ord(m[2])}\u0001{m[3]}", text)
    protected = _PUNCT.sub(" ", protected)
    protected = re.sub(r"\u0001(\d+)\u0001", lambda m: chr(int(m[1])), protected)
    tokens = []
    for raw in protected.split():
        norm = raw if re.fullmatch(r"[\d/:.\-]+", raw) else (normalize(raw) or raw.lower())
        tokens.append(Token(raw=raw, norm=norm))
    return tokens


def _date_word(norm: str) -> str:
    """يزيل حروف الجر الملتصقة بكلمات الوقت: "للخميس" -> "الخميس"، "لباجر" -> "باجر"."""
    if _is_date_word(norm):
        return norm
    candidates = []
    if norm.startswith("لل"):
        candidates.append("ال" + norm[2:])
    for prefix in ("بال", "وال", "فال", "لال", "عال"):
        if norm.startswith(prefix):
            candidates.append(norm[1:] if prefix != "عال" else "ال" + norm[3:])
    for prefix in ("ل", "ب", "و", "ع"):
        if norm.startswith(prefix):
            candidates.append(norm[1:])
    for c in candidates:
        if _is_date_word(c):
            return c
    return norm


def _is_date_word(w: str) -> bool:
    return (
        w in WEEKDAY_ALIASES or w in _TODAY or w in _TOMORROW or bool(_NUMERIC_DATE.match(w))
        or any(w == p.split()[0] for p in _AFTER_TOMORROW)
    )


@dataclass
class DateMatch:
    start: int
    end: int  # غير شامل
    expression: str
    value: date


def find_dates(tokens: list[Token], today: date, week_start: int) -> list[DateMatch]:
    """يبحث عن كل التعابير الزمنية في الرسالة (الأطول أولاً، بدون تداخل)."""
    words = [_date_word(t.norm) for t in tokens]
    matches: list[DateMatch] = []
    i = 0
    while i < len(words):
        found = None
        for length in range(min(6, len(words) - i), 0, -1):
            chunk = words[i:i + length]
            if any(_DECIMAL.match(w) for w in chunk):
                continue
            expr = " ".join(chunk)
            value = resolve_date_expression(expr, today, week_start)
            if value is not None:
                found = DateMatch(i, i + length, " ".join(t.raw for t in tokens[i:i + length]), value)
                break
        if found:
            matches.append(found)
            i = found.end
        else:
            i += 1
    return matches


def find_time(tokens: list[Token]) -> tuple[str | None, set[int]]:
    """يستخرج الوقت: "10:30"، "الساعة 2"، "الساعه 8 الصبح"، "الساعه 9 ونص"."""
    for i, t in enumerate(tokens):
        hour = minute = None
        used = {i}
        m = _CLOCK.match(t.norm)
        if m:
            hour, minute = int(m[1]), int(m[2])
        elif t.norm in ("الساعه", "ساعه", "بالساعه", "الساعة") and i + 1 < len(tokens):
            nxt = tokens[i + 1].norm
            m2 = _CLOCK.match(nxt)
            if m2:
                hour, minute = int(m2[1]), int(m2[2])
            elif nxt.isdigit():
                hour, minute = int(nxt), 0
            elif nxt in NUMBER_WORDS:
                hour, minute = NUMBER_WORDS[nxt], 0
            if hour is not None:
                used.add(i + 1)
                if i + 2 < len(tokens) and tokens[i + 2].norm in ("ونص", "ونصف"):
                    minute = 30
                    used.add(i + 2)
                elif i + 2 < len(tokens) and tokens[i + 2].norm in ("وربع",):
                    minute = 15
                    used.add(i + 2)
        if hour is None or not (0 <= hour <= 23 and 0 <= (minute or 0) <= 59):
            continue
        after = {tokens[j].norm for j in range(max(used) + 1, min(max(used) + 3, len(tokens)))}
        if after & _PM_WORDS and hour < 12:
            hour += 12
        elif after & _AM_WORDS:
            hour = 0 if hour == 12 else hour
        elif 1 <= hour <= 6:
            hour += 12  # "الساعة 2" بالدوام الجامعي = بعد الظهر
        return f"{hour:02d}:{minute or 0:02d}", used
    return None, set()


def find_kind(tokens: list[Token]) -> tuple[str | None, int | None]:
    best: tuple[int, int, str] | None = None  # (أولوية القائمة، الموضع، النوع)
    order = {k: n for n, (k, _) in enumerate(KIND_KEYWORDS)}
    for i, t in enumerate(tokens):
        word = t.norm
        if word.startswith(("بال", "وال", "لل")) and word not in _KIND_OF_WORD:
            word = ("ال" + word[2:]) if word.startswith("لل") else word[1:]
        kind = _KIND_OF_WORD.get(word)
        if kind and (best is None or order[kind] < best[0]):
            best = (order[kind], i, kind)
    if best:
        return best[2], best[1]
    for i, t in enumerate(tokens):
        if t.norm in _DEADLINE_WORDS:
            return "homework", i
    return None, None


# ---------------------------------------------------------------------------
# المادة والمحتوى
# ---------------------------------------------------------------------------

def _clean_subject_word(raw: str) -> str:
    w = raw
    for prefix in ("بال", "وال", "فال", "كال"):
        if w.startswith(prefix) and len(w) > 5:
            return w[len(prefix):]
    if w.startswith("لل") and len(w) > 4:
        return w[2:]
    if w.startswith("ال") and len(w) > 4:
        return w[2:]
    return w


def _subject_variants(raw: str) -> set[str]:
    """مفاتيح محتملة لكلمة: "بالفيزياء" -> {"بالفيزياء", "فيزياء"}."""
    key = subject_key(raw)
    out = {key}
    for prefix in ("بال", "وال", "فال", "كال"):
        if key.startswith(prefix):
            out.add(subject_key(key[1:]))
    if key.startswith("لل"):
        out.add(subject_key("ال" + key[2:]))
    for prefix in ("ب", "ل", "و"):
        if key.startswith(prefix) and len(key) > 3:
            out.add(key[1:])
    return out


def find_known_subject(tokens: list[Token], known: list[str]) -> tuple[str | None, set[int]]:
    variants = [_subject_variants(t.raw) for t in tokens]
    best: tuple[int, str, set[int]] | None = None
    for subject in known:
        target = subject_key(subject).split()
        if not target:
            continue
        n = len(target)
        for i in range(len(tokens) - n + 1):
            if all(target[k] in variants[i + k] for k in range(n)):
                if best is None or n > best[0]:
                    best = (n, subject, set(range(i, i + n)))
    return (best[1], best[2]) if best else (None, set())


def _is_subject_candidate(t: Token, used: set[int], i: int) -> bool:
    w = t.norm
    return (
        i not in used
        and w not in _STOP_WORDS
        and w not in _CONTENT_MARKERS
        and w not in _KIND_OF_WORD
        and w not in _QUESTION_WORDS
        and w not in _UPDATE_WORDS
        and w not in _DELETE_WORDS
        and w not in _PAST_WORDS
        and _date_word(w) not in WEEKDAY_ALIASES
        and not any(ch.isdigit() for ch in w)
        and len(w) >= 2
    )


def guess_subject(tokens: list[Token], kind_index: int | None, used: set[int]) -> tuple[str | None, set[int]]:
    """يخمّن اسم المادة: الكلمة (أو الكلمتان) بعد نوع الموعد، أو بعد كلمة "مادة"."""
    anchors = [i for i, t in enumerate(tokens) if t.norm in ("ماده", "الماده", "بماده", "لماده")]
    if kind_index is not None:
        anchors.append(kind_index)
    for anchor in anchors:
        j = anchor + 1
        while j < len(tokens) and j not in used and (tokens[j].norm in _STOP_WORDS or tokens[j].norm in _KIND_OF_WORD):
            j += 1
        if j < len(tokens) and _is_subject_candidate(tokens[j], used, j):
            first = _clean_subject_word(tokens[j].raw)
            picked = {j}
            if (
                normalize(first) in _COMPOUND_SUBJECT_HEADS
                and j + 1 < len(tokens)
                and _is_subject_candidate(tokens[j + 1], used, j + 1)
            ):
                picked.add(j + 1)
                return f"{first} {_clean_subject_word(tokens[j + 1].raw)}", picked
            return first, picked
    # "فيزياء عدنا امتحان": المادة قبل النوع مباشرة
    if kind_index is not None:
        j = kind_index - 1
        while j >= 0 and tokens[j].norm in _STOP_WORDS:
            j -= 1
        if j >= 0 and _is_subject_candidate(tokens[j], used, j):
            return _clean_subject_word(tokens[j].raw), {j}
    return None, set()


def find_content(tokens: list[Token], used: set[int]) -> str:
    """المحتوى يبدأ من أول كلمة مثل "الفصل" أو "تمارين" أو "صفحة"."""
    for i, t in enumerate(tokens):
        if i in used or t.norm not in _CONTENT_MARKERS:
            continue
        start = i
        if t.norm in ("كل",) and i + 1 < len(tokens) and tokens[i + 1].norm not in _CONTENT_MARKERS:
            continue
        words = []
        for j in range(start, len(tokens)):
            if j in used:
                if words:
                    break
                continue
            if tokens[j].norm in _KIND_OF_WORD or tokens[j].norm in _UPDATE_WORDS or tokens[j].norm in _DEADLINE_WORDS:
                break
            words.append(tokens[j].raw)
        while words and normalize(words[-1]) in _STOP_WORDS | {"و"}:
            words.pop()
        return " ".join(words)[:120]
    return ""


# ---------------------------------------------------------------------------
# المحلّل
# ---------------------------------------------------------------------------

def _empty(intent: str, **kw) -> Analysis:
    data = dict(intent=intent, tasks=[], target_task_id=None, changes=None, query=None, reply=None)
    data.update(kw)
    return Analysis(**data)


def _split_segments(text: str) -> list[str]:
    """يقسّم الرسالة إلى أجزاء، كل جزء قد يحتوي موعداً مستقلاً."""
    # النقطة بعد الألقاب المختصرة ("د. عمار"، "أ.م. طه") ليست نهاية جملة
    for _ in range(2):
        text = re.sub(r"(?:(?<=[\s.])|^)([دمأا])\.", r"\1 ", text)
    parts = re.split(r"[\n،;؛]+|(?<!\d)\.(?!\d)", text)
    kind_alt = "|".join(sorted((re.escape(w) for w in _KIND_OF_WORD if not w.isascii()), key=len, reverse=True))
    out = []
    for p in parts:
        out.extend(re.split(rf"\s+و(?=(?:{kind_alt})\b)", p))
    return [p.strip() for p in out if p and p.strip()]


class RuleBasedAnalyzer:
    """محلّل مجاني يعمل بدون إنترنت وبدون أي مفتاح."""

    async def analyze(self, req: AnalysisRequest) -> Analysis | None:
        return analyze_message(req)


def analyze_message(req: AnalysisRequest) -> Analysis:
    text = req.message
    tokens = tokenize(text)
    norms = [t.norm for t in tokens]
    word_set = set(norms)
    if not tokens:
        return _empty("ignore")

    dates = find_dates(tokens, req.today, req.week_start)
    time_value, time_used = find_time(tokens)
    kind, kind_index = find_kind(tokens)

    is_question = "؟" in text or "?" in text or bool(word_set & _QUESTION_WORDS) or bool(word_set & _QUERY_WORDS)
    replied_id = _replied_task_id(req.replied_to)

    # ----- "خلصت" (رداً على بطاقة، أو مع ذكر المهمة) -----
    talks_about_past = bool(word_set & (_PAST_WORDS - _DONE_WORDS))
    if word_set & _DONE_WORDS and not is_question and not talks_about_past:
        if replied_id or kind or _subject_in(tokens, req)[0]:
            target, reply = _find_target(tokens, req, kind, dates, replied_id, for_delete=True)
            if target:
                return _empty("done", target_task_id=target)
            if req.direct:
                return _empty("clarify", reply=reply)

    # ----- "شنو باقي علي؟" / "مهامي" -----
    if _is_personal_query(word_set):
        query = _build_query(tokens, req, kind, dates)
        if not dates:
            query.start_date = req.today.isoformat()
            query.end_date = (req.today + timedelta(days=30)).isoformat()
        query.only_mine = True
        return _empty("query", query=query)

    # ----- إلغاء -----
    if word_set & _DELETE_WORDS and (kind or replied_id or _subject_in(tokens, req)[0]):
        target, reply = _find_target(tokens, req, kind, dates, replied_id, for_delete=True)
        if target:
            return _empty("delete", target_task_id=target)
        return _empty("clarify", reply=reply) if req.direct else _empty("ignore")

    # ----- تعديل -----
    if word_set & _UPDATE_WORDS and (dates or time_value) and not is_question:
        target, reply = _find_target(tokens, req, kind, dates[:-1] if len(dates) > 1 else [], replied_id)
        if target:
            new_date = dates[-1] if dates else None
            return _empty(
                "update",
                target_task_id=target,
                changes=TaskChanges(
                    kind=None, subject=None, title=None,
                    date_expression=new_date.expression if new_date else None,
                    date=new_date.value.isoformat() if new_date else None,
                    time=time_value, notes=None,
                ),
            )
        if reply and req.direct:
            return _empty("clarify", reply=reply)

    # ----- سؤال -----
    if is_question:
        return _empty("query", query=_build_query(tokens, req, kind, dates))

    # ----- كلام عن الماضي -----
    if word_set & _PAST_WORDS:
        return _empty("ignore")

    # ----- إضافة -----
    if kind is not None:
        items = _extract_tasks(text, req)
        if items:
            return _empty("add", tasks=items)
        if req.direct:
            label = kind_of(kind).label
            return _empty("clarify", reply=f"فهمت إنه أكو {label}، بس متى بالضبط؟ اكتب اليوم أو التاريخ.")
        return _empty("ignore")

    if req.direct and dates and len(tokens) <= 4:
        # "باجر؟" أو "الأربعاء" وحدها في الخاص = سؤال عن جدول ذلك اليوم
        return _empty("query", query=_build_query(tokens, req, None, dates))
    return _empty("ignore")


def _is_personal_query(words: set[str]) -> bool:
    if words & _PERSONAL_WORDS:
        return True
    if "باقيلي" in words or ("باقي" in words and words & {"علي", "الي", "الى", "لي"}):
        return True
    # "شنو ما خلصت؟" / "شنو بعدني ما مخلص"
    return bool(words & {"ما", "مو"}) and bool(words & {"خلصت", "مخلص", "مخلصه", "خلصته"}) and bool(
        words & _QUESTION_WORDS
    )


_EXTERNAL_WORDS = {"خارجيه", "خارجي", "الخارجيه", "الخارجي", "external"}
_NEXT_LECTURE = (
    "المحاضره الجايه", "المحاضره القادمه", "المحاضره الياي", "المحاضره الياييه", "المحاضره الجاييه",
    "الحصه الجايه", "الحصه القادمه", "اللكچر الجاي", "المحاضره اللي جايه", "المحاضره الي جايه",
    "بالمحاضره الجايه", "بالمحاضره القادمه", "بالحصه الجايه", "next lecture",
)


def _subject_in(tokens: list[Token], req: AnalysisRequest) -> tuple[str | None, str, set[int]]:
    """المادة المذكورة: (الاسم المعتمد، الجزء مثل "أسس"، مواضع الكلمات)."""
    if req.catalog is not None:
        match = req.catalog.find([t.raw for t in tokens])
        if match:
            return match.subject, match.part, set(range(match.start, match.end))
        return None, "", set()
    name, used = find_known_subject(tokens, req.known_subjects)
    return name, "", used


def _extract_tasks(text: str, req: AnalysisRequest) -> list[ExtractedTask]:
    from .timetable import next_lecture_date

    segments = _split_segments(text)
    whole_dates = find_dates(tokenize(text), req.today, req.week_start)
    shared_date = whole_dates[0] if len(whole_dates) == 1 else None
    norm_text = " ".join(normalize(w) for w in text.split())
    external = bool(set(norm_text.split()) & _EXTERNAL_WORDS)
    items: list[ExtractedTask] = []
    for seg in segments:
        tokens = tokenize(seg)
        kind, kind_index = find_kind(tokens)
        if kind is None:
            continue
        dates = find_dates(tokens, req.today, req.week_start)
        used = set()
        for d in dates:
            used |= set(range(d.start, d.end))
        time_value, time_used = find_time(tokens)
        used |= time_used
        if kind_index is not None:
            used.add(kind_index)

        subject, part, subject_used = _subject_in(tokens, req)
        is_external = False
        if subject is None:
            stop = used | {i for i, t in enumerate(tokens) if t.norm in _EXTERNAL_WORDS}
            subject, subject_used = guess_subject(tokens, kind_index, stop)
            is_external = external and subject is not None
        used |= subject_used

        date_expression, due = None, None
        date_match = dates[0] if dates else shared_date
        if date_match is not None and normalize(date_match.expression) not in _NEXT_WEEK:
            date_expression, due = date_match.expression, date_match.value
        elif subject and any(p in norm_text for p in _NEXT_LECTURE):
            # "الواجب بالمحاضرة الجاية": نحسب موعد المحاضرة القادمة من جدول الدروس
            due = next_lecture_date(subject, req.today)
            date_expression = "المحاضرة الجاية" if due else None
        if due is None:
            continue

        title = find_content(tokens, used)
        if part and part not in title:
            title = f"{part} – {title}" if title else part
        items.append(ExtractedTask(
            kind=kind,
            subject=subject or "",
            title=title,
            date_expression=date_expression or "",
            date=due.isoformat(),
            time=time_value,
            notes=None,
            external=is_external,
        ))
    return items


def _replied_task_id(replied_to: str | None) -> int | None:
    if not replied_to:
        return None
    ids = _TASK_REF.findall(replied_to)
    return int(ids[-1]) if ids else None


def _kind_matches(task: Task, kind: str | None) -> bool:
    if kind is None:
        return True
    if kind in EXAM_KINDS and task.kind in EXAM_KINDS:
        return kind == task.kind or kind == "exam" or task.kind == "exam"
    return task.kind == kind


def _find_target(
    tokens: list[Token],
    req: AnalysisRequest,
    kind: str | None,
    dates: list[DateMatch],
    replied_id: int | None,
    for_delete: bool = False,
) -> tuple[int | None, str]:
    upcoming = [t for t in req.upcoming if t.due_date >= req.today]
    if replied_id and any(t.id == replied_id for t in upcoming):
        return replied_id, ""
    subject = _subject_in(tokens, req)[0]
    candidates = [
        t for t in upcoming
        if _kind_matches(t, kind) and (subject is None or subject_key(t.subject) == subject_key(subject))
    ]
    if kind is None and subject is None:
        candidates = []
    if dates and len(candidates) > 1:
        on_date = [t for t in candidates if t.due_date in {d.value for d in dates}]
        if on_date:
            candidates = on_date
    elif dates and for_delete:
        candidates = [t for t in candidates if t.due_date in {d.value for d in dates}] or candidates
    if len(candidates) == 1:
        return candidates[0].id, ""
    if not candidates:
        return None, "ما لقيت هذا الموعد بالجدول. اذكر المادة ونوع الموعد، أو اعمل رد (Reply) على بطاقة الموعد."
    listing = "\n".join(
        f"#{t.id} {kind_of(t.kind).label} {t.subject} ({WEEKDAY_NAMES_AR[t.due_date.weekday()]} {t.due_date.day}/{t.due_date.month})"
        for t in candidates[:6]
    )
    return None, f"لقيت أكثر من موعد، أي واحد تقصد؟ اعمل رد (Reply) على بطاقته:\n{listing}"


def _build_query(tokens: list[Token], req: AnalysisRequest, kind: str | None, dates: list[DateMatch]) -> QuerySpec:
    today = req.today
    text = " ".join(_date_word(t.norm) for t in tokens)
    this_start, this_end = week_bounds(today, req.week_start)
    title = ""

    weekday_dates = [d for d in dates if normalize(d.expression) not in _NEXT_WEEK]
    if weekday_dates:
        start = end = weekday_dates[0].value
        rel = {0: "اليوم", 1: "الغد"}.get((start - today).days)
        title = f"مهام {rel}" if rel else f"مهام يوم {WEEKDAY_NAMES_AR[start.weekday()]}"
    elif any(p in text for p in _NEXT_WEEK):
        start, end = this_start + timedelta(days=7), this_start + timedelta(days=13)
        title = "مهام الأسبوع القادم"
    elif any(p in text for p in _THIS_WEEK):
        start, end = today, this_end
        title = "بقية هذا الأسبوع"
    elif "الشهر الجاي" in text or "الشهر القادم" in text:
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        start = first_next
        end = (first_next + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        title = "مهام الشهر القادم"
    elif "الشهر" in text or "هالشهر" in text:
        start = today
        end = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        title = "بقية هذا الشهر"
    else:
        long_range = bool({"متى", "امتى"} & {t.norm for t in tokens}) or kind in ("final", "midterm")
        start, end = today, today + timedelta(days=180 if long_range else 14)
        title = "المهام القادمة"

    subject = _subject_in(tokens, req)[0]
    kinds: list[str] | None = None
    if kind is not None:
        kinds = sorted(EXAM_KINDS) if kind == "exam" else [kind]
    elif {t.norm for t in tokens} & _EXAM_FAMILY_WORDS:
        kinds = sorted(EXAM_KINDS)

    filters = []
    if kinds:
        filters.append("الامتحانات" if len(kinds) > 1 else kind_of(kinds[0]).label)
    if subject:
        filters.append(subject)
    if filters:
        title += " – " + " ".join(filters)
    return QuerySpec(
        start_date=start.isoformat(), end_date=end.isoformat(), subject=subject, kinds=kinds, title=title,
        only_mine=False,
    )
