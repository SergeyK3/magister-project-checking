"""Тесты шаблонов напоминаний магистранту."""

from __future__ import annotations

import unittest

from magister_checking.bot.student_notify_text import build_standard_reminder


class StudentNotifyTextTests(unittest.TestCase):
    def test_standard_without_name(self) -> None:
        text = build_standard_reminder()
        self.assertIn("/start", text)
        self.assertIn("/recheck", text)
        self.assertNotIn("Замечания", text)

    def test_standard_with_name(self) -> None:
        text = build_standard_reminder(recipient_fio="Иванов И.И.")
        self.assertIn("Иванов И.И.", text)

    def test_extra_lines(self) -> None:
        text = build_standard_reminder(extra_lines=["Нет доступа по ссылке", "Мало источников"])
        self.assertIn("Замечания:", text)
        self.assertIn("• Нет доступа по ссылке", text)


if __name__ == "__main__":
    unittest.main()
