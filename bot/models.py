from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Task:
    created_at: str
    chat_id: str
    task: str
    priority: str
    deadline: str
    status: str = "new"

    def to_row(self) -> list[str]:
        return [self.created_at, self.chat_id, self.task, self.priority, self.deadline, self.status]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    chat_id: str
    user_id: str
    username: str
    input_type: str
    user_message: str
    assistant_reply: str
    calendar_command: str

    def to_row(self) -> list[str]:
        return [
            self.timestamp,
            self.chat_id,
            self.user_id,
            self.username,
            self.input_type,
            self.user_message,
            self.assistant_reply,
            self.calendar_command,
        ]


@dataclass(frozen=True)
class CalendarEvent:
    chat_id: str
    summary: str
    start: str
    end: str
    description: str = "Создано Telegram AI-секретарём"
