"""Доступ к Google Sheets через Service Account.

Содержит I/O-операции и не зависит от Telegram. Все функции принимают
worksheet-объект (gspread.Worksheet) — это упрощает тестирование на фейке.
"""

from __future__ import annotations

import logging
from datetime import datetime
import html
import re
from dataclasses import dataclass, fields
from typing import Iterable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import SHEET_HEADER, UserForm, effective_fill_status
from magister_checking.bot.phone_normalize import normalize_phone_ru_kz
from magister_checking.bot.row_pipeline import Stage3CellUpdate, Stage4CellUpdate

logger = logging.getLogger(__name__)

_SHEETS_VALUE_INPUT_OPTION = "RAW"
"""Режим записи в Google Sheets.

RAW гарантирует, что введённые магистрантом строки вида ``=IMPORTRANGE(...)``,
``=HYPERLINK(...)``, ``=IMAGE(...)`` и т.п. сохраняются как текст и не
исполняются как формулы. Это защита от CSV/formula-injection в листе
регистрации, не завязанная на версию gspread.
"""


def _safe_update(worksheet: "gspread.Worksheet", range_a1: str, values) -> None:
    """Обёртка над ``worksheet.update`` с явным RAW-режимом.

    Если конкретная реализация worksheet (в т.ч. тестовая) не поддерживает
    ``value_input_option``, молча откатываемся к позиционному вызову — это
    не ослабляет защиту, потому что в production используется gspread,
    где kwarg поддержан.
    """

    try:
        worksheet.update(range_a1, values, value_input_option=_SHEETS_VALUE_INPUT_OPTION)
    except TypeError:
        worksheet.update(range_a1, values)


_FIO_COLUMN_INDEX = SHEET_HEADER.index("fio")
_TELEGRAM_ID_COLUMN_INDEX = SHEET_HEADER.index("telegram_id")
_TELEGRAM_USERNAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_username")
_TELEGRAM_FIRST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_first_name")
_TELEGRAM_LAST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_last_name")
_WHITESPACE_RE = re.compile(r"\s+")
DASHBOARD_WORKSHEET_NAME = "Dashboard"
ADMINS_WORKSHEET_NAME = "Администраторы"
SUPERVISORS_WORKSHEET_NAME = "научрук"
"""Лист с научными руководителями: как у «Администраторы» (``fio``, ``telegram_id``, ``active``)."""
RECHECK_HISTORY_WORKSHEET_NAME = "История проверок"
_DASHBOARD_RANGE = "A1:B16"


# Stage 4 (c): схема листа «История проверок». Колонки фиксированы:
# мы сами создаём этот лист и не ожидаем кастомизации заголовка
# (в отличие от «Регистрация», где порядок колонок может меняться).
RECHECK_HISTORY_HEADER: tuple[str, ...] = (
    "timestamp",
    "row_number",
    "fio",
    "source",
    "stopped_at",
    "passed",
    "issues",
    "pages_total",
    "sources_count",
    "compliance",
    "fingerprint",
)


@dataclass
class RecheckHistoryEntry:
    """Одна запись в листе ``История проверок``.

    Соответствует схеме ``RECHECK_HISTORY_HEADER``. Все поля опциональны
    (кроме ``timestamp``/``row_number``), пустые значения пишутся как ""
    — лист предназначен для аудита, а не для вычислений.
    """

    timestamp: str
    row_number: int
    fio: str = ""
    source: str = ""
    stopped_at: str = ""
    passed: str = ""
    issues: str = ""
    pages_total: str = ""
    sources_count: str = ""
    compliance: str = ""
    fingerprint: str = ""

    def to_row(self) -> list[str]:
        """Список значений в порядке ``RECHECK_HISTORY_HEADER``."""
        return [
            self.timestamp,
            str(self.row_number),
            self.fio,
            self.source,
            self.stopped_at,
            self.passed,
            self.issues,
            self.pages_total,
            self.sources_count,
            self.compliance,
            self.fingerprint,
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> "RecheckHistoryEntry":
        """Восстанавливает запись из листа. Недостающие колонки → ""."""
        padded = list(row) + [""] * max(0, len(RECHECK_HISTORY_HEADER) - len(row))
        try:
            row_number = int(str(padded[1]).strip() or "0")
        except ValueError:
            row_number = 0
        return cls(
            timestamp=str(padded[0] or ""),
            row_number=row_number,
            fio=str(padded[2] or ""),
            source=str(padded[3] or ""),
            stopped_at=str(padded[4] or ""),
            passed=str(padded[5] or ""),
            issues=str(padded[6] or ""),
            pages_total=str(padded[7] or ""),
            sources_count=str(padded[8] or ""),
            compliance=str(padded[9] or ""),
            fingerprint=str(padded[10] or ""),
        )

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "отметка времени", "дата регистрации", "дата и время"),
    "telegram_id": ("telegram_id", "telegram id", "id telegram"),
    "telegram_username": ("telegram_username", "telegram username", "username telegram"),
    "telegram_first_name": ("telegram_first_name", "telegram first name"),
    "telegram_last_name": ("telegram_last_name", "telegram last name"),
    "fio": ("fio", "фио"),
    "active": ("active", "активен", "вкл"),
    "group_name": ("group_name", "group name", "группа"),
    "workplace": ("workplace", "место работы"),
    "position": ("position", "должность"),
    "phone": (
        "phone",
        "телефон",
        "сотовый контактный телефон",
        "контактный телефон",
    ),
    "supervisor": ("supervisor", "научный руководитель"),
    "report_url": (
        "report_url",
        "ссылка на промежуточный отчет",
        "ссылка на промежуточный отчёт",
    ),
    "report_url_valid": ("report_url_valid", "проверка ссылки"),
    "report_url_accessible": ("report_url_accessible", "доступ открыт"),
    "project_folder_url": (
        "project_folder_url",
        "ссылка на магистерский проект",
        "ссылка на папку",
        "ссылка на папку 1",
        "ссылка на папку 1с",
        "ссылка на папку магистерский проект",
    ),
    "lkb_url": ("lkb_url", "ссылка на лкб"),
    "dissertation_url": ("dissertation_url", "ссылка на диссер", "ссылка на диссертацию"),
    "publication_url": (
        "publication_url",
        "ссылка на публикацию",
        "ссылка на публик",
    ),
    "pages_total": ("pages_total", "число страниц", "всего страниц"),
    "sources_count": ("sources_count", "число источников", "источников"),
    "compliance": (
        "compliance",
        "соответствие",
        "соответствие оформлению",
        "соответствие офо",
    ),
    "dissertation_title": ("dissertation_title", "название диссертации"),
    "dissertation_language": ("dissertation_language", "язык диссертации"),
    "fill_status": ("fill_status", "статус заполнения"),
    "last_action": ("last_action", "последнее действие"),
}

