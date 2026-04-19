"""Доступ к Google Sheets через Service Account.

Содержит I/O-операции и не зависит от Telegram. Все функции принимают
worksheet-объект (gspread.Worksheet) — это упрощает тестирование на фейке.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Iterable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import SHEET_HEADER, UserForm

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
