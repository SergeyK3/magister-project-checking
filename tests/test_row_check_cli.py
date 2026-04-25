"""Интеграционные тесты IO-слоя row_check_cli (с моками сервисов)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.dissertation_metrics import DissertationMetrics
from magister_checking.report_parser import ParsedReport
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.sheets_repo import RecheckHistoryEntry
from magister_checking.row_check_cli import (
    RowLocator,
    _build_history_entry,
    _compute_recheck_fingerprint,
    _try_load_dissertation_metrics,
    format_report,
    run_row_check,
)


def _make_metrics(
    *,
    approx_pages: int = 80,
    sources_count: int | None = 35,
    formatting_compliance: bool | None = True,
    pdf_pages: int | None = None,
) -> DissertationMetrics:
    return DissertationMetrics(
        approx_pages=approx_pages,
        pdf_pages=pdf_pages,
        sources_count=sources_count,
        review_pages=None,
        review_sources_count=None,
        has_literature_review=True,
        has_results=True,
        has_discussion=True,
        formatting_compliance=formatting_compliance,
        font_size_14_ratio=1.0,
        times_new_roman_ratio=1.0,
        single_spacing_ratio=1.0,
    )


def _doc_with_text(text: str) -> dict:
    return {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": text}}],
                    }
                }
            ]
        }
    }


def _parsed(**overrides: object) -> ParsedReport:
    base: dict[str, object] = {
        "lkb_status": "да",
        "lkb_url": None,
        "dissertation_url": None,
        "review_article_url": None,
        "review_article_note": "",
        "results_article_url": None,
    }
    base.update(overrides)
    return ParsedReport(**base)  # type: ignore[arg-type]


def _make_native_doc_drive_service() -> MagicMock:
    """Drive-мок, у которого file.get(...).execute() возвращает метаданные
    нативного Google Doc. С таким MIME ``drive_docx.google_doc_from_drive_file``
    отдаёт file_id без копирования и удаления — эквивалент «старого» поведения.
    """

    drive = MagicMock()
    drive.files.return_value.get.return_value.execute.return_value = {
        "id": "doc-id",
        "name": "doc",
        "mimeType": "application/vnd.google-apps.document",
    }
    return drive


def _install_io_mocks(
    *,
    user: UserForm,
    document: object | None,
    parsed: ParsedReport | None,
    url_probe_map: dict[str, tuple[str, str]] | None = None,
    matched_rows: list[int] | None = None,
    drive_service: MagicMock | None = None,
):
    """Возвращает контекстный менеджер, маскирующий все IO вызовы run_row_check.

    url_probe_map: {url: (valid, accessible)} — результаты check_report_url
    для каждого URL, к которому обратится пайплайн. Значение по умолчанию
    («yes», «yes») используется, если URL не указан явно.

    drive_service: кастомный мок Drive API; по умолчанию — «нативный Google Doc»,
    чтобы ``google_doc_from_drive_file`` отдавал id без копирования.
    """

    probes = url_probe_map or {}

    def fake_check_report_url(url: str) -> tuple[str, str]:
        return probes.get(url, ("yes", "yes"))

    spreadsheet = MagicMock()
    worksheet = MagicMock()
    spreadsheet.worksheet.return_value = worksheet

    effective_drive = drive_service or _make_native_doc_drive_service()

    patches = [
        patch(
            "magister_checking.row_check_cli.get_spreadsheet",
            return_value=spreadsheet,
        ),
        patch(
            "magister_checking.row_check_cli.load_user",
            return_value=user,
        ),
        patch(
            "magister_checking.row_check_cli.find_rows_by_fio",
            return_value=matched_rows if matched_rows is not None else [2],
        ),
        patch(
            "magister_checking.row_check_cli._service_account_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "magister_checking.row_check_cli.build",
            side_effect=[MagicMock(), MagicMock()],
        ),
        patch(
            "magister_checking.row_check_cli.resolve_report_google_doc_id",
            return_value="doc-id",
        ),
        patch(
            "magister_checking.row_check_cli.parse_intermediate_report",
            return_value=parsed,
        ),
        patch(
            "magister_checking.row_check_cli.check_report_url",
            side_effect=fake_check_report_url,
        ),
    ]

    class _Manager:
        def __enter__(self) -> None:
            for p in patches:
                p.start()
            docs_service = MagicMock()
            if document is None:
                docs_service.documents.return_value.get.return_value.execute.side_effect = (
                    RuntimeError("no doc")
                )
            else:
                docs_service.documents.return_value.get.return_value.execute.return_value = document
            patches[4].stop()
            self._build_patch = patch(
                "magister_checking.row_check_cli.build",
                side_effect=[docs_service, effective_drive],
            )
            self._build_patch.start()

        def __exit__(self, *args: object) -> None:
            self._build_patch.stop()
            for p in patches[:4] + patches[5:]:
                p.stop()

    return _Manager()


class RunRowCheckTests(unittest.TestCase):
    def test_row_with_wrong_doc_marker_stops_at_stage1(self) -> None:
        user = UserForm(
            fio="Камзебаева Анель Дулатовна",
            phone="+77052107246",
            report_url="https://drive.google.com/drive/folders/magister-project",
        )
        wrong_doc = _doc_with_text("Магистерский проект: содержание")
        config = MagicMock()
        with _install_io_mocks(user=user, document=wrong_doc, parsed=_parsed()):
            report = run_row_check(config, RowLocator(row_number=2))
        self.assertEqual(report.row_number, 2)
        self.assertEqual(report.stopped_at, "stage1")
        self.assertFalse(report.stage2.executed)

    def test_row_stops_at_stage2_when_link_not_open(self) -> None:
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url="https://docs.google.com/document/d/report/edit",
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=_parsed(),
            url_probe_map={
                "https://docs.google.com/document/d/report/edit": ("yes", "no")
            },
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        self.assertEqual(report.stopped_at, "stage2")
        self.assertIn("Ссылка не открыта", report.stage2.issues)

    def test_full_stage3_with_mixed_accessibility(self) -> None:
        report_url = "https://docs.google.com/document/d/report/edit"
        diss_url = "https://docs.google.com/document/d/diss/edit"
        lkb_url = "https://drive.google.com/file/d/lkb"
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url=report_url,
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(dissertation_url=diss_url, lkb_url=lkb_url)
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
            url_probe_map={
                report_url: ("yes", "yes"),
                diss_url: ("yes", "yes"),
                lkb_url: ("yes", "no"),
            },
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        self.assertIsNone(report.stopped_at)
        self.assertTrue(report.stage3.executed)
        lkb_cell = next(
            c for c in report.stage3_cells if c.column_key == "lkb_url"
        )
        self.assertTrue(lkb_cell.strikethrough)
        self.assertIn("Ссылка на заключение ЛКБ не открывается", report.stage3.issues)
        self.assertIn("Ссылка на магистерский проект отсутствует", report.stage3.issues)

    def test_fio_locator_resolves_single_row(self) -> None:
        user = UserForm(
            fio="Иванов И.И.",
            phone="+77052100000",
            report_url="",
        )
        config = MagicMock()
        with _install_io_mocks(
            user=user, document=None, parsed=None, matched_rows=[5]
        ):
            report = run_row_check(config, RowLocator(fio="Иванов И.И."))
        self.assertEqual(report.row_number, 5)

    def test_stage4_native_doc_metrics_passed_into_pipeline(self) -> None:
        """Если диссертация — google_doc, в пайплайн идут метрики и пишутся в лист."""
        report_url = "https://docs.google.com/document/d/report/edit"
        diss_url = "https://docs.google.com/document/d/diss-id/edit"
        user = UserForm(
            fio="Гизатова И.В.",
            phone="+77052107777",
            report_url=report_url,
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(dissertation_url=diss_url)
        metrics = _make_metrics(
            approx_pages=80,
            sources_count=35,
            formatting_compliance=True,
        )
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
            url_probe_map={report_url: ("yes", "yes"), diss_url: ("yes", "yes")},
        ), patch(
            "magister_checking.row_check_cli._try_load_dissertation_metrics",
            return_value=metrics,
        ) as load_metrics:
            report = run_row_check(config, RowLocator(row_number=3), apply=False)

        load_metrics.assert_called_once()
        kwargs = load_metrics.call_args.kwargs
        self.assertEqual(kwargs["dissertation_url"], diss_url)

        self.assertTrue(report.stage4.executed)
        self.assertEqual(report.stage4.pages_total, 80)
        self.assertEqual(report.stage4.sources_count, 35)
        self.assertIs(report.stage4.compliance, True)

        cells = {c.column_key: c.value for c in report.stage4_cells}
        self.assertEqual(cells["pages_total"], "80")
        self.assertEqual(cells["sources_count"], "35")
        self.assertEqual(cells["compliance"], "соответствует")

    def test_stage4_skipped_when_metrics_load_returns_none(self) -> None:
        """Загрузка не удалась → метрики None, Stage 4 skipped, в лист ничего."""
        report_url = "https://docs.google.com/document/d/report/edit"
        diss_url = "https://docs.google.com/document/d/diss-id/edit"
        user = UserForm(fio="X Y", phone="+7", report_url=report_url)
        parsed = _parsed(dissertation_url=diss_url)
        good_doc = _doc_with_text("Промежуточный отчёт")
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
            url_probe_map={report_url: ("yes", "yes"), diss_url: ("yes", "yes")},
        ), patch(
            "magister_checking.row_check_cli._try_load_dissertation_metrics",
            return_value=None,
        ):
            report = run_row_check(config, RowLocator(row_number=3))

        self.assertFalse(report.stage4.executed)
        self.assertEqual(report.stage4_cells, [])
        self.assertIsNotNone(report.stage4.skipped_reason)

    def test_stage4_not_invoked_when_dissertation_is_folder(self) -> None:
        """Stage 3 hard-fail (диссертация-папка) → метрики не грузятся."""
        report_url = "https://docs.google.com/document/d/report/edit"
        diss_url = "https://drive.google.com/drive/folders/diss-folder"
        user = UserForm(fio="Макишева", phone="+7", report_url=report_url)
        parsed = _parsed(dissertation_url=diss_url)
        good_doc = _doc_with_text("Промежуточный отчёт")
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
            url_probe_map={report_url: ("yes", "yes"), diss_url: ("yes", "yes")},
        ), patch(
            "magister_checking.row_check_cli._try_load_dissertation_metrics"
        ) as load_metrics:
            report = run_row_check(config, RowLocator(row_number=9))

        load_metrics.assert_not_called()
        self.assertFalse(report.stage4.executed)

    def test_format_report_includes_links_block(self) -> None:
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url="https://docs.google.com/document/d/report/edit",
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        text = format_report(report)
        self.assertIn("Магистрант: Гизатова Ирина Владимировна", text)
        self.assertIn("Извлечённые ссылки", text)
        self.assertIn("dissertation_url: https://docs.google.com/document/d/diss", text)


class TryLoadDissertationMetricsTests(unittest.TestCase):
    """Прямые тесты IO-помощника Stage 4 (без всего остального пайплайна)."""

    def _metrics(self) -> DissertationMetrics:
        return _make_metrics(
            approx_pages=70,
            sources_count=30,
            formatting_compliance=True,
        )

    def test_native_google_doc_path_calls_docs_api_and_analyzer(self) -> None:
        diss_url = "https://docs.google.com/document/d/diss-id/edit"
        docs_service = MagicMock()
        doc_payload = {"body": {"content": []}}
        docs_service.documents.return_value.get.return_value.execute.return_value = (
            doc_payload
        )
        drive_service = MagicMock()
        with patch(
            "magister_checking.row_check_cli.analyze_dissertation",
            return_value=self._metrics(),
        ) as analyze:
            result = _try_load_dissertation_metrics(
                dissertation_url=diss_url,
                docs_service=docs_service,
                drive_service=drive_service,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.approx_pages, 70)
        docs_service.documents.return_value.get.assert_called_with(
            documentId="diss-id"
        )
        analyze.assert_called_once_with(doc_payload)

    def test_native_google_doc_api_error_returns_none(self) -> None:
        diss_url = "https://docs.google.com/document/d/diss-id/edit"
        docs_service = MagicMock()
        docs_service.documents.return_value.get.return_value.execute.side_effect = (
            RuntimeError("403")
        )
        drive_service = MagicMock()
        result = _try_load_dissertation_metrics(
            dissertation_url=diss_url,
            docs_service=docs_service,
            drive_service=drive_service,
        )
        self.assertIsNone(result)

    def test_drive_file_docx_path_downloads_and_analyzes(self) -> None:
        diss_url = "https://drive.google.com/file/d/diss-docx-id/view"
        docs_service = MagicMock()
        drive_service = MagicMock()
        with patch(
            "magister_checking.row_check_cli._download_drive_file_bytes_all_drives",
            return_value=b"PK\x03\x04docx-bytes",
        ) as dl, patch(
            "magister_checking.row_check_cli.analyze_docx_bytes",
            return_value=self._metrics(),
        ) as analyze:
            result = _try_load_dissertation_metrics(
                dissertation_url=diss_url,
                docs_service=docs_service,
                drive_service=drive_service,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.sources_count, 30)
        dl.assert_called_once_with(
            drive_service=drive_service, file_id="diss-docx-id"
        )
        analyze.assert_called_once()

    def test_drive_file_download_failure_returns_none(self) -> None:
        diss_url = "https://drive.google.com/file/d/diss-docx-id/view"
        with patch(
            "magister_checking.row_check_cli._download_drive_file_bytes_all_drives",
            return_value=None,
        ), patch(
            "magister_checking.row_check_cli.analyze_docx_bytes"
        ) as analyze:
            result = _try_load_dissertation_metrics(
                dissertation_url=diss_url,
                docs_service=MagicMock(),
                drive_service=MagicMock(),
            )
        self.assertIsNone(result)
        analyze.assert_not_called()

    def test_drive_file_analyzer_failure_returns_none(self) -> None:
        diss_url = "https://drive.google.com/file/d/diss-docx-id/view"
        with patch(
            "magister_checking.row_check_cli._download_drive_file_bytes_all_drives",
            return_value=b"corrupted",
        ), patch(
            "magister_checking.row_check_cli.analyze_docx_bytes",
            side_effect=ValueError("not a zip"),
        ):
            result = _try_load_dissertation_metrics(
                dissertation_url=diss_url,
                docs_service=MagicMock(),
                drive_service=MagicMock(),
            )
        self.assertIsNone(result)

    def test_unsupported_kind_returns_none(self) -> None:
        result = _try_load_dissertation_metrics(
            dissertation_url="https://drive.google.com/drive/folders/folder-id",
            docs_service=MagicMock(),
            drive_service=MagicMock(),
        )
        self.assertIsNone(result)

    def test_empty_url_returns_none(self) -> None:
        result = _try_load_dissertation_metrics(
            dissertation_url="",
            docs_service=MagicMock(),
            drive_service=MagicMock(),
        )
        self.assertIsNone(result)


class ComputeRecheckFingerprintTests(unittest.TestCase):
    """Стабильность fingerprint: одинаковые входы → одинаковый sha256."""

    def test_same_inputs_produce_same_fingerprint(self) -> None:
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
            lkb_url="https://drive.google.com/file/d/lkb",
        )
        f1 = _compute_recheck_fingerprint(
            report_url="https://docs.google.com/document/d/r",
            report_modified_time="2026-04-25T05:00:00Z",
            parsed=parsed,
        )
        f2 = _compute_recheck_fingerprint(
            report_url="https://docs.google.com/document/d/r",
            report_modified_time="2026-04-25T05:00:00Z",
            parsed=parsed,
        )
        self.assertEqual(f1, f2)
        self.assertEqual(len(f1), 64)

    def test_modified_time_change_changes_fingerprint(self) -> None:
        parsed = _parsed()
        f1 = _compute_recheck_fingerprint(
            report_url="u", report_modified_time="t1", parsed=parsed
        )
        f2 = _compute_recheck_fingerprint(
            report_url="u", report_modified_time="t2", parsed=parsed
        )
        self.assertNotEqual(f1, f2)

    def test_stage3_url_change_changes_fingerprint(self) -> None:
        f1 = _compute_recheck_fingerprint(
            report_url="u",
            report_modified_time="t",
            parsed=_parsed(dissertation_url="https://docs.google.com/document/d/A"),
        )
        f2 = _compute_recheck_fingerprint(
            report_url="u",
            report_modified_time="t",
            parsed=_parsed(dissertation_url="https://docs.google.com/document/d/B"),
        )
        self.assertNotEqual(f1, f2)

    def test_parsed_none_does_not_raise(self) -> None:
        f = _compute_recheck_fingerprint(
            report_url="u", report_modified_time="t", parsed=None
        )
        self.assertEqual(len(f), 64)


class BuildHistoryEntryTests(unittest.TestCase):
    """Сборка RecheckHistoryEntry из RowCheckReport."""

    def test_builds_entry_with_passed_yes_when_no_issues(self) -> None:
        report = RowCheckReport(fio="X Y", row_number=7)
        entry = _build_history_entry(
            report=report, source="cli", fingerprint="fp123"
        )
        self.assertIsInstance(entry, RecheckHistoryEntry)
        self.assertEqual(entry.row_number, 7)
        self.assertEqual(entry.fio, "X Y")
        self.assertEqual(entry.source, "cli")
        self.assertEqual(entry.passed, "yes")
        self.assertEqual(entry.fingerprint, "fp123")

    def test_builds_entry_with_passed_no_and_issues_joined(self) -> None:
        report = RowCheckReport(fio="X Y", row_number=7)
        report.stage1.issues.append("проблема А")
        report.stage3.issues.append("проблема Б")
        entry = _build_history_entry(report=report, source="bot", fingerprint="fp")
        self.assertEqual(entry.passed, "no")
        self.assertIn("проблема А", entry.issues)
        self.assertIn("проблема Б", entry.issues)
        self.assertEqual(entry.source, "bot")


class OnlyIfChangedTests(unittest.TestCase):
    """Поведение run_row_check(only_if_changed=True) с историей."""

    def _user(self) -> UserForm:
        return UserForm(
            fio="Гизатова И.В.",
            phone="+7",
            report_url="https://docs.google.com/document/d/report/edit",
        )

    def test_short_circuits_when_fingerprint_matches_last_history(self) -> None:
        user = self._user()
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()

        # Первый прогон — берём fingerprint из реального вычисления.
        with _install_io_mocks(user=user, document=good_doc, parsed=parsed):
            first = run_row_check(config, RowLocator(row_number=3))
        self.assertFalse(first.unchanged)

        last_entry = RecheckHistoryEntry(
            timestamp="2026-04-25 05:00:00",
            row_number=3,
            fio=user.fio,
            source="bot",
            fingerprint="UNKNOWN",
        )

        # Подменяем fingerprint, который вернёт _compute_recheck_fingerprint,
        # на тот же, что и в last_entry — чтобы сработало короткое замыкание.
        with _install_io_mocks(user=user, document=good_doc, parsed=parsed), patch(
            "magister_checking.row_check_cli._compute_recheck_fingerprint",
            return_value="MATCHING_FP",
        ), patch(
            "magister_checking.row_check_cli.read_last_recheck_entry",
            return_value=RecheckHistoryEntry(
                timestamp="t", row_number=3, fingerprint="MATCHING_FP"
            ),
        ), patch(
            "magister_checking.row_check_cli.run_row_pipeline"
        ) as run_pipeline, patch(
            "magister_checking.row_check_cli.apply_row_check_updates"
        ) as apply_updates, patch(
            "magister_checking.row_check_cli.append_recheck_history"
        ) as append_history:
            second = run_row_check(
                config,
                RowLocator(row_number=3),
                apply=True,
                only_if_changed=True,
            )

        self.assertTrue(second.unchanged)
        self.assertEqual(second.row_number, 3)
        run_pipeline.assert_not_called()
        apply_updates.assert_not_called()
        append_history.assert_not_called()

    def test_runs_pipeline_when_fingerprint_differs(self) -> None:
        user = self._user()
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()

        with _install_io_mocks(user=user, document=good_doc, parsed=parsed), patch(
            "magister_checking.row_check_cli._compute_recheck_fingerprint",
            return_value="NEW_FP",
        ), patch(
            "magister_checking.row_check_cli.read_last_recheck_entry",
            return_value=RecheckHistoryEntry(
                timestamp="t", row_number=3, fingerprint="OLD_FP"
            ),
        ):
            report = run_row_check(
                config,
                RowLocator(row_number=3),
                only_if_changed=True,
            )

        self.assertFalse(report.unchanged)
        self.assertTrue(report.stage3.executed)

    def test_apply_writes_history_with_source(self) -> None:
        user = self._user()
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()

        with _install_io_mocks(user=user, document=good_doc, parsed=parsed), patch(
            "magister_checking.row_check_cli.apply_row_check_updates"
        ), patch(
            "magister_checking.row_check_cli.append_recheck_history"
        ) as append_history:
            run_row_check(
                config,
                RowLocator(row_number=3),
                apply=True,
                history_source="bot",
            )

        append_history.assert_called_once()
        spreadsheet_arg, entry_arg = append_history.call_args.args
        self.assertEqual(entry_arg.source, "bot")
        self.assertEqual(entry_arg.row_number, 3)
        self.assertEqual(len(entry_arg.fingerprint), 64)

    def test_history_failure_is_swallowed(self) -> None:
        user = self._user()
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()

        with _install_io_mocks(user=user, document=good_doc, parsed=parsed), patch(
            "magister_checking.row_check_cli.apply_row_check_updates"
        ), patch(
            "magister_checking.row_check_cli.append_recheck_history",
            side_effect=RuntimeError("history boom"),
        ):
            # Не должно бросать — история вспомогательна.
            report = run_row_check(
                config,
                RowLocator(row_number=3),
                apply=True,
                history_source="cli",
            )
        self.assertEqual(report.row_number, 3)


class CliCheckRowFlagsTests(unittest.TestCase):
    """Парсинг argparse и проброс флагов в run_row_check."""

    def _run_cmd(self, argv: list[str]) -> tuple[int, MagicMock]:
        from magister_checking import cli

        fake_report = RowCheckReport(fio="X Y", row_number=4)

        with patch(
            "magister_checking.bot.config.load_config", return_value=MagicMock()
        ), patch(
            "magister_checking.row_check_cli.run_row_check",
            return_value=fake_report,
        ) as run:
            code = cli.main(argv)
        return code, run

    def test_only_if_changed_propagates_to_run_row_check(self) -> None:
        code, run = self._run_cmd(
            ["check-row", "--row", "4", "--only-if-changed"]
        )
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs["only_if_changed"])
        self.assertEqual(run.call_args.kwargs["history_source"], "cli")
        self.assertFalse(run.call_args.kwargs["apply"])

    def test_default_check_row_does_not_pass_only_if_changed(self) -> None:
        code, run = self._run_cmd(["check-row", "--row", "4"])
        self.assertEqual(code, 0)
        self.assertFalse(run.call_args.kwargs["only_if_changed"])

    def test_apply_flag_combines_with_only_if_changed(self) -> None:
        code, run = self._run_cmd(
            ["check-row", "--row", "4", "--apply", "--only-if-changed"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(run.call_args.kwargs["apply"])
        self.assertTrue(run.call_args.kwargs["only_if_changed"])


class FormatReportUnchangedTests(unittest.TestCase):
    def test_unchanged_report_renders_short_message(self) -> None:
        report = RowCheckReport(fio="Иванов И.И.", row_number=5, unchanged=True)
        text = format_report(report, applied=False)
        self.assertIn("Иванов И.И.", text)
        self.assertIn("Строка: 5", text)
        self.assertIn("--only-if-changed", text)
        self.assertIn("не тронуты", text)
        self.assertNotIn("Извлечённые ссылки", text)
        self.assertNotIn("dry-run", text)


if __name__ == "__main__":
    unittest.main()