# Четыре колонки ссылок (папка / ЛКБ / диссертация / публикация): временный ввод вручную
# при сбое извлечения из промежуточного отчёта.
SHEET_LINK_OVERRIDE_KEYS: tuple[str, ...] = (
    "project_folder_url",
    "lkb_url",
    "dissertation_url",
    "publication_url",
)


def read_sheet_link_overrides_for_row(
    worksheet: gspread.Worksheet,
    row_number: int,
) -> dict[str, str]:
    """Возвращает текущие значения четырёх колонок ссылок из строки листа.

    Используется как обходной путь, если ссылки нельзя извлечь из текста
    промежуточного отчёта (Docs API / разбор). Фильтрация «настоящий URL»
    выполняется в ``row_check_cli``."""
    header = _header_row(worksheet)
    row = worksheet.row_values(row_number)
    padded = list(row) + [""] * max(0, len(header) - len(row))
    field_map = _field_to_column_map_from_header(header)
    out: dict[str, str] = {}
    for key in SHEET_LINK_OVERRIDE_KEYS:
        idx = field_map.get(key)
        if idx is None or idx >= len(padded):
            continue
        out[key] = str(padded[idx] or "").strip()
    return out


GOOGLE_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _column_letter(index_zero_based: int) -> str:
    """Возвращает A1-обозначение столбца по 0-based индексу (0 -> 'A')."""

    n = index_zero_based + 1
    letters = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


_LAST_COLUMN_LETTER = _column_letter(len(SHEET_HEADER) - 1)


def _normalize_header(value: str) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _header_row(worksheet: gspread.Worksheet) -> List[str]:
    return worksheet.row_values(1)


def _field_to_column_map(worksheet: gspread.Worksheet) -> dict[str, int]:
    header = _header_row(worksheet)
    normalized_to_index = {
        _normalize_header(name): idx for idx, name in enumerate(header) if _normalize_header(name)
    }
    mapping: dict[str, int] = {}
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            idx = normalized_to_index.get(_normalize_header(alias))
            if idx is not None:
                mapping[field_name] = idx
                break
    return mapping


def _present_data_columns(worksheet: gspread.Worksheet) -> List[int]:
    mapping = _field_to_column_map(worksheet)
    preferred_fields = (
        "telegram_id",
        "fio",
        "group_name",
        "workplace",
        "position",
        "phone",
        "supervisor",
        "report_url",
        "report_url_valid",
        "report_url_accessible",
        "fill_status",
        "last_action",
    )
    columns = [mapping[name] for name in preferred_fields if name in mapping]
    if columns:
        return sorted(set(columns))
    return [_TELEGRAM_ID_COLUMN_INDEX]


def _build_row_for_header(
    worksheet: gspread.Worksheet,
    user: UserForm,
    existing_row: Iterable[str] | None = None,
    extra_values: dict[str, str] | None = None,
) -> List[str]:
    header = _header_row(worksheet)
    base = list(existing_row or [])
    width = max(len(header), len(base))
    out = base + [""] * (width - len(base))
    mapping = _field_to_column_map(worksheet)
    for field_name, col_idx in mapping.items():
        while len(out) <= col_idx:
            out.append("")
        if hasattr(user, field_name):
            out[col_idx] = str(getattr(user, field_name) or "")
        elif extra_values and field_name in extra_values:
            out[col_idx] = str(extra_values[field_name] or "")
    return out[: max(len(header), len(out))]


def _range_for_row(row_number: int, width: int) -> str:
    last_col = _column_letter(max(width - 1, 0))
    return f"A{row_number}:{last_col}{row_number}"


def get_gspread_client(config: BotConfig) -> gspread.Client:
    """Создаёт авторизованного gspread-клиента из Service Account JSON."""

    creds = Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )
    return gspread.authorize(creds)


def get_worksheet(config: BotConfig) -> gspread.Worksheet:
    """Открывает рабочий лист, имя которого задано в конфиге."""

    spreadsheet = get_spreadsheet(config)
    return spreadsheet.worksheet(config.worksheet_name)


def get_spreadsheet(config: BotConfig) -> gspread.Spreadsheet:
    """Открывает Google Sheets по ID из конфига."""

    client = get_gspread_client(config)
    return client.open_by_key(config.spreadsheet_id)


def get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    *,
    rows: int = 20,
    cols: int = 2,
) -> gspread.Worksheet:
    """Возвращает лист по имени или создаёт его, если он отсутствует."""

    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def get_optional_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
) -> gspread.Worksheet | None:
    """Возвращает лист по имени или ``None``, если его нет."""

    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return None


def _ensure_recheck_history_worksheet(
    spreadsheet: gspread.Spreadsheet,
) -> gspread.Worksheet:
    """Возвращает лист «История проверок», создавая при необходимости.

    При создании сразу пишет заголовок ``RECHECK_HISTORY_HEADER``. Если
    лист существовал ранее, но первая строка не совпадает с заголовком
    (или пуста), header перезаписывается — это безопасно, потому что
    лист принадлежит боту и пользователи туда руками не пишут.
    """

    worksheet = get_or_create_worksheet(
        spreadsheet,
        RECHECK_HISTORY_WORKSHEET_NAME,
        rows=200,
        cols=len(RECHECK_HISTORY_HEADER),
    )
    current_header = worksheet.row_values(1)
    if list(current_header) != list(RECHECK_HISTORY_HEADER):
        end_letter = _column_letter(len(RECHECK_HISTORY_HEADER) - 1)
        _safe_update(
            worksheet, f"A1:{end_letter}1", [list(RECHECK_HISTORY_HEADER)]
        )
    return worksheet


def append_recheck_history(
    spreadsheet: gspread.Spreadsheet,
    entry: RecheckHistoryEntry,
) -> None:
    """Дописывает одну строку аудита прогона в «Историю проверок».

    Лист создаётся лениво — никаких дополнительных миграций не нужно,
    бот просто начнёт писать историю на любой существующей таблице.
    Запись идёт в RAW-режиме, чтобы значения вида ``=...`` (теоретически
    из ``issues``) не исполнялись как формулы.
    """

    worksheet = _ensure_recheck_history_worksheet(spreadsheet)
    row = entry.to_row()
    try:
        worksheet.append_row(row, value_input_option=_SHEETS_VALUE_INPUT_OPTION)
    except TypeError:
        worksheet.append_row(row)


