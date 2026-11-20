"""تخزين المهام في SQLite.

كل المهام مشتركة بين طلاب الدفعة (قاعدة بيانات واحدة لكل البوت)، لأن البوت
مصمم لمجموعة واحدة من الطلاب. يمكن تقييد من يستخدم البوت عبر ALLOWED_CHAT_IDS.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .arabic import normalize
from .models import Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL,
    due_time TEXT,
    notes TEXT,
    chat_id INTEGER,
    created_by INTEGER NOT NULL,
    created_by_name TEXT NOT NULL DEFAULT '',
    source_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(deleted, due_date);

CREATE TABLE IF NOT EXISTS confirmations (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    user_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, user_id)
);

CREATE TABLE IF NOT EXISTS done (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    user_id INTEGER NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (task_id, user_id)
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    reminders INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sent_reminders (
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (chat_id, kind, day)
);

CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL
);
"""


def subject_key(subject: str) -> str:
    """مفتاح مقارنة لاسم المادة: "الفيزياء" و"فيزياء" يعطيان المفتاح نفسه."""
    words = []
    for w in normalize(subject).split():
        if w.startswith("ال") and len(w) > 4:
            w = w[2:]
        words.append(w)
    return " ".join(words)


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        # القروبات التي سُجّلت فيها مهام قبل إضافة جدول chats (أرقام القروبات سالبة)
        self.conn.execute(
            "INSERT OR IGNORE INTO chats (chat_id) SELECT DISTINCT chat_id FROM tasks WHERE chat_id < 0"
        )
        self.conn.commit()

    # ---------- المهام ----------

    def add_task(
        self,
        *,
        kind: str,
        subject: str,
        title: str,
        due_date: date,
        due_time: str | None,
        notes: str | None,
        chat_id: int | None,
        user_id: int,
        user_name: str,
        source_text: str | None = None,
    ) -> Task:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            """INSERT INTO tasks (kind, subject, subject_key, title, due_date, due_time, notes,
                                  chat_id, created_by, created_by_name, source_text, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kind, subject.strip(), subject_key(subject), title.strip(), due_date.isoformat(),
                due_time, notes, chat_id, user_id, user_name, source_text, now, now,
            ),
        )
        task_id = cur.lastrowid
        self.conn.execute("INSERT OR IGNORE INTO confirmations VALUES (?, ?)", (task_id, user_id))
        self.conn.commit()
        task = self.get_task(task_id)
        assert task is not None
        return task

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute(
            f"{self._select()} WHERE t.id = ? AND t.deleted = 0 GROUP BY t.id", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task(self, task_id: int, **fields) -> Task | None:
        allowed = {"kind", "subject", "title", "due_date", "due_time", "notes"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            if key == "due_date" and isinstance(value, date):
                value = value.isoformat()
            sets.append(f"{key} = ?")
            values.append(value)
            if key == "subject":
                sets.append("subject_key = ?")
                values.append(subject_key(value))
        if not sets:
            return self.get_task(task_id)
        sets.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        self.conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*values, task_id))
        self.conn.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> bool:
        cur = self.conn.execute("UPDATE tasks SET deleted = 1 WHERE id = ? AND deleted = 0", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def confirm_task(self, task_id: int, user_id: int) -> tuple[Task | None, bool]:
        """يسجّل تأكيد طالب للمهمة. يرجع (المهمة، هل هو تأكيد جديد)."""
        cur = self.conn.execute("INSERT OR IGNORE INTO confirmations VALUES (?, ?)", (task_id, user_id))
        self.conn.commit()
        return self.get_task(task_id), cur.rowcount > 0

    def tasks_between(
        self,
        start: date,
        end: date,
        *,
        subject: str | None = None,
        kinds: list[str] | None = None,
    ) -> list[Task]:
        sql = f"{self._select()} WHERE t.deleted = 0 AND t.due_date BETWEEN ? AND ?"
        params: list = [start.isoformat(), end.isoformat()]
        if subject:
            sql += " AND t.subject_key = ?"
            params.append(subject_key(subject))
        if kinds:
            sql += f" AND t.kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " GROUP BY t.id ORDER BY t.due_date, t.due_time IS NULL, t.due_time, t.id"
        return [self._row_to_task(r) for r in self.conn.execute(sql, params)]

    def find_similar(self, kind: str, subject: str, from_date: date) -> list[Task]:
        """مهام قادمة من نفس النوع ونفس المادة (لكشف التكرار والتعارض)."""
        rows = self.conn.execute(
            f"{self._select()} WHERE t.deleted = 0 AND t.kind = ? AND t.subject_key = ? AND t.due_date >= ?"
            " GROUP BY t.id ORDER BY t.due_date",
            (kind, subject_key(subject), from_date.isoformat()),
        )
        return [self._row_to_task(r) for r in rows]

    def known_subjects(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT subject, COUNT(*) AS n FROM tasks WHERE deleted = 0 GROUP BY subject_key ORDER BY n DESC LIMIT 40"
        )
        return [r["subject"] for r in rows]

    # ---------- الأعضاء ----------

    def touch_member(self, user_id: int, name: str) -> None:
        self.conn.execute(
            "INSERT INTO members (user_id, name, last_seen) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen",
            (user_id, name, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def is_member(self, user_id: int) -> bool:
        return self.conn.execute("SELECT 1 FROM members WHERE user_id = ?", (user_id,)).fetchone() is not None

    # ---------- الإنجاز ("خلصت") ----------

    def toggle_done(self, task_id: int, user_id: int) -> bool:
        """يبدّل حالة الإنجاز للطالب. يرجع True إذا صارت المهمة منجزة."""
        if self.is_done(task_id, user_id):
            self.conn.execute("DELETE FROM done WHERE task_id = ? AND user_id = ?", (task_id, user_id))
            self.conn.commit()
            return False
        self.mark_done(task_id, user_id)
        return True

    def mark_done(self, task_id: int, user_id: int) -> bool:
        """يرجع True إذا كان تسجيلاً جديداً."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO done VALUES (?, ?, ?)",
            (task_id, user_id, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def is_done(self, task_id: int, user_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM done WHERE task_id = ? AND user_id = ?", (task_id, user_id)
        ).fetchone() is not None

    def done_ids(self, user_id: int) -> set[int]:
        return {r["task_id"] for r in self.conn.execute("SELECT task_id FROM done WHERE user_id = ?", (user_id,))}

    # ---------- القروبات والتذكيرات ----------

    def register_chat(self, chat_id: int, title: str) -> None:
        self.conn.execute(
            "INSERT INTO chats (chat_id, title) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title",
            (chat_id, title or ""),
        )
        self.conn.commit()

    def set_reminders(self, chat_id: int, enabled: bool) -> None:
        self.conn.execute(
            "INSERT INTO chats (chat_id, reminders) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET reminders = excluded.reminders",
            (chat_id, int(enabled)),
        )
        self.conn.commit()

    def reminders_enabled(self, chat_id: int) -> bool:
        row = self.conn.execute("SELECT reminders FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return bool(row["reminders"]) if row else True

    def reminder_chats(self) -> list[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM chats WHERE reminders = 1")]

    def was_sent(self, chat_id: int, kind: str, day: date) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sent_reminders WHERE chat_id = ? AND kind = ? AND day = ?",
            (chat_id, kind, day.isoformat()),
        ).fetchone() is not None

    def mark_sent(self, chat_id: int, kind: str, day: date) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sent_reminders VALUES (?, ?, ?)", (chat_id, kind, day.isoformat())
        )
        # لا نحتاج سجلات أقدم من شهر
        self.conn.execute("DELETE FROM sent_reminders WHERE day < date(?, '-30 days')", (day.isoformat(),))
        self.conn.commit()

    # ---------- داخلي ----------

    @staticmethod
    def _select() -> str:
        return (
            "SELECT t.*, COUNT(c.user_id) AS confirmations,"
            " (SELECT COUNT(*) FROM done d WHERE d.task_id = t.id) AS done_count FROM tasks t"
            " LEFT JOIN confirmations c ON c.task_id = t.id"
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            kind=row["kind"],
            subject=row["subject"],
            title=row["title"],
            due_date=date.fromisoformat(row["due_date"]),
            due_time=row["due_time"],
            notes=row["notes"],
            created_by=row["created_by"],
            created_by_name=row["created_by_name"],
            confirmations=max(1, row["confirmations"]),
            done_count=row["done_count"],
        )
