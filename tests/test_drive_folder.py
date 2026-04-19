"""Имена файлов промежуточного отчёта в папке Drive."""

import unittest

from magister_checking.drive_folder import filename_starts_with_intermediate_report


class TestIntermediateReportFilename(unittest.TestCase):
    def test_prefix_as_requested(self) -> None:
        self.assertTrue(
            filename_starts_with_intermediate_report("Проммежуточный отчет — Иванов")
        )
        self.assertTrue(filename_starts_with_intermediate_report("Проммежуточный отчёт"))

    def test_standard_spelling(self) -> None:
        self.assertTrue(filename_starts_with_intermediate_report("Промежуточный отчет v2"))

    def test_non_matching(self) -> None:
        self.assertFalse(filename_starts_with_intermediate_report("Отчет промежуточный"))
        self.assertFalse(filename_starts_with_intermediate_report(""))


if __name__ == "__main__":
    unittest.main()
