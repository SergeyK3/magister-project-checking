"""Tests for drive URL / id parsing."""

import unittest

from magister_checking.drive_urls import (
    classify_drive_url,
    extract_google_file_id,
    extract_google_folder_id,
    is_google_drive_folder_url,
)


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

    def test_folder_url_and_id(self) -> None:
        url = "https://drive.google.com/drive/folders/1AbCdEfGhIJkLmNoPqRsTuVwXyZ?usp=sharing"
        self.assertTrue(is_google_drive_folder_url(url))
        self.assertEqual(extract_google_folder_id(url), "1AbCdEfGhIJkLmNoPqRsTuVwXyZ")

    def test_folder_url_with_u_segment(self) -> None:
        url = "https://drive.google.com/drive/u/0/folders/ZZZfolderId42"
        self.assertTrue(is_google_drive_folder_url(url))
        self.assertEqual(extract_google_folder_id(url), "ZZZfolderId42")


class TestClassifyDriveUrl(unittest.TestCase):
    """Семантическая классификация Drive/Docs URL для Stage 3 пайплайна.

    Используется в bot/row_pipeline.py: для каждой колонки L/M/N/O
    политика говорит, какие ``DriveUrlKind`` допустимы. Mismatch →
    strikethrough + warning в справку магистранту.
    """

    def test_google_doc(self) -> None:
        for url in (
            "https://docs.google.com/document/d/ABC/edit",
            "https://docs.google.com/document/d/ABC/edit?usp=sharing&ouid=1",
            "http://docs.google.com/document/d/ABC",
        ):
            self.assertEqual(classify_drive_url(url), "google_doc", msg=url)

    def test_google_sheet(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/SHEET_ID/edit#gid=0"
        self.assertEqual(classify_drive_url(url), "google_sheet")

    def test_drive_folder(self) -> None:
        for url in (
            "https://drive.google.com/drive/folders/FOLDER_ID",
            "https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing",
            "https://drive.google.com/drive/u/0/folders/FOLDER_ID",
        ):
            self.assertEqual(classify_drive_url(url), "drive_folder", msg=url)

    def test_drive_file(self) -> None:
        for url in (
            "https://drive.google.com/file/d/FILE_ID/view",
            "https://drive.google.com/file/d/FILE_ID/view?usp=drive_link",
            "https://drive.google.com/file/d/FILE_ID/preview",
        ):
            self.assertEqual(classify_drive_url(url), "drive_file", msg=url)

    def test_other_for_unknown_or_empty(self) -> None:
        for url in (
            "",
            "   ",
            "not-a-url",
            "ftp://drive.google.com/file/d/X",  # не http(s)
            "https://example.com/x",
            "https://drive.google.com/open?id=X",  # legacy ?id= форма — не «drive_file»
            "https://docs.google.com/presentation/d/ABC",  # presentation вне политики
        ):
            self.assertEqual(classify_drive_url(url), "other", msg=url)


if __name__ == "__main__":
    unittest.main()
