"""Тесты normalize_phone_ru_kz."""

from __future__ import annotations

import unittest

from magister_checking.bot.phone_normalize import normalize_phone_ru_kz


class NormalizePhoneRuKzTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(normalize_phone_ru_kz(""), "")
        self.assertEqual(normalize_phone_ru_kz("   "), "")
        self.assertEqual(normalize_phone_ru_kz(None), "")

    def test_plus7(self) -> None:
        self.assertEqual(normalize_phone_ru_kz("+7 701 123 45 67"), "+77011234567")
        self.assertEqual(normalize_phone_ru_kz("+7 (999) 000-00-00"), "+79990000000")

    def test_leading_8(self) -> None:
        self.assertEqual(normalize_phone_ru_kz("8 999 000-00-00"), "+79990000000")

    def test_leading_7_without_plus(self) -> None:
        self.assertEqual(normalize_phone_ru_kz("79990000000"), "+79990000000")

    def test_ten_digits(self) -> None:
        self.assertEqual(normalize_phone_ru_kz("9990000000"), "+79990000000")

    def test_invalid(self) -> None:
        self.assertEqual(normalize_phone_ru_kz("123"), "")
        self.assertEqual(normalize_phone_ru_kz("abc"), "")
        self.assertEqual(normalize_phone_ru_kz("+1 999 000 0000"), "")


if __name__ == "__main__":
    unittest.main()
