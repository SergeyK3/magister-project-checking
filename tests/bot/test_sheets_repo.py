"""Юнит-тесты sheets_repo с in-memory worksheet (без сети)."""

from __future__ import annotations

import unittest
from typing import List
from unittest.mock import MagicMock, patch

import gspread

from magister_checking.bot.models import SHEET_HEADER, UserForm
from magister_checking.bot.sheets_repo import (
    ADMINS_WORKSHEET_NAME,
    _column_letter,
    attach_telegram_to_row,
    build_dashboard_rows,
    DASHBOARD_WORKSHEET_NAME,
    ensure_header,
    find_row_by_telegram_id,
    find_rows_by_fio,
    get_or_create_worksheet,
    is_admin_telegram_id,
    load_user,
    save_user_to_row_with_extras,
    normalize_fio,
    sync_registration_dashboard,
    upsert_user,
    upsert_user_with_extras,
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


class FakeSpreadsheet:
    def __init__(self, worksheets: dict[str, FakeWorksheet] | None = None) -> None:
        self.worksheets = dict(worksheets or {})

    def worksheet(self, title: str) -> FakeWorksheet:
        if title not in self.worksheets:
            raise gspread.WorksheetNotFound(title)
        return self.worksheets[title]

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        ws = FakeWorksheet([[""] * cols for _ in range(rows)])
        self.worksheets[title] = ws
        return ws


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
        self.assertEqual(ws.update_calls[0][0], "A1:O1")

    def test_keeps_existing_non_empty_header(self) -> None:
        ws = FakeWorksheet([["wrong", "header"]])
        ensure_header(ws)
        self.assertEqual(ws.rows[0], ["wrong", "header"])
        self.assertEqual(ws.update_calls, [])

    def test_noop_when_correct(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ensure_header(ws)
        self.assertEqual(ws.update_calls, [])


class FindRowByTelegramIdTests(unittest.TestCase):
    def test_returns_none_for_empty_id(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["123"] + [""] * 14])
        self.assertIsNone(find_row_by_telegram_id(ws, ""))

    def test_finds_existing_row(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111"] + [""] * 14,
                ["222"] + [""] * 14,
            ]
        )
        self.assertEqual(find_row_by_telegram_id(ws, "222"), 3)

    def test_returns_none_when_not_found(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["111"] + [""] * 14])
        self.assertIsNone(find_row_by_telegram_id(ws, "999"))

    def test_skips_header_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        self.assertIsNone(find_row_by_telegram_id(ws, "telegram_id"))

    def test_returns_none_when_custom_header_has_no_telegram_id_column(self) -> None:
        ws = FakeWorksheet(
            [
                ["Отметка времени", "ФИО", "Группа"],
                ["14.04.2026 10:00", "Иванов И.И.", "МЭП1"],
            ]
        )
        self.assertIsNone(find_row_by_telegram_id(ws, "111"))


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

    def test_insert_new_user_uses_first_free_row_gap(self) -> None:
        rows = [
            list(SHEET_HEADER),
            ["111"] + [""] * 14,
            ["222"] + [""] * 14,
            ["333"] + [""] * 14,
            ["444"] + [""] * 14,
            ["555"] + [""] * 14,
            ["666"] + [""] * 14,
            ["777"] + [""] * 14,
            ["888"] + [""] * 14,
            [""] * 15,
            ["999"] + [""] * 14,
        ]
        ws = FakeWorksheet(rows)

        row = upsert_user(ws, self._form("1010", "Новый"))

        self.assertEqual(row, 10)
        loaded = load_user(ws, 10)
        self.assertEqual(loaded.telegram_id, "1010")
        self.assertEqual(loaded.fio, "Новый")
        self.assertEqual(ws.rows[10][0], "999")

    def test_insert_new_user_with_custom_header_uses_first_free_row_gap(self) -> None:
        rows = [
            [
                "Отметка времени",
                "ФИО",
                "Группа",
                "Место работы",
                "Должность",
                "Сотовый контактный телефон",
                "Научный руководитель",
                "Ссылка на промежуточный отчет",
                "Проверка ссылки",
                "Доступ открыт",
            ],
            ["14.04.2026 10:00", "Иванов И.И.", "МЭП1", "", "", "", "", "", "", ""],
            ["14.04.2026 10:01", "Петров П.П.", "МЭП2", "", "", "", "", "", "", ""],
            [""] * 10,
            ["14.04.2026 10:02", "Сидоров С.С.", "МЭП3", "", "", "", "", "", "", ""],
        ]
        ws = FakeWorksheet(rows)

        form = UserForm(
            telegram_id="444",
            fio="Новый",
            group_name="МЭП4",
            workplace="АО НЦ",
            position="аналитик",
            phone="+77001234567",
            supervisor="Руководитель Р.Р.",
            report_url="https://docs.google.com/document/d/abc/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
        )
        row = upsert_user(ws, form)

        self.assertEqual(row, 4)
        loaded = load_user(ws, 4)
        self.assertEqual(loaded.fio, "Новый")
        self.assertEqual(loaded.group_name, "МЭП4")
        self.assertEqual(ws.rows[0][0], "Отметка времени")
        self.assertEqual(ws.rows[3][0], "")
        self.assertEqual(ws.rows[3][1], "Новый")
        self.assertEqual(ws.rows[3][2], "МЭП4")
        self.assertEqual(ws.rows[3][3], "АО НЦ")
        self.assertEqual(ws.rows[3][4], "аналитик")
        self.assertEqual(ws.rows[3][5], "+77001234567")
        self.assertEqual(ws.rows[3][6], "Руководитель Р.Р.")
        self.assertEqual(ws.rows[3][7], "https://docs.google.com/document/d/abc/edit")
        self.assertEqual(ws.rows[3][8], "yes")
        self.assertEqual(ws.rows[3][9], "yes")

    def test_insert_new_user_with_report_extras_maps_analytic_columns(self) -> None:
        rows = [
            [
                "Отметка времени",
                "ФИО",
                "Группа",
                "Ссылка на промежуточный отчет",
                "Проверка ссылки",
                "Доступ открыт",
                "Ссылка на папку 1",
                "Ссылка на ЛКБ",
                "Ссылка на диссер",
                "Число страниц",
                "Число источников",
                "Соответствие",
            ]
        ]
        ws = FakeWorksheet(rows)
        form = UserForm(
            telegram_id="111",
            fio="Иванов И.И.",
            group_name="МЭП1",
            report_url="https://docs.google.com/document/d/report/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
        )

        row = upsert_user_with_extras(
            ws,
            form,
            extra_values={
                "project_folder_url": "https://drive.google.com/drive/folders/xyz",
                "lkb_url": "https://drive.google.com/file/d/lkb/view",
                "dissertation_url": "https://docs.google.com/document/d/diss/edit",
                "pages_total": "87",
                "sources_count": "13",
                "compliance": "yes",
            },
        )

        self.assertEqual(row, 2)
        self.assertEqual(ws.rows[1][1], "Иванов И.И.")
        self.assertEqual(ws.rows[1][2], "МЭП1")
        self.assertEqual(ws.rows[1][6], "https://drive.google.com/drive/folders/xyz")
        self.assertEqual(ws.rows[1][7], "https://drive.google.com/file/d/lkb/view")
        self.assertEqual(ws.rows[1][8], "https://docs.google.com/document/d/diss/edit")
        self.assertEqual(ws.rows[1][9], "87")
        self.assertEqual(ws.rows[1][10], "13")
        self.assertEqual(ws.rows[1][11], "yes")

    def test_update_existing_user_with_report_extras_refreshes_analytic_columns(self) -> None:
        rows = [
            [
                "telegram_id",
                "ФИО",
                "Ссылка на промежуточный отчет",
                "Проверка ссылки",
                "Доступ открыт",
                "Ссылка на диссер",
                "Число страниц",
                "Число источников",
                "Соответствие оформлению",
            ],
            [
                "111",
                "Иванов И.И.",
                "https://docs.google.com/document/d/old-report/edit",
                "yes",
                "yes",
                "https://docs.google.com/document/d/old-diss/edit",
                "70",
                "10",
                "Не соответствует",
            ],
        ]
        ws = FakeWorksheet(rows)
        form = UserForm(
            telegram_id="111",
            fio="Иванов И.И.",
            report_url="https://docs.google.com/document/d/new-report/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
        )

        row = upsert_user_with_extras(
            ws,
            form,
            extra_values={
                "dissertation_url": "https://docs.google.com/document/d/new-diss/edit",
                "pages_total": "87",
                "sources_count": "13",
                "compliance": "Соответствует",
            },
        )

        self.assertEqual(row, 2)
        self.assertEqual(ws.rows[1][2], "https://docs.google.com/document/d/new-report/edit")
        self.assertEqual(ws.rows[1][5], "https://docs.google.com/document/d/new-diss/edit")
        self.assertEqual(ws.rows[1][6], "87")
        self.assertEqual(ws.rows[1][7], "13")
        self.assertEqual(ws.rows[1][8], "Соответствует")


