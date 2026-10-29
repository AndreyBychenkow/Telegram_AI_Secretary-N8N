from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.pipeline import ERROR_REPLY, UNSUPPORTED_REPLY, UpdateProcessor
from bot.repository import Repository
from bot.storage import Storage
from bot.tests.conftest import FakeMessenger, FakeResponder, FakeTranscriber


def make(
    storage: Storage, repository: Repository, responder: FakeResponder, voice_text: str = "голос"
) -> tuple[UpdateProcessor, FakeMessenger]:
    messenger = FakeMessenger()
    processor = UpdateProcessor(
        messenger, FakeTranscriber(voice_text), responder, repository, storage, "Europe/Moscow"
    )
    return processor, messenger


def text_update(update_id: int, text: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": 7}, "from": {"id": 9, "username": "andrey"}, "text": text},
    }


def logs(db: Path) -> list[tuple[str, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT input_type, user_message, assistant_reply, calendar_command, username FROM logs"
        ).fetchall()


def test_text_message_flow(storage: Storage, repository: Repository, tmp_path: Path) -> None:
    responder = FakeResponder("ок")
    processor, messenger = make(storage, repository, responder)

    processor.process(text_update(1, "Запланируй встречу"))

    assert responder.received == [("7", "Запланируй встречу")]
    assert messenger.sent == [(7, "ок")]
    assert logs(tmp_path / "test.sqlite3") == [
        ("text", "Запланируй встречу", "ок", "yes", "andrey")
    ]


def test_voice_message_is_transcribed(storage: Storage, repository: Repository) -> None:
    responder = FakeResponder()
    processor, _ = make(storage, repository, responder, voice_text="добавь задачу")

    processor.process({"update_id": 2, "message": {"chat": {"id": 7}, "voice": {"file_id": "f"}}})

    assert responder.received == [("7", "добавь задачу")]


def test_duplicate_update_is_ignored(storage: Storage, repository: Repository) -> None:
    responder = FakeResponder()
    processor, messenger = make(storage, repository, responder)

    processor.process(text_update(3, "привет"))
    processor.process(text_update(3, "привет"))

    assert len(messenger.sent) == 1


def test_unsupported_and_empty_messages(storage: Storage, repository: Repository) -> None:
    responder = FakeResponder()
    processor, messenger = make(storage, repository, responder, voice_text="")

    processor.process({"update_id": 4, "message": {"chat": {"id": 7}, "sticker": {}}})
    processor.process({"update_id": 5, "message": {"chat": {"id": 7}, "voice": {"file_id": "f"}}})
    processor.process({"update_id": 6, "edited_message": {"chat": {"id": 7}, "text": "x"}})

    assert messenger.sent[0] == (7, UNSUPPORTED_REPLY)
    assert len(messenger.sent) == 2
    assert responder.received == []


def test_agent_failure_sends_generic_error(storage: Storage, repository: Repository) -> None:
    processor, messenger = make(storage, repository, FakeResponder(error=RuntimeError("secret")))

    processor.process(text_update(7, "привет"))

    assert messenger.sent == [(7, ERROR_REPLY)]
