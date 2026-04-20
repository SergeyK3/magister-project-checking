"""Юнит-тесты для magister_checking.bot.validation."""

from __future__ import annotations

import socket
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


if __name__ == "__main__":
    unittest.main()