class LoadUserTests(unittest.TestCase):
    def test_pads_short_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["123", "user"]])
        user = load_user(ws, 2)
        self.assertEqual(user.telegram_id, "123")
        self.assertEqual(user.telegram_username, "user")
        self.assertEqual(user.fio, "")
        self.assertEqual(user.last_action, "")


class FioBindingTests(unittest.TestCase):
    def _row(self, *, fio: str, telegram_id: str = "") -> List[str]:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = telegram_id
        row[SHEET_HEADER.index("fio")] = fio
        return row

    def test_normalize_fio_collapses_spaces_and_yo(self) -> None:
        self.assertEqual(normalize_fio("  Иванов   Иван  Иванович "), "иванов иван иванович")
        self.assertEqual(normalize_fio("Алёшин"), "алешин")
        self.assertEqual(normalize_fio(None), "")

    def test_find_rows_by_fio_matches_normalized(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                self._row(fio="Иванов И.И."),
                self._row(fio="иванов  и.и."),
                self._row(fio="Петров П.П."),
            ]
        )

        self.assertEqual(find_rows_by_fio(ws, "Иванов И.И."), [2, 3])
        self.assertEqual(find_rows_by_fio(ws, "Петров П.П."), [4])
        self.assertEqual(find_rows_by_fio(ws, ""), [])

    def test_find_rows_by_fio_matches_custom_russian_header(self) -> None:
        ws = FakeWorksheet(
            [
                ["Отметка времени", "ФИО", "Группа"],
                ["14.04.2026 10:00", "Иванов И.И.", "МЭП1"],
                ["14.04.2026 10:01", "иванов  и.и.", "МЭП2"],
            ]
        )

        self.assertEqual(find_rows_by_fio(ws, "Иванов И.И."), [2, 3])

    def test_attach_telegram_writes_first_four_columns(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                self._row(fio="Иванов И.И."),
            ]
        )

        attach_telegram_to_row(
            ws,
            2,
            telegram_id="111",
            telegram_username="ivanov",
            telegram_first_name="Иван",
            telegram_last_name="Иванов",
        )

        self.assertEqual(ws.rows[1][0], "111")
        self.assertEqual(ws.rows[1][1], "ivanov")
        self.assertEqual(ws.rows[1][2], "Иван")
        self.assertEqual(ws.rows[1][3], "Иванов")
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("fio")], "Иванов И.И.")


