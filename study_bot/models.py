"""أنواع المهام وبياناتها."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TaskKind:
    key: str
    label: str
    icon: str
    priority: int  # 0 = الأهم


TASK_KINDS: dict[str, TaskKind] = {
    k.key: k
    for k in [
        TaskKind("final", "امتحان فاينل", "🎓", 0),
        TaskKind("midterm", "امتحان ميد", "📝", 1),
        TaskKind("exam", "امتحان", "📝", 1),
        TaskKind("quiz", "كويز", "✏️", 2),
        TaskKind("project", "مشروع", "📊", 2),
        TaskKind("presentation", "عرض تقديمي", "🎤", 2),
        TaskKind("report", "تقرير", "📄", 3),
        TaskKind("homework", "واجب", "📘", 3),
        TaskKind("lab", "مختبر", "🧪", 3),
        TaskKind("lecture", "محاضرة", "🏫", 4),
        TaskKind("other", "مهمة", "📌", 4),
    ]
}

EXAM_KINDS = frozenset({"final", "midterm", "exam", "quiz"})

PRIORITY_DOTS = {0: "🔴", 1: "🟠", 2: "🟡", 3: "🟢", 4: "⚪"}


def kind_of(key: str) -> TaskKind:
    return TASK_KINDS.get(key, TASK_KINDS["other"])


@dataclass
class Task:
    id: int
    kind: str
    subject: str
    title: str
    due_date: date
    due_time: str | None
    notes: str | None
    created_by: int
    created_by_name: str
    confirmations: int = 1

    @property
    def info(self) -> TaskKind:
        return kind_of(self.kind)

    @property
    def priority(self) -> int:
        return self.info.priority
