"""إعدادات البوت من متغيرات البيئة (أو ملف .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from zoneinfo import ZoneInfo

try:  # ملف .env اختياري
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    telegram_token: str = ""
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"
    timezone: str = "Asia/Baghdad"
    week_start: int = 6  # الأحد
    month_style: str = "levantine"  # levantine = تشرين الثاني، standard = نوفمبر
    db_path: str = "data/study_bot.db"
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    group_prefilter: bool = True
    private_mode: bool = True  # البوت خاص بقروب الشعبة فقط
    reminders: bool = True
    # أوقات التذكير اليومية: 6 صباحاً، 3 عصراً، 6 مغرب، 9 ليلاً
    reminder_times: tuple[str, ...] = ("06:00", "15:00", "18:00", "21:00")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @classmethod
    def from_env(cls) -> "Config":
        week_start_name = os.getenv("WEEK_START", "sunday").strip().lower()
        if week_start_name not in _WEEKDAYS:
            raise ValueError(f"WEEK_START غير صحيح: {week_start_name}")
        return cls(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            claude_effort=os.getenv("CLAUDE_EFFORT", "low"),
            timezone=os.getenv("TIMEZONE", "Asia/Baghdad"),
            week_start=_WEEKDAYS[week_start_name],
            month_style=os.getenv("MONTH_STYLE", "levantine"),
            db_path=os.getenv("DB_PATH", "data/study_bot.db"),
            allowed_chat_ids=_parse_ids(os.getenv("ALLOWED_CHAT_IDS", "")),
            group_prefilter=os.getenv("GROUP_PREFILTER", "true").lower() not in ("0", "false", "no"),
            reminders=os.getenv("REMINDERS", "true").lower() not in ("0", "false", "no"),
            private_mode=os.getenv("PRIVATE_MODE", "true").lower() not in ("0", "false", "no"),
            reminder_times=tuple(
                t.strip() for t in (os.getenv("REMINDER_TIMES") or "06:00,15:00,18:00,21:00").split(",") if t.strip()
            ),
        )
