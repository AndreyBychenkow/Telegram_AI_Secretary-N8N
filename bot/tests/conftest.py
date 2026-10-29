from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bot.config import Settings
from bot.repository import Repository
from bot.storage import Storage


class FakeModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self.calls.append(list(messages))
        return self.responses.pop(0)


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int | str, str]] = []

    def send_message(self, chat_id: int | str, text: str) -> None:
        self.sent.append((chat_id, text))

    def download_file(self, file_id: str) -> bytes:
        return b"audio:" + file_id.encode()


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio: bytes, language: str = "ru") -> str:
        return self.text


class FakeResponder:
    def __init__(self, answer: str = "ответ", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.received: list[tuple[str, str]] = []

    def reply(self, chat_id: str, user_text: str) -> str:
        self.received.append((chat_id, user_text))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "test.sqlite3")


@pytest.fixture
def repository(storage: Storage) -> Repository:
    return Repository(storage)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_token="tg",
        groq_api_key="groq",
        webhook_secret="secret",
        data_dir=tmp_path,
    )
