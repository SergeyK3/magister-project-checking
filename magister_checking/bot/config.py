"""Конфигурация Telegram-бота: чтение секретов из переменных окружения / .env."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:  # python-dotenv не обязателен в рантайме
    load_dotenv = None  # type: ignore[assignment]


DEFAULT_WORKSHEET_NAME = "Регистрация"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_PERSISTENCE_FILE = Path("state") / "magistrcheckbot_state.pickle"
"""Путь по умолчанию к файлу PicklePersistence.

Интерпретируется относительно текущей рабочей директории на момент запуска
(обычно это корень репозитория — именно туда смотрят PowerShell-скрипты
``scripts/bot_start.ps1`` / ``scripts/bot_stop.ps1``). Каталог ``state/``
попадает под `.gitignore`, поэтому файл состояния не утечёт в Git."""


class ConfigError(RuntimeError):
    """Ошибка конфигурации бота (отсутствуют обязательные переменные)."""


@dataclass(frozen=True)
class BotConfig:
    """Параметры запуска бота."""

    telegram_bot_token: str
    spreadsheet_id: str
    worksheet_name: str
    project_card_output_folder_url: str
    google_service_account_json: Path
    log_level: int
    persistence_file: Path
    docx_conversion_folder_id: str = ""
    """ID папки Google Drive (буфер для конверсии), куда копируются .docx
    отчёты с преобразованием в Google Doc перед чтением Docs API. Должна
    лежать в Shared Drive, иначе Service Account упрётся в storageQuota = 0
    при ``drive.files().copy(...)``. Пустая строка — конверсия отключена,
    .docx-ссылки будут падать как неподдерживаемый формат.

    Источники в порядке приоритета (см. ``load_config``):
    ``GOOGLE_DRIVE_BUFFER_FOLDER_URL`` → ``GOOGLE_DRIVE_BUFFER_FOLDER_ID``
    → ``DOCX_CONVERSION_FOLDER_URL`` → ``DOCX_CONVERSION_FOLDER_ID``."""

    log_file: Optional[Path] = None
    """Опциональный путь к файлу лога. Если задан — `configure_logging`
    добавит ``FileHandler`` к корневому логгеру в дополнение к StreamHandler.

    Источник: env ``BOT_LOG_FILE``. Используется headless-launcher'ом
    (``scripts/bot_run_headless.ps1``), чтобы Task Scheduler-задача писала
    лог в ``state/logs/bot.log`` через стандартный Python logging — без
    хрупкого PowerShell-пайпа ``2>&1 | Out-File`` (PowerShell 5.x с
    ``$ErrorActionPreference = 'Stop'`` оборачивает каждую stderr-строку
    нативной команды как ``NativeCommandError`` и роняет пайплайн)."""

    alert_chat_ids: tuple[int, ...] = ()
    """Числовые ``chat_id`` для алертов при необработанных ошибках в хендлерах.

    Источник: env ``BOT_ALERT_CHAT_IDS`` — список через запятую (личный чат,
    группа или канал). Пусто — только логирование, без рассылки в Telegram."""

    @property
    def log_level_name(self) -> str:
        return logging.getLevelName(self.log_level)


def _read_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _parse_alert_chat_ids(raw: Optional[str]) -> tuple[int, ...]:
    """Парсит BOT_ALERT_CHAT_IDS: «123,-456» → (123, -456). Пусто → ()."""

    if raw is None or not raw.strip():
        return ()
    out: list[int] = []
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError as exc:
            raise ConfigError(
                f"Некорректный фрагмент BOT_ALERT_CHAT_IDS: {part!r} "
                "(ожидаются целые chat_id через запятую)"
            ) from exc
    return tuple(out)


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
    sa_content_raw = _read_env("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
    worksheet_name = _read_env("WORKSHEET_NAME", DEFAULT_WORKSHEET_NAME) or DEFAULT_WORKSHEET_NAME
    project_card_output_folder_url = _read_env("PROJECT_CARD_OUTPUT_FOLDER_URL", "") or ""
    log_level_raw = _read_env("LOG_LEVEL", DEFAULT_LOG_LEVEL) or DEFAULT_LOG_LEVEL
    persistence_file_raw = _read_env("BOT_PERSISTENCE_FILE")
    log_file_raw = _read_env("BOT_LOG_FILE")
    alert_chat_ids_raw = _read_env("BOT_ALERT_CHAT_IDS")
    docx_conv_raw = (
        _read_env("GOOGLE_DRIVE_BUFFER_FOLDER_URL")
        or _read_env("GOOGLE_DRIVE_BUFFER_FOLDER_ID")
        or _read_env("DOCX_CONVERSION_FOLDER_URL")
        or _read_env("DOCX_CONVERSION_FOLDER_ID")
        or ""
    )

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("SPREADSHEET_ID", spreadsheet_id),
        )
        if not value
    ]
    if not sa_path_raw and not sa_content_raw:
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON или GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
    if missing:
        raise ConfigError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )

    sa_path = _resolve_service_account_path(sa_path_raw, sa_content_raw)
    persistence_file = (
        Path(persistence_file_raw).expanduser()
        if persistence_file_raw
        else DEFAULT_PERSISTENCE_FILE
    )
    log_file = Path(log_file_raw).expanduser() if log_file_raw else None
    docx_conversion_folder_id = _resolve_docx_conversion_folder_id(docx_conv_raw)
    alert_chat_ids = _parse_alert_chat_ids(alert_chat_ids_raw)

    return BotConfig(
        telegram_bot_token=token,  # type: ignore[arg-type]
        spreadsheet_id=spreadsheet_id,  # type: ignore[arg-type]
        worksheet_name=worksheet_name,
        project_card_output_folder_url=project_card_output_folder_url,
        google_service_account_json=sa_path,
        log_level=_coerce_log_level(log_level_raw),
        persistence_file=persistence_file,
        docx_conversion_folder_id=docx_conversion_folder_id,
        log_file=log_file,
        alert_chat_ids=alert_chat_ids,
    )


def _resolve_docx_conversion_folder_id(raw: str) -> str:
    """Принимает либо чистый id, либо URL папки Drive, возвращает id.

    Пустая строка → пустая строка (фича отключена). Некорректный URL не
    роняет загрузку конфига: просто возвращаем ``""`` и логируем, чтобы
    бот/CLI мог стартовать даже при опечатке в .env и выдавать понятное
    сообщение в момент реальной конверсии.
    """

    s = (raw or "").strip()
    if not s:
        return ""
    from magister_checking.drive_urls import (
        extract_google_folder_id,
        is_google_drive_folder_url,
    )

    if is_google_drive_folder_url(s):
        try:
            return extract_google_folder_id(s)
        except ValueError:
            return ""
    return s


def _resolve_service_account_path(
    sa_path_raw: Optional[str],
    sa_content_raw: Optional[str],
) -> Path:
    """Возвращает путь к файлу JSON-ключа SA.

    Поддерживает три варианта ввода (в порядке приоритета):
    1. ``GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT`` — содержимое JSON, пишем во временный файл;
    2. ``GOOGLE_SERVICE_ACCOUNT_JSON`` со значением, похожим на JSON
       (начинается с ``{``) — также пишем во временный файл;
    3. ``GOOGLE_SERVICE_ACCOUNT_JSON`` со значением-путём — используем как есть.
    """

    if sa_content_raw and sa_content_raw.lstrip().startswith("{"):
        return _write_sa_json_to_tempfile(sa_content_raw)

    assert sa_path_raw is not None  # гарантировано вызывающей стороной
    if sa_path_raw.lstrip().startswith("{"):
        return _write_sa_json_to_tempfile(sa_path_raw)

    sa_path = Path(sa_path_raw).expanduser()
    if not sa_path.is_file():
        raise ConfigError(f"Файл Service Account JSON не найден: {sa_path}")
    return sa_path


def _write_sa_json_to_tempfile(raw_content: str) -> Path:
    try:
        json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Содержимое Service Account JSON некорректно: " + str(exc)
        ) from exc

    fd, name = tempfile.mkstemp(prefix="magistrcheckbot_sa_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw_content)
    except Exception:
        os.unlink(name)
        raise
    os.chmod(name, 0o600)
    return Path(name)
