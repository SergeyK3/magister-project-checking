"""Валидация пользовательского ввода и первичная проверка URL отчёта."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any, Tuple
from urllib.parse import urlparse

import requests

from magister_checking.drive_urls import is_google_drive_folder_url


SKIP_TOKEN = "-"
"""Если магистрант ввёл этот токен — поле считаем пропущенным."""

_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

FIO_INVALID_MESSAGE = "В поле «ФИО» введена фраза, не похожая на имя"
PHONE_INVALID_MESSAGE = "В поле «Телефон» введён неверный номер"
REPORT_URL_WRONG_TARGET_MESSAGE = "Ссылка на промежуточный отчёт неверна"
REPORT_URL_HTTP_INACCESSIBLE_MESSAGE = (
    "По ссылке бот не смог открыть страницу (часто так бывает, если документ "
    "доступен только вам). В Google Docs или Drive: «Настроить доступ» → "
    "для пункта «Все, у кого есть ссылка» выберите «Читатель»."
)
REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE = (
    "Ссылка на промежуточный отчет содержит адрес папки, а не документа. "
    "Пожалуйста, исправьте!"
)
"""Магистрант вместо ссылки на сам Google Doc прислал URL папки Drive
(``…/drive/folders/…``). Дублируется в колонке «Проверка ссылки» листа,
чтобы админ видел причину «промежуточный отчёт не загрузился» без захода
в Drive (реальный кейс — Камзебаева, row 2)."""

_CYRILLIC_CLASS = r"А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
_CYRILLIC_UPPER_CLASS = r"А-ЯЁӘҒҚҢӨҰҮҺІ"
_CYRILLIC_NAME_WORD = re.compile(
    rf"^[{_CYRILLIC_CLASS}]{{2,}}(?:[-’'][{_CYRILLIC_CLASS}]{{2,}})*\.?$"
)
_CYRILLIC_INITIALS = re.compile(rf"^(?:[{_CYRILLIC_UPPER_CLASS}]\.){{1,3}}$")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
# «отчёт» / «отчет»; латинская «e» в «отчет» часто при копировании из Word/Docs.
_INTERIM_REPORT_MARKER = re.compile(
    r"промежуточн\w*\s+отч(?:ёт|[еe]т)",
    re.IGNORECASE | re.UNICODE,
)

_REQUEST_TIMEOUT_SECONDS = 15
_USER_AGENT = "Mozilla/5.0 (compatible; magistrcheckbot/1.0)"
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 5


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


def validate_fio_shape(value: str) -> str | None:
    """Возвращает сообщение об ошибке или None, если ФИО похоже на имя.

    Правила:
    - минимум два слова, разделённых пробелом;
    - каждое слово — кириллица (включая казахские буквы), длиной ≥ 2,
      допускаются дефис и апостроф между частями (Петров-Водкин);
    - недопустимы латинские буквы. «ТОО Viamedis Kosshy» отсекается.
    """

    text = (value or "").strip()
    if not text:
        return FIO_INVALID_MESSAGE
    if _LATIN_LETTER.search(text):
        return FIO_INVALID_MESSAGE
    words = [w for w in text.split() if w]
    if len(words) < 2:
        return FIO_INVALID_MESSAGE
    for word in words:
        if _CYRILLIC_NAME_WORD.match(word):
            continue
        if _CYRILLIC_INITIALS.match(word):
            continue
        return FIO_INVALID_MESSAGE
    return None


def validate_phone_shape(value: str) -> str | None:
    """Проверяет формат телефонного номера.

    Принимаем международные/местные форматы: оставляем только цифры
    (и разрешаем ведущий «+»); требуем 10–15 цифр. Примеры валидных:
    ``+77052107246``, ``87052107246``, ``+7 (705) 210-72-46``.
    """

    text = (value or "").strip()
    if not text:
        return PHONE_INVALID_MESSAGE
    digits = re.sub(r"\D", "", text)
    if not 10 <= len(digits) <= 15:
        return PHONE_INVALID_MESSAGE
    return None


def _document_plain_text(document: Any) -> str:
    """Выдёргивает первые ~2000 символов текста Google-Doc без импорта docs_extract.

    Используется для лёгкой проверки содержимого (например, что документ —
    именно «Промежуточный отчёт»). На чужих/неожиданных структурах
    возвращает пустую строку, чтобы не ронять пайплайн.
    """

    if not isinstance(document, dict):
        return ""
    parts: list[str] = []
    total = 0
    stack: list[Any] = [document.get("body", {}).get("content", [])]
    while stack:
        node = stack.pop()
        if total >= 2000:
            break
        if isinstance(node, list):
            for item in reversed(node):
                stack.append(item)
            continue
        if not isinstance(node, dict):
            continue
        paragraph = node.get("paragraph")
        if isinstance(paragraph, dict):
            for element in paragraph.get("elements", []) or []:
                text_run = (element or {}).get("textRun") or {}
                content = text_run.get("content") or ""
                if content:
                    parts.append(content)
                    total += len(content)
                    if total >= 2000:
                        break
            continue
        table = node.get("table")
        if isinstance(table, dict):
            for row in table.get("tableRows", []) or []:
                for cell in row.get("tableCells", []) or []:
                    stack.append(cell.get("content", []))
            continue
    return "".join(parts)


def is_interim_report_document(document: Any) -> bool:
    """True, если в документе есть маркер «промежуточн… отчёт».

    Учитывается :attr:`title` из ответа Docs API (часто только там) и первые
    символы тела; допускается латинская «e» в «отчет».
    """

    parts: list[str] = []
    if isinstance(document, dict):
        title = document.get("title")
        if title:
            parts.append(str(title))
    parts.append(_document_plain_text(document))
    plain = "".join(parts).replace("ё", "е").replace("Ё", "Е")
    return bool(_INTERIM_REPORT_MARKER.search(plain))


def check_report_document_marker(document: Any) -> str | None:
    """Возвращает сообщение об ошибке, если документ — не «Промежуточный отчёт».

    None означает, что документ прошёл проверку типа.
    """

    if is_interim_report_document(document):
        return None
    return REPORT_URL_WRONG_TARGET_MESSAGE


def check_report_url_target_kind(url: str) -> str | None:
    """Проверяет, что ссылка на отчёт ведёт на документ, а не на папку Drive.

    Это формальная (без сети) проверка по виду URL. Сейчас отлавливает
    только «папка вместо документа» — самый частый случай (Камзебаева,
    row 2: ``https://drive.google.com/drive/folders/…``). Возвращает
    готовое сообщение для магистранта при нарушении правила; ``None``
    — формат URL допустим (документ Docs, файл Drive, любая http(s)
    ссылка не на ``/folders/…``).

    Пустой URL пропускаем (``None``): валидация «обязательное поле»
    делается отдельно.
    """

    if not url:
        return None
    if is_google_drive_folder_url(url):
        return REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
    return None


def _is_dangerous_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_public_host(host: str) -> bool:
    """Возвращает True, если все IP-адреса host — публичные (не loopback/LAN/link-local).

    Защита от SSRF: пользователь вводит URL, бот делает GET. Без этой проверки
    можно просканировать внутренний периметр хоста (127.0.0.1, 169.254.169.254,
    10.0.0.0/8 и т.п.) по коду ответа ``accessible=yes/no``.
    """

    if not host:
        return False

    literal = host.strip("[]")
    try:
        ip_literal = ipaddress.ip_address(literal)
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        return not _is_dangerous_ip(ip_literal)

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw_ip = sockaddr[0]
        if "%" in raw_ip:
            raw_ip = raw_ip.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False
        if _is_dangerous_ip(ip):
            return False
    return True


def _is_safe_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    host = parsed.hostname or ""
    return _is_public_host(host)


def check_report_url(url: str) -> Tuple[str, str]:
    """Первичная проверка ссылки на отчёт.

    Возвращает кортеж ``(valid, accessible)`` со значениями
    ``"yes" / "no" / ""``. Пустая строка — поле не заполнено.

    Безопасность: запросы идут только на публичные http(s)-адреса;
    редиректы на приватные/loopback хосты считаются недоступными,
    чтобы исключить SSRF через промежуточные перенаправления.
    """

    if not url:
        return "", ""

    stripped = url.strip()
    if not is_valid_url(stripped) or not _is_safe_http_url(stripped):
        return "no", "no"

    current = stripped
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            response = requests.get(
                current,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
                headers={"User-Agent": _USER_AGENT},
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                if not location:
                    return "yes", "no"
                if not _is_safe_http_url(location):
                    return "yes", "no"
                current = location
                continue
            accessible = "yes" if response.status_code < 400 else "no"
            return "yes", accessible
    except requests.RequestException:
        return "yes", "no"

    return "yes", "no"
