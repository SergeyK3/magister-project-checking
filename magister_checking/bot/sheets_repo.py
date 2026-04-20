"""Доступ к Google Sheets через Service Account.

Содержит I/O-операции и не зависит от Telegram. Все функции принимают
worksheet-объект (gspread.Worksheet) — это упрощает тестирование на фейке.
"""

from __future__ import annotations

from datetime import datetime
import re
from dataclasses import fields
from typing import Iterable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import SHEET_HEADER, UserForm, compute_fill_status

_FIO_COLUMN_INDEX = SHEET_HEADER.index("fio")
_TELEGRAM_ID_COLUMN_INDEX = SHEET_HEADER.index("telegram_id")
_TELEGRAM_USERNAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_username")
_TELEGRAM_FIRST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_first_name")
_TELEGRAM_LAST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_last_name")
_WHITESPACE_RE = re.compile(r"\s+")
DASHBOARD_WORKSHEET_NAME = "Dashboard"
ADMINS_WORKSHEET_NAME = "Администраторы"
_DASHBOARD_RANGE = "A1:B12"

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "отметка времени", "дата регистрации", "дата и время"),
    "telegram_id": ("telegram_id", "telegram id", "id telegram"),
    "telegram_username": ("telegram_username", "telegram username", "username telegram"),
    "telegram_first_name": ("telegram_first_name", "telegram first name"),
    "telegram_last_name": ("telegram_last_name", "telegram last name"),
    "fio": ("fio", "фио"),
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
        "ссылка на папку",
        "ссылка на папку 1",
        "ссылка на папку 1с",
        "ссылка на папку магистерский проект",
    ),
    "lkb_url": ("lkb_url", "ссылка на лкб"),
    "dissertation_url": ("dissertation_url", "ссылка на диссер", "ссылка на диссертацию"),
    "pages_total": ("pages_total", "число страниц", "всего страниц"),
    "sources_count": ("sources_count", "число источников", "источников"),
    "compliance": ("compliance", "соответствие", "соответствие оформлению"),
    "fill_status": ("fill_status", "статус заполнения"),
    "last_action": ("last_action", "последнее действие"),
}

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
    worksheet.update(range_a1, [SHEET_HEADER])


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
    worksheet.update(_range_for_row(row_number, len(row)), [row])


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
            kwargs[field_name] = padded[idx] or ""
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
    worksheet.update(range_a1, [built_row])
    return row_number


def _iter_users(worksheet: gspread.Worksheet) -> List[UserForm]:
    header = _header_row(worksheet)
    if not header:
        return []

    max_rows = max((len(worksheet.col_values(idx + 1)) for idx in range(len(header))), default=0)
    users: List[UserForm] = []
    for row_number in range(2, max_rows + 1):
        row = worksheet.row_values(row_number)
        if not any(str(value).strip() for value in row):
            continue
        users.append(_row_to_user(header, row))
    return users


def build_dashboard_rows(registration_worksheet: gspread.Worksheet) -> List[List[str]]:
    """Строит простую сводку по регистрациям для листа Dashboard."""

    users = _iter_users(registration_worksheet)
    statuses = [compute_fill_status(user).value for user in users]
    total = len(users)
    rows = [
        ["Показатель", "Значение"],
        ["Обновлено", datetime.now().strftime("%d.%m.%Y %H:%M:%S")],
        ["Всего регистраций", str(total)],
        ["Полностью зарегистрированы", str(sum(status == "REGISTERED" for status in statuses))],
        ["Частично заполнены", str(sum(status == "PARTIAL" for status in statuses))],
        ["Новые / пустые", str(sum(status == "NEW" for status in statuses))],
        ["Привязаны к Telegram", str(sum(bool((user.telegram_id or "").strip()) for user in users))],
        ["Есть ссылка на отчет", str(sum(bool((user.report_url or "").strip()) for user in users))],
        ["Доступ открыт", str(sum((user.report_url_accessible or "").strip().lower() == "yes" for user in users))],
        ["Доступ не открыт", str(sum((user.report_url_accessible or "").strip().lower() == "no" for user in users))],
    ]
    while len(rows) < 12:
        rows.append(["", ""])
    return rows


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
    dashboard_worksheet.update(_DASHBOARD_RANGE, dashboard_rows)


def _bool_cell(value: str) -> bool:
    normalized = _normalize_header(value)
    return normalized in {"yes", "y", "true", "1", "active", "да"}


def is_admin_telegram_id(config: BotConfig, telegram_id: str) -> bool:
    """Проверяет, есть ли Telegram ID в листе `Администраторы`."""

    if not telegram_id or not str(telegram_id).strip():
        return False

    spreadsheet = get_spreadsheet(config)
    admins_worksheet = get_optional_worksheet(spreadsheet, ADMINS_WORKSHEET_NAME)
    if admins_worksheet is None:
        return False

    header = _header_row(admins_worksheet)
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
    max_rows = max((len(admins_worksheet.col_values(idx + 1)) for idx in range(len(header))), default=0)
    for row_number in range(2, max_rows + 1):
        row = admins_worksheet.row_values(row_number)
        if telegram_col >= len(row):
            continue
        if str(row[telegram_col]).strip() != needle:
            continue
        if active_col is None or active_col >= len(row):
            return True
        return _bool_cell(row[active_col])
    return False


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
        worksheet.update(range_a1, [built_row])
        return existing_row

    target_row = _find_first_free_data_row(worksheet)
    built_row = _build_row_for_header(
        worksheet,
        user,
        worksheet.row_values(target_row),
        extra_values=extra_values,
    )
    range_a1 = _range_for_row(target_row, max(len(header), len(built_row)))
    worksheet.update(range_a1, [built_row])
    return target_row
