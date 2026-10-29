from __future__ import annotations

import json

from bot.agent import CALENDAR_OFFLINE_NOTE, FALLBACK_REPLY, MEMORY_WINDOW, Agent
from bot.repository import Repository
from bot.storage import Storage
from bot.tests.conftest import FakeModel

TZ = "Europe/Moscow"


def tool_call(name: str, args: dict[str, object], call_id: str = "c1") -> dict[str, object]:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def test_plain_reply_saves_history(storage: Storage, repository: Repository) -> None:
    model = FakeModel([{"content": "Привет!"}])
    agent = Agent(model, repository, storage, TZ)

    assert agent.reply("1", "привет") == "Привет!"
    assert storage.get_history("1", MEMORY_WINDOW) == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "Привет!"},
    ]


def test_add_task_tool_persists_and_normalises(storage: Storage, repository: Repository) -> None:
    model = FakeModel(
        [
            tool_call(
                "GoogleSheets_Add_Task",
                {"task": "презентация", "priority": "URGENT", "deadline": "2026-09-04"},
            ),
            {"content": "✅ Задача добавлена"},
        ]
    )
    agent = Agent(model, repository, storage, TZ)

    assert agent.reply("42", "добавь задачу") == "✅ Задача добавлена"
    tasks = storage.list_tasks("42")
    assert [(t.task, t.priority, t.deadline, t.status) for t in tasks] == [
        ("презентация", "medium", "2026-09-04", "new")
    ]
    tool_message = model.calls[1][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["storage"] == "saved_locally"


def test_read_tasks_is_scoped_to_chat(storage: Storage, repository: Repository) -> None:
    agent_other = Agent(
        FakeModel(
            [
                tool_call(
                    "GoogleSheets_Add_Task", {"task": "чужая", "priority": "low", "deadline": ""}
                ),
                {"content": "ok"},
            ]
        ),
        repository,
        storage,
        TZ,
    )
    agent_other.reply("other", "добавь")
    model = FakeModel([tool_call("GoogleSheets_Read_Tasks", {}), {"content": "пусто"}])

    Agent(model, repository, storage, TZ).reply("me", "мои задачи")

    assert json.loads(model.calls[1][-1]["content"]) == {"tasks": []}


def test_calendar_defaults_and_validation(storage: Storage, repository: Repository) -> None:
    model = FakeModel(
        [
            tool_call(
                "GoogleCalendar_Create_Event",
                {"summary": "созвон", "start": "2026-09-05T15:00:00", "end": ""},
            ),
            tool_call(
                "GoogleCalendar_Create_Event",
                {
                    "summary": "x",
                    "start": "2026-09-05T15:00:00+03:00",
                    "end": "2026-09-05T14:00:00+03:00",
                },
                "c2",
            ),
            {"content": "готово"},
        ]
    )

    Agent(model, repository, storage, TZ).reply("1", "встреча")

    first = json.loads(model.calls[1][-1]["content"])
    assert first == {
        "summary": "созвон",
        "start": "2026-09-05T15:00:00+03:00",
        "end": "2026-09-05T16:00:00+03:00",
        "google_calendar": False,
        "note": CALENDAR_OFFLINE_NOTE,
    }
    assert "error" in json.loads(model.calls[2][-1]["content"])


def test_bad_arguments_and_unknown_tool_return_errors(
    storage: Storage, repository: Repository
) -> None:
    bad_json = {
        "content": "",
        "tool_calls": [
            {"id": "a", "function": {"name": "GoogleSheets_Add_Task", "arguments": "{"}}
        ],
    }
    model = FakeModel([bad_json, tool_call("Nope", {}, "b"), {"content": ""}])

    assert Agent(model, repository, storage, TZ).reply("1", "x") == FALLBACK_REPLY
    assert "error" in json.loads(model.calls[1][-1]["content"])
    assert "error" in json.loads(model.calls[2][-1]["content"])


def test_tool_rounds_are_bounded(storage: Storage, repository: Repository) -> None:
    looping = [tool_call("GoogleSheets_Read_Tasks", {}, f"c{i}") for i in range(3)]
    model = FakeModel([*looping, {"content": "финал"}])

    assert Agent(model, repository, storage, TZ).reply("1", "x") == "финал"
    assert len(model.calls) == 4
