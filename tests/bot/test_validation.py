"""Юнит-тесты для magister_checking.bot.validation."""

from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch

import requests

from magister_checking.bot.validation import (
    FIO_INVALID_MESSAGE,
    PHONE_INVALID_MESSAGE,
    REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
    REPORT_URL_WRONG_TARGET_MESSAGE,
    SKIP_TOKEN,
    check_report_document_marker,
    check_report_url,
    check_report_url_target_kind,
    is_interim_report_document,
    is_valid_url,
    normalize_text,
    validate_fio_shape,
    validate_phone_shape,
)


class NormalizeTextTests(unittest.TestCase):
    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_text("  hello  "), "hello")

    def test_skip_token_returns_empty(self) -> None:
        self.assertEqual(normalize_text(SKIP_TOKEN), "")
        self.assertEqual(normalize_text(f"  {SKIP_TOKEN}  "), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(normalize_text(None), "")  # type: ignore[arg-type]

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(normalize_text(""), "")


class IsValidUrlTests(unittest.TestCase):
    def test_https_url(self) -> None:
        self.assertTrue(is_valid_url("https://docs.google.com/document/d/abc/edit"))

    def test_http_url(self) -> None:
        self.assertTrue(is_valid_url("http://example.com"))

    def test_invalid_no_scheme(self) -> None:
        self.assertFalse(is_valid_url("docs.google.com/document"))

    def test_empty(self) -> None:
        self.assertFalse(is_valid_url(""))

    def test_ftp_rejected(self) -> None:
        self.assertFalse(is_valid_url("ftp://example.com/file"))


def _public_resolver(host: str, *_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("142.250.190.78", 0))]


class CheckReportUrlTests(unittest.TestCase):
    def test_empty_url_returns_empty_tuple(self) -> None:
        self.assertEqual(check_report_url(""), ("", ""))

    def test_invalid_url_marks_no(self) -> None:
        self.assertEqual(
            check_report_url("not-a-url"),
            ("no", "no"),
        )

    def test_loopback_host_rejected_without_network_call(self) -> None:
        with patch("magister_checking.bot.validation.requests.get") as mock_get:
            result = check_report_url("http://127.0.0.1/admin")
        self.assertEqual(result, ("no", "no"))
        mock_get.assert_not_called()

    def test_link_local_metadata_endpoint_rejected(self) -> None:
        with patch("magister_checking.bot.validation.requests.get") as mock_get:
            result = check_report_url(
                "http://169.254.169.254/latest/meta-data/"
            )
        self.assertEqual(result, ("no", "no"))
        mock_get.assert_not_called()

    def test_private_host_resolution_rejected(self) -> None:
        def private_resolver(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
            ]

        with patch(
            "magister_checking.bot.validation.socket.getaddrinfo",
            side_effect=private_resolver,
        ), patch("magister_checking.bot.validation.requests.get") as mock_get:
            result = check_report_url("http://internal.example.lan/")

        self.assertEqual(result, ("no", "no"))
        mock_get.assert_not_called()

    def test_ok_response(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        with patch(
            "magister_checking.bot.validation.socket.getaddrinfo",
            side_effect=_public_resolver,
        ), patch(
            "magister_checking.bot.validation.requests.get",
            return_value=response,
        ):
            valid, accessible = check_report_url(
                "https://docs.google.com/document/d/X/edit"
            )
        self.assertEqual((valid, accessible), ("yes", "yes"))

    def test_403_response(self) -> None:
        response = MagicMock()
        response.status_code = 403
        response.headers = {}
        with patch(
            "magister_checking.bot.validation.socket.getaddrinfo",
            side_effect=_public_resolver,
        ), patch(
            "magister_checking.bot.validation.requests.get",
            return_value=response,
        ):
            valid, accessible = check_report_url(
                "https://docs.google.com/document/d/X/edit"
            )
        self.assertEqual((valid, accessible), ("yes", "no"))

    def test_request_exception(self) -> None:
        with patch(
            "magister_checking.bot.validation.socket.getaddrinfo",
            side_effect=_public_resolver,
        ), patch(
            "magister_checking.bot.validation.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            valid, accessible = check_report_url(
                "https://docs.google.com/document/d/X/edit"
            )
        self.assertEqual((valid, accessible), ("yes", "no"))

    def test_redirect_to_private_host_is_blocked(self) -> None:
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "http://127.0.0.1/secret"}
        with patch(
            "magister_checking.bot.validation.socket.getaddrinfo",
            side_effect=_public_resolver,
        ), patch(
            "magister_checking.bot.validation.requests.get",
            return_value=redirect_response,
        ) as mock_get:
            valid, accessible = check_report_url(
                "https://shortener.example.com/abc"
            )
        self.assertEqual((valid, accessible), ("yes", "no"))
        self.assertEqual(mock_get.call_count, 1)


