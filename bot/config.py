from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED_KEYS = ("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "WEBHOOK_SECRET")


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    groq_api_key: str
    webhook_secret: str
    data_dir: Path
    groq_chat_model: str = "openai/gpt-oss-120b"
    groq_whisper_model: str = "whisper-large-v3-turbo"
    timezone: str = "Europe/Moscow"
    google_service_account_file: str = ""
    google_sheet_id: str = ""
    google_calendar_id: str = "primary"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_service_account_file and self.google_sheet_id)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        values = {key: source.get(key, "").strip() for key in REQUIRED_KEYS}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")

        def opt(key: str, default: str) -> str:
            return source.get(key, "").strip() or default

        return cls(
            telegram_token=values["TELEGRAM_BOT_TOKEN"],
            groq_api_key=values["GROQ_API_KEY"],
            webhook_secret=values["WEBHOOK_SECRET"],
            data_dir=Path(opt("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))),
            groq_chat_model=opt("GROQ_CHAT_MODEL", cls.groq_chat_model),
            groq_whisper_model=opt("GROQ_WHISPER_MODEL", cls.groq_whisper_model),
            timezone=opt("TIMEZONE", cls.timezone),
            google_service_account_file=opt("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
            google_sheet_id=opt("GOOGLE_SHEET_ID", ""),
            google_calendar_id=opt("GOOGLE_CALENDAR_ID", cls.google_calendar_id),
        )


def parse_env_file(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip("'\"")
    return result


def load_env_file(path: Path, target: MutableMapping[str, str] | None = None) -> None:
    env = os.environ if target is None else target
    if not path.is_file():
        return
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        env.setdefault(key, value)
