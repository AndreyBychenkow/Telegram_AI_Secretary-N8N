from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

API_BASE = "https://api.groq.com/openai/v1"
MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 3.0
MAX_RETRY_DELAY = 15.0


class GroqError(RuntimeError):
    pass


def _retry_delay(header: str | None) -> float:
    try:
        delay = float(header) if header else DEFAULT_RETRY_DELAY
    except ValueError:
        delay = DEFAULT_RETRY_DELAY
    return min(max(delay, 0.5), MAX_RETRY_DELAY)


class GroqClient:
    def __init__(
        self,
        api_key: str,
        chat_model: str,
        whisper_model: str,
        session: requests.Session | None = None,
        timeout: float = 60,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._chat_model = chat_model
        self._whisper_model = whisper_model
        self._session = session or requests.Session()
        self._timeout = timeout
        self._sleep = sleep

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._session.post(
                    f"{API_BASE}{path}", headers=self._headers, timeout=self._timeout, **kwargs
                )
            except requests.RequestException as exc:
                raise GroqError(f"Groq {path}: {type(exc).__name__}") from None
            if response.status_code != 429 or attempt == MAX_ATTEMPTS - 1:
                break
            self._sleep(_retry_delay(response.headers.get("retry-after")))
        if response.status_code != 200:
            raise GroqError(f"Groq {path}: HTTP {response.status_code}: {response.text[:300]}")
        data: dict[str, Any] = response.json()
        return data

    def transcribe(self, audio: bytes, language: str = "ru") -> str:
        data = self._post(
            "/audio/transcriptions",
            files={"file": ("voice.ogg", audio, "audio/ogg")},
            data={"model": self._whisper_model, "language": language},
        )
        return str(data.get("text", "")).strip()

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._chat_model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = self._post("/chat/completions", json=body)
        choices = data.get("choices") or []
        if not choices:
            raise GroqError("Groq chat: empty choices")
        message: dict[str, Any] = choices[0].get("message") or {}
        return message
