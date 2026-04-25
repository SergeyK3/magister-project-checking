"""Юнит-тесты sheets_repo с in-memory worksheet (без сети)."""

from __future__ import annotations

import unittest
from typing import List
from unittest.mock import MagicMock, patch

import gspread

from magister_checking.bot.models import SHEET_HEADER, UserForm
from magister_checking.bot.row_pipeline import Stage3CellUpdate, Stage4CellUpdate
from magister_checking.bot.sheets_repo import (
    ADMINS_WORKSHEET_NAME,
    RECHECK_HISTORY_HEADER,
    RECHECK_HISTORY_WORKSHEET_NAME,
    RecheckHistoryEntry,
    _column_letter,
    append_recheck_history,
    apply_row_check_updates,
    attach_telegram_to_row,
    build_dashboard_rows,
    DASHBOARD_WORKSHEET_NAME,
    ensure_header,
    find_row_by_telegram_id,
    find_rows_by_fio,
    get_or_create_worksheet,
    is_admin_telegram_id,
    load_user,
    read_last_recheck_entry,
    save_user_to_row_with_extras,
    normalize_fio,
    sync_registration_dashboard,
    upsert_user,
    upsert_user_with_extras,
)


class FakeWorksheet:
    """Минимальная замена gspread.Worksheet для тестов."""

    def __init__(
        self,
        rows: List[List[str]] | None = None,
        *,
        sheet_id: int = 0,
        spreadsheet: "FakeSpreadsheet | None" = None,
    ) -> None:
        self.rows: List[List[str]] = [list(r) for r in (rows or [])]
        self.update_calls: List[tuple] = []
        self.batch_update_calls: List[tuple] = []
        self.append_row_calls: List[tuple] = []
        self.id = sheet_id
        self.spreadsheet = spreadsheet

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

    def append_row(
        self, values: list, value_input_option: str | None = None
    ) -> None:
        """Аналог gspread.Worksheet.append_row для тестов истории."""
        self.append_row_calls.append((list(values), value_input_option))
        self.rows.append([str(v) for v in values])

    def get_all_values(self) -> List[List[str]]:
        return [list(r) for r in self.rows]

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

    def batch_update(
        self,
        data: List[dict],
        value_input_option: str = "RAW",
    ) -> None:
        self.batch_update_calls.append((list(data), value_input_option))
        for entry in data:
            range_a1 = entry["range"]
            values = entry["values"]
            self.update(range_a1, values)


