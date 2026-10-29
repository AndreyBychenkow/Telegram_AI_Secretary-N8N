from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import Settings, load_env_file, parse_env_file
from bot.telegram_api import split_message


def test_settings_require_secrets() -> None:
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        Settings.from_env({"TELEGRAM_BOT_TOKEN": "a", "GROQ_API_KEY": "b"})


def test_settings_defaults() -> None:
    settings = Settings.from_env(
        {"TELEGRAM_BOT_TOKEN": "a", "GROQ_API_KEY": "b", "WEBHOOK_SECRET": "c"}
    )
    assert settings.groq_chat_model == "openai/gpt-oss-120b"
    assert not settings.google_enabled


def test_parse_env_file() -> None:
    text = "# comment\nA=1\nB = 'two'\nbroken\nC=x=y\n"
    assert parse_env_file(text) == {"A": "1", "B": "two", "C": "x=y"}


def test_load_env_file_does_not_override(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=file\nB=file\n", encoding="utf-8")
    env = {"A": "real"}
    load_env_file(path, env)
    assert env == {"A": "real", "B": "file"}


def test_split_message() -> None:
    text = "a" * 5 + "\n" + "b" * 5
    assert split_message(text, limit=7) == ["aaaaa", "bbbbb"]
    assert split_message("x" * 10, limit=4) == ["xxxx", "xxxx", "xx"]
    assert split_message("") == []
