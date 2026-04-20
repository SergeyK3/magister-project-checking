"""Юнит-тесты для magister_checking.bot.validation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from magister_checking.bot.validation import (
    SKIP_TOKEN,
    check_report_url,
    is_valid_url,
    normalize_text,
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


class CheckReportUrlTests(unittest.TestCase):
    def test_empty_url_returns_empty_tuple(self) -> None:
        self.assertEqual(check_report_url(""), ("", ""))

    def test_invalid_url_marks_no(self) -> None:
        self.assertEqual(
            check_report_url("not-a-url"),
            ("no", "no"),
        )

    @patch("magister_checking.bot.validation.requests.get")
    def test_ok_response(self, mock_get: MagicMock) -> None:
        response = MagicMock()
        response.status_code = 200
        response.text = "google docs page"
        mock_get.return_value = response

        valid, accessible = check_report_url(
            "https://docs.google.com/document/d/X/edit"
        )
        self.assertEqual((valid, accessible), ("yes", "yes"))

    @patch("magister_checking.bot.validation.requests.get")
    def test_403_response(self, mock_get: MagicMock) -> None:
        response = MagicMock()
        response.status_code = 403
        response.text = "you need access"
        mock_get.return_value = response

        valid, accessible = check_report_url(
            "https://docs.google.com/document/d/X/edit"
        )
        self.assertEqual((valid, accessible), ("yes", "no"))

    @patch("magister_checking.bot.validation.requests.get")
    def test_request_exception(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("boom")
        valid, accessible = check_report_url(
            "https://docs.google.com/document/d/X/edit"
        )
        self.assertEqual((valid, accessible), ("yes", "no"))


if __name__ == "__main__":
    unittest.main()
