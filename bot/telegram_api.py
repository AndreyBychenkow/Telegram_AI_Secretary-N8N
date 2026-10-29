from __future__ import annotations

from typing import Any

import requests

API_BASE = "https://api.telegram.org"
MESSAGE_LIMIT = 4096


class TelegramError(RuntimeError):
    pass


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


class TelegramClient:
    def __init__(
        self, token: str, session: requests.Session | None = None, timeout: float = 30
    ) -> None:
        self._token = token
        self._session = session or requests.Session()
        self._timeout = timeout

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        try:
            response = self._session.post(url, json=payload or {}, timeout=self._timeout)
            data = response.json()
        except (requests.RequestException, ValueError):
            raise TelegramError(f"Telegram {method}: network or decode error") from None
        if not data.get("ok"):
            raise TelegramError(f"Telegram {method}: {data.get('description', 'unknown error')}")
        return data.get("result")

    def send_message(self, chat_id: int | str, text: str) -> None:
        for chunk in split_message(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def download_file(self, file_id: str) -> bytes:
        info = self._call("getFile", {"file_id": file_id})
        file_path = info.get("file_path") if isinstance(info, dict) else None
        if not file_path:
            raise TelegramError("Telegram getFile: empty file_path")
        url = f"{API_BASE}/file/bot{self._token}/{file_path}"
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException:
            raise TelegramError("Telegram file download failed") from None
        return response.content

    def set_webhook(self, url: str, secret_token: str, drop_pending: bool = False) -> None:
        self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message"],
                "drop_pending_updates": drop_pending,
                "max_connections": 1,
            },
        )

    def get_webhook_info(self) -> dict[str, Any]:
        result = self._call("getWebhookInfo")
        return result if isinstance(result, dict) else {}
