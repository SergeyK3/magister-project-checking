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


def is_probably_public_google_doc_response(text: str) -> str:
    """Очень грубая эвристика по HTML-ответу: открыт ли документ публично.

    Возвращает ``"yes"``, ``"no"`` или ``"unknown"``. Это не финальная проверка
    прав доступа — она нужна только для первичной отметки в Sheets (п.7.3 ТЗ).
    """

    lowered = (text or "").lower()

    deny_markers = (
        "you need access",
        "нужен доступ",
        "request access",
        "запросить доступ",
        "sign in",
        "войти",
    )
    for marker in deny_markers:
        if marker in lowered:
            return "no"

    ok_markers = (
        "docs.google.com",
        "google docs",
        "google drive",
    )
    for marker in ok_markers:
        if marker in lowered:
            return "yes"

    return "unknown"


def check_report_url(url: str) -> Tuple[str, str, str]:
    """Первичная проверка ссылки на отчёт.

    Возвращает кортеж ``(valid, accessible, public_guess)`` со значениями
    ``"yes" / "no" / "unknown" / ""``. Пустая строка — поле не заполнено.
    """

    if not url:
        return "", "", ""

    if not is_valid_url(url):
        return "no", "no", "no"

    try:
        response = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except requests.RequestException:
        return "yes", "no", "unknown"

    accessible = "yes" if response.status_code < 400 else "no"
    public_guess = is_probably_public_google_doc_response(response.text or "")
    return "yes", accessible, public_guess
