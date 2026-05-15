"""Юнит-тесты оркестратора первого этапа проверки."""

from __future__ import annotations

import unittest

from magister_checking.bot.models import UserForm
from magister_checking.bot.stage_checks import run_stage1_checks
from magister_checking.bot.validation import (
    FIO_INVALID_MESSAGE,
    PHONE_INVALID_MESSAGE,
    REPORT_URL_WRONG_TARGET_MESSAGE,
)


def _doc_with_text(text: str) -> dict:
    return {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": text}}],
                    }
                }
            ]
        }
    }


VALID_FIO = "Камзебаева Анель Дулатовна"
VALID_PHONE = "+77052107246"


class Stage1Tests(unittest.TestCase):
    def test_clean_form_without_doc_has_no_issues_but_report_not_checked(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE)
        result = run_stage1_checks(form)
        self.assertEqual(result.issues, [])
        self.assertFalse(result.report_checked)
        self.assertFalse(result.report_link_ok)

    def test_invalid_fio_added_to_issues(self) -> None:
        form = UserForm(fio="ТОО Viamedis Kosshy", phone=VALID_PHONE)
        result = run_stage1_checks(form)
        self.assertIn(FIO_INVALID_MESSAGE, result.issues)
        self.assertNotIn(PHONE_INVALID_MESSAGE, result.issues)

    def test_invalid_phone_added_to_issues(self) -> None:
        form = UserForm(fio=VALID_FIO, phone="abc")
        result = run_stage1_checks(form)
        self.assertIn(PHONE_INVALID_MESSAGE, result.issues)
        self.assertNotIn(FIO_INVALID_MESSAGE, result.issues)

    def test_both_fields_invalid_reports_both(self) -> None:
        form = UserForm(fio="ТОО Viamedis Kosshy", phone="abc")
        result = run_stage1_checks(form)
        self.assertEqual(
            result.issues,
            [FIO_INVALID_MESSAGE, PHONE_INVALID_MESSAGE],
        )

    def test_wrong_report_document_marks_link_not_ok(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE)
        doc = _doc_with_text("Магистерский проект: описание")
        result = run_stage1_checks(form, report_document=doc)
        self.assertTrue(result.report_checked)
        self.assertFalse(result.report_link_ok)
        self.assertIn(REPORT_URL_WRONG_TARGET_MESSAGE, result.issues)

    def test_correct_report_document_marks_link_ok(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE)
        doc = _doc_with_text("Промежуточный отчёт магистранта за семестр")
        result = run_stage1_checks(form, report_document=doc)
        self.assertTrue(result.report_checked)
        self.assertTrue(result.report_link_ok)
        self.assertEqual(result.issues, [])


if __name__ == "__main__":
    unittest.main()
