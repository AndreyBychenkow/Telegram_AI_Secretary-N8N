from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from bot.models import CalendarEvent, LogEntry, Task
from bot.storage import Storage

logger = logging.getLogger(__name__)

SAVED_LOCALLY = "saved_locally"
SAVED_TO_GOOGLE = "saved_to_google"
GOOGLE_FAILED = "saved_locally_google_failed"


class GoogleBackend(Protocol):
    def append_task(self, task: Task) -> None: ...

    def append_log(self, entry: LogEntry) -> None: ...

    def list_tasks(self, chat_id: str) -> list[Task]: ...

    def create_event(self, event: CalendarEvent) -> str: ...


class Repository:
    """SQLite is the source of truth; Google Sheets/Calendar are mirrored when configured."""

    def __init__(self, storage: Storage, google: GoogleBackend | None = None) -> None:
        self._storage = storage
        self._google = google

    def add_task(self, task: Task) -> str:
        self._storage.add_task(task)
        return self._mirror("append_task", lambda google: google.append_task(task))

    def list_tasks(self, chat_id: str) -> list[Task]:
        if self._google is not None:
            try:
                return self._google.list_tasks(chat_id)
            except Exception:
                logger.exception("Google list_tasks failed, using local storage")
        return self._storage.list_tasks(chat_id)

    def create_event(self, event: CalendarEvent) -> str:
        self._storage.add_event(event)
        return self._mirror("create_event", lambda google: google.create_event(event))

    def add_log(self, entry: LogEntry) -> None:
        self._storage.add_log(entry)
        self._mirror("append_log", lambda google: google.append_log(entry))

    def _mirror(self, name: str, action: Callable[[GoogleBackend], object]) -> str:
        if self._google is None:
            return SAVED_LOCALLY
        try:
            action(self._google)
        except Exception:
            logger.exception("Google %s failed", name)
            return GOOGLE_FAILED
        return SAVED_TO_GOOGLE