def read_last_recheck_entry(
    spreadsheet: gspread.Spreadsheet,
    row_number: int,
) -> RecheckHistoryEntry | None:
    """Последняя по таблице запись истории для данной строки регистрации.

    Используется ``--only-if-changed`` (handoff §8 — diff_detection): чтобы
    решить, делать ли полный прогон, надо сравнить fingerprint текущих
    входов с fingerprint прошлого прогона. Возвращает ``None``, если листа
    «История проверок» ещё нет или записей по этой строке не было.
    """

    worksheet = get_optional_worksheet(spreadsheet, RECHECK_HISTORY_WORKSHEET_NAME)
    if worksheet is None:
        return None
    try:
        rows = worksheet.get_all_values()
    except Exception:  # noqa: BLE001
        return None
    needle = str(row_number).strip()
    last: RecheckHistoryEntry | None = None
    for raw in rows[1:]:
        if not raw:
            continue
        if len(raw) < 2:
            continue
        if str(raw[1]).strip() != needle:
            continue
        last = RecheckHistoryEntry.from_row(raw)
    return last


def ensure_header(worksheet: gspread.Worksheet) -> None:
    """Гарантирует шапку только для пустого листа.

    Если первая строка уже непустая, считаем её пользовательским заголовком и не
    перезаписываем: это позволяет писать данные ниже даже при защищённой шапке.
    """

    current = worksheet.row_values(1)
    if current == SHEET_HEADER:
        return
    if any(str(value).strip() for value in current):
        return
    range_a1 = f"A1:{_LAST_COLUMN_LETTER}1"
    _safe_update(worksheet, range_a1, [SHEET_HEADER])


def find_row_by_telegram_id(
    worksheet: gspread.Worksheet, telegram_id: str
) -> Optional[int]:
    """Возвращает 1-based номер строки магистранта по Telegram ID или None."""

    if telegram_id is None or str(telegram_id).strip() == "":
        return None
    field_map = _field_to_column_map(worksheet)
    col_idx = field_map.get("telegram_id")
    if col_idx is None:
        return None

    needle = str(telegram_id).strip()
    values = worksheet.col_values(col_idx + 1)
    for idx, cell_value in enumerate(values, start=1):
        if idx == 1:
            continue
        if str(cell_value).strip() == needle:
            return idx
    return None


