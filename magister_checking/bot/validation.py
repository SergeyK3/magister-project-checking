"""Валидация пользовательского ввода и первичная проверка URL отчёта."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Tuple
from urllib.parse import urlparse

import requests


SKIP_TOKEN = "-"
"""Если магистрант ввёл этот токен — поле считаем пропущенным."""

_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

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
