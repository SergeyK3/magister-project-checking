"""Валидация пользовательского ввода и первичная проверка URL отчёта."""

from __future__ import annotations

import re
from typing import Tuple

import requests


SKIP_TOKEN = "-"
"""Если магистрант ввёл этот токен — поле считаем пропущенным."""

_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

_REQUEST_TIMEOUT_SECONDS = 15
_USER_AGENT = "Mozilla/5.0 (compatible; magistrcheckbot/1.0)"


def normalize_text(value: str) -> str:
    """Обрезает пробелы; токен ``-`` интерпретирует как пропуск (пустая строка)."""

    if value is None:
        return ""
    stripped = value.strip()
    if stripped == SKIP_TOKEN:
        return ""
    return stripped


def is_valid_url(url: str) -> bool:
    """Проверяет, что строка похожа на http(s)-URL."""

    if not url:
        return False
    return bool(_URL_PATTERN.match(url.strip()))


def check_report_url(url: str) -> Tuple[str, str]:
    """Первичная проверка ссылки на отчёт.

    Возвращает кортеж ``(valid, accessible)`` со значениями
    ``"yes" / "no" / ""``. Пустая строка — поле не заполнено.
    """

    if not url:
        return "", ""

    if not is_valid_url(url):
        return "no", "no"

    try:
        response = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except requests.RequestException:
        return "yes", "no"

    accessible = "yes" if response.status_code < 400 else "no"
    return "yes", accessible