class ValidateFioShapeTests(unittest.TestCase):
    def test_full_russian_name_accepted(self) -> None:
        self.assertIsNone(validate_fio_shape("Камзебаева Анель Дулатовна"))

    def test_kazakh_cyrillic_accepted(self) -> None:
        self.assertIsNone(validate_fio_shape("Сапарбаева Жайна Саматкызы"))

    def test_hyphenated_surname_accepted(self) -> None:
        self.assertIsNone(validate_fio_shape("Петров-Водкин Иван Иванович"))

    def test_initials_accepted(self) -> None:
        self.assertIsNone(validate_fio_shape("Иванов И.И."))

    def test_latin_letters_rejected(self) -> None:
        self.assertEqual(
            validate_fio_shape("ТОО Viamedis Kosshy"),
            FIO_INVALID_MESSAGE,
        )

    def test_single_word_rejected(self) -> None:
        self.assertEqual(validate_fio_shape("Иванов"), FIO_INVALID_MESSAGE)

    def test_empty_rejected(self) -> None:
        self.assertEqual(validate_fio_shape(""), FIO_INVALID_MESSAGE)
        self.assertEqual(validate_fio_shape("   "), FIO_INVALID_MESSAGE)

    def test_digits_rejected(self) -> None:
        self.assertEqual(validate_fio_shape("Иванов И1"), FIO_INVALID_MESSAGE)


class ValidatePhoneShapeTests(unittest.TestCase):
    def test_plus7_accepted(self) -> None:
        self.assertIsNone(validate_phone_shape("+77052107246"))

    def test_local_8_accepted(self) -> None:
        self.assertIsNone(validate_phone_shape("87052107246"))

    def test_formatted_accepted(self) -> None:
        self.assertIsNone(validate_phone_shape("+7 (705) 210-72-46"))

    def test_too_short_rejected(self) -> None:
        self.assertEqual(validate_phone_shape("12345"), PHONE_INVALID_MESSAGE)

    def test_letters_rejected(self) -> None:
        self.assertEqual(validate_phone_shape("abc"), PHONE_INVALID_MESSAGE)

    def test_empty_rejected(self) -> None:
        self.assertEqual(validate_phone_shape(""), PHONE_INVALID_MESSAGE)


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


class InterimReportMarkerTests(unittest.TestCase):
    def test_phrase_in_first_paragraph_is_interim(self) -> None:
        doc = _doc_with_text("Промежуточный отчёт магистранта\n")
        self.assertTrue(is_interim_report_document(doc))
        self.assertIsNone(check_report_document_marker(doc))

    def test_lowercase_variant_without_yo_is_interim(self) -> None:
        doc = _doc_with_text("промежуточный отчет за семестр")
        self.assertTrue(is_interim_report_document(doc))

    def test_unrelated_document_is_not_interim(self) -> None:
        doc = _doc_with_text("Магистерский проект: оглавление")
        self.assertFalse(is_interim_report_document(doc))
        self.assertEqual(
            check_report_document_marker(doc),
            REPORT_URL_WRONG_TARGET_MESSAGE,
        )

    def test_marker_inside_table_cell(self) -> None:
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                {
                                                    "paragraph": {
                                                        "elements": [
                                                            {
                                                                "textRun": {
                                                                    "content": "Промежуточный отчёт"
                                                                }
                                                            }
                                                        ]
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        }
        self.assertTrue(is_interim_report_document(doc))

    def test_marker_only_in_api_title(self) -> None:
        doc = {"title": "Промежуточный отчёт — Хайтбаева", "body": {"content": []}}
        self.assertTrue(is_interim_report_document(doc))

    def test_latin_e_in_otchet(self) -> None:
        # Латинская U+0065 в слове «отчет» (частый артефакт копирования).
        doc = _doc_with_text("Промежуточный отч" + "\u0065" + "т\n")
        self.assertTrue(is_interim_report_document(doc))

    def test_empty_document_rejected(self) -> None:
        self.assertFalse(is_interim_report_document({}))
        self.assertFalse(is_interim_report_document(None))


class CheckReportUrlTargetKindTests(unittest.TestCase):
    """Формальная проверка вида URL: документ vs папка Drive."""

    def test_empty_url_returns_none(self) -> None:
        self.assertIsNone(check_report_url_target_kind(""))

    def test_google_doc_url_is_document(self) -> None:
        self.assertIsNone(
            check_report_url_target_kind(
                "https://docs.google.com/document/d/abc123/edit"
            )
        )

    def test_drive_file_url_is_document(self) -> None:
        # Загруженный .docx в Drive — тоже документ для целей этой проверки.
        self.assertIsNone(
            check_report_url_target_kind(
                "https://drive.google.com/file/d/abc123/view"
            )
        )

    def test_arbitrary_https_is_document(self) -> None:
        # «не папка Drive» — пропускаем (другие проверки сработают позже).
        self.assertIsNone(
            check_report_url_target_kind("https://example.com/report.pdf")
        )

    def test_drive_folder_url_returns_message(self) -> None:
        self.assertEqual(
            check_report_url_target_kind(
                "https://drive.google.com/drive/folders/1AbCdEf"
            ),
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )

    def test_drive_folder_url_with_user_segment_returns_message(self) -> None:
        # Реальная форма Камзебаевой (row 2): /drive/u/0/folders/<id>.
        self.assertEqual(
            check_report_url_target_kind(
                "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjK"
            ),
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )


if __name__ == "__main__":
    unittest.main()
