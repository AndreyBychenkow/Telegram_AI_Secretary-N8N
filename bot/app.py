from __future__ import annotations

import hmac
import logging
from typing import Any, Protocol

from flask import Flask, Response, jsonify, request

from bot.agent import Agent
from bot.config import Settings
from bot.groq_api import GroqClient
from bot.pipeline import UpdateProcessor
from bot.repository import GoogleBackend, Repository
from bot.storage import Storage
from bot.telegram_api import TelegramClient

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 - header name, not a secret
MAX_BODY_BYTES = 1024 * 1024

logger = logging.getLogger(__name__)


def build_processor(settings: Settings) -> UpdateProcessor:
    storage = Storage(settings.data_dir / "bot.sqlite3")
    google: GoogleBackend | None = None
    if settings.google_enabled:
        try:
            from bot.google_api import GoogleClient

            google = GoogleClient(
                settings.google_service_account_file,
                settings.google_sheet_id,
                settings.google_calendar_id,
                settings.timezone,
            )
        except Exception:
            logger.exception("Google integration disabled: failed to initialise")
    repository = Repository(storage, google)
    groq = GroqClient(settings.groq_api_key, settings.groq_chat_model, settings.groq_whisper_model)
    agent = Agent(groq, repository, storage, settings.timezone)
    telegram = TelegramClient(settings.telegram_token)
    return UpdateProcessor(telegram, groq, agent, repository, storage, settings.timezone)


class UpdateHandler(Protocol):
    def process(self, update: dict[str, Any]) -> None: ...


def create_app(settings: Settings, processor: UpdateHandler | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    handler = processor or build_processor(settings)
    expected_secret = settings.webhook_secret.encode()

    @app.get("/")
    @app.get("/healthz")
    def health() -> Response:
        return jsonify(status="ok")

    @app.post("/webhook/telegram")
    def webhook() -> tuple[Response, int]:
        provided = request.headers.get(SECRET_HEADER, "").encode()
        if not hmac.compare_digest(provided, expected_secret):
            return jsonify(ok=False), 403
        update = request.get_json(silent=True)
        if isinstance(update, dict):
            handler.process(update)
        return jsonify(ok=True), 200

    return app
