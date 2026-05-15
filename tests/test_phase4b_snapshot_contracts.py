"""Phase 4B snapshot upload/pick regression contracts."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.drive_latest_snapshot import (
    list_snapshot_json_candidates,
    pick_latest_snapshot_for_row,
)
from magister_checking.project_snapshot import build_project_snapshot
from magister_checking.snapshot_drive import try_upload_project_snapshot_json


def _make_config(*folder_urls: str) -> BotConfig:
    return BotConfig(
        telegram_bot_token="123:ABCdefGHIjklMNOpqrstUVwxyz1234567890",
        spreadsheet_id="sheet123",
        worksheet_name="Регистрация",
        project_card_output_folder_url=folder_urls[0] if folder_urls else "",
        google_service_account_json=Path("credentials/unused.json"),
        log_level=20,
        persistence_file=Path("unused.pickle"),
        project_snapshot_output_folder_urls=tuple(folder_urls),
        magistrants_worksheet_name="",
    )


def _make_snapshot(row_number: int = 5):
    return build_project_snapshot(
        user=UserForm(fio="Иванов И.И."),
        report=RowCheckReport(fio="Иванов И.И.", row_number=row_number),
        extra_values={},
    )


class Phase4BSnapshotUploadContracts(unittest.TestCase):
    @patch(
        "magister_checking.snapshot_drive._service_account_creds_from_config",
        return_value=MagicMock(),
    )
    @patch("magister_checking.snapshot_drive.build")
    def test_upload_preserves_each_configured_parent_folder(
        self, m_build: MagicMock, _m_creds: MagicMock
    ) -> None:
        cfg = _make_config(
            "https://drive.google.com/drive/folders/folderOne123",
            "https://drive.google.com/drive/folders/folderTwo456",
        )
        drive = MagicMock()
        drive.files.return_value.create.return_value.execute.side_effect = [
            {"id": "file-one", "webViewLink": "https://drive.google.com/file/d/file-one/view"},
            {"id": "file-two", "webViewLink": "https://drive.google.com/file/d/file-two/view"},
        ]
        m_build.return_value = drive

        out = try_upload_project_snapshot_json(cfg, _make_snapshot())

        self.assertEqual(
            out,
            [
                "https://drive.google.com/file/d/file-one/view",
                "https://drive.google.com/file/d/file-two/view",
            ],
        )
        create_calls = drive.files.return_value.create.call_args_list
        self.assertEqual(len(create_calls), 2)
        self.assertEqual(
            [item.kwargs["body"]["parents"] for item in create_calls],
            [["folderOne123"], ["folderTwo456"]],
        )

    @patch(
        "magister_checking.snapshot_drive._service_account_creds_from_config",
        return_value=MagicMock(),
    )
    @patch("magister_checking.snapshot_drive.build")
    def test_upload_uses_supports_all_drives(
        self, m_build: MagicMock, _m_creds: MagicMock
    ) -> None:
        cfg = _make_config("https://drive.google.com/drive/folders/folderOne123")
        drive = MagicMock()
        drive.files.return_value.create.return_value.execute.return_value = {
            "id": "file-one",
        }
        m_build.return_value = drive

        try_upload_project_snapshot_json(cfg, _make_snapshot())

        call_kw = drive.files.return_value.create.call_args.kwargs
        self.assertIs(call_kw["supportsAllDrives"], True)


class Phase4BSnapshotPickContracts(unittest.TestCase):
    def test_list_candidates_filters_snapshot_filenames_for_exact_row_prefix(self) -> None:
        drive = MagicMock()
        drive.files.return_value.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "row-5-ok",
                    "name": "project_snapshot_r5_Ivanov_2026-05-07T01-00-00.json",
                    "modifiedTime": "2026-05-07T01:00:00Z",
                },
                {
                    "id": "row-50-cross-row",
                    "name": "project_snapshot_r50_Ivanov_2026-05-07T02-00-00.json",
                    "modifiedTime": "2026-05-07T02:00:00Z",
                },
                {
                    "id": "wrong-prefix",
                    "name": "project_card_r5_Ivanov_2026-05-07T03-00-00.json",
                    "modifiedTime": "2026-05-07T03:00:00Z",
                },
                {
                    "id": "wrong-extension",
                    "name": "project_snapshot_r5_Ivanov_2026-05-07T04-00-00.txt",
                    "modifiedTime": "2026-05-07T04:00:00Z",
                },
            ]
        }

        out = list_snapshot_json_candidates(
            folder_id="folderOne123",
            drive=drive,
            row_number=5,
        )

        self.assertEqual([item["id"] for item in out], ["row-5-ok"])

    def test_list_candidates_queries_exact_row_prefix_and_supports_all_drives(self) -> None:
        drive = MagicMock()
        drive.files.return_value.list.return_value.execute.return_value = {"files": []}

        list_snapshot_json_candidates(
            folder_id="folderOne123",
            drive=drive,
            row_number=5,
        )

        call_kw = drive.files.return_value.list.call_args.kwargs
        self.assertIn("'folderOne123' in parents", call_kw["q"])
        self.assertIn("name contains 'project_snapshot_r5_'", call_kw["q"])
        self.assertIs(call_kw["supportsAllDrives"], True)
        self.assertIs(call_kw["includeItemsFromAllDrives"], True)
        self.assertEqual(call_kw["corpora"], "allDrives")

    @patch(
        "magister_checking.drive_latest_snapshot._service_account_creds_from_config",
        return_value=MagicMock(),
    )
    @patch("magister_checking.drive_latest_snapshot.build")
    @patch("magister_checking.drive_latest_snapshot.list_snapshot_json_candidates")
    def test_pick_latest_snapshot_selects_highest_modified_time_across_folders(
        self,
        m_list_candidates: MagicMock,
        m_build: MagicMock,
        _m_creds: MagicMock,
    ) -> None:
        cfg = _make_config(
            "https://drive.google.com/drive/folders/folderOne123",
            "https://drive.google.com/drive/folders/folderTwo456",
        )
        drive = MagicMock()
        m_build.return_value = drive
        m_list_candidates.side_effect = [
            [
                {
                    "id": "older",
                    "name": "project_snapshot_r5_Ivanov_older.json",
                    "modifiedTime": "2026-05-07T01:00:00Z",
                }
            ],
            [
                {
                    "id": "newer",
                    "name": "project_snapshot_r5_Ivanov_newer.json",
                    "modifiedTime": "2026-05-07T02:00:00Z",
                }
            ],
        ]

        pick = pick_latest_snapshot_for_row(cfg, 5)

        self.assertIsNotNone(pick)
        self.assertEqual(pick.file_id, "newer")
        self.assertEqual(pick.name, "project_snapshot_r5_Ivanov_newer.json")
        self.assertEqual(
            m_list_candidates.call_args_list,
            [
                call(folder_id="folderOne123", drive=drive, row_number=5),
                call(folder_id="folderTwo456", drive=drive, row_number=5),
            ],
        )

    def test_list_candidates_does_not_select_cross_row_snapshot(self) -> None:
        drive = MagicMock()
        drive.files.return_value.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "row-12",
                    "name": "project_snapshot_r12_Petrov_2026-05-07T01-00-00.json",
                    "modifiedTime": "2026-05-07T01:00:00Z",
                }
            ]
        }

        out = list_snapshot_json_candidates(
            folder_id="folderOne123",
            drive=drive,
            row_number=1,
        )

        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
