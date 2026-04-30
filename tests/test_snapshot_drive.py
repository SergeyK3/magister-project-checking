"""Тесты загрузки JSON-снимка на Google Drive."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.project_snapshot import build_project_snapshot
from magister_checking.snapshot_drive import try_upload_project_snapshot_json


class SnapshotDriveTests(unittest.TestCase):
    def test_no_folder_url_skips_upload(self) -> None:
        cfg = MagicMock()
        cfg.project_card_output_folder_url = ""
        snap = build_project_snapshot(
            user=UserForm(fio="Тест"),
            report=RowCheckReport(fio="Тест", row_number=1),
            extra_values={},
        )
        self.assertEqual(try_upload_project_snapshot_json(cfg, snap), [])

    @patch("magister_checking.snapshot_drive._service_account_creds_from_config", return_value=MagicMock())
    @patch("magister_checking.snapshot_drive.build")
    def test_upload_calls_drive_create(
        self, m_build: MagicMock, _m_creds: MagicMock
    ) -> None:
        cfg = MagicMock()
        url = "https://drive.google.com/drive/folders/abcFolderId123"
        cfg.project_card_output_folder_url = url
        cfg.project_snapshot_output_folder_urls = (url,)
        cfg.google_service_account_json = "/fake/sa.json"
        snap = build_project_snapshot(
            user=UserForm(fio="Иванов И.И."),
            report=RowCheckReport(fio="Иванов И.И.", row_number=3),
            extra_values={},
        )
        drive = MagicMock()
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "fileid",
            "webViewLink": "https://drive.google.com/file/d/fileid/view",
        }
        m_build.return_value = drive

        out = try_upload_project_snapshot_json(cfg, snap)

        self.assertEqual(
            out, ["https://drive.google.com/file/d/fileid/view"]
        )
        drive.files.return_value.create.assert_called_once()
        call_kw = drive.files.return_value.create.call_args.kwargs
        self.assertEqual(call_kw["supportsAllDrives"], True)
        self.assertIn("parents", call_kw["body"])
        self.assertEqual(call_kw["body"]["parents"], ["abcFolderId123"])


if __name__ == "__main__":
    unittest.main()
