from __future__ import annotations

from typing import Any, cast

import pytest
import requests

from bot.groq_api import GroqClient, GroqError


class FakeResponse:
    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def post(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        return self.responses.pop(0)


def client(session: FakeSession, sleeps: list[float]) -> GroqClient:
    return GroqClient(
        "key", "chat", "whisper", session=cast(requests.Session, session), sleep=sleeps.append
    )


def test_retries_on_rate_limit() -> None:
    ok = {"choices": [{"message": {"content": "hi"}}]}
    session = FakeSession([FakeResponse(429, {}, {"retry-after": "2"}), FakeResponse(200, ok)])
    sleeps: list[float] = []

    assert client(session, sleeps).chat([{"role": "user", "content": "x"}]) == {"content": "hi"}
    assert sleeps == [2.0]


def test_gives_up_after_max_attempts() -> None:
    session = FakeSession([FakeResponse(429, {}, {"retry-after": "999"}) for _ in range(3)])
    sleeps: list[float] = []

    with pytest.raises(GroqError, match="429"):
        client(session, sleeps).transcribe(b"audio")
    assert session.calls == 3
    assert sleeps == [15.0, 15.0]
