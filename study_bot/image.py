"""رسم جدول الأسبوع كصورة PNG أنيقة (من اليمين لليسار).

نستخدم arabic-reshaper لوصل الحروف العربية و python-bidi لترتيبها من اليمين،
ثم نرسم كل جزء بالخط المناسب: Noto Sans Arabic للعربي، و Noto Sans للإنجليزي.
الخطوط مرفقة في study_bot/assets/fonts (رخصة SIL OFL).
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import arabic_reshaper
from PIL import Image, ImageDraw, ImageFont

try:  # python-bidi >= 0.5
    from bidi import get_display
except ImportError:  # pragma: no cover - الإصدارات الأقدم
    from bidi.algorithm import get_display

from .arabic import WEEKDAY_NAMES_AR
from .formatting import short_date, sort_key
from .models import Task

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

WIDTH = 1080
PAD = 44
SCALE = 1  # للتكبير إن احتجنا دقة أعلى

COLORS = {
    "bg": (243, 245, 250),
    "header": (30, 58, 138),
    "header_sub": (191, 205, 245),
    "card": (255, 255, 255),
    "card_today": (255, 248, 225),
    "border_today": (251, 192, 45),
    "text": (33, 37, 41),
    "muted": (120, 128, 140),
    "empty": (170, 176, 186),
    "line": (230, 233, 240),
    "footer": (120, 128, 140),
}
PRIORITY_COLORS = {
    0: (229, 57, 53),
    1: (245, 124, 0),
    2: (249, 168, 37),
    3: (67, 160, 71),
    4: (144, 151, 160),
}


# ---------------------------------------------------------------------------
# الخطوط والنص
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _font(kind: str, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    name = {
        ("ar", False): "NotoSansArabic-Regular.ttf",
        ("ar", True): "NotoSansArabic-Bold.ttf",
        ("lat", False): "NotoSans-Regular.ttf",
        ("lat", True): "NotoSans-Bold.ttf",
    }[(kind, bold)]
    # BASIC: النص مرتّب مسبقاً بـ bidi، فلا نريد أن يعيد Pillow ترتيبه
    return ImageFont.truetype(str(FONTS_DIR / name), size * SCALE, layout_engine=ImageFont.Layout.BASIC)


def _is_arabic_glyph(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x0600 <= cp <= 0x06FF
        or 0x0750 <= cp <= 0x077F
        or 0x08A0 <= cp <= 0x08FF
        or 0xFB50 <= cp <= 0xFDFF
        or 0xFE70 <= cp <= 0xFEFF
    )


def _runs(visual: str) -> list[tuple[str, str]]:
    """يقسّم النص (بترتيبه المرئي) إلى أجزاء حسب الخط المطلوب."""
    runs: list[tuple[str, str]] = []
    for ch in visual:
        if _is_arabic_glyph(ch):
            kind = "ar"
        elif ch == " " and runs:
            kind = runs[-1][0]
        else:
            kind = "lat"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def _visual(text: str) -> str:
    # base_dir="R": الاتجاه الأساسي من اليمين لليسار حتى في النصوص المختلطة ("كويز Physics – chapter 3")
    visual = get_display(arabic_reshaper.reshape(text), base_dir="R")
    # python-bidi لا يعكس الأقواس، فنعكسها في النص العربي حتى تظهر "(أسس)" وليس ")أسس("
    if any(_is_arabic_glyph(ch) for ch in text):
        visual = visual.translate(_MIRROR)
    return visual


def text_width(text: str, size: int, bold: bool = False) -> int:
    return sum(
        int(_font(kind, bold, size).getlength(chunk)) for kind, chunk in _runs(_visual(text))
    )


def draw_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    right: int | None = None,
    left: int | None = None,
    y: int,
    size: int,
    fill,
    bold: bool = False,
) -> int:
    """يرسم النص محاذياً لليمين (افتراضياً) أو لليسار. يرجع عرض النص."""
    runs = _runs(_visual(text))
    widths = [int(_font(kind, bold, size).getlength(chunk)) for kind, chunk in runs]
    total = sum(widths)
    x = left if left is not None else (right or 0) - total
    for (kind, chunk), w in zip(runs, widths):
        font = _font(kind, bold, size)
        # نوحّد خط الأساس بين الخطين
        ascent = font.getmetrics()[0]
        base = _font("ar", bold, size).getmetrics()[0]
        draw.text((x, y + (base - ascent)), chunk, font=font, fill=fill)
        x += w
    return total


def centered_y(top: int, box_height: int, size: int, bold: bool = False, text: str = "اليوم ثقيل") -> int:
    """قيمة y التي تجعل النص في منتصف صندوق ارتفاعه box_height."""
    font = _font("ar", bold, size)
    _, t, _, b = font.getbbox(_visual(text))
    return top + (box_height - (b - t)) // 2 - t


def _pill(draw, text: str, *, left: int, top: int, fill, text_fill, size: int = 22) -> None:
    height = 38
    width = text_width(text, size, True) + 30
    draw.rounded_rectangle([left, top, left + width, top + height], radius=height // 2, fill=fill)
    draw_text(draw, text, left=left + 15, y=centered_y(top, height, size, True, text), size=size, fill=text_fill, bold=True)


def fit_text(text: str, max_width: int, size: int, bold: bool = False) -> str:
    """يقصّ النص ويضيف "…" إذا كان أعرض من المساحة المتاحة."""
    if text_width(text, size, bold) <= max_width:
        return text
    words = text.split()
    while len(words) > 1:
        words.pop()
        candidate = " ".join(words) + "…"
        if text_width(candidate, size, bold) <= max_width:
            return candidate
    return text[: max(1, len(text) // 2)] + "…"


# ---------------------------------------------------------------------------
# الجدول الأسبوعي
# ---------------------------------------------------------------------------

def _time_label(t: str | None) -> str:
    if not t:
        return ""
    hh, mm = (int(x) for x in t.split(":")[:2])
    return f"{hh % 12 or 12}:{mm:02d} {'ص' if hh < 12 else 'م'}"


DAY_HEADER = 70
TASK_ROW = 56
EMPTY_ROW = 44
CARD_GAP = 16
CARD_PAD = 20


def render_week(
    tasks: list[Task],
    *,
    start: date,
    today: date,
    title: str = "جدول الأسبوع",
    style: str = "levantine",
) -> bytes:
    """يرسم أيام الأسبوع السبعة بدءاً من start ويرجع PNG."""
    days = [start + timedelta(days=i) for i in range(7)]
    by_day: dict[date, list[Task]] = defaultdict(list)
    for t in tasks:
        by_day[t.due_date].append(t)

    header_h = 170
    body_h = 0
    for d in days:
        n = len(by_day.get(d, []))
        body_h += DAY_HEADER + (n * TASK_ROW if n else EMPTY_ROW) + CARD_PAD + CARD_GAP
    footer_h = 90
    height = header_h + PAD + body_h + footer_h

    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    right = WIDTH - PAD

    # ----- الترويسة -----
    draw.rectangle([0, 0, WIDTH, header_h], fill=COLORS["header"])
    draw_text(draw, title, right=right, y=34, size=48, fill=(255, 255, 255), bold=True)
    draw_text(draw, f"من {short_date(days[0])} إلى {short_date(days[-1])}", right=right, y=104, size=28, fill=COLORS["header_sub"])
    total = len([t for t in tasks if days[0] <= t.due_date <= days[-1]])
    badge = f"{total} مهام" if 3 <= total <= 10 else ("مهمة واحدة" if total == 1 else ("مهمتان" if total == 2 else f"{total} مهمة"))
    bw = text_width(badge, 26, True) + 40
    draw.rounded_rectangle([PAD, 58, PAD + bw, 108], radius=25, fill=(255, 255, 255))
    draw_text(draw, badge, left=PAD + 20, y=centered_y(58, 50, 26, True), size=26, fill=COLORS["header"], bold=True)

    # ----- الأيام -----
    y = header_h + PAD
    for d in days:
        day_tasks = sorted(by_day.get(d, []), key=sort_key)
        card_h = DAY_HEADER + (len(day_tasks) * TASK_ROW if day_tasks else EMPTY_ROW) + CARD_PAD
        is_today = d == today
        draw.rounded_rectangle(
            [PAD, y, WIDTH - PAD, y + card_h],
            radius=22,
            fill=COLORS["card_today"] if is_today else COLORS["card"],
            outline=COLORS["border_today"] if is_today else COLORS["line"],
            width=3 if is_today else 2,
        )
        inner_r = WIDTH - PAD - 28
        inner_l = PAD + 28
        day_label = f"{WEEKDAY_NAMES_AR[d.weekday()]}  {short_date(d)}"
        draw_text(draw, day_label, right=inner_r, y=y + 12, size=30, fill=COLORS["text"], bold=True)
        if is_today:
            _pill(draw, "اليوم", left=inner_l, top=y + 14, fill=COLORS["border_today"], text_fill=(60, 45, 0))
        elif len(day_tasks) >= 3 or sum(1 for t in day_tasks if t.priority <= 2) >= 2:
            _pill(draw, "يوم ثقيل", left=inner_l, top=y + 14, fill=PRIORITY_COLORS[0], text_fill=(255, 255, 255))

        ty = y + DAY_HEADER
        draw.line([inner_l, ty - 6, inner_r, ty - 6], fill=COLORS["line"], width=2)
        if not day_tasks:
            draw_text(draw, "لا توجد مهام", right=inner_r, y=ty + 4, size=24, fill=COLORS["empty"])
        for t in day_tasks:
            color = PRIORITY_COLORS.get(t.priority, PRIORITY_COLORS[4])
            # شريط ملوّن على اليمين حسب الأهمية
            draw.rounded_rectangle([inner_r - 8, ty + 10, inner_r, ty + TASK_ROW - 10], radius=4, fill=color)
            time_text = _time_label(t.due_time)
            time_w = 0
            if time_text:
                time_w = draw_text(draw, time_text, left=inner_l, y=ty + 10, size=24, fill=COLORS["muted"]) + 24
            label = f"{t.info.label} {t.subject}"
            label_w = draw_text(draw, label, right=inner_r - 22, y=ty + 8, size=28, fill=color, bold=True)
            if t.title:
                space = (inner_r - 22 - label_w - 16) - (inner_l + time_w)
                if space > 60:
                    detail = fit_text(f"– {t.title}", space, 24)
                    draw_text(draw, detail, right=inner_r - 22 - label_w - 12, y=ty + 12, size=24, fill=COLORS["muted"])
            ty += TASK_ROW
        y += card_h + CARD_GAP

    # ----- التذييل -----
    draw_text(draw, "مُذكِّر الدفعة", right=right, y=height - footer_h + 22, size=26, fill=COLORS["footer"], bold=True)
    legend = [(0, "فاينل"), (1, "امتحان/ميد"), (2, "كويز/مشروع"), (3, "واجب/تقرير")]
    lx = PAD
    for prio, name in legend:
        draw.ellipse([lx, height - footer_h + 34, lx + 18, height - footer_h + 52], fill=PRIORITY_COLORS[prio])
        lx += 26
        lx += draw_text(draw, name, left=lx, y=height - footer_h + 26, size=22, fill=COLORS["footer"]) + 22

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# جدول الدروس الأسبوعي
# ---------------------------------------------------------------------------

LESSON_ROW = 92
LESSON_DAY_HEADER = 64


def render_timetable(today: date | None = None, day: int | None = None) -> bytes:
    """صورة جدول دروس الشعبة: اليوم، الوقت، المادة، الأستاذ.

    day: رقم اليوم (datetime.weekday). إذا حُدد نقصّ من الصورة الكاملة بطاقة ذلك اليوم فقط،
    بدون الترويسة وبدون باقي الأيام.
    """
    from .timetable import LECTURES, SECTION_DETAILS, SECTION_TITLE, SUBJECT_BY_NAME, SUN, time_range_text

    order = [SUN, 0, 1, 2, 3]  # الأحد إلى الخميس
    by_day = {d: sorted((lec for lec in LECTURES if lec.weekday == d), key=lambda lec: lec.start) for d in order}

    header_h = 190
    body_h = sum(LESSON_DAY_HEADER + len(by_day[d]) * LESSON_ROW + CARD_PAD + CARD_GAP for d in order)
    footer_h = 70
    height = header_h + PAD + body_h + footer_h
    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    right = WIDTH - PAD

    draw.rectangle([0, 0, WIDTH, header_h], fill=COLORS["header"])
    draw_text(draw, "جدول الدروس", right=right, y=26, size=50, fill=(255, 255, 255), bold=True)
    draw_text(draw, SECTION_TITLE, right=right, y=100, size=28, fill=(255, 255, 255))
    draw_text(draw, SECTION_DETAILS, right=right, y=142, size=22, fill=COLORS["header_sub"])

    y = header_h + PAD
    boxes: dict[int, tuple[int, int]] = {}
    for d in order:
        lessons = by_day[d]
        card_h = LESSON_DAY_HEADER + len(lessons) * LESSON_ROW + CARD_PAD
        boxes[d] = (y, y + card_h)
        is_today = today is not None and today.weekday() == d
        draw.rounded_rectangle(
            [PAD, y, WIDTH - PAD, y + card_h],
            radius=22,
            fill=COLORS["card_today"] if is_today else COLORS["card"],
            outline=COLORS["border_today"] if is_today else COLORS["line"],
            width=3 if is_today else 2,
        )
        inner_r = WIDTH - PAD - 28
        inner_l = PAD + 28
        draw_text(draw, WEEKDAY_NAMES_AR[d], right=inner_r, y=y + 10, size=32, fill=COLORS["text"], bold=True)
        if is_today:
            _pill(draw, "اليوم", left=inner_l, top=y + 14, fill=COLORS["border_today"], text_fill=(60, 45, 0))
        ty = y + LESSON_DAY_HEADER
        for lec in lessons:
            draw.line([inner_l, ty, inner_r, ty], fill=COLORS["line"], width=2)
            subject = SUBJECT_BY_NAME[lec.subject]
            # الوقت في شارة ملونة على اليمين
            # الوقت يُكتب من اليسار لليمين كما في الجدول الرسمي: 8:00 – 10:00
            time_text = time_range_text(lec)
            time_font = _font("lat", True, 24)
            tw = int(time_font.getlength(time_text)) + 28
            box_top = ty + 14
            draw.rounded_rectangle([inner_r - tw, box_top, inner_r, box_top + 40], radius=12, fill=subject.color)
            _, bt, _, bb = time_font.getbbox(time_text)
            draw.text((inner_r - tw + 14, box_top + (40 - (bb - bt)) // 2 - bt), time_text, font=time_font,
                      fill=(255, 255, 255))
            name = lec.subject + (f" ({lec.part})" if lec.part else "")
            text_r = inner_r - tw - 18
            name = fit_text(name, text_r - inner_l, 27, True)
            draw_text(draw, name, right=text_r, y=ty + 10, size=27, fill=COLORS["text"], bold=True)
            details = " + ".join(lec.teachers) if lec.teachers else ""  # بدون الملاحظات
            if details:
                details = fit_text(details, text_r - inner_l, 21)
                draw_text(draw, details, right=text_r, y=ty + 52, size=21, fill=COLORS["muted"])
            ty += LESSON_ROW
        y += card_h + CARD_GAP

    draw_text(draw, "مُذكِّر الدفعة", right=right, y=height - footer_h + 14, size=24, fill=COLORS["footer"], bold=True)
    draw_text(draw, "الجمعة والسبت عطلة", left=PAD, y=height - footer_h + 16, size=22, fill=COLORS["footer"])
    if day is not None and day in boxes:
        top, bottom = boxes[day]
        margin = 14
        img = img.crop((PAD - margin, top - margin, WIDTH - PAD + margin, bottom + margin))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
