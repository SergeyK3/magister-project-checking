"""Конфигурация Telegram-бота: чтение секретов из переменных окружения / .env."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:  # python-dotenv не обязателен в рантайме
    load_dotenv = None  # type: ignore[assignment]


DEFAULT_WORKSHEET_NAME = "Регистрация"
DEFAULT_LOG_LEVEL = "INFO"


class ConfigError(RuntimeError):
    """Ошибка конфигурации бота (отсутствуют обязательные переменные)."""


@dataclass(frozen=True)
class BotConfig:
    """Параметры запуска бота."""

    telegram_bot_token: str
    spreadsheet_id: str
    worksheet_name: str
    google_service_account_json: Path
    log_level: int

    @property
    def log_level_name(self) -> str:
        return logging.getLevelName(self.log_level)


def _read_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _coerce_log_level(raw: str) -> int:
    candidate = raw.strip().upper()
    numeric = logging.getLevelName(candidate)
    if isinstance(numeric, int):
        return numeric
    try:
        return int(candidate)
    except ValueError as exc:
        raise ConfigError(f"Некорректный LOG_LEVEL: {raw!r}") from exc


def load_config(*, dotenv_path: Optional[Path] = None) -> BotConfig:
    """Загружает конфигурацию бота из переменных окружения.

    Если установлен ``python-dotenv`` — подхватывает ``.env`` (по умолчанию из
    текущей рабочей директории; путь можно переопределить через ``dotenv_path``).
    Существующие переменные окружения имеют приоритет над значениями из .env.
    """

    if load_dotenv is not None:
        if dotenv_path is None:
            load_dotenv(override=False)
        else:
            load_dotenv(dotenv_path=dotenv_path, override=False)

    token = _read_env("TELEGRAM_BOT_TOKEN")
    spreadsheet_id = _read_env("SPREADSHEET_ID")
    sa_path_raw = _read_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    worksheet_name = _read_env("WORKSHEET_NAME", DEFAULT_WORKSHEET_NAME) or DEFAULT_WORKSHEET_NAME
    log_level_raw = _read_env("LOG_LEVEL", DEFAULT_LOG_LEVEL) or DEFAULT_LOG_LEVEL

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("SPREADSHEET_ID", spreadsheet_id),
            ("GOOGLE_SERVICE_ACCOUNT_JSON", sa_path_raw),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )

    sa_path = Path(sa_path_raw).expanduser()  # type: ignore[arg-type]
    if not sa_path.is_file():
        raise ConfigError(
            f"Файл Service Account JSON не найден: {sa_path}"
        )

    return BotConfig(
        telegram_bot_token=token,  # type: ignore[arg-type]
        spreadsheet_id=spreadsheet_id,  # type: ignore[arg-type]
        worksheet_name=worksheet_name,
        google_service_account_json=sa_path,
        log_level=_coerce_log_level(log_level_raw),
    )
