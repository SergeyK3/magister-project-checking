"""Phase 0 readonly baselines for retry parsing and project snapshots."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from magister_checking.bot.handlers import (
    _parse_recheck_callback_row,
    _parse_recheck_command_parts,
)
from magister_checking.bot.models import FillStatus, UserForm
from magister_checking.bot.row_pipeline import (
    RowCheckReport,
    Stage3CellUpdate,
    StageResult,
    resolve_fill_status_after_row_check,
)
from magister_checking.project_snapshot import (
    PROJECT_SNAPSHOT_SCHEMA_VERSION,
    build_project_snapshot,
    project_snapshot_to_json,
)


class Phase0RetryParsingBaselineTests(unittest.TestCase):
    def test_recheck_command_is_full_by_default(self) -> None:
        self.assertEqual(_parse_recheck_command_parts("/recheck"), (False, None))
        self.assertEqual(_parse_recheck_command_parts("/recheck 42"), (False, "42"))
        self.assertEqual(
            _parse_recheck_command_parts("/recheck Иванов Иван"),
            (False, "Иванов Иван"),
        )

    def test_recheck_quick_tokens_enable_only_if_changed_and_preserve_target(self) -> None:
        self.assertEqual(_parse_recheck_command_parts("/recheck quick"), (True, None))
        self.assertEqual(_parse_recheck_command_parts("/recheck fast 42"), (True, "42"))
        self.assertEqual(
            _parse_recheck_command_parts("/recheck Иванов diff Иван"),
            (True, "Иванов Иван"),
        )
        self.assertEqual(
            _parse_recheck_command_parts("/recheck only-if-changed Петров"),
            (True, "Петров"),
        )

    def test_recheck_callback_accepts_only_full_row_payload(self) -> None:
        self.assertEqual(_parse_recheck_callback_row("recheck:full:17"), 17)
        self.assertIsNone(_parse_recheck_callback_row("recheck:full"))
        self.assertIsNone(_parse_recheck_callback_row("recheck:quick:17"))
        self.assertIsNone(_parse_recheck_callback_row("recheck:full:not-a-row"))
        self.assertIsNone(_parse_recheck_callback_row(None))

    def test_unchanged_retry_keeps_fill_status_readonly(self) -> None:
        report = RowCheckReport(fio="Иванов", unchanged=True)
        user = UserForm(fio="Иванов", fill_status=FillStatus.NEED_FIX.value)

        self.assertIsNone(resolve_fill_status_after_row_check(user, report))


class Phase0ProjectSnapshotBaselineTests(unittest.TestCase):
    def test_snapshot_json_shape_for_unchanged_retry_is_frozen(self) -> None:
        report = RowCheckReport(
            fio="Петров П.П.",
            row_number=12,
            stage1=StageResult("stage1", executed=True, passed=True),
            stage2=StageResult("stage2", executed=True, passed=True),
            stage3=StageResult(
                "stage3",
                executed=True,
                passed=True,
                issues=["«Заключение ЛКБ»: ссылка недоступна"],
            ),
            stage3_cells=[
                Stage3CellUpdate(
                    column_key="lkb_url",
                    value="https://drive.google.com/file/d/lkb/view",
                    strikethrough=True,
                )
            ],
            unchanged=True,
            source_fingerprint="fp-current",
        )
        user = UserForm(
            fio="Петров П.П.",
            group_name="МП-21",
            workplace="Университет",
            position="магистрант",
            phone="+77000000000",
            supervisor="Сидоров С.С.",
            report_url="https://docs.google.com/document/d/report/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
            fill_status=FillStatus.NEED_FIX.value,
        )

        snapshot = build_project_snapshot(
            user=user,
            report=report,
            extra_values={
                "project_folder_url": "https://drive.google.com/drive/folders/project",
                "lkb_url": "https://drive.google.com/file/d/lkb/view",
                "dissertation_url": "https://docs.google.com/document/d/diss/edit",
                "publication_url": "https://drive.google.com/file/d/pub/view",
                "dissertation_title": "Тема диссертации",
                "dissertation_language": "ru",
                "pages_total": "80",
                "sources_count": "42",
                "compliance": "соответствует",
            },
            trigger="bot",
            generated_at=datetime(2026, 5, 7, 3, 0, tzinfo=timezone.utc),
        )
        data = json.loads(project_snapshot_to_json(snapshot))

        self.assertEqual(
            list(data.keys()),
            [
                "schema_version",
                "generated_at",
                "row_number",
                "identity",
                "links",
                "fill_status",
                "phases",
                "metrics",
                "stage3_extracted",
                "stage4_skipped_reason",
                "unchanged",
                "stopped_at",
                "sheet_enrichment_metrics",
                "provenance",
            ],
        )
        self.assertEqual(data["schema_version"], PROJECT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(data["generated_at"], "2026-05-07T03:00:00+00:00")
        self.assertEqual(data["row_number"], 12)
        self.assertEqual(
            data["identity"],
            {
                "fio": "Петров П.П.",
                "group": "МП-21",
                "workplace": "Университет",
                "position": "магистрант",
                "phone": "+77000000000",
                "supervisor": "Сидоров С.С.",
            },
        )
        self.assertEqual(
            data["links"],
            {
                "report_url": "https://docs.google.com/document/d/report/edit",
                "project_folder_url": "https://drive.google.com/drive/folders/project",
                "lkb_url": "https://drive.google.com/file/d/lkb/view",
                "dissertation_url": "https://docs.google.com/document/d/diss/edit",
                "publication_url": "https://drive.google.com/file/d/pub/view",
                "report_url_valid": "yes",
                "report_url_accessible": "yes",
                "dissertation_title": "Тема диссертации",
                "dissertation_language": "ru",
            },
        )
        self.assertEqual(data["fill_status"], FillStatus.NEED_FIX.value)
        self.assertEqual(
            data["phases"],
            [
                {
                    "id": "stage1",
                    "status": "passed",
                    "summary": "Нарушений не найдено",
                    "details": "",
                    "warnings": [],
                },
                {
                    "id": "stage2",
                    "status": "passed",
                    "summary": "Нарушений не найдено",
                    "details": "",
                    "warnings": [],
                },
                {
                    "id": "stage3",
                    "status": "passed",
                    "summary": "",
                    "details": "",
                    "warnings": ["«Заключение ЛКБ»: ссылка недоступна"],
                },
                {
                    "id": "stage4",
                    "status": "skipped",
                    "summary": "Stage 4 не выполнялся",
                    "details": "",
                    "warnings": [],
                },
            ],
        )
        self.assertIsNone(data["metrics"])
        self.assertEqual(
            data["stage3_extracted"],
            [
                {
                    "column_key": "lkb_url",
                    "value": "https://drive.google.com/file/d/lkb/view",
                    "strikethrough": True,
                }
            ],
        )
        self.assertIsNone(data["stage4_skipped_reason"])
        self.assertTrue(data["unchanged"])
        self.assertIsNone(data["stopped_at"])
        self.assertEqual(
            data["sheet_enrichment_metrics"],
            ["80", "42", "соответствует"],
        )
        self.assertEqual(
            data["provenance"],
            {"trigger": "bot", "source_fingerprint": "fp-current"},
        )


if __name__ == "__main__":
    unittest.main()
