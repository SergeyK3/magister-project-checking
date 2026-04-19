"""Юнит-тесты sheets_repo с in-memory worksheet (без сети)."""

from __future__ import annotations

import unittest
from typing import List

from magister_checking.bot.models import SHEET_HEADER, UserForm
from magister_checking.bot.sheets_repo import (
    _column_letter,
    ensure_header,
    find_row_by_telegram_id,
    load_user,
    upsert_user,
)


class FakeWorksheet:
    """Минимальная замена gspread.Worksheet для тестов."""

    def __init__(self, rows: List[List[str]] | None = None) -> None:
        self.rows: List[List[str]] = [list(r) for r in (rows or [])]
        self.update_calls: List[tuple] = []

    def _ensure_width(self, width: int) -> None:
        for row in self.rows:
            while len(row) < width:
                row.append("")

    def row_values(self, row_number: int) -> List[str]:
        if row_number < 1 or row_number > len(self.rows):
            return []
        return list(self.rows[row_number - 1])

    def col_values(self, col_number: int) -> List[str]:
        result: List[str] = []
        for row in self.rows:
            if col_number - 1 < len(row):
                result.append(row[col_number - 1])
            else:
                result.append("")
        while result and result[-1] == "":
            result.pop()
        return result

    def update(self, range_a1: str, values: List[List[str]]) -> None:
        self.update_calls.append((range_a1, values))
        cell, _, end = range_a1.partition(":")
        col_letters = "".join(c for c in cell if c.isalpha())
        row_str = "".join(c for c in cell if c.isdigit())
        start_row = int(row_str)
        col_index = 0
        for ch in col_letters:
            col_index = col_index * 26 + (ord(ch.upper()) - ord("A") + 1)
        col_index -= 1

        for offset, value_row in enumerate(values):
            target_row_idx = start_row - 1 + offset
            while len(self.rows) <= target_row_idx:
                self.rows.append([""] * (col_index + len(value_row)))
            row = self.rows[target_row_idx]
            for j, value in enumerate(value_row):
                while len(row) <= col_index + j:
                    row.append("")
                row[col_index + j] = value

    def append_rows(self, values: List[List[str]], value_input_option: str = "RAW") -> None:
        for value_row in values:
            self.rows.append(list(value_row))


class ColumnLetterTests(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(_column_letter(0), "A")
        self.assertEqual(_column_letter(15), "P")
        self.assertEqual(_column_letter(25), "Z")
        self.assertEqual(_column_letter(26), "AA")


class EnsureHeaderTests(unittest.TestCase):
    def test_writes_header_when_empty(self) -> None:
        ws = FakeWorksheet()
        ensure_header(ws)
        self.assertEqual(ws.rows[0], SHEET_HEADER)
        self.assertEqual(ws.update_calls[0][0], "A1:P1")

    def test_overwrites_wrong_header(self) -> None:
        ws = FakeWorksheet([["wrong", "header"]])
        ensure_header(ws)
        self.assertEqual(ws.rows[0], SHEET_HEADER)

    def test_noop_when_correct(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ensure_header(ws)
        self.assertEqual(ws.update_calls, [])


class FindRowByTelegramIdTests(unittest.TestCase):
    def test_returns_none_for_empty_id(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["123"] + [""] * 15])
        self.assertIsNone(find_row_by_telegram_id(ws, ""))

    def test_finds_existing_row(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111"] + [""] * 15,
                ["222"] + [""] * 15,
            ]
        )
        self.assertEqual(find_row_by_telegram_id(ws, "222"), 3)

    def test_returns_none_when_not_found(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["111"] + [""] * 15])
        self.assertIsNone(find_row_by_telegram_id(ws, "999"))

    def test_skips_header_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        self.assertIsNone(find_row_by_telegram_id(ws, "telegram_id"))


class UpsertUserTests(unittest.TestCase):
    def _form(self, tg_id: str, fio: str = "FIO") -> UserForm:
        return UserForm(telegram_id=tg_id, fio=fio)

    def test_insert_new_user_returns_correct_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        row = upsert_user(ws, self._form("111", "Иванов"))
        self.assertEqual(row, 2)
        loaded = load_user(ws, 2)
        self.assertEqual(loaded.telegram_id, "111")
        self.assertEqual(loaded.fio, "Иванов")

    def test_insert_returns_actual_row_not_row_count(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        upsert_user(ws, self._form("111"))
        row = upsert_user(ws, self._form("222"))
        self.assertEqual(row, 3)

    def test_update_existing_user(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        upsert_user(ws, self._form("111", "Старое"))
        row = upsert_user(ws, self._form("111", "Новое"))
        self.assertEqual(row, 2)
        loaded = load_user(ws, 2)
        self.assertEqual(loaded.fio, "Новое")
        self.assertEqual(len([r for r in ws.rows if r[0] == "111"]), 1)


class LoadUserTests(unittest.TestCase):
    def test_pads_short_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["123", "user"]])
        user = load_user(ws, 2)
        self.assertEqual(user.telegram_id, "123")
        self.assertEqual(user.telegram_username, "user")
        self.assertEqual(user.fio, "")
        self.assertEqual(user.last_action, "")


if __name__ == "__main__":
    unittest.main()
