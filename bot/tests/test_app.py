from __future__ import annotations

from typing import Any

from bot.app import SECRET_HEADER, create_app
from bot.config import Settings


class RecordingProcessor:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def process(self, update: dict[str, Any]) -> None:
        self.updates.append(update)


def test_webhook_requires_secret(settings: Settings) -> None:
    processor = RecordingProcessor()
    client = create_app(settings, processor).test_client()

    assert client.post("/webhook/telegram", json={"update_id": 1}).status_code == 403
    wrong = client.post("/webhook/telegram", json={}, headers={SECRET_HEADER: "nope"})
    assert wrong.status_code == 403
    assert processor.updates == []


def test_webhook_passes_update(settings: Settings) -> None:
    processor = RecordingProcessor()
    client = create_app(settings, processor).test_client()

    response = client.post(
        "/webhook/telegram", json={"update_id": 1}, headers={SECRET_HEADER: "secret"}
    )

    assert response.status_code == 200
    assert processor.updates == [{"update_id": 1}]


def test_health(settings: Settings) -> None:
    client = create_app(settings, RecordingProcessor()).test_client()

    assert client.get("/healthz").get_json() == {"status": "ok"}
