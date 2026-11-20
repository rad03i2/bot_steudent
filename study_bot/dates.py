"""حساب التواريخ من التعابير العربية بشكل حتمي (بدون ذكاء اصطناعي).

الفكرة: نموذج الذكاء الاصطناعي يستخرج التعبير الزمني كما قاله الطالب
("الخميس الجاي"، "باجر"، "بعد أسبوعين"، "26/11")، وهذا الملف يحوّله إلى تاريخ
فعلي بدقة. إذا لم نفهم التعبير بثقة كاملة نرجع None ونعتمد على تقدير النموذج،
الذي يحصل بدوره على تقويم مرجعي جاهز لتقليل الأخطاء.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .arabic import MONTH_ALIASES, NUMBER_WORDS, WEEKDAY_ALIASES, WEEKDAY_NAMES_AR, normalize

SUNDAY = 6
SATURDAY = 5
MONDAY = 0

_PREFIX_WORDS = {"يوم", "في", "بيوم", "بتاريخ", "تاريخ", "موعد", "موعده", "على", "الساعه"}

_TODAY = {"اليوم", "هاليوم", "اليوم هذا"}
_TOMORROW = {"بكره", "بكرا", "باجر", "باكر", "غدا", "غد", "الغد", "غدوه", "بكرة", "بكره الصبح"}
_AFTER_TOMORROW = {"بعد بكره", "بعد بكرا", "بعد باجر", "بعد غد", "بعد الغد", "عقب باجر", "بعد باكر"}

_NEXT_WORDS = {"الجاي", "القادم", "الجايه", "القادمه", "الياي", "الجاييه", "اللي جاي", "الي جاي", "المقبل", "الماي"}
_THIS_WORDS = {"هذا", "هاذا", "ها", "هذي", "هذه"}
_AFTER_NEXT = {
    "بعد الجاي", "اللي بعده", "الي بعده", "اللي بعد الجاي", "الي بعد الجاي",
    "بعد القادم", "الذي يليه", "الجاي بعد", "بعد الياي",
}
_NEXT_WEEK = {
    "الاسبوع الجاي", "الاسبوع القادم", "الاسبوع الياي", "الاسبوع المقبل", "الاسبوع اللي جاي",
    "الاسبوع الي جاي", "من الاسبوع الجاي", "من الاسبوع القادم", "بالاسبوع الجاي",
}
_THIS_WEEK = {"هذا الاسبوع", "هالاسبوع", "بهذا الاسبوع", "من هذا الاسبوع", "الاسبوع هذا"}


def week_bounds(d: date, week_start: int = SUNDAY) -> tuple[date, date]:
    """بداية ونهاية الأسبوع الذي يقع فيه التاريخ d."""
    offset = (d.weekday() - week_start) % 7
    start = d - timedelta(days=offset)
    return start, start + timedelta(days=6)


def next_weekday(today: date, weekday: int, include_today: bool = True) -> date:
    """أقرب يوم قادم يطابق اسم اليوم."""
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and not include_today:
        delta = 7
    return today + timedelta(days=delta)


def _strip_prefixes(text: str) -> str:
    words = text.split()
    while words and words[0] in _PREFIX_WORDS:
        words = words[1:]
    # "بالخميس" -> "الخميس"
    if words and words[0].startswith("بال") and words[0][1:] in WEEKDAY_ALIASES:
        words[0] = words[0][1:]
    return " ".join(words)


def _parse_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _year_for(day: int, month: int, today: date) -> date | None:
    """تاريخ بدون سنة: نختار هذه السنة، وإذا كان قد مضى بأكثر من شهرين نختار السنة القادمة."""
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if (today - candidate).days > 60:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def resolve_date_expression(expr: str | None, today: date, week_start: int = SUNDAY) -> date | None:
    """يحوّل تعبيراً زمنياً عربياً إلى تاريخ. يرجع None إذا لم يفهمه بثقة."""
    if not expr:
        return None
    text = _strip_prefixes(normalize(expr))
    if not text:
        return None

    if text in _TODAY:
        return today
    if text in _TOMORROW:
        return today + timedelta(days=1)
    if text in _AFTER_TOMORROW:
        return today + timedelta(days=2)

    # ISO: 2026-11-26
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    # 26/11 أو 26/11/2026 أو 26-11-26
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?", text)
    if m:
        day, month = int(m[1]), int(m[2])
        if m[3]:
            year = int(m[3])
            year = year + 2000 if year < 100 else year
            try:
                return date(year, month, day)
            except ValueError:
                return None
        return _year_for(day, month, today)

    # 26 نوفمبر / 26 تشرين الثاني / 26 من شهر 11
    m = re.fullmatch(r"(\d{1,2}) (?:من )?(?:شهر )?(.+?)(?: (\d{4}))?", text)
    if m:
        month_token = m[2]
        month = MONTH_ALIASES.get(month_token)
        if month is None and month_token.isdigit():
            month = int(month_token) if 1 <= int(month_token) <= 12 else None
        if month is not None:
            if m[3]:
                try:
                    return date(int(m[3]), month, int(m[1]))
                except ValueError:
                    return None
            return _year_for(int(m[1]), month, today)

    # بعد N يوم/أيام/أسبوع/أسابيع
    m = re.fullmatch(r"(?:بعد|خلال) (?:(\S+) )?(يوم|يومين|ايام|اسبوع|اسبوعين|اسابيع|جمعه|جمعتين)", text)
    if m:
        count_token, unit = m[1], m[2]
        if unit in ("يومين", "اسبوعين", "جمعتين"):
            count = 2
        elif count_token is None:
            count = 1
        else:
            parsed = _parse_number(count_token)
            if parsed is None:
                return None
            count = parsed
        days_per_unit = 1 if unit.startswith("يوم") or unit == "ايام" else 7
        return today + timedelta(days=count * days_per_unit)

    if text in _NEXT_WEEK:
        start, _ = week_bounds(today, week_start)
        return start + timedelta(days=7)

    return _resolve_weekday_phrase(text, today, week_start)


def _resolve_weekday_phrase(text: str, today: date, week_start: int) -> date | None:
    words = text.split()
    weekday = None
    rest: list[str] = []
    for w in words:
        if weekday is None and w in WEEKDAY_ALIASES:
            weekday = WEEKDAY_ALIASES[w]
        else:
            rest.append(w)
    if weekday is None:
        return None
    modifier = " ".join(rest)

    if modifier == "" or modifier in _THIS_WORDS:
        return next_weekday(today, weekday, include_today=True)
    if modifier in _NEXT_WORDS:
        return next_weekday(today, weekday, include_today=False)
    if modifier in _AFTER_NEXT or modifier in {n + " بعد" for n in _NEXT_WORDS}:
        return next_weekday(today, weekday, include_today=False) + timedelta(days=7)
    if modifier in _NEXT_WEEK:
        start, _ = week_bounds(today, week_start)
        nxt = start + timedelta(days=7)
        return nxt + timedelta(days=(weekday - nxt.weekday()) % 7)
    if modifier in _THIS_WEEK:
        start, _ = week_bounds(today, week_start)
        return start + timedelta(days=(weekday - start.weekday()) % 7)
    return None


def calendar_reference(today: date, days: int = 60, week_start: int = SUNDAY) -> str:
    """تقويم مرجعي يُعطى للنموذج حتى لا يحسب التواريخ من رأسه."""
    lines = []
    this_start, _ = week_bounds(today, week_start)
    for i in range(-3, days):
        d = today + timedelta(days=i)
        week_index = (week_bounds(d, week_start)[0] - this_start).days // 7
        if week_index == 0:
            week_label = "هذا الأسبوع"
        elif week_index == 1:
            week_label = "الأسبوع القادم"
        elif week_index == -1:
            week_label = "الأسبوع الماضي"
        else:
            week_label = f"بعد {week_index} أسابيع"
        tag = ""
        if i == 0:
            tag = " ← اليوم"
        elif i == 1:
            tag = " ← غداً"
        lines.append(f"{d.isoformat()} {WEEKDAY_NAMES_AR[d.weekday()]} ({week_label}){tag}")
    return "\n".join(lines)


def relative_day_label(d: date, today: date) -> str | None:
    diff = (d - today).days
    return {0: "اليوم", 1: "غداً", 2: "بعد غد", -1: "أمس"}.get(diff)


def days_left_phrase(d: date, today: date) -> str:
    diff = (d - today).days
    if diff < 0:
        n = -diff
        return "انتهى أمس" if n == 1 else f"انتهى منذ {_count_days(n)}"
    if diff == 0:
        return "اليوم!"
    if diff == 1:
        return "غداً"
    return f"باقي {_count_days(diff)}"


def _count_days(n: int) -> str:
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومان"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوماً"
