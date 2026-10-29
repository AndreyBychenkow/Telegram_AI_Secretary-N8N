from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from bot.models import CalendarEvent, LogEntry, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    task TEXT NOT NULL,
    priority TEXT NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_chat ON tasks (chat_id);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    input_type TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_reply TEXT NOT NULL,
    calendar_command TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_chat ON memory (chat_id, id);
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id INTEGER PRIMARY KEY
);
"""

MAX_PROCESSED_UPDATES = 1000


class Storage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def mark_update_processed(self, update_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO processed_updates (update_id) VALUES (?)", (update_id,)
            )
            conn.execute(
                "DELETE FROM processed_updates WHERE update_id NOT IN "
                "(SELECT update_id FROM processed_updates ORDER BY update_id DESC LIMIT ?)",
                (MAX_PROCESSED_UPDATES,),
            )
            return cursor.rowcount == 1

    def add_task(self, task: Task) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks (created_at, chat_id, task, priority, deadline, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                task.to_row(),
            )

    def list_tasks(self, chat_id: str) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, chat_id, task, priority, deadline, status "
                "FROM tasks WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
        return [Task(*row) for row in rows]

    def add_event(self, event: CalendarEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO events (chat_id, summary, start, "end", description) '
                "VALUES (?, ?, ?, ?, ?)",
                (event.chat_id, event.summary, event.start, event.end, event.description),
            )

    def add_log(self, entry: LogEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO logs (timestamp, chat_id, user_id, username, input_type, "
                "user_message, assistant_reply, calendar_command) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                entry.to_row(),
            )

    def get_history(self, chat_id: str, limit: int) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM memory WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    def append_history(self, chat_id: str, messages: list[tuple[str, str]], keep: int) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO memory (chat_id, role, content) VALUES (?, ?, ?)",
                [(chat_id, role, content) for role, content in messages],
            )
            conn.execute(
                "DELETE FROM memory WHERE chat_id = ? AND id NOT IN "
                "(SELECT id FROM memory WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
                (chat_id, chat_id, keep),
            )
