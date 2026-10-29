from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from bot.models import LogEntry
from bot.repository import Repository
from bot.storage import Storage
from bot.timeutil import now_iso

logger = logging.getLogger(__name__)

CALENDAR_PATTERN = re.compile(r"запланир|встреч|календар|созвон|событи|переговор", re.IGNORECASE)
UNSUPPORTED_REPLY = (
    "Извините, я поддерживаю только текстовые и голосовые сообщения 🙂 "
    "Напишите текстом или отправьте голосовое сообщение."
)
EMPTY_REPLY = "Не удалось распознать сообщение. Попробуйте ещё раз!"
ERROR_REPLY = "Произошла временная ошибка при обработке запроса. Попробуйте ещё раз чуть позже 🙏"


class Messenger(Protocol):
    def send_message(self, chat_id: int | str, text: str) -> None: ...

    def download_file(self, file_id: str) -> bytes: ...


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, language: str = "ru") -> str: ...


class Responder(Protocol):
    def reply(self, chat_id: str, user_text: str) -> str: ...


class UpdateProcessor:
    def __init__(
        self,
        messenger: Messenger,
        transcriber: Transcriber,
        agent: Responder,
        repository: Repository,
        storage: Storage,
        timezone: str,
    ) -> None:
        self._messenger = messenger
        self._transcriber = transcriber
        self._agent = agent
        self._repository = repository
        self._storage = storage
        self._timezone = timezone

    def process(self, update: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        if isinstance(update_id, int) and not self._storage.mark_update_processed(update_id):
            logger.info("Duplicate update %s skipped", update_id)
            return
        message = update.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
            return
        chat_id = message["chat"]["id"]
        try:
            self._handle(chat_id, message)
        except Exception:
            logger.exception("Failed to process update %s", update_id)
            self._safe_send(chat_id, ERROR_REPLY)

    def _handle(self, chat_id: int, message: dict[str, Any]) -> None:
        sender = message.get("from") or {}
        voice = message.get("voice")
        if isinstance(voice, dict) and voice.get("file_id"):
            input_type = "voice"
            audio = self._messenger.download_file(str(voice["file_id"]))
            user_text = self._transcriber.transcribe(audio)
        elif isinstance(message.get("text"), str):
            input_type = "text"
            user_text = message["text"].strip()
        else:
            self._messenger.send_message(chat_id, UNSUPPORTED_REPLY)
            return

        if not user_text:
            self._messenger.send_message(chat_id, EMPTY_REPLY)
            return

        reply = self._agent.reply(str(chat_id), user_text)
        self._messenger.send_message(chat_id, reply)
        self._repository.add_log(
            LogEntry(
                timestamp=now_iso(self._timezone),
                chat_id=str(chat_id),
                user_id=str(sender.get("id", chat_id)),
                username=str(sender.get("username") or sender.get("first_name") or "user"),
                input_type=input_type,
                user_message=user_text,
                assistant_reply=reply,
                calendar_command="yes" if CALENDAR_PATTERN.search(user_text) else "no",
            )
        )

    def _safe_send(self, chat_id: int, text: str) -> None:
        try:
            self._messenger.send_message(chat_id, text)
        except Exception:
            logger.exception("Failed to send error reply to %s", chat_id)
