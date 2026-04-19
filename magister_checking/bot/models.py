"""Модель данных анкеты магистранта и статусы заполнения."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import List, Tuple


class FillStatus(str, Enum):
    """Статусы заполненности анкеты (см. п.12 ТЗ)."""

    NEW = "NEW"
    PARTIAL = "PARTIAL"
    REGISTERED = "REGISTERED"


@dataclass
class UserForm:
    """Регистрационная анкета магистранта.

    Порядок полей жёстко соответствует 16 столбцам Google Sheets из п.8.1 ТЗ.
    """

    telegram_id: str = ""
    telegram_username: str = ""
    telegram_first_name: str = ""
    telegram_last_name: str = ""
    fio: str = ""
    group_name: str = ""
    workplace: str = ""
    position: str = ""
    phone: str = ""
    supervisor: str = ""
    report_url: str = ""
    report_url_valid: str = ""
    report_url_accessible: str = ""
    report_url_public_guess: str = ""
    fill_status: str = ""
    last_action: str = ""


SHEET_HEADER: List[str] = [f.name for f in fields(UserForm)]
"""Шапка таблицы Google Sheets (16 столбцов п.8.1 ТЗ)."""


REQUIRED_FIELDS: Tuple[str, ...] = (
    "fio",
    "group_name",
    "workplace",
    "position",
    "phone",
    "supervisor",
    "report_url",
)
"""Поля, которые магистрант обязан заполнить через бота (п.5.3 ТЗ)."""


FIELD_LABELS: "dict[str, str]" = {
    "fio": "ФИО",
    "group_name": "Группа",
    "workplace": "Место работы",
    "position": "Должность",
    "phone": "Телефон",
    "supervisor": "Научный руководитель",
    "report_url": "Ссылка на промежуточный отчет",
}
"""Человеко-читаемые названия обязательных полей для сообщений бота."""


FIELD_PROMPTS: "dict[str, str]" = {
    "fio": "Введите ФИО магистранта:",
    "group_name": "Введите группу:",
    "workplace": "Введите место работы:",
    "position": "Введите должность:",
    "phone": "Введите сотовый контактный телефон:",
    "supervisor": "Введите ФИО научного руководителя:",
    "report_url": "Введите ссылку на промежуточный отчет:",
}
"""Тексты подсказок при запросе каждого обязательного поля."""


def get_missing_fields(user: UserForm) -> List[str]:
    """Возвращает человеко-читаемые имена незаполненных обязательных полей."""

    return [FIELD_LABELS[name] for name in REQUIRED_FIELDS if not getattr(user, name)]


def get_missing_field_keys(user: UserForm) -> List[str]:
    """Возвращает ключи (имена атрибутов) незаполненных обязательных полей."""

    return [name for name in REQUIRED_FIELDS if not getattr(user, name)]


def compute_fill_status(user: UserForm) -> FillStatus:
    """Считает статус заполненности по правилам п.12 ТЗ.

    - NEW — ни одно обязательное поле не заполнено;
    - REGISTERED — все обязательные поля заполнены;
    - PARTIAL — часть обязательных полей заполнена.
    """

    filled = [bool(getattr(user, name)) for name in REQUIRED_FIELDS]
    if not any(filled):
        return FillStatus.NEW
    if all(filled):
        return FillStatus.REGISTERED
    return FillStatus.PARTIAL