class DashboardTests(unittest.TestCase):
    def test_build_dashboard_rows_counts_registration_metrics(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "", "", "", "Иванов", "М1", "ООО", "инж", "+7", "Петров", "https://x", "yes", "yes", "REGISTERED", "confirmed_save"],
                ["222", "", "", "", "", "", "", "", "", "", "", "", "", "NEW", "start_new"],
                ["", "", "", "", "Сидоров", "М2", "АО", "", "", "", "https://y", "yes", "no", "PARTIAL", "ask_position"],
            ]
        )

        rows = build_dashboard_rows(ws)

        self.assertEqual(rows[0], ["Показатель", "Значение"])
        self.assertEqual(rows[2], ["Всего регистраций", "3"])
        self.assertEqual(rows[3], ["Полностью зарегистрированы", "1"])
        self.assertEqual(rows[4], ["Частично заполнены", "1"])
        self.assertEqual(rows[5], ["Новые / пустые", "1"])
        self.assertEqual(rows[6], ["Привязаны к Telegram", "2"])
        self.assertEqual(rows[7], ["Есть ссылка на отчет", "2"])
        self.assertEqual(rows[8], ["Доступ открыт", "1"])
        self.assertEqual(rows[9], ["Доступ не открыт", "1"])

    def test_get_or_create_worksheet_creates_missing_dashboard_sheet(self) -> None:
        spreadsheet = FakeSpreadsheet()

        with self.assertRaises(gspread.WorksheetNotFound):
            spreadsheet.worksheet(DASHBOARD_WORKSHEET_NAME)

        ws = get_or_create_worksheet(spreadsheet, DASHBOARD_WORKSHEET_NAME, rows=20, cols=2)

        self.assertIs(spreadsheet.worksheet(DASHBOARD_WORKSHEET_NAME), ws)
        self.assertEqual(len(ws.rows), 20)
        self.assertEqual(len(ws.rows[0]), 2)

    def test_sync_registration_dashboard_writes_dashboard_sheet(self) -> None:
        registration = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "", "", "", "Иванов", "М1", "ООО", "инж", "+7", "Петров", "https://x", "yes", "yes", "REGISTERED", "confirmed_save"],
            ]
        )
        dashboard = FakeWorksheet()
        spreadsheet = FakeSpreadsheet({"Регистрация": registration, DASHBOARD_WORKSHEET_NAME: dashboard})

        with unittest.mock.patch(
            "magister_checking.bot.sheets_repo.get_spreadsheet",
            return_value=spreadsheet,
        ):
            sync_registration_dashboard(unittest.mock.MagicMock(worksheet_name="Регистрация"))

        self.assertEqual(dashboard.rows[0], ["Показатель", "Значение"])
        self.assertEqual(dashboard.rows[2], ["Всего регистраций", "1"])
        self.assertEqual(dashboard.rows[3], ["Полностью зарегистрированы", "1"])