class FakeSpreadsheet:
    def __init__(self, worksheets: dict[str, FakeWorksheet] | None = None) -> None:
        self.worksheets = dict(worksheets or {})
        self.batch_update_calls: List[dict] = []

    def batch_update(self, body: dict) -> None:
        self.batch_update_calls.append(body)

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

    def test_insert_with_new_schema_headers_maps_all_analytic_columns(self) -> None:
        rows = [
            [
                "Отметка времени",
                "ФИО",
                "Группа",
                "Место работы",
                "Должность",
                "Телефон",
                "Научный руководитель",
                "Ссылка на промежуточный отчет",
                "Проверка ссылки",
                "Доступ открыт",
                "Ссылка на Магистерский проект",
                "Ссылка на ЛКБ",
                "Ссылка на диссер",
                "Ссылка на публикацию",
                "Число страниц",
                "Число источников",
                "Соответствие офо",
                "Название диссертации",
                "Язык диссертации",
            ]
        ]
        ws = FakeWorksheet(rows)
        form = UserForm(
            telegram_id="222",
            fio="Камзебаева А.Д.",
            group_name="Оз-11",
            report_url="https://docs.google.com/document/d/report/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
        )

        row = upsert_user_with_extras(
            ws,
            form,
            extra_values={
                "project_folder_url": "https://drive.google.com/drive/folders/proj",
                "lkb_url": "https://drive.google.com/file/d/lkb/view",
                "dissertation_url": "https://docs.google.com/document/d/diss/edit",
                "publication_url": "https://docs.google.com/document/d/pub/edit",
                "pages_total": "87",
                "sources_count": "42",
                "compliance": "Соответствует",
                "dissertation_title": "",
                "dissertation_language": "",
            },
        )

        self.assertEqual(row, 2)
        saved = ws.rows[1]
        self.assertEqual(saved[1], "Камзебаева А.Д.")
        self.assertEqual(saved[2], "Оз-11")
        self.assertEqual(saved[7], "https://docs.google.com/document/d/report/edit")
        self.assertEqual(saved[8], "yes")
        self.assertEqual(saved[9], "yes")
        self.assertEqual(saved[10], "https://drive.google.com/drive/folders/proj")
        self.assertEqual(saved[11], "https://drive.google.com/file/d/lkb/view")
        self.assertEqual(saved[12], "https://docs.google.com/document/d/diss/edit")
        self.assertEqual(saved[13], "https://docs.google.com/document/d/pub/edit")
        self.assertEqual(saved[14], "87")
        self.assertEqual(saved[15], "42")
        self.assertEqual(saved[16], "Соответствует")
        self.assertEqual(saved[17], "")
        self.assertEqual(saved[18], "")


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
        self.assertEqual(rows[1][0], "Обновлено")
        self.assertEqual(len(rows[1][1].split()), 2)  # date + time
        self.assertEqual(rows[2], ["Всего регистраций", "3"])
        self.assertEqual(rows[3], ["Полностью зарегистрированы", "1"])
        self.assertEqual(rows[4], ["Частично заполнены", "1"])
        self.assertEqual(rows[5], ["Новые / пустые", "1"])
        self.assertEqual(rows[6], ["Проверка пройдена (OK)", "0"])
        self.assertEqual(rows[7], ["Нужны исправления (NEED_FIX)", "0"])
        self.assertEqual(rows[8], ["Ошибка проверки (ERROR)", "0"])
        self.assertEqual(rows[9], ["Привязаны к Telegram", "2"])
        self.assertEqual(rows[10], ["Есть ссылка на отчет", "2"])
        self.assertEqual(rows[11], ["Доступ открыт", "1"])
        self.assertEqual(rows[12], ["Доступ не открыт", "1"])
        self.assertEqual(len(rows), 16)

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