def list_registered_telegram_ids(worksheet: gspread.Worksheet) -> List[str]:
    """Список уникальных непустых telegram_id из листа Регистрация.

    Заголовочная строка пропускается. Дубликаты схлопываются с сохранением
    первого появления (стабильный порядок). Если колонки ``telegram_id`` нет —
    возвращает пустой список (а не падает: вызывающий broadcast CLI просто
    получит «получателей нет», что корректнее, чем исключение).

    Используется broadcast-командой как один из источников аудитории
    (handoff §1 — рассылка обновления Stage 4 .docx fix).
    """

    field_map = _field_to_column_map(worksheet)
    col_idx = field_map.get("telegram_id")
    if col_idx is None:
        return []
    values = worksheet.col_values(col_idx + 1)
    out: List[str] = []
    seen: set[str] = set()
    for idx, raw in enumerate(values, start=1):
        if idx == 1:
            continue
        cleaned = str(raw or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def normalize_fio(value: str) -> str:
    """Приводит ФИО к канонической форме для сравнения.

    - убирает регистр;
    - схлопывает любые пробелы в один;
    - заменяет ``ё`` на ``е`` (частая причина расхождений в формах).
    """

    if value is None:
        return ""
    text = str(value).strip().lower().replace("ё", "е")
    return _WHITESPACE_RE.sub(" ", text)


def find_rows_by_fio(worksheet: gspread.Worksheet, fio: str) -> List[int]:
    """Возвращает список 1-based номеров строк, где ФИО совпадает после нормализации.

    Заголовочная строка пропускается. Если ``fio`` пустое — возвращает ``[]``.
    """

    needle = normalize_fio(fio)
    if not needle:
        return []
    field_map = _field_to_column_map(worksheet)
    col_idx = field_map.get("fio", _FIO_COLUMN_INDEX)
    column_values = worksheet.col_values(col_idx + 1)
    matches: List[int] = []
    for idx, value in enumerate(column_values, start=1):
        if idx == 1:
            continue
        if normalize_fio(value) == needle:
            matches.append(idx)
    return matches


def attach_telegram_to_row(
    worksheet: gspread.Worksheet,
    row_number: int,
    *,
    telegram_id: str,
    telegram_username: str = "",
    telegram_first_name: str = "",
    telegram_last_name: str = "",
) -> None:
    """Записывает идентификаторы Telegram в первые 4 колонки указанной строки.

    Нужно для привязки чата к уже существующей записи (из Google Form или
    предыдущей выгрузки), у которой ``telegram_id`` пуст.
    """
    field_map = _field_to_column_map(worksheet)
    telegram_fields = (
        "telegram_id",
        "telegram_username",
        "telegram_first_name",
        "telegram_last_name",
    )
    if not any(name in field_map for name in telegram_fields):
        return

    existing_row = worksheet.row_values(row_number)
    width = max(len(_header_row(worksheet)), len(existing_row))
    row = existing_row + [""] * (width - len(existing_row))
    updates = {
        "telegram_id": telegram_id,
        "telegram_username": telegram_username,
        "telegram_first_name": telegram_first_name,
        "telegram_last_name": telegram_last_name,
    }
    for field_name, value in updates.items():
        col_idx = field_map.get(field_name)
        if col_idx is None:
            continue
        while len(row) <= col_idx:
            row.append("")
        row[col_idx] = str(value or "")
    _safe_update(worksheet, _range_for_row(row_number, len(row)), [row])


def _user_to_row(user: UserForm) -> List[str]:
    return [str(getattr(user, name) or "") for name in SHEET_HEADER]


def _find_first_free_data_row(worksheet: gspread.Worksheet) -> int:
    """Возвращает первую свободную строку данных под заголовком.

    Ищет первую пустую ячейку в колонке ``telegram_id`` начиная со 2-й строки.
    Это позволяет заполнять «дырки» внутри листа (например, A10:P10), а не
    всегда писать в конец через append.
    """

    columns = _present_data_columns(worksheet)
    max_rows = max(len(worksheet.col_values(col_idx + 1)) for col_idx in columns)
    if max_rows <= 1:
        return 2

    for idx in range(2, max_rows + 1):
        row = worksheet.row_values(idx)
        if all(
            str(row[col_idx]).strip() == "" if col_idx < len(row) else True
            for col_idx in columns
        ):
            return idx
    return max_rows + 1


def _row_to_user(header: Iterable[str], row: Iterable[str]) -> UserForm:
    header_list = list(header)
    row_list = list(row)
    padded = row_list + [""] * (len(header_list) - len(row_list))
    normalized_header = {
        field_name: idx
        for field_name, idx in _field_to_column_map_from_header(header_list).items()
    }
    kwargs = {name: "" for name in SHEET_HEADER}
    for field_name, idx in normalized_header.items():
        if idx < len(padded):
            raw = padded[idx]
            kwargs[field_name] = "" if raw is None or raw == "" else str(raw)
    declared = {f.name for f in fields(UserForm)}
    cleaned = {k: v for k, v in kwargs.items() if k in declared}
    return UserForm(**cleaned)


def _field_to_column_map_from_header(header: List[str]) -> dict[str, int]:
    normalized_to_index = {
        _normalize_header(name): idx for idx, name in enumerate(header) if _normalize_header(name)
    }
    mapping: dict[str, int] = {}
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            idx = normalized_to_index.get(_normalize_header(alias))
            if idx is not None:
                mapping[field_name] = idx
                break
    return mapping


def load_user(worksheet: gspread.Worksheet, row_number: int) -> UserForm:
    """Загружает анкету магистранта из указанной строки таблицы."""

    header = _header_row(worksheet)
    row = worksheet.row_values(row_number)
    return _row_to_user(header, row)


def load_row_values(worksheet: gspread.Worksheet, row_number: int) -> List[str]:
    """Возвращает сырые значения строки листа."""

    return worksheet.row_values(row_number)


def save_user_to_row_with_extras(
    worksheet: gspread.Worksheet,
    row_number: int,
    user: UserForm,
    *,
    extra_values: dict[str, str] | None = None,
) -> int:
    """Обновляет конкретную строку листа, не полагаясь на telegram_id."""

    ensure_header(worksheet)
    header = _header_row(worksheet)
    built_row = _build_row_for_header(
        worksheet,
        user,
        worksheet.row_values(row_number),
        extra_values=extra_values,
    )
    range_a1 = _range_for_row(row_number, max(len(header), len(built_row)))
    _safe_update(worksheet, range_a1, [built_row])
    return row_number


def _iter_users(worksheet: gspread.Worksheet) -> List[UserForm]:
    header = _header_row(worksheet)
    if not header:
        return []

    # Один values.get на весь лист вместо len(header) вызовов col_values и
    # по одному row_values на строку — снижает риск 429 (Read requests/min).
    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        return []
    users: List[UserForm] = []
    n_header = len(header)
    for row in all_values[1:]:
        if not any(str(value).strip() for value in row):
            continue
        padded = (list(row) + [""] * n_header)[:n_header]
        users.append(_row_to_user(header, padded))
    return users


def build_dashboard_rows(registration_worksheet: gspread.Worksheet) -> List[List[str]]:
    """Строит простую сводку по регистрациям для листа Dashboard."""

    users = _iter_users(registration_worksheet)
    statuses = [effective_fill_status(user).value for user in users]
    total = len(users)
    rows = [
        ["Показатель", "Значение"],
        ["Обновлено", datetime.now().strftime("%d.%m.%Y %H:%M:%S")],
        ["Всего регистраций", str(total)],
        ["Полностью зарегистрированы", str(sum(status == "REGISTERED" for status in statuses))],
        ["Частично заполнены", str(sum(status == "PARTIAL" for status in statuses))],
        ["Новые / пустые", str(sum(status == "NEW" for status in statuses))],
        ["Проверка пройдена (OK)", str(sum(status == "OK" for status in statuses))],
        ["Нужны исправления (NEED_FIX)", str(sum(status == "NEED_FIX" for status in statuses))],
        ["Ошибка проверки (ERROR)", str(sum(status == "ERROR" for status in statuses))],
        ["Привязаны к Telegram", str(sum(bool((user.telegram_id or "").strip()) for user in users))],
        ["Есть ссылка на отчет", str(sum(bool((user.report_url or "").strip()) for user in users))],
        ["Доступ открыт", str(sum((user.report_url_accessible or "").strip().lower() == "yes" for user in users))],
        ["Доступ не открыт", str(sum((user.report_url_accessible or "").strip().lower() == "no" for user in users))],
    ]
    while len(rows) < 16:
        rows.append(["", ""])
    return rows


def format_dashboard_telegram_message(dashboard_rows: List[List[str]]) -> str:
    """Те же пары «показатель / значение», что на листе Dashboard, текстом для Telegram (D1)."""

    lines: List[str] = ["<b>Сводка (лист Dashboard)</b>", ""]
    for row in dashboard_rows:
        if not row or len(row) < 2:
            continue
        key = (row[0] or "").strip()
        val = (row[1] or "").strip()
        if not key and not val:
            continue
        ek = html.escape(key)
        ev = html.escape(val)
        if key:
            lines.append(f"{ek}: {ev}" if val else ek)
    return "\n".join(lines)


def sync_registration_dashboard(config: BotConfig) -> None:
    """Пересчитывает лист Dashboard в той же Google-таблице, что и регистрации."""

    spreadsheet = get_spreadsheet(config)
    registration_worksheet = spreadsheet.worksheet(config.worksheet_name)
    dashboard_worksheet = get_or_create_worksheet(
        spreadsheet,
        DASHBOARD_WORKSHEET_NAME,
        rows=20,
        cols=2,
    )
    dashboard_rows = build_dashboard_rows(registration_worksheet)
    _safe_update(dashboard_worksheet, _DASHBOARD_RANGE, dashboard_rows)


MAGISTRANTS_REGISTERED_LABEL = "зарегистрирован"
MAGISTRANTS_NOT_REGISTERED_LABEL = "нет"

_MAGISTRANTS_HEADER_FIO: tuple[str, ...] = ("фио магистранта", "фио")
_MAGISTRANTS_HEADER_PHONE: tuple[str, ...] = (
    "телефон",
    "сотовый контактный телефон",
    "телефон магистранта",
)
_MAGISTRANTS_HEADER_SUPERVISOR: tuple[str, ...] = (
    "научный руков",
    "научный руководитель",
    "научный руководитель ссылка",
    "научрук",
)
_MAGISTRANTS_HEADER_REG: tuple[str, ...] = ("регистрация",)
_MAGISTRANTS_HEADER_TELEGRAM_ID: tuple[str, ...] = (
    "telegram_id",
    "telegram id",
    "id telegram",
    "телеграм id",
    "телеграм",
)


def _magistrants_resolve_column(
    header: List[str], aliases: tuple[str, ...]
) -> Optional[int]:
    normalized_to_index = {
        _normalize_header(name): idx
        for idx, name in enumerate(header)
        if _normalize_header(name)
    }
    for alias in aliases:
        key = _normalize_header(alias)
        if key in normalized_to_index:
            return normalized_to_index[key]
    # Заголовок вида «Научный руководитель (ссылка)» → «научный руководитель ссылка»;
    # допускаем совпадение по префиксу для длинных алиасов (ключ + пробел + хвост).
    for alias in aliases:
        key = _normalize_header(alias)
        if len(key) < 12:
            continue
        for norm_name, idx in normalized_to_index.items():
            if norm_name == key or norm_name.startswith(key + " "):
                return idx
    return None


def magistrants_sheet_column_indices(header: List[str]) -> Optional[dict[str, int]]:
    """Индексы колонок листа master-списка (0-based). Ключ ``supervisor`` — если колонка есть."""

    fio_i = _magistrants_resolve_column(header, _MAGISTRANTS_HEADER_FIO)
    phone_i = _magistrants_resolve_column(header, _MAGISTRANTS_HEADER_PHONE)
    reg_i = _magistrants_resolve_column(header, _MAGISTRANTS_HEADER_REG)
    sup_i = _magistrants_resolve_column(header, _MAGISTRANTS_HEADER_SUPERVISOR)
    tg_i = _magistrants_resolve_column(header, _MAGISTRANTS_HEADER_TELEGRAM_ID)
    if fio_i is None or phone_i is None or reg_i is None:
        return None
    out: dict[str, int] = {
        "fio": fio_i,
        "phone": phone_i,
        "registration": reg_i,
    }
    if sup_i is not None:
        out["supervisor"] = sup_i
    if tg_i is not None:
        out["telegram_id"] = tg_i
    return out


def registration_students_by_fio_phone(
    registration_worksheet: gspread.Worksheet,
) -> dict[tuple[str, str], UserForm]:
    """Ключ (normalize_fio, normalize_phone) → анкета; только непустые tg_id, ФИО, телефон."""

    users = _iter_users(registration_worksheet)
    out: dict[tuple[str, str], UserForm] = {}
    for u in users:
        if not (u.telegram_id or "").strip():
            continue
        if not (u.fio or "").strip():
            continue
        if not (u.phone or "").strip():
            continue
        fk = normalize_fio(u.fio)
        pk = normalize_phone_ru_kz(u.phone)
        if not fk or not pk:
            continue
        key = (fk, pk)
        if key in out:
            logger.warning("Дубликат ключа ФИО+тел в листе Регистрация: %s", key)
        out[key] = u
    return out


def _supervisor_name_rest_word_align(words_a: list[str], words_b: list[str]) -> bool:
    """Совпадение хвоста ФИО без инициалов с точками: по словам, допуская префиксы."""

    if not words_a and not words_b:
        return True
    if not words_a or not words_b:
        return True
    n = min(len(words_a), len(words_b))
    for i in range(n):
        x, y = words_a[i], words_b[i]
        if x == y:
            continue
        if x.startswith(y) or y.startswith(x):
            continue
        if len(x) == 1 and y.startswith(x):
            continue
        if len(y) == 1 and x.startswith(y):
            continue
        return False
    return True


def _supervisor_initial_letters_from_rest(abbrev_rest: str) -> list[str]:
    """Буквы инициалов из «г. к.», «Г.К.», «и.и.» (порядок сохраняется)."""

    letters: list[str] = []
    for raw in re.split(r"[\s.]+", abbrev_rest.strip()):
        chunk = raw.strip().lower()
        if not chunk:
            continue
        if len(chunk) == 1 and chunk.isalpha():
            letters.append(chunk)
        elif len(chunk) == 2 and chunk.isalpha():
            letters.extend([chunk[0], chunk[1]])
        else:
            letters.append(chunk[0])
    return letters


def _supervisor_rest_initials_vs_words(abbrev_rest: str, full_rest: str) -> bool:
    full_words = [w for w in full_rest.split() if w]
    if not full_words:
        return False
    letters = _supervisor_initial_letters_from_rest(abbrev_rest)
    if not letters:
        return False
    if not full_words[0].startswith(letters[0]):
        return False
    if len(letters) == 1:
        return True
    for i, letter in enumerate(letters[1:], start=1):
        if i >= len(full_words):
            return False
        if not full_words[i].startswith(letter):
            return False
    return True


def _supervisor_name_rest_compatible(rest_a: str, rest_b: str) -> bool:
    if not rest_a and not rest_b:
        return True
    if not rest_a or not rest_b:
        return True
    ra, rb = rest_a.strip(), rest_b.strip()
    if ra in rb or rb in ra or ra == rb:
        return True
    wa, wb = ra.split(), rb.split()
    if "." in ra or "." in rb:
        abbrev, full = (ra, rb) if "." in ra else (rb, ra)
        return _supervisor_rest_initials_vs_words(abbrev, full)
    return _supervisor_name_rest_word_align(wa, wb)


def supervisor_name_matches(sup_fio_sheet: str, cell_supervisor: str) -> bool:
    """Сопоставляет ФИО научрука из листа «научрук» с ячейкой на листе магистрантов.

    После равенства и вхождения подстроки (как раньше) допускает расхождение
    отчества и сокращения имени до инициалов при совпадении **фамилии**
    (первое слово) и согласованном **имени** (второе и далее слово / «Г.» / «Г.К.»).
    """

    a = normalize_fio(sup_fio_sheet)
    b = normalize_fio(cell_supervisor)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    wa = [w for w in a.split() if w]
    wb = [w for w in b.split() if w]
    if not wa or not wb:
        return False
    if wa[0] != wb[0]:
        return False
    rest_a = " ".join(wa[1:])
    rest_b = " ".join(wb[1:])
    return _supervisor_name_rest_compatible(rest_a, rest_b)


def get_supervisor_fio_for_telegram_id(config: BotConfig, telegram_id: str) -> str:
    """ФИО научрука по telegram_id из листа «научрук»."""

    if not telegram_id or not str(telegram_id).strip():
        return ""
    spreadsheet = get_spreadsheet(config)
    ws = get_optional_worksheet(spreadsheet, SUPERVISORS_WORKSHEET_NAME)
    if ws is None:
        return ""
    row_no = find_row_by_telegram_id(ws, telegram_id)
    if row_no is None:
        return ""
    return fio_text_from_worksheet_row(ws, row_no)


def sync_magistrants_registration_status(config: BotConfig) -> None:
    """Нормализует телефоны (+7…), колонку «Регистрация»: зарегистрирован / нет.

    Если в первой строке листа есть колонка ``telegram_id`` (или синоним из
    ``_MAGISTRANTS_HEADER_TELEGRAM_ID``), подставляет туда ``telegram_id`` из
    листа «Регистрация» для совпавших пар (ФИО + телефон); для несовпавших
    строк очищает ячейку.
    """

    title = (config.magistrants_worksheet_name or "").strip()
    if not title:
        return
    spreadsheet = get_spreadsheet(config)
    mag_ws = get_optional_worksheet(spreadsheet, title)
    if mag_ws is None:
        logger.warning("Лист «%s» не найден — пропуск синхронизации магистрантов", title)
        return
    reg_ws = spreadsheet.worksheet(config.worksheet_name)
    header = _header_row(mag_ws)
    colmap = magistrants_sheet_column_indices(header)
    if colmap is None:
        logger.warning(
            "Лист «%s»: в первой строке нет колонок ФИО / Телефон / Регистрация.",
            title,
        )
        return
    reg_by_key = registration_students_by_fio_phone(reg_ws)
    reg_keys = set(reg_by_key.keys())
    all_rows = mag_ws.get_all_values()
    if len(all_rows) < 2:
        return
    fio_i = colmap["fio"]
    phone_i = colmap["phone"]
    reg_i = colmap["registration"]
    tg_i = colmap.get("telegram_id")
    values_phone: list[list[str]] = []
    values_reg: list[list[str]] = []
    values_tg: list[list[str]] = []
    for row in all_rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            values_phone.append([""])
            values_reg.append([""])
            if tg_i is not None:
                values_tg.append([""])
            continue
        width = max(len(header), len(row), fio_i + 1, phone_i + 1, reg_i + 1)
        if tg_i is not None:
            width = max(width, tg_i + 1)
        padded = list(row) + [""] * (width - len(row))
        raw_phone = str(padded[phone_i] or "").strip()
        pk = normalize_phone_ru_kz(raw_phone)
        phone_display = pk if pk else raw_phone
        fk = normalize_fio(str(padded[fio_i] or ""))
        reg_val = (
            MAGISTRANTS_REGISTERED_LABEL
            if fk and pk and (fk, pk) in reg_keys
            else MAGISTRANTS_NOT_REGISTERED_LABEL
        )
        values_phone.append([phone_display])
        values_reg.append([reg_val])
        if tg_i is not None:
            tg_val = ""
            if fk and pk and (fk, pk) in reg_by_key:
                tg_val = (reg_by_key[(fk, pk)].telegram_id or "").strip()
            values_tg.append([tg_val])
    start = 2
    end = start + len(values_phone) - 1
    letter_p = _column_letter(phone_i)
    letter_r = _column_letter(reg_i)
    ranges_batch: list[dict] = [
        {"range": f"{letter_p}{start}:{letter_p}{end}", "values": values_phone},
        {"range": f"{letter_r}{start}:{letter_r}{end}", "values": values_reg},
    ]
    if tg_i is not None and values_tg:
        letter_t = _column_letter(tg_i)
        ranges_batch.append(
            {"range": f"{letter_t}{start}:{letter_t}{end}", "values": values_tg}
        )
    _safe_batch_update_values(mag_ws, ranges_batch)


def _bool_cell(value: str) -> bool:
    normalized = _normalize_header(value)
    return normalized in {"yes", "y", "true", "1", "active", "да"}


def _is_telegram_id_active_in_worksheet(
    worksheet: gspread.Worksheet, telegram_id: str
) -> bool:
    """Совпадение ``telegram_id`` в строке; если есть колонка ``active`` — она должна быть «истиной»."""

    if not telegram_id or not str(telegram_id).strip():
        return False
    header = _header_row(worksheet)
    if not header:
        return False

    field_map = _field_to_column_map_from_header(header)
    telegram_col = field_map.get("telegram_id")
    active_col = field_map.get("active")
    normalized_to_index = {
        _normalize_header(name): idx
        for idx, name in enumerate(header)
        if _normalize_header(name)
    }
    if telegram_col is None:
        telegram_col = normalized_to_index.get("telegram_id")
    if active_col is None:
        active_col = normalized_to_index.get("active")
    if telegram_col is None:
        return False

    needle = str(telegram_id).strip()
    max_rows = max(
        (len(worksheet.col_values(idx + 1)) for idx in range(len(header))), default=0
    )
    for row_number in range(2, max_rows + 1):
        row = worksheet.row_values(row_number)
        if telegram_col >= len(row):
            continue
        if str(row[telegram_col]).strip() != needle:
            continue
        if active_col is None or active_col >= len(row):
            return True
        return _bool_cell(row[active_col])
    return False


def is_admin_telegram_id(config: BotConfig, telegram_id: str) -> bool:
    """Проверяет, есть ли Telegram ID в листе `Администраторы`."""

    if not telegram_id or not str(telegram_id).strip():
        return False
    spreadsheet = get_spreadsheet(config)
    ws = get_optional_worksheet(spreadsheet, ADMINS_WORKSHEET_NAME)
    if ws is None:
        return False
    return _is_telegram_id_active_in_worksheet(ws, telegram_id)


def is_supervisor_telegram_id(config: BotConfig, telegram_id: str) -> bool:
    """Проверяет, есть ли Telegram ID в листе «научрук» (``SUPERVISORS_WORKSHEET_NAME``)."""

    if not telegram_id or not str(telegram_id).strip():
        return False
    spreadsheet = get_spreadsheet(config)
    ws = get_optional_worksheet(spreadsheet, SUPERVISORS_WORKSHEET_NAME)
    if ws is None:
        return False
    return _is_telegram_id_active_in_worksheet(ws, telegram_id)


def get_telegram_id_at_row(worksheet: gspread.Worksheet, row_number: int) -> str:
    """``telegram_id`` в строке (по маппингу заголовка) либо пустая строка."""

    field_map = _field_to_column_map(worksheet)
    col = field_map.get("telegram_id")
    if col is None:
        return ""
    row = worksheet.row_values(row_number)
    if col >= len(row):
        return ""
    return str(row[col] or "").strip()


def fio_text_from_worksheet_row(worksheet: gspread.Worksheet, row_number: int) -> str:
    """ФИО из строки по колонке ``fio`` (как в ``find_rows_by_fio``)."""

    field_map = _field_to_column_map(worksheet)
    col = field_map.get("fio")
    if col is None:
        return ""
    row = worksheet.row_values(row_number)
    if col >= len(row):
        return ""
    return str(row[col] or "").strip()


def phone_text_from_worksheet_row(worksheet: gspread.Worksheet, row_number: int) -> str:
    """Телефон из строки по колонке ``phone`` (алиасы как в ``_HEADER_ALIASES``)."""

    field_map = _field_to_column_map(worksheet)
    col = field_map.get("phone")
    if col is None:
        return ""
    row = worksheet.row_values(row_number)
    if col >= len(row):
        return ""
    return str(row[col] or "").strip()


def normalize_worksheet_phones_ru_kz(worksheet: gspread.Worksheet) -> dict[str, object]:
    """Переписывает колонку ``phone`` в канонический вид ``+7XXXXXXXXXX``, где возможно.

    Если ``normalize_phone_ru_kz`` возвращает пустую строку для непустого сырья,
    сохраняется исходный текст ячейки (кроме строк, признанных полностью пустыми).
    Обновляет только колонку телефона через один ``batch_update``.

    Возвращает метаданные: ``skipped`` при отсутствии распознанной колонки телефона.
    """

    sheet_title = getattr(worksheet, "title", "")
    header = _header_row(worksheet)
    field_map = _field_to_column_map(worksheet)
    phone_i = field_map.get("phone")
    if phone_i is None:
        logger.warning(
            "Лист «%s»: колонка телефона не найдена по заголовку — пропуск нормализации.",
            sheet_title or "?",
        )
        return {"sheet": sheet_title, "skipped": True, "reason": "no_phone_column"}

    all_rows = worksheet.get_all_values()
    if len(all_rows) < 2:
        return {"sheet": sheet_title, "skipped": False, "data_rows": 0, "cells_changed": 0}

    values_out: list[list[str]] = []
    cells_changed = 0

    for row in all_rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            values_out.append([""])
            continue

        width = max(len(header), len(row), phone_i + 1)
        padded = list(row) + [""] * (width - len(row))
        old_display = str(padded[phone_i] if phone_i < len(padded) else "").strip()
        new_val = normalize_phone_ru_kz(old_display) or old_display
        if new_val != old_display:
            cells_changed += 1
        values_out.append([new_val])

    start = 2
    end = start + len(values_out) - 1
    letter = _column_letter(phone_i)
    _safe_batch_update_values(
        worksheet,
        [{"range": f"{letter}{start}:{letter}{end}", "values": values_out}],
    )
    return {
        "sheet": sheet_title,
        "skipped": False,
        "data_rows": len(values_out),
        "cells_changed": cells_changed,
    }


def normalize_phones_to_ru_kz_standard(config: BotConfig) -> dict[str, dict[str, object]]:
    """Нормализует телефоны на листе «Регистрация», «научрук» и (если задано) «Магистранты».

    Для листа магистрантов вызывает :func:`sync_magistrants_registration_status`
    (телефоны **+7** и колонка «Регистрация»).
    """

    spreadsheet = get_spreadsheet(config)
    out: dict[str, dict[str, object]] = {}

    reg_title = config.worksheet_name
    reg_ws = spreadsheet.worksheet(reg_title)
    out[reg_title] = normalize_worksheet_phones_ru_kz(reg_ws)

    sup_ws = get_optional_worksheet(spreadsheet, SUPERVISORS_WORKSHEET_NAME)
    if sup_ws is None:
        out[SUPERVISORS_WORKSHEET_NAME] = {"skipped": True, "reason": "worksheet_not_found"}
    else:
        out[SUPERVISORS_WORKSHEET_NAME] = normalize_worksheet_phones_ru_kz(sup_ws)

    mag_title = (config.magistrants_worksheet_name or "").strip()
    if mag_title:
        sync_magistrants_registration_status(config)
        out[mag_title] = {
            "synced": True,
            "via": "sync_magistrants_registration_status",
            "note": "телефоны +7, колонка «Регистрация», при наличии колонки — telegram_id",
        }
    return out


def _safe_batch_update_values(
    worksheet: gspread.Worksheet,
    batch_values: list[dict],
) -> None:
    """Применяет ``worksheet.batch_update`` в RAW-режиме с безопасным откатом.

    Нужен для пакетной записи отдельных ячеек строки без сбивания соседей:
    ``worksheet.update`` по одной ячейке прошёл бы N раз, ``batch_update`` —
    один HTTP-вызов. RAW-режим удерживается по тем же причинам, что и в
    ``_safe_update`` (защита от случайного исполнения «формул» из данных).
    """

    if not batch_values:
        return
    try:
        worksheet.batch_update(batch_values, value_input_option=_SHEETS_VALUE_INPUT_OPTION)
    except TypeError:
        worksheet.batch_update(batch_values)


_CHECK_RESULT_COLUMN_KEYS: tuple[str, ...] = (
    "report_url_valid",
    "report_url_accessible",
    "project_folder_url",
    "lkb_url",
    "dissertation_url",
    "publication_url",
    "pages_total",
    "sources_count",
    "compliance",
)
"""Колонки, которые перезаписывает каждая прогонка ``check-row --apply``.

Stage 4 (c) — re-check (handoff §8 пункт «overwrite_clean»): перед записью
свежих значений зачищаем все эти колонки и снимаем ``strikethrough``,
чтобы устаревшие отметки прошлого прогона не оставались в листе, если
магистрант поправил ссылки.
"""


def apply_row_check_updates(
    worksheet: gspread.Worksheet,
    row_number: int,
    *,
    report_url_valid: str | None = None,
    report_url_accessible: str | None = None,
    stage3_executed: bool = True,
    stage3_cells: list[Stage3CellUpdate] | None = None,
    stage4_cells: list[Stage4CellUpdate] | None = None,
    fill_status: str | None = None,
) -> None:
    """Записывает результаты прогона одной строки листа «Регистрация».

    Сначала очищает все колонки результатов проверки
    (``_CHECK_RESULT_COLUMN_KEYS``) и снимает с них ``strikethrough``,
    затем поверх записывает свежие значения этого прогона.

    Если ``stage3_executed=False`` (пайплайн остановился до Stage 3), колонки
    четырёх ссылок из ``SHEET_LINK_OVERRIDE_KEYS`` не зачищаются — чтобы
    ручной ввод администратора не затирался пустым ``stage3_cells``.

    - Колонки «Проверка ссылки» (``report_url_valid``) и «Доступ открыт»
      (``report_url_accessible``) обновляются, если соответствующее значение
      передано (``None`` — Stage 2 не выполнялся, ячейка остаётся пустой
      после очистки).
    - Stage 3 пишет значения колонок ``project_folder_url`` / ``lkb_url`` /
      ``dissertation_url`` / ``publication_url`` и формат ``textFormat``
      ``strikethrough`` (True/False) для каждой из этих ячеек.
    - Stage 4 пишет ``pages_total`` / ``sources_count`` / ``compliance``
      без strikethrough (handoff §8.3 — warning-модель). Если Stage 4 не
      выполнялся, ``stage4_cells`` пуст и колонки остаются пустыми после
      очистки.

    При первом прогоне строки очистка — no-op (колонки и так пусты).
    При re-check (handoff Stage 4 (c)) — гарантирует, что устаревшие
    значения и зачёркивания исчезают, если магистрант исправил ссылку.
    Все значения уходят одним ``worksheet.batch_update`` (RAW), форматы —
    одним ``spreadsheet.batch_update`` с ``repeatCell``.

    ``fill_status`` — опционально: код п.12 ТЗ (``OK`` / ``NEED_FIX`` и т.д.);
    не входит в clean-write Stage 2–4, перезаписывается только если передан.
    """

    stage3_cells = list(stage3_cells or [])
    stage4_cells = list(stage4_cells or [])
    field_map = _field_to_column_map(worksheet)

    # Карта значений колонка → строка. Используем dict, чтобы последняя
    # запись по тому же ключу побеждала: сначала чистим (пустая строка),
    # потом перетираем свежим значением, если оно есть в этом прогоне.
    column_values: dict[str, str] = {}
    # Карта strikethrough флагов по индексу колонки. Тоже dict — финальный
    # флаг побеждает, поэтому очистка (False) и поздняя перезапись Stage 3
    # дают консистентный repeatCell.
    column_strike: dict[int, bool] = {}

    def _set_value(field_name: str, value: str) -> int | None:
        col_idx = field_map.get(field_name)
        if col_idx is None:
            return None
        column_values[field_name] = value
        return col_idx

    def _set_strike(col_idx: int, value: bool) -> None:
        column_strike[col_idx] = bool(value)

    sheet_id = getattr(worksheet, "id", None)

    # 1) Зачистка: затираем все известные колонки проверки и снимаем
    # strikethrough. Колонки, которых нет в заголовке, тихо пропускаются.
    for key in _CHECK_RESULT_COLUMN_KEYS:
        if not stage3_executed and key in SHEET_LINK_OVERRIDE_KEYS:
            continue
        col_idx = _set_value(key, "")
        if col_idx is not None and sheet_id is not None:
            _set_strike(col_idx, False)

    # 2) Stage 2: статус валидности и доступности отчёта.
    if report_url_valid is not None:
        _set_value("report_url_valid", report_url_valid)
    if report_url_accessible is not None:
        _set_value("report_url_accessible", report_url_accessible)

    # 3) Stage 3: значения и strikethrough перезаписывают значения зачистки.
    for cell in stage3_cells:
        col_idx = _set_value(cell.column_key, cell.value)
        if col_idx is not None and sheet_id is not None:
            _set_strike(col_idx, bool(cell.strikethrough))

    # 4) Stage 4: только значения, strikethrough не трогаем (warning-модель).
    for cell in stage4_cells:
        _set_value(cell.column_key, cell.value)

    if fill_status is not None:
        _set_value("fill_status", fill_status)

    # Сборка batch — порядок диапазонов идёт по индексу колонки для
    # детерминизма (тесты рассчитывают на стабильный порядок ranges).
    ordered_keys = sorted(
        column_values.keys(),
        key=lambda k: field_map.get(k, 1_000_000),
    )
    batch_values: list[dict] = []
    for key in ordered_keys:
        col_idx = field_map.get(key)
        if col_idx is None:
            continue
        letter = _column_letter(col_idx)
        batch_values.append(
            {"range": f"{letter}{row_number}", "values": [[column_values[key]]]}
        )

    format_requests: list[dict] = []
    if sheet_id is not None:
        for col_idx in sorted(column_strike.keys()):
            format_requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {
                                    "strikethrough": column_strike[col_idx]
                                }
                            }
                        },
                        "fields": "userEnteredFormat.textFormat.strikethrough",
                    }
                }
            )

    _safe_batch_update_values(worksheet, batch_values)

    if format_requests:
        spreadsheet = getattr(worksheet, "spreadsheet", None)
        if spreadsheet is not None and hasattr(spreadsheet, "batch_update"):
            spreadsheet.batch_update({"requests": format_requests})


