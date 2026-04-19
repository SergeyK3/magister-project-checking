"""Юнит-тесты модели UserForm и расчёта статусов."""

from __future__ import annotations

import unittest

from magister_checking.bot.models import (
    FIELD_LABELS,
    REQUIRED_FIELDS,
    SHEET_HEADER,
    FillStatus,
    UserForm,
    compute_fill_status,
    get_missing_field_keys,
    get_missing_fields,
)


def _full_form() -> UserForm:
    return UserForm(
        telegram_id="111",
        fio="Иванов Иван",
        group_name="М-101",
        workplace="ООО Ромашка",
        position="Инженер",
        phone="+7 999 000-00-00",
        supervisor="Петров П.П.",
        report_url="https://docs.google.com/document/d/abc/edit",
    )


class SheetHeaderTests(unittest.TestCase):
    def test_header_contains_16_columns_in_tz_order(self) -> None:
        expected = [
            "telegram_id",
            "telegram_username",
            "telegram_first_name",
            "telegram_last_name",
            "fio",
            "group_name",
            "workplace",
            "position",
            "phone",
            "supervisor",
            "report_url",
            "report_url_valid",
            "report_url_accessible",
            "report_url_public_guess",
            "fill_status",
            "last_action",
        ]
        self.assertEqual(SHEET_HEADER, expected)
        self.assertEqual(len(SHEET_HEADER), 16)


class FillStatusTests(unittest.TestCase):
    def test_new_when_empty(self) -> None:
        self.assertEqual(compute_fill_status(UserForm()), FillStatus.NEW)

    def test_partial_when_some_filled(self) -> None:
        form = UserForm(fio="Иванов", group_name="М-101")
        self.assertEqual(compute_fill_status(form), FillStatus.PARTIAL)

    def test_registered_when_all_filled(self) -> None:
        self.assertEqual(compute_fill_status(_full_form()), FillStatus.REGISTERED)

    def test_telegram_fields_do_not_affect_status(self) -> None:
        form = UserForm(telegram_id="123", telegram_username="abc")
        self.assertEqual(compute_fill_status(form), FillStatus.NEW)


class MissingFieldsTests(unittest.TestCase):
    def test_all_missing_for_empty(self) -> None:
        keys = get_missing_field_keys(UserForm())
        self.assertEqual(keys, list(REQUIRED_FIELDS))
        labels = get_missing_fields(UserForm())
        self.assertEqual(labels, [FIELD_LABELS[k] for k in REQUIRED_FIELDS])

    def test_no_missing_for_full(self) -> None:
        self.assertEqual(get_missing_field_keys(_full_form()), [])
        self.assertEqual(get_missing_fields(_full_form()), [])

    def test_partial_missing(self) -> None:
        form = _full_form()
        form.phone = ""
        form.supervisor = ""
        self.assertEqual(get_missing_field_keys(form), ["phone", "supervisor"])


if __name__ == "__main__":
    unittest.main()
