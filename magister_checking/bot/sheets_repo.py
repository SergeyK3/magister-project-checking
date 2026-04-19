"""Доступ к Google Sheets через Service Account.

Содержит I/O-операции и не зависит от Telegram. Все функции принимают
worksheet-объект (gspread.Worksheet) — это упрощает тестирование на фейке.
"""

from __future__ import annotations

import re
from dataclasses import fields
from typing import Iterable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import SHEET_HEADER, UserForm

_FIO_COLUMN_INDEX = SHEET_HEADER.index("fio")
_TELEGRAM_ID_COLUMN_INDEX = SHEET_HEADER.index("telegram_id")
_TELEGRAM_USERNAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_username")
_TELEGRAM_FIRST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_first_name")
_TELEGRAM_LAST_NAME_COLUMN_INDEX = SHEET_HEADER.index("telegram_last_name")
_WHITESPACE_RE = re.compile(r"\s+")

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


def get_gspread_client(config: BotConfig) -> gspread.Client:
    """Создаёт авторизованного gspread-клиента из Service Account JSON."""

    creds = Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )
    return gspread.authorize(creds)


def get_worksheet(config: BotConfig) -> gspread.Worksheet:
    """Открывает рабочий лист, имя которого задано в конфиге."""

    client = get_gspread_client(config)
    spreadsheet = client.open_by_key(config.spreadsheet_id)
    return spreadsheet.worksheet(config.worksheet_name)


def ensure_header(worksheet: gspread.Worksheet) -> None:
    """Гарантирует, что первая строка содержит ожидаемую шапку."""

    current = worksheet.row_values(1)
    if current == SHEET_HEADER:
        return
    range_a1 = f"A1:{_LAST_COLUMN_LETTER}1"
    worksheet.update(range_a1, [SHEET_HEADER])


def find_row_by_telegram_id(
    worksheet: gspread.Worksheet, telegram_id: str
) -> Optional[int]:
    """Возвращает 1-based номер строки магистранта по Telegram ID или None."""

    if telegram_id is None or str(telegram_id).strip() == "":
        return None
    needle = str(telegram_id).strip()
    values = worksheet.col_values(1)
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
    column_values = worksheet.col_values(_FIO_COLUMN_INDEX + 1)
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

    range_a1 = (
        f"A{row_number}:"
        f"{_column_letter(_TELEGRAM_LAST_NAME_COLUMN_INDEX)}{row_number}"
    )
    worksheet.update(
        range_a1,
        [
            [
                str(telegram_id or ""),
                str(telegram_username or ""),
                str(telegram_first_name or ""),
                str(telegram_last_name or ""),
            ]
        ],
    )


def _user_to_row(user: UserForm) -> List[str]:
    return [str(getattr(user, name) or "") for name in SHEET_HEADER]


def _row_to_user(row: Iterable[str]) -> UserForm:
    padded = list(row) + [""] * (len(SHEET_HEADER) - len(list(row)))
    kwargs = {name: (padded[idx] or "") for idx, name in enumerate(SHEET_HEADER)}
    declared = {f.name for f in fields(UserForm)}
    cleaned = {k: v for k, v in kwargs.items() if k in declared}
    return UserForm(**cleaned)


def load_user(worksheet: gspread.Worksheet, row_number: int) -> UserForm:
    """Загружает анкету магистранта из указанной строки таблицы."""

    row = worksheet.row_values(row_number)
    return _row_to_user(row)


def upsert_user(worksheet: gspread.Worksheet, user: UserForm) -> int:
    """Обновляет существующую строку по telegram_id или добавляет новую.

    Возвращает 1-based номер строки, в которой данные оказались после операции.
    """

    ensure_header(worksheet)
    row_data = [_user_to_row(user)]

    existing_row = find_row_by_telegram_id(worksheet, user.telegram_id)
    if existing_row:
        range_a1 = f"A{existing_row}:{_LAST_COLUMN_LETTER}{existing_row}"
        worksheet.update(range_a1, row_data)
        return existing_row

    worksheet.append_rows(row_data, value_input_option="USER_ENTERED")
    return len(worksheet.col_values(1))
