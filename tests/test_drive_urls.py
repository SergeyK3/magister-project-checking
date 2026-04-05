"""Tests for drive URL / id parsing."""

import unittest

from magister_checking.drive_urls import extract_google_file_id


class TestExtractGoogleFileId(unittest.TestCase):
    def test_raw_id(self) -> None:
        self.assertEqual(extract_google_file_id("1aZ_-x9"), "1aZ_-x9")

    def test_doc_url(self) -> None:
        url = "https://docs.google.com/document/d/1ABCdEfGhIJ/edit?usp=sharing"
        self.assertEqual(extract_google_file_id(url), "1ABCdEfGhIJ")

    def test_sheets_url(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit"
        self.assertEqual(extract_google_file_id(url), "SPREADSHEET_ID")

    def test_drive_open(self) -> None:
        url = "https://drive.google.com/open?id=FILEID42"
        self.assertEqual(extract_google_file_id(url), "FILEID42")


if __name__ == "__main__":
    unittest.main()
