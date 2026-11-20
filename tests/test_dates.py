from datetime import date

import pytest

from study_bot.dates import (
    MONDAY,
    SATURDAY,
    SUNDAY,
    calendar_reference,
    days_left_phrase,
    resolve_date_expression,
    week_bounds,
)

FRIDAY = date(2026, 11, 20)  # الجمعة
MON = date(2026, 11, 23)  # الاثنين
THU = date(2026, 11, 26)  # الخميس


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("اليوم", FRIDAY),
        ("باجر", date(2026, 11, 21)),
        ("بكرة", date(2026, 11, 21)),
        ("غداً", date(2026, 11, 21)),
        ("بعد باجر", date(2026, 11, 22)),
        ("بعد غد", date(2026, 11, 22)),
        ("الخميس", THU),
        ("الخميس الجاي", THU),
        ("الخميس القادم", THU),
        ("يوم الخميس", THU),
        ("بالخميس", THU),
        ("هذا الخميس", THU),
        ("الخميس اللي بعده", date(2026, 12, 3)),
        ("الخميس بعد الجاي", date(2026, 12, 3)),
        ("الأحد", date(2026, 11, 22)),
        ("الاحد الجاي", date(2026, 11, 22)),
        ("الثلاثاء", date(2026, 11, 24)),
        ("الثلاثا", date(2026, 11, 24)),
        ("الأربعاء", date(2026, 11, 25)),
        ("الجمعة", FRIDAY),  # اليوم نفسه
        ("الجمعة الجاية", date(2026, 11, 27)),
        ("الخميس الأسبوع الجاي", THU),
        ("الأسبوع الجاي", date(2026, 11, 22)),
        ("بعد أسبوع", date(2026, 11, 27)),
        ("بعد اسبوعين", date(2026, 12, 4)),
        ("بعد 3 أيام", date(2026, 11, 23)),
        ("بعد ثلاث ايام", date(2026, 11, 23)),
        ("بعد يومين", date(2026, 11, 22)),
        ("26/11", THU),
        ("٢٦/١١", THU),
        ("3/12", date(2026, 12, 3)),
        ("5/1", date(2027, 1, 5)),  # الشهر القادم من السنة القادمة
        ("26/11/2026", THU),
        ("2026-12-01", date(2026, 12, 1)),
        ("26 نوفمبر", THU),
        ("26 تشرين الثاني", THU),
        ("10 كانون الثاني", date(2027, 1, 10)),
        ("31/2", None),
        ("قريباً", None),
        ("", None),
        (None, None),
    ],
)
def test_resolve_from_friday(expr, expected):
    assert resolve_date_expression(expr, FRIDAY, SUNDAY) == expected


def test_next_week_weekday_from_monday():
    # يوم الاثنين: "الخميس" = هذا الأسبوع، "الخميس الأسبوع الجاي" = الأسبوع القادم
    assert resolve_date_expression("الخميس", MON) == THU
    assert resolve_date_expression("الخميس الجاي", MON) == THU
    assert resolve_date_expression("الخميس الأسبوع الجاي", MON) == date(2026, 12, 3)
    assert resolve_date_expression("الأسبوع الجاي الخميس", MON) == date(2026, 12, 3)


def test_same_weekday_next():
    assert resolve_date_expression("الخميس", THU) == THU
    assert resolve_date_expression("الخميس الجاي", THU) == date(2026, 12, 3)


def test_week_bounds():
    assert week_bounds(FRIDAY, SUNDAY) == (date(2026, 11, 15), date(2026, 11, 21))
    assert week_bounds(FRIDAY, SATURDAY) == (date(2026, 11, 14), date(2026, 11, 20))
    assert week_bounds(FRIDAY, MONDAY) == (date(2026, 11, 16), date(2026, 11, 22))


def test_saturday_week_start_changes_next_week():
    # إذا الأسبوع يبدأ بالسبت، "الأسبوع الجاي" يبدأ غداً (السبت 21)
    assert resolve_date_expression("الأسبوع الجاي", FRIDAY, SATURDAY) == date(2026, 11, 21)


def test_calendar_reference_marks_today():
    ref = calendar_reference(FRIDAY, days=10)
    assert "2026-11-20 الجمعة (هذا الأسبوع) ← اليوم" in ref
    assert "2026-11-26 الخميس (الأسبوع القادم)" in ref


def test_days_left_phrase():
    assert days_left_phrase(FRIDAY, FRIDAY) == "اليوم!"
    assert days_left_phrase(date(2026, 11, 21), FRIDAY) == "غداً"
    assert days_left_phrase(date(2026, 11, 22), FRIDAY) == "باقي يومان"
    assert days_left_phrase(THU, FRIDAY) == "باقي 6 أيام"
    assert days_left_phrase(date(2026, 12, 20), FRIDAY) == "باقي 30 يوماً"
