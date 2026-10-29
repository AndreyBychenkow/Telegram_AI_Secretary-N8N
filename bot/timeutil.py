from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def now_iso(timezone: str) -> str:
    return now(timezone).isoformat(timespec="seconds")


def describe_now(timezone: str) -> str:
    current = now(timezone)
    return f"{current.isoformat(timespec='seconds')} ({WEEKDAYS_RU[current.weekday()]})"
