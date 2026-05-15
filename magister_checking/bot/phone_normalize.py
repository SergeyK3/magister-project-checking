"""Нормализация номеров телефона РФ/КЗ к виду +7 и 10 цифрам после кода страны."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")


def normalize_phone_ru_kz(raw: str) -> str:
    """Возвращает ``+7XXXXXXXXXX`` (12 символов) или пустую строку, если разобрать нельзя.

    Допускает ввод: +7…, 7…, 8…, или 10 цифр «национальной» части без префикса.
    """

    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    digits = _NON_DIGITS.sub("", s)
    if not digits:
        return ""

    # 10 цифр — считаем, что национальная часть без кода страны
    if len(digits) == 10:
        return "+7" + digits

    if len(digits) == 11:
        if digits.startswith("8"):
            return "+7" + digits[1:]
        if digits.startswith("7"):
            return "+" + digits
        return ""

    return ""
