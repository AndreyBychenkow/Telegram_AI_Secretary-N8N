from __future__ import annotations

from typing import Any
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from bot.models import CalendarEvent, LogEntry, Task

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar.events",
)
SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars"
TASK_COLUMNS = ("created_at", "chat_id", "task", "priority", "deadline", "status")


class GoogleError(RuntimeError):
    pass


class GoogleClient:
    def __init__(
        self,
        service_account_file: str,
        sheet_id: str,
        calendar_id: str,
        timezone: str,
        timeout: float = 30,
    ) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=SCOPES
        )
        self._session = AuthorizedSession(credentials)
        self._sheet_id = sheet_id
        self._calendar_id = calendar_id
        self._timezone = timezone
        self._timeout = timeout

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self._session.request(method, url, timeout=self._timeout, **kwargs)
        if response.status_code >= 300:
            raise GoogleError(f"Google {method} {url}: HTTP {response.status_code}")
        data: dict[str, Any] = response.json()
        return data

    def _append(self, sheet_range: str, row: list[str]) -> None:
        url = f"{SHEETS_BASE}/{self._sheet_id}/values/{quote(sheet_range)}:append"
        self._request(
            "POST",
            url,
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": [row]},
        )

    def append_task(self, task: Task) -> None:
        self._append("Tasks!A:F", task.to_row())

    def append_log(self, entry: LogEntry) -> None:
        self._append("Logs!A:H", entry.to_row())

    def list_tasks(self, chat_id: str) -> list[Task]:
        url = f"{SHEETS_BASE}/{self._sheet_id}/values/{quote('Tasks!A2:F')}"
        rows = self._request("GET", url).get("values", [])
        tasks: list[Task] = []
        for row in rows:
            padded = [str(cell) for cell in row] + [""] * (len(TASK_COLUMNS) - len(row))
            task = Task(*padded[: len(TASK_COLUMNS)])
            if task.chat_id == chat_id:
                tasks.append(task)
        return tasks

    def create_event(self, event: CalendarEvent) -> str:
        url = f"{CALENDAR_BASE}/{quote(self._calendar_id, safe='')}/events"
        data = self._request(
            "POST",
            url,
            json={
                "summary": event.summary,
                "description": event.description,
                "start": {"dateTime": event.start, "timeZone": self._timezone},
                "end": {"dateTime": event.end, "timeZone": self._timezone},
            },
        )
        return str(data.get("htmlLink", ""))
