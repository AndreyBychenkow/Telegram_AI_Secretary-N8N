from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from bot.models import CalendarEvent, Task
from bot.repository import SAVED_TO_GOOGLE, Repository
from bot.storage import Storage
from bot.timeutil import describe_now, now_iso

logger = logging.getLogger(__name__)

MEMORY_WINDOW = 10
MAX_TOOL_ROUNDS = 3
PRIORITIES = ("high", "medium", "low")
FALLBACK_REPLY = "Готово! Чем ещё могу помочь?"
CALENDAR_OFFLINE_NOTE = (
    "Google Календарь не подключён: встреча сохранена только в памяти бота. "
    "Не пиши, что она создана в календаре; напиши «сохранена в памяти бота»."
)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "GoogleSheets_Add_Task",
            "description": "Добавить новую задачу пользователя в таблицу (лист Tasks).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Описание задачи (например: подготовить презентацию)",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(PRIORITIES),
                        "description": "Приоритет задачи на английском: high, medium или low",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "Срок в формате YYYY-MM-DD (например: 2026-09-01)",
                    },
                },
                "required": ["task", "priority", "deadline"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "GoogleSheets_Read_Tasks",
            "description": "Прочитать список сохранённых задач пользователя из листа Tasks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "GoogleCalendar_Create_Event",
            "description": "Запланировать встречу или событие в Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Название встречи (например: обсуждение проекта)",
                    },
                    "start": {
                        "type": "string",
                        "description": "Начало в ISO 8601 с часовым поясом, "
                        "например 2026-09-02T15:00:00+03:00",
                    },
                    "end": {
                        "type": "string",
                        "description": "Окончание в ISO 8601 с часовым поясом, "
                        "например 2026-09-02T16:00:00+03:00",
                    },
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
]

SYSTEM_PROMPT = """Ты — дружелюбный и лаконичный AI-помощник и личный секретарь в Telegram.
Текущая дата и время ({timezone}): {now}.

Твои правила работы:
1. ДОБАВЛЕНИЕ ЗАДАЧИ:
   Когда пользователь просит добавить задачу, обязательно вызови инструмент GoogleSheets_Add_Task.
   После вызова инструмента ответь строго в таком стиле:
   ✅ Задача «<название>» добавлена в список.
   📅 Срок: <дата по-русски>
   ⚡ Приоритет: <высокий/средний/низкий>
   Если понадобится уточнить детали или добавить другие задачи — дайте знать!
2. СПИСОК ЗАДАЧ:
   Когда пользователь спрашивает, какие задачи у него сохранены,
   обязательно вызови GoogleSheets_Read_Tasks.
   Никогда не используй Markdown-таблицы и Markdown-разметку! Форматируй ответ списком:
   📋 Список задач
   1. 📝 Задача: <название>
      ⚡ Приоритет: <высокий/средний/низкий>
      📅 Срок: <ДД.ММ.ГГГГ>
      📌 Статус: <новый>
   Если задач нет — так и скажи.
3. СОЗДАНИЕ ВСТРЕЧИ В КАЛЕНДАРЕ:
   Когда пользователь просит запланировать встречу или событие, вызови GoogleCalendar_Create_Event
   (если длительность не указана, ставь 1 час).
   После вызова ответь строго в таком стиле:
   ✅ Встреча «<название>» успешно создана в календаре.
   📅 Дата: <дата по-русски>
   🕒 Время: <ЧЧ:ММ – ЧЧ:ММ>
   ⏱ Продолжительность: <длительность>
   Если понадобится изменить детали или добавить участников — дайте знать!
   Если в результате инструмента google_calendar = false, вместо «создана в календаре» напиши
   «сохранена в памяти бота (Google Календарь не подключён)».
4. ОБЫЧНЫЙ ДИАЛОГ:
   Отвечай дружелюбно, коротко и естественно по-русски."""


class ChatModel(Protocol):
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]: ...


class ToolArgumentError(ValueError):
    pass


def _parse_datetime(value: str, timezone: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolArgumentError(f"Неверный формат даты: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


class Agent:
    def __init__(
        self, model: ChatModel, repository: Repository, storage: Storage, timezone: str
    ) -> None:
        self._model = model
        self._repository = repository
        self._storage = storage
        self._timezone = timezone

    def reply(self, chat_id: str, user_text: str) -> str:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    timezone=self._timezone, now=describe_now(self._timezone)
                ),
            },
            *self._storage.get_history(chat_id, MEMORY_WINDOW),
            {"role": "user", "content": user_text},
        ]
        answer = self._run(chat_id, messages)
        self._storage.append_history(
            chat_id, [("user", user_text), ("assistant", answer)], keep=MEMORY_WINDOW
        )
        return answer

    def _run(self, chat_id: str, messages: list[dict[str, Any]]) -> str:
        for _ in range(MAX_TOOL_ROUNDS):
            message = self._model.chat(messages, tools=TOOLS)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or "").strip() or FALLBACK_REPLY
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                function = call.get("function") or {}
                result = self._execute(
                    chat_id, str(function.get("name", "")), str(function.get("arguments") or "{}")
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        final = self._model.chat(messages, tools=None)
        return str(final.get("content") or "").strip() or FALLBACK_REPLY

    def _execute(self, chat_id: str, name: str, raw_args: str) -> dict[str, Any]:
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ToolArgumentError("Аргументы должны быть объектом")
            if name == "GoogleSheets_Add_Task":
                return self._add_task(chat_id, args)
            if name == "GoogleSheets_Read_Tasks":
                tasks = self._repository.list_tasks(chat_id)
                return {"tasks": [task.to_dict() for task in tasks]}
            if name == "GoogleCalendar_Create_Event":
                return self._create_event(chat_id, args)
            return {"error": f"Неизвестный инструмент: {name}"}
        except (ToolArgumentError, json.JSONDecodeError) as exc:
            return {"error": str(exc)}
        except Exception:
            logger.exception("Tool %s failed", name)
            return {"error": "Внутренняя ошибка инструмента"}

    def _add_task(self, chat_id: str, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("task", "")).strip()
        if not text:
            raise ToolArgumentError("Пустое описание задачи")
        priority = str(args.get("priority", "medium")).lower()
        task = Task(
            created_at=now_iso(self._timezone),
            chat_id=chat_id,
            task=text,
            priority=priority if priority in PRIORITIES else "medium",
            deadline=str(args.get("deadline", "")).strip(),
        )
        storage = self._repository.add_task(task)
        return {**task.to_dict(), "storage": storage}

    def _create_event(self, chat_id: str, args: dict[str, Any]) -> dict[str, Any]:
        start = _parse_datetime(str(args.get("start", "")), self._timezone)
        raw_end = str(args.get("end", "")).strip()
        end = _parse_datetime(raw_end, self._timezone) if raw_end else start + timedelta(hours=1)
        if end <= start:
            raise ToolArgumentError("Окончание встречи должно быть позже начала")
        event = CalendarEvent(
            chat_id=chat_id,
            summary=str(args.get("summary", "")).strip() or "Встреча",
            start=start.isoformat(),
            end=end.isoformat(),
        )
        in_google = self._repository.create_event(event) == SAVED_TO_GOOGLE
        result: dict[str, Any] = {
            "summary": event.summary,
            "start": event.start,
            "end": event.end,
            "google_calendar": in_google,
        }
        if not in_google:
            result["note"] = CALENDAR_OFFLINE_NOTE
        return result