class ApplyRowCheckUpdatesTests(unittest.TestCase):
    """Тесты записи результатов Stage 2 / Stage 3 в лист «Регистрация»."""

    _HEADER_PHASE_A = [
        "Отметка времени",
        "ФИО",
        "Группа",
        "Место работы",
        "Должность",
        "Телефон",
        "Научный руководитель",
        "Ссылка на промежуточный отчет",
        "Проверка ссылки",
        "Доступ открыт",
        "Ссылка на Магистерский проект",
        "Ссылка на ЛКБ",
        "Ссылка на диссер",
        "Ссылка на публикацию",
        "Число страниц",
        "Число источников",
        "Соответствие офо",
        "Название диссертации",
        "Язык диссертации",
    ]

    def _fresh_sheet(self) -> tuple["FakeSpreadsheet", FakeWorksheet]:
        spreadsheet = FakeSpreadsheet()
        ws = FakeWorksheet(
            [list(self._HEADER_PHASE_A), [""] * len(self._HEADER_PHASE_A)],
            sheet_id=42,
            spreadsheet=spreadsheet,
        )
        spreadsheet.worksheets["Регистрация"] = ws
        return spreadsheet, ws

    def test_writes_stage2_and_stage3_values(self) -> None:
        spreadsheet, ws = self._fresh_sheet()

        stage3_cells = [
            Stage3CellUpdate(
                column_key="project_folder_url",
                value="https://drive.google.com/drive/folders/proj",
                strikethrough=False,
            ),
            Stage3CellUpdate(
                column_key="lkb_url",
                value="https://drive.google.com/file/d/lkb",
                strikethrough=True,
            ),
            Stage3CellUpdate(
                column_key="dissertation_url",
                value="https://docs.google.com/document/d/diss",
                strikethrough=False,
            ),
            Stage3CellUpdate(
                column_key="publication_url",
                value="нет",
                strikethrough=False,
            ),
        ]

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=stage3_cells,
        )

        saved = ws.rows[1]
        # Позиции в тестовом заголовке (индексы): Проверка ссылки=8, Доступ открыт=9,
        # Магистерский проект=10, ЛКБ=11, диссер=12, публикация=13.
        self.assertEqual(saved[8], "yes")
        self.assertEqual(saved[9], "yes")
        self.assertEqual(saved[10], "https://drive.google.com/drive/folders/proj")
        self.assertEqual(saved[11], "https://drive.google.com/file/d/lkb")
        self.assertEqual(saved[12], "https://docs.google.com/document/d/diss")
        self.assertEqual(saved[13], "нет")

    def test_batch_update_is_single_call_in_raw_mode(self) -> None:
        _, ws = self._fresh_sheet()

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="no",
            stage3_cells=[
                Stage3CellUpdate(column_key="dissertation_url", value="нет"),
            ],
        )

        self.assertEqual(len(ws.batch_update_calls), 1)
        batch, value_input_option = ws.batch_update_calls[0]
        self.assertEqual(value_input_option, "RAW")
        ranges = [entry["range"] for entry in batch]
        # Re-check (Stage 4 (c) overwrite_clean): batch включает все
        # 9 известных колонок проверки, даже если пайплайн не дал значения
        # — тогда туда уходит "" (clean-write).
        self.assertEqual(
            ranges,
            ["I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "Q2"],
        )

    def test_strikethrough_format_requests_match_cells(self) -> None:
        spreadsheet, ws = self._fresh_sheet()

        stage3_cells = [
            Stage3CellUpdate(column_key="project_folder_url", value="нет"),
            Stage3CellUpdate(
                column_key="lkb_url",
                value="https://drive.google.com/file/d/lkb",
                strikethrough=True,
            ),
            Stage3CellUpdate(
                column_key="dissertation_url",
                value="https://docs.google.com/document/d/diss",
                strikethrough=False,
            ),
            Stage3CellUpdate(column_key="publication_url", value="нет"),
        ]

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=stage3_cells,
        )

        self.assertEqual(len(spreadsheet.batch_update_calls), 1)
        body = spreadsheet.batch_update_calls[0]
        requests = body["requests"]
        # Clean-write: 9 repeatCell — strike False по всем известным колонкам
        # проверки (I..Q), плюс stage3-значения переопределяют 4 из них.
        self.assertEqual(len(requests), 9)

        # Извлекаем strike по индексу колонки для удобной проверки.
        strike_by_col = {
            req["repeatCell"]["range"]["startColumnIndex"]: req["repeatCell"][
                "cell"
            ]["userEnteredFormat"]["textFormat"]["strikethrough"]
            for req in requests
        }
        self.assertEqual(strike_by_col[10], False)  # project_folder_url
        self.assertEqual(strike_by_col[11], True)   # lkb_url (передан True)
        self.assertEqual(strike_by_col[12], False)  # dissertation_url
        self.assertEqual(strike_by_col[13], False)  # publication_url
        # Колонки Stage 2 / Stage 4: clean-write кладёт strike=False.
        for col_idx in (8, 9, 14, 15, 16):
            self.assertEqual(strike_by_col[col_idx], False)

        for request in requests:
            range_ = request["repeatCell"]["range"]
            self.assertEqual(range_["sheetId"], 42)
            self.assertEqual(range_["startRowIndex"], 1)
            self.assertEqual(range_["endRowIndex"], 2)
            self.assertEqual(
                request["repeatCell"]["fields"],
                "userEnteredFormat.textFormat.strikethrough",
            )

    def test_clears_stage2_cells_when_probe_is_none(self) -> None:
        """Re-check (overwrite_clean): если probe=None, I/J всё равно
        затираются — никакой stale 'yes' от прошлого прогона не
        остаётся, если магистрант сменил отчёт и Stage 2 не дошёл."""
        spreadsheet, ws = self._fresh_sheet()
        ws.rows[1] = ["x"] * len(self._HEADER_PHASE_A)  # эмуляция прежнего прогона

        apply_row_check_updates(
            ws,
            2,
            report_url_valid=None,
            report_url_accessible=None,
            stage3_cells=[
                Stage3CellUpdate(column_key="dissertation_url", value="нет"),
            ],
        )

        self.assertEqual(ws.rows[1][8], "")   # Проверка ссылки очищена
        self.assertEqual(ws.rows[1][9], "")   # Доступ открыт очищен
        self.assertEqual(ws.rows[1][12], "нет")  # Stage 3 dissertation
        ranges = [entry["range"] for entry in ws.batch_update_calls[0][0]]
        self.assertEqual(
            ranges,
            ["I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "Q2"],
        )
        self.assertEqual(len(spreadsheet.batch_update_calls), 1)

    def test_skips_missing_columns_in_custom_header(self) -> None:
        # Заголовок без «Ссылка на публикацию» — ячейка не записывается.
        header = [
            "Отметка времени",
            "ФИО",
            "Ссылка на промежуточный отчет",
            "Проверка ссылки",
            "Доступ открыт",
            "Ссылка на Магистерский проект",
            "Ссылка на ЛКБ",
            "Ссылка на диссер",
        ]
        spreadsheet = FakeSpreadsheet()
        ws = FakeWorksheet(
            [list(header), [""] * len(header)],
            sheet_id=7,
            spreadsheet=spreadsheet,
        )

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=[
                Stage3CellUpdate(column_key="project_folder_url", value="url-L"),
                Stage3CellUpdate(column_key="publication_url", value="нет"),
            ],
        )

        ranges = [entry["range"] for entry in ws.batch_update_calls[0][0]]
        # Колонок publication_url, pages_total, sources_count, compliance
        # в этом заголовке нет — clean-write их пропускает. Остальные
        # 5 колонок (I,J,K,L,M в Phase A → D..H здесь) затираются и
        # перезаписываются.
        self.assertEqual(ranges, ["D2", "E2", "F2", "G2", "H2"])
        requests = spreadsheet.batch_update_calls[0]["requests"]
        # 5 strike-сбросов (по числу присутствующих в заголовке колонок,
        # для которых _set_strike зовётся), плюс stage3 переопределяет
        # один из них (project_folder_url=col 5 → strike=False, что
        # совпадает с дефолтом).
        self.assertEqual(len(requests), 5)
        cols = sorted(
            req["repeatCell"]["range"]["startColumnIndex"] for req in requests
        )
        self.assertEqual(cols, [3, 4, 5, 6, 7])

    def test_clean_write_format_call_even_without_stage3(self) -> None:
        """Re-check: clean-write всегда снимает strike со всех известных
        колонок, даже если Stage 3 не дал ячеек (pipeline остановился
        раньше). Это нужно, чтобы прошлый прогон не оставил зачёркивания."""
        spreadsheet, ws = self._fresh_sheet()

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="no",
            report_url_accessible="no",
            stage3_cells=None,
        )

        self.assertEqual(len(ws.batch_update_calls), 1)
        ranges = [entry["range"] for entry in ws.batch_update_calls[0][0]]
        self.assertEqual(
            ranges,
            ["I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "Q2"],
        )
        self.assertEqual(len(spreadsheet.batch_update_calls), 1)
        requests = spreadsheet.batch_update_calls[0]["requests"]
        # Все 9 cell-ов сбрасывают strikethrough в False.
        self.assertEqual(len(requests), 9)
        for req in requests:
            self.assertFalse(
                req["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"][
                    "strikethrough"
                ]
            )

    def test_writes_stage4_cells_in_same_batch(self) -> None:
        """Stage 4 пишет pages_total/sources_count/compliance в общий batch."""
        spreadsheet, ws = self._fresh_sheet()

        stage4_cells = [
            Stage4CellUpdate(column_key="pages_total", value="87"),
            Stage4CellUpdate(column_key="sources_count", value="42"),
            Stage4CellUpdate(column_key="compliance", value="соответствует"),
        ]

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=[
                Stage3CellUpdate(
                    column_key="dissertation_url",
                    value="https://docs.google.com/document/d/diss",
                    strikethrough=False,
                ),
            ],
            stage4_cells=stage4_cells,
        )

        saved = ws.rows[1]
        # В тестовом заголовке: Число страниц=14, Число источников=15,
        # Соответствие офо=16.
        self.assertEqual(saved[14], "87")
        self.assertEqual(saved[15], "42")
        self.assertEqual(saved[16], "соответствует")

        # Один batch_update — все 9 колонок проверки (clean-write).
        self.assertEqual(len(ws.batch_update_calls), 1)
        batch, vio = ws.batch_update_calls[0]
        self.assertEqual(vio, "RAW")
        ranges = [entry["range"] for entry in batch]
        self.assertEqual(
            ranges,
            ["I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "Q2"],
        )

        # Strikethrough — 9 запросов (по числу clean-write колонок).
        # Stage 3 переопределяет один из них (dissertation_url col 12).
        self.assertEqual(len(spreadsheet.batch_update_calls), 1)
        requests = spreadsheet.batch_update_calls[0]["requests"]
        self.assertEqual(len(requests), 9)

    def test_stage4_columns_cleared_when_not_provided(self) -> None:
        """Re-check: stage4_cells=None → Stage 4 колонки в листе ЗАТИРАЮТСЯ
        (clean-write), а не остаются как были. Если в прошлом прогоне
        туда что-то записали, после нового прогона без Stage 4 они пустеют."""
        spreadsheet, ws = self._fresh_sheet()
        # Эмуляция прошлой записи Stage 4: P (14)=87, Q (15)=42, R (16)=да
        ws.rows[1][14] = "87"
        ws.rows[1][15] = "42"
        ws.rows[1][16] = "соответствует"

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=[
                Stage3CellUpdate(column_key="dissertation_url", value="нет"),
            ],
            stage4_cells=None,
        )

        ranges = [entry["range"] for entry in ws.batch_update_calls[0][0]]
        self.assertEqual(
            ranges,
            ["I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "Q2"],
        )
        # Stage 4 колонки в Phase A header — индексы 14, 15, 16 — пусты.
        self.assertEqual(ws.rows[1][14], "")
        self.assertEqual(ws.rows[1][15], "")
        self.assertEqual(ws.rows[1][16], "")

    def test_stage4_skips_missing_columns(self) -> None:
        """Заголовок без Stage 4 колонок — соответствующие cells пропускаются."""
        spreadsheet = FakeSpreadsheet()
        header = [
            "Отметка времени",
            "ФИО",
            "Ссылка на промежуточный отчет",
            "Проверка ссылки",
            "Доступ открыт",
            "Ссылка на диссер",
        ]
        ws = FakeWorksheet(
            [list(header), [""] * len(header)],
            sheet_id=11,
            spreadsheet=spreadsheet,
        )

        apply_row_check_updates(
            ws,
            2,
            report_url_valid="yes",
            report_url_accessible="yes",
            stage3_cells=[
                Stage3CellUpdate(column_key="dissertation_url", value="diss"),
            ],
            stage4_cells=[
                Stage4CellUpdate(column_key="pages_total", value="100"),
                Stage4CellUpdate(column_key="sources_count", value="40"),
                Stage4CellUpdate(column_key="compliance", value="соответствует"),
            ],
        )

        # Stage 4 столбцов нет в заголовке — ни одного диапазона по ним.
        ranges = [entry["range"] for entry in ws.batch_update_calls[0][0]]
        self.assertEqual(ranges, ["D2", "E2", "F2"])


class RecheckHistoryEntryTests(unittest.TestCase):
    """Сериализация одной строки истории."""

    def test_to_row_in_header_order(self) -> None:
        entry = RecheckHistoryEntry(
            timestamp="2026-04-25 09:30:00",
            row_number=3,
            fio="Гизатова И.В.",
            source="bot",
            stopped_at="",
            passed="yes",
            issues="Ссылка не открывается",
            pages_total="87",
            sources_count="42",
            compliance="соответствует",
            fingerprint="abc123",
        )
        row = entry.to_row()
        self.assertEqual(len(row), len(RECHECK_HISTORY_HEADER))
        self.assertEqual(
            row,
            [
                "2026-04-25 09:30:00",
                "3",
                "Гизатова И.В.",
                "bot",
                "",
                "yes",
                "Ссылка не открывается",
                "87",
                "42",
                "соответствует",
                "abc123",
            ],
        )

    def test_from_row_round_trip(self) -> None:
        entry = RecheckHistoryEntry(
            timestamp="2026-04-25", row_number=5, fio="X", fingerprint="hash"
        )
        restored = RecheckHistoryEntry.from_row(entry.to_row())
        self.assertEqual(restored.row_number, 5)
        self.assertEqual(restored.fio, "X")
        self.assertEqual(restored.fingerprint, "hash")

    def test_from_row_pads_short_rows(self) -> None:
        restored = RecheckHistoryEntry.from_row(["2026-04-25", "7"])
        self.assertEqual(restored.row_number, 7)
        self.assertEqual(restored.fingerprint, "")

    def test_from_row_handles_invalid_row_number(self) -> None:
        restored = RecheckHistoryEntry.from_row(["ts", "abc", "fio"])
        self.assertEqual(restored.row_number, 0)


class AppendRecheckHistoryTests(unittest.TestCase):
    """Лист «История проверок» создаётся лениво и заголовок выставляется."""

    def test_creates_worksheet_with_header_on_first_call(self) -> None:
        spreadsheet = FakeSpreadsheet()
        entry = RecheckHistoryEntry(
            timestamp="2026-04-25", row_number=3, fio="X", source="bot"
        )

        append_recheck_history(spreadsheet, entry)

        self.assertIn(RECHECK_HISTORY_WORKSHEET_NAME, spreadsheet.worksheets)
        ws = spreadsheet.worksheets[RECHECK_HISTORY_WORKSHEET_NAME]
        self.assertEqual(list(ws.rows[0]), list(RECHECK_HISTORY_HEADER))
        self.assertEqual(len(ws.append_row_calls), 1)
        appended_values, vio = ws.append_row_calls[0]
        self.assertEqual(vio, "RAW")
        self.assertEqual(appended_values[1], "3")
        self.assertEqual(appended_values[2], "X")
        self.assertEqual(appended_values[3], "bot")

    def test_reuses_existing_worksheet_keeps_header(self) -> None:
        spreadsheet = FakeSpreadsheet()
        existing = FakeWorksheet(
            [list(RECHECK_HISTORY_HEADER)],
            sheet_id=99,
            spreadsheet=spreadsheet,
        )
        spreadsheet.worksheets[RECHECK_HISTORY_WORKSHEET_NAME] = existing

        append_recheck_history(
            spreadsheet,
            RecheckHistoryEntry(timestamp="t", row_number=4, fio="Y"),
        )

        self.assertEqual(len(existing.update_calls), 0)
        self.assertEqual(len(existing.append_row_calls), 1)

    def test_repairs_wrong_header(self) -> None:
        """Если в листе вдруг окажется чужая шапка — переписываем (наш лист)."""
        spreadsheet = FakeSpreadsheet()
        existing = FakeWorksheet(
            [["ts", "row"]],
            sheet_id=99,
            spreadsheet=spreadsheet,
        )
        spreadsheet.worksheets[RECHECK_HISTORY_WORKSHEET_NAME] = existing

        append_recheck_history(
            spreadsheet,
            RecheckHistoryEntry(timestamp="t", row_number=4),
        )

        self.assertEqual(list(existing.rows[0]), list(RECHECK_HISTORY_HEADER))


class ReadLastRecheckEntryTests(unittest.TestCase):
    """Поиск последней записи по row_number для --only-if-changed."""

    def _ws(
        self, spreadsheet: FakeSpreadsheet, *rows: list[str]
    ) -> FakeWorksheet:
        ws = FakeWorksheet(
            [list(RECHECK_HISTORY_HEADER), *(list(r) for r in rows)],
            sheet_id=77,
            spreadsheet=spreadsheet,
        )
        spreadsheet.worksheets[RECHECK_HISTORY_WORKSHEET_NAME] = ws
        return ws

    def test_returns_none_when_history_sheet_missing(self) -> None:
        spreadsheet = FakeSpreadsheet()
        self.assertIsNone(read_last_recheck_entry(spreadsheet, 3))

    def test_returns_none_when_no_entries_for_row(self) -> None:
        spreadsheet = FakeSpreadsheet()
        self._ws(
            spreadsheet,
            ["t1", "5", "X", "bot", "", "yes", "", "", "", "", "fp1"],
        )
        self.assertIsNone(read_last_recheck_entry(spreadsheet, 3))

    def test_returns_last_for_row_when_multiple_present(self) -> None:
        spreadsheet = FakeSpreadsheet()
        self._ws(
            spreadsheet,
            ["t1", "3", "X", "cli", "", "yes", "", "", "", "", "fp_old"],
            ["t2", "5", "Y", "bot", "", "yes", "", "", "", "", "fp_other"],
            ["t3", "3", "X", "bot", "", "no", "issue", "", "", "", "fp_new"],
        )

        entry = read_last_recheck_entry(spreadsheet, 3)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.timestamp, "t3")
        self.assertEqual(entry.fingerprint, "fp_new")
        self.assertEqual(entry.passed, "no")
        self.assertEqual(entry.issues, "issue")


if __name__ == "__main__":
    unittest.main()