class ExistingRowSaveTests(unittest.TestCase):
    def test_save_user_to_row_with_extras_updates_existing_row(self) -> None:
        ws = FakeWorksheet(
            [
                ["telegram_id", "ФИО", "Число страниц", "Соответствие оформлению"],
                ["111", "Иванов И.И.", "70", "Не соответствует"],
            ]
        )
        user = UserForm(telegram_id="111", fio="Иванов И.И.")

        row = save_user_to_row_with_extras(
            ws,
            2,
            user,
            extra_values={"pages_total": "87", "compliance": "Соответствует"},
        )

        self.assertEqual(row, 2)
        self.assertEqual(ws.rows[1][2], "87")
        self.assertEqual(ws.rows[1][3], "Соответствует")


class AdminSheetTests(unittest.TestCase):
    def test_is_admin_telegram_id_checks_admin_sheet(self) -> None:
        registration = FakeWorksheet([list(SHEET_HEADER)])
        admins = FakeWorksheet(
            [
                ["telegram_id", "username", "fio", "role", "active"],
                ["300398364", "kim", "Ким СВ", "admin", "yes"],
                ["999", "old", "Старый", "admin", "no"],
            ]
        )
        spreadsheet = FakeSpreadsheet(
            {
                "Регистрация": registration,
                ADMINS_WORKSHEET_NAME: admins,
            }
        )

        with patch(
            "magister_checking.bot.sheets_repo.get_spreadsheet",
            return_value=spreadsheet,
        ):
            cfg = MagicMock()
            self.assertTrue(is_admin_telegram_id(cfg, "300398364"))
            self.assertFalse(is_admin_telegram_id(cfg, "999"))
            self.assertFalse(is_admin_telegram_id(cfg, "123"))


if __name__ == "__main__":
    unittest.main()