def write_dissertation_meta_columns(
    worksheet: gspread.Worksheet,
    row_number: int,
    *,
    title: str,
    language: str,
) -> None:
    """Пишет название и язык диссертации (колонки вне clean-write Stage 2–4)."""

    field_map = _field_to_column_map(worksheet)
    batch_values: list[dict] = []
    for key, val in (
        ("dissertation_title", title or ""),
        ("dissertation_language", language or ""),
    ):
        col_idx = field_map.get(key)
        if col_idx is None:
            continue
        letter = _column_letter(col_idx)
        batch_values.append(
            {"range": f"{letter}{row_number}", "values": [[val]]}
        )
    if batch_values:
        _safe_batch_update_values(worksheet, batch_values)


def write_dissertation_sheet_metrics(
    worksheet: gspread.Worksheet,
    row_number: int,
    *,
    pages_total: str,
    sources_count: str,
    dissertation_title: str,
    dissertation_language: str,
    compliance: str | None = None,
) -> None:
    """Пишет в строку число страниц, источников, тему, язык и (опционально) оформление.

    Не очищает остальные колонки проверки — только указанные ячейки.
    Столбцы ищутся по ``_HEADER_ALIASES`` (как в листе «Регистрация»).

    ``compliance`` — текст колонки «Соответствие оформлению» (как из
    :func:`evaluate_formatting_compliance` / Stage 4). ``None`` — колонку не
    трогать; иначе записать переданную строку (в том числе пустую).
    """

    field_map = _field_to_column_map(worksheet)
    batch_values: list[dict] = []
    items: list[tuple[str, str]] = [
        ("pages_total", pages_total or ""),
        ("sources_count", sources_count or ""),
        ("dissertation_title", dissertation_title or ""),
        ("dissertation_language", dissertation_language or ""),
    ]
    if compliance is not None:
        items.append(("compliance", compliance))
    for key, val in items:
        col_idx = field_map.get(key)
        if col_idx is None:
            continue
        letter = _column_letter(col_idx)
        batch_values.append(
            {"range": f"{letter}{row_number}", "values": [[val]]}
        )
    if batch_values:
        _safe_batch_update_values(worksheet, batch_values)


