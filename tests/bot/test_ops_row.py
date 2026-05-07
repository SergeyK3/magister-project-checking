"""Minimal tests for /ops_row diagnostics helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import SHEET_HEADER, UserForm
from magister_checking.bot.ops_diagnostics import (
    OpsRowDiagnostics,
    OpsRetrySummary,
    OpsSnapshotSummary,
    OpsStageSummary,
    build_ops_row_diagnostics,
    collect_ops_row_diagnostics,
)
from magister_checking.bot.ops_render import render_ops_row_diagnostics
from magister_checking.bot.sheets_repo import (
    RECHECK_HISTORY_HEADER,
    RECHECK_HISTORY_WORKSHEET_NAME,
    RecheckHistoryEntry,
)
from tests.bot.test_sheets_repo import FakeSpreadsheet, FakeWorksheet


def _make_config() -> BotConfig:
    return BotConfig(
        telegram_bot_token="123:ABCdefGHIjklMNOpqrstUVwxyz1234567890",
        spreadsheet_id="sheet123",
        worksheet_name="Регистрация",
        project_card_output_folder_url="",
        google_service_account_json=Path("credentials/unused.json"),
        log_level=20,
        persistence_file=Path("state/unused.pickle"),
    )


class OpsRenderTests(unittest.TestCase):
    def test_render_is_compact_and_sanitizes_urls(self) -> None:
        diag = OpsRowDiagnostics(
            row_number=7,
            fio="Иванов И.И.",
            fill_status="OK",
            retry=OpsRetrySummary(
                timestamp="2026-05-07T07:00:00Z",
                passed="yes",
                issues="см. https://docs.google.com/document/d/full/edit",
                fingerprint_prefix="abcdef123456",
            ),
            snapshot=OpsSnapshotSummary(
                present=True,
                modified_time="2026-05-07T07:01:00Z",
                fingerprint_prefix="123456abcdef",
                stages=(
                    OpsStageSummary("stage1", "passed"),
                    OpsStageSummary("stage4", "failed", "warning text", 2),
                ),
            ),
        )

        out = render_ops_row_diagnostics(diag)

        self.assertIn("Ops row 7", out)
        self.assertIn("abcdef123456", out)
        self.assertIn("[url]", out)
        self.assertNotIn("https://", out)
        self.assertNotIn("callback_data", out)
        self.assertNotIn("PIN", out)


class OpsDiagnosticsTests(unittest.TestCase):
    def test_build_uses_only_short_fingerprint_prefixes(self) -> None:
        entry = RecheckHistoryEntry(
            timestamp="ts",
            row_number=2,
            fingerprint="abcdef1234567890",
        )

        diag = build_ops_row_diagnostics(
            row_number=2,
            user=UserForm(fio="Петров П.П.", fill_status="NEED_FIX"),
            retry_entry=entry,
            latest_snapshot=None,
        )

        self.assertEqual(diag.retry.fingerprint_prefix, "abcdef123456")
        self.assertEqual(diag.fill_status, "NEED_FIX")
        self.assertFalse(diag.snapshot.present)

    def test_collect_is_read_only_for_sheets(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("fio")] = "Петров П.П."
        row[SHEET_HEADER.index("fill_status")] = "OK"
        registration = FakeWorksheet([list(SHEET_HEADER), row])
        history = FakeWorksheet(
            [
                list(RECHECK_HISTORY_HEADER),
                ["ts", "2", "Петров П.П.", "bot", "stage4", "yes", "", "80", "40", "yes", "abcdef1234567890"],
            ]
        )
        spreadsheet = FakeSpreadsheet(
            {
                "Регистрация": registration,
                RECHECK_HISTORY_WORKSHEET_NAME: history,
            }
        )
        cfg = _make_config()

        with patch(
            "magister_checking.bot.ops_diagnostics.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.bot.ops_diagnostics.pick_latest_snapshot_for_row",
            return_value=None,
        ):
            diag = collect_ops_row_diagnostics(cfg, 2)

        self.assertEqual(diag.fio, "Петров П.П.")
        self.assertEqual(diag.retry.passed, "yes")
        self.assertEqual(registration.update_calls, [])
        self.assertEqual(registration.batch_update_calls, [])
        self.assertEqual(registration.append_row_calls, [])
        self.assertEqual(history.update_calls, [])
        self.assertEqual(history.batch_update_calls, [])
        self.assertEqual(history.append_row_calls, [])
        self.assertEqual(spreadsheet.batch_update_calls, [])


if __name__ == "__main__":
    unittest.main()
