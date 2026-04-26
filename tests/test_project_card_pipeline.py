"""Tests for project card PDF pipeline."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.project_card_pipeline import generate_project_card_pdf


class ProjectCardPipelineTests(unittest.TestCase):
    def test_generate_project_card_pdf_updates_sheet_and_returns_bytes(self) -> None:
        config = MagicMock()
        config.spreadsheet_id = "sheet-id"
        config.worksheet_name = "Регистрация"
        config.project_card_output_folder_url = ""
        worksheet = MagicMock()
        worksheet.row_values.return_value = ["111", "ivanov", "Иван", "Иванов", "Иванов И.И."]
        spreadsheet = MagicMock()
        spreadsheet.worksheet.return_value = worksheet

        with patch(
            "magister_checking.project_card_pipeline.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.project_card_pipeline.load_user",
            return_value=UserForm(
                telegram_id="111",
                fio="Иванов И.И.",
                group_name="М-101",
                supervisor="Петров",
                report_url="https://docs.google.com/document/d/report/edit",
                report_url_valid="yes",
                report_url_accessible="yes",
            ),
        ), patch(
            "magister_checking.project_card_pipeline.build_sheet_enrichment",
            return_value={
                "project_folder_url": "https://drive.google.com/drive/folders/project",
                "lkb_url": "https://drive.google.com/file/d/lkb/view",
                "dissertation_url": "https://docs.google.com/document/d/diss/edit",
                "pages_total": "87",
                "sources_count": "13",
                "compliance": "Соответствует",
            },
        ), patch(
            "magister_checking.project_card_pipeline.save_user_to_row_with_extras",
        ) as mock_save, patch(
            "magister_checking.project_card_pipeline.sync_registration_dashboard",
        ) as mock_dashboard, patch(
            "magister_checking.project_card_pipeline._render_pdf",
            return_value=b"%PDF-1.4 test",
        ), patch(
            "magister_checking.project_card_pipeline.try_upload_project_snapshot_json",
        ) as mock_snap:
            result = generate_project_card_pdf(config=config, row_number=2)

        mock_snap.assert_called_once()
        mock_save.assert_called_once()
        mock_dashboard.assert_called_once_with(config)
        self.assertEqual(result.row_number, 2)
        self.assertEqual(result.pdf_name, "Карточка проекта - Иванов И.И.pdf")
        self.assertEqual(result.pdf_bytes, b"%PDF-1.4 test")

    def test_generate_project_card_pdf_raises_when_render_returns_empty(self) -> None:
        config = MagicMock()
        config.spreadsheet_id = "sheet-id"
        config.worksheet_name = "Регистрация"
        config.project_card_output_folder_url = ""
        worksheet = MagicMock()
        worksheet.row_values.return_value = ["111", "ivanov", "Иван", "Иванов", "Иванов И.И."]
        spreadsheet = MagicMock()
        spreadsheet.worksheet.return_value = worksheet

        with patch(
            "magister_checking.project_card_pipeline.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.project_card_pipeline.load_user",
            return_value=UserForm(
                telegram_id="111",
                fio="Иванов И.И.",
                report_url="https://docs.google.com/document/d/report/edit",
                report_url_valid="yes",
                report_url_accessible="yes",
            ),
        ), patch(
            "magister_checking.project_card_pipeline.build_sheet_enrichment",
            return_value={},
        ), patch(
            "magister_checking.project_card_pipeline.save_user_to_row_with_extras",
        ), patch(
            "magister_checking.project_card_pipeline.sync_registration_dashboard",
        ), patch(
            "magister_checking.project_card_pipeline._render_pdf",
            return_value=b"",
        ), patch(
            "magister_checking.project_card_pipeline.try_upload_project_snapshot_json",
        ):
            with self.assertRaises(RuntimeError):
                generate_project_card_pdf(config=config, row_number=2)


if __name__ == "__main__":
    unittest.main()