def set_row_fill_status(
    worksheet: gspread.Worksheet,
    row_number: int,
    status: str,
) -> None:
    """Пишет одну ячейку ``fill_status`` (если столбец есть в заголовке)."""

    field_map = _field_to_column_map(worksheet)
    col_idx = field_map.get("fill_status")
    if col_idx is None:
        return
    letter = _column_letter(col_idx)
    _safe_update(worksheet, f"{letter}{row_number}", [[status]])


def upsert_user(worksheet: gspread.Worksheet, user: UserForm) -> int:
    """Обновляет существующую строку по telegram_id или добавляет новую.

    Возвращает 1-based номер строки, в которой данные оказались после операции.
    """

    ensure_header(worksheet)
    header = _header_row(worksheet)

    return upsert_user_with_extras(worksheet, user, extra_values=None)


def upsert_user_with_extras(
    worksheet: gspread.Worksheet,
    user: UserForm,
    *,
    extra_values: dict[str, str] | None = None,
) -> int:
    """Как ``upsert_user``, но позволяет дописать вычисленные поля по заголовкам листа."""

    ensure_header(worksheet)
    header = _header_row(worksheet)

    existing_row = find_row_by_telegram_id(worksheet, user.telegram_id)
    if existing_row:
        built_row = _build_row_for_header(
            worksheet,
            user,
            worksheet.row_values(existing_row),
            extra_values=extra_values,
        )
        range_a1 = _range_for_row(existing_row, max(len(header), len(built_row)))
        _safe_update(worksheet, range_a1, [built_row])
        return existing_row

    target_row = _find_first_free_data_row(worksheet)
    built_row = _build_row_for_header(
        worksheet,
        user,
        worksheet.row_values(target_row),
        extra_values=extra_values,
    )
    range_a1 = _range_for_row(target_row, max(len(header), len(built_row)))
    _safe_update(worksheet, range_a1, [built_row])
    return target_row
