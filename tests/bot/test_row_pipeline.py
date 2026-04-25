"""Юнит-тесты чистого конвейера проверки строки."""

from __future__ import annotations

import unittest

from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    COMPLIANCE_TEXT_NO,
    COMPLIANCE_TEXT_UNKNOWN,
    COMPLIANCE_TEXT_YES,
    DOCX_MIME,
    LINK_MISSING_VALUE,
    PDF_MIME,
    Stage4CellUpdate,
    build_stage4_cells,
    compliance_to_text,
    run_row_pipeline,
    run_stage2,
    run_stage3,
    run_stage4,
)
from magister_checking.bot.validation import (
    FIO_INVALID_MESSAGE,
    PHONE_INVALID_MESSAGE,
    REPORT_URL_WRONG_TARGET_MESSAGE,
)
from magister_checking.dissertation_metrics import DissertationMetrics
from magister_checking.report_parser import ParsedReport


VALID_FIO = "Камзебаева Анель Дулатовна"
VALID_PHONE = "+77052107246"
REPORT_URL = "https://docs.google.com/document/d/report/edit"


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


class Stage2Tests(unittest.TestCase):
    def test_accessible_url_passes(self) -> None:
        result = run_stage2(report_url=REPORT_URL, url_probe=("yes", "yes"))
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])

    def test_invalid_url_format_fails(self) -> None:
        result = run_stage2(report_url="not-a-url", url_probe=("no", "no"))
        self.assertFalse(result.passed)
        self.assertEqual(result.issues, ["Ссылка не открыта"])

    def test_valid_url_but_not_accessible_fails(self) -> None:
        result = run_stage2(report_url=REPORT_URL, url_probe=("yes", "no"))
        self.assertFalse(result.passed)
        self.assertEqual(result.issues, ["Ссылка не открыта"])

    def test_empty_url_specific_message(self) -> None:
        result = run_stage2(report_url="", url_probe=("", ""))
        self.assertFalse(result.passed)
        self.assertEqual(
            result.issues, ["Ссылка на промежуточный отчёт отсутствует"]
        )


class Stage3Tests(unittest.TestCase):
    def test_all_links_present_and_accessible(self) -> None:
        parsed = _parsed(
            lkb_url="https://drive.google.com/file/d/lkb/view",
            dissertation_url="https://docs.google.com/document/d/diss/edit",
            project_folder_url="https://drive.google.com/drive/folders/proj",
            publication_url="https://drive.google.com/file/d/pub/view",
        )
        result, cells = run_stage3(parsed=parsed)
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])
        cells_map = {c.column_key: c for c in cells}
        self.assertEqual(
            cells_map["project_folder_url"].value,
            "https://drive.google.com/drive/folders/proj",
        )
        self.assertEqual(
            cells_map["lkb_url"].value, "https://drive.google.com/file/d/lkb/view"
        )
        self.assertEqual(
            cells_map["dissertation_url"].value,
            "https://docs.google.com/document/d/diss/edit",
        )
        self.assertEqual(
            cells_map["publication_url"].value,
            "https://drive.google.com/file/d/pub/view",
        )
        for cell in cells:
            self.assertFalse(cell.strikethrough, msg=cell.column_key)

    def test_missing_links_become_no_and_reported(self) -> None:
        parsed = _parsed()
        result, cells = run_stage3(parsed=parsed)
        self.assertFalse(result.passed)
        cells_map = {c.column_key: c for c in cells}
        for key in (
            "project_folder_url",
            "lkb_url",
            "dissertation_url",
            "publication_url",
        ):
            self.assertEqual(cells_map[key].value, LINK_MISSING_VALUE)
            self.assertFalse(cells_map[key].strikethrough)
        self.assertIn("Ссылка на диссертацию отсутствует", result.issues)
        self.assertIn("Ссылка на магистерский проект отсутствует", result.issues)
        self.assertIn("Ссылка на заключение ЛКБ отсутствует", result.issues)
        self.assertIn("Ссылка на публикацию отсутствует", result.issues)

    def test_unreachable_link_marked_strikethrough(self) -> None:
        diss_url = "https://docs.google.com/document/d/diss/edit"
        lkb_url = "https://drive.google.com/file/d/lkb/view"
        parsed = _parsed(dissertation_url=diss_url, lkb_url=lkb_url)
        accessibility = {diss_url: True, lkb_url: False}
        result, cells = run_stage3(parsed=parsed, accessibility=accessibility)
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["lkb_url"].strikethrough)
        self.assertFalse(cells_map["dissertation_url"].strikethrough)
        self.assertIn("Ссылка на заключение ЛКБ не открывается", result.issues)

    def test_passed_requires_accessible_dissertation(self) -> None:
        diss_url = "https://docs.google.com/document/d/diss/edit"
        parsed = _parsed(dissertation_url=diss_url)
        result_ok, _ = run_stage3(parsed=parsed, accessibility={diss_url: True})
        self.assertTrue(result_ok.passed)
        result_bad, _ = run_stage3(parsed=parsed, accessibility={diss_url: False})
        self.assertFalse(result_bad.passed)

    def test_publication_prefers_results_over_review(self) -> None:
        """Источник для колонки публикации: при отсутствии явного
        publication_url берётся results_article_url (а review_article_url —
        последний fallback). Тест семантики выбора, не валидации типа."""
        parsed = _parsed(
            results_article_url="https://drive.google.com/file/d/results/view",
            review_article_url="https://drive.google.com/file/d/review/view",
        )
        _, cells = run_stage3(parsed=parsed)
        pub = next(c for c in cells if c.column_key == "publication_url")
        self.assertEqual(pub.value, "https://drive.google.com/file/d/results/view")

    def test_publication_prefers_explicit_publication_url(self) -> None:
        """parsed.publication_url (новый источник «Публикации: ...») имеет
        приоритет над legacy results_article_url / review_article_url."""
        parsed = _parsed(
            publication_url="https://drive.google.com/file/d/pub/view",
            results_article_url="https://drive.google.com/file/d/results/view",
            review_article_url="https://drive.google.com/file/d/review/view",
        )
        _, cells = run_stage3(parsed=parsed)
        pub = next(c for c in cells if c.column_key == "publication_url")
        self.assertEqual(pub.value, "https://drive.google.com/file/d/pub/view")


class Stage3TypePolicyTests(unittest.TestCase):
    """Семантическая валидация типа ссылки в Stage 3.

    Политика по полям (см. _FIELD_POLICIES в bot/row_pipeline.py):
      project_folder_url — только folder, soft fail;
      lkb_url            — только file + PDF, soft fail;
      dissertation_url   — Doc или file + DOCX, **hard fail** (Stage 3
                            не passed);
      publication_url    — только file + PDF, soft fail.

    «Soft fail» означает: ячейка зачёркивается, в issues есть warning,
    но Stage 3 продолжает считаться passed (если только диссертация в
    порядке). «Hard fail» — Stage 3 passed=False.
    """

    DOC_DISS = "https://docs.google.com/document/d/diss/edit"
    FOLDER_PROJ = "https://drive.google.com/drive/folders/proj"
    FILE_LKB = "https://drive.google.com/file/d/lkb/view"
    FILE_PUB = "https://drive.google.com/file/d/pub/view"

    def _good_parsed(self, **overrides: object) -> ParsedReport:
        base: dict[str, object] = {
            "project_folder_url": self.FOLDER_PROJ,
            "lkb_url": self.FILE_LKB,
            "dissertation_url": self.DOC_DISS,
            "publication_url": self.FILE_PUB,
        }
        base.update(overrides)
        return _parsed(**base)

    def _good_mimes(self) -> dict[str, str]:
        return {self.FILE_LKB: PDF_MIME, self.FILE_PUB: PDF_MIME}

    def test_baseline_all_correct_passes(self) -> None:
        result, cells = run_stage3(
            parsed=self._good_parsed(), link_mime_types=self._good_mimes()
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])
        for cell in cells:
            self.assertFalse(cell.strikethrough, msg=cell.column_key)

    def test_project_folder_as_doc_url_soft_fails(self) -> None:
        """project_folder = Google Doc URL → strike + warning, но Stage 3 ок."""
        parsed = self._good_parsed(project_folder_url=self.DOC_DISS)
        result, cells = run_stage3(parsed=parsed, link_mime_types=self._good_mimes())
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["project_folder_url"].strikethrough)
        self.assertEqual(cells_map["project_folder_url"].value, self.DOC_DISS)
        self.assertTrue(any("магистерский проект" in i for i in result.issues))
        self.assertTrue(result.passed)

    def test_lkb_as_folder_soft_fails(self) -> None:
        parsed = self._good_parsed(lkb_url=self.FOLDER_PROJ)
        result, cells = run_stage3(parsed=parsed, link_mime_types=self._good_mimes())
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["lkb_url"].strikethrough)
        self.assertTrue(any("заключение ЛКБ" in i for i in result.issues))
        self.assertTrue(result.passed)

    def test_lkb_file_with_wrong_mime_soft_fails(self) -> None:
        """ЛКБ ведёт на drive_file, но это не PDF — strike + warning, Stage 3 ок."""
        parsed = self._good_parsed()
        mimes = self._good_mimes()
        mimes[self.FILE_LKB] = "image/jpeg"
        result, cells = run_stage3(parsed=parsed, link_mime_types=mimes)
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["lkb_url"].strikethrough)
        self.assertTrue(
            any("image/jpeg" in i and "заключение ЛКБ" in i for i in result.issues)
        )
        self.assertTrue(result.passed)

    def test_publication_as_doc_url_soft_fails(self) -> None:
        parsed = self._good_parsed(publication_url=self.DOC_DISS)
        result, cells = run_stage3(parsed=parsed, link_mime_types=self._good_mimes())
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["publication_url"].strikethrough)
        self.assertTrue(any("публикацию" in i for i in result.issues))
        self.assertTrue(result.passed)

    def test_publication_file_without_mime_soft_fails(self) -> None:
        """Publication = drive_file, но MIME неизвестен (нет в карте) →
        strike + «не удалось определить формат». Stage 3 не падает."""
        parsed = self._good_parsed()
        # Только LKB mime известен; для FILE_PUB mime неизвестен.
        mimes = {self.FILE_LKB: PDF_MIME}
        result, cells = run_stage3(parsed=parsed, link_mime_types=mimes)
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["publication_url"].strikethrough)
        self.assertTrue(
            any("определить формат" in i and "публикацию" in i for i in result.issues)
        )
        self.assertTrue(result.passed)

    def test_dissertation_as_folder_hard_fails(self) -> None:
        """diss = folder → strike + warning + Stage 3 НЕ passed."""
        parsed = self._good_parsed(dissertation_url=self.FOLDER_PROJ)
        result, cells = run_stage3(parsed=parsed, link_mime_types=self._good_mimes())
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["dissertation_url"].strikethrough)
        self.assertTrue(any("диссертацию" in i for i in result.issues))
        self.assertFalse(result.passed)

    def test_dissertation_as_drive_file_with_docx_mime_passes(self) -> None:
        """Диссертация может быть .docx-файлом в Drive (помимо Google Doc)."""
        diss_file = "https://drive.google.com/file/d/diss/view"
        parsed = self._good_parsed(dissertation_url=diss_file)
        mimes = self._good_mimes()
        mimes[diss_file] = DOCX_MIME
        result, cells = run_stage3(parsed=parsed, link_mime_types=mimes)
        cells_map = {c.column_key: c for c in cells}
        self.assertFalse(cells_map["dissertation_url"].strikethrough)
        self.assertTrue(result.passed)

    def test_dissertation_as_drive_file_with_pdf_mime_hard_fails(self) -> None:
        """drive_file у диссертации, но MIME=PDF (не DOCX) → hard fail."""
        diss_file = "https://drive.google.com/file/d/diss/view"
        parsed = self._good_parsed(dissertation_url=diss_file)
        mimes = self._good_mimes()
        mimes[diss_file] = PDF_MIME
        result, cells = run_stage3(parsed=parsed, link_mime_types=mimes)
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["dissertation_url"].strikethrough)
        self.assertFalse(result.passed)

    def test_dissertation_as_drive_file_without_mime_hard_fails(self) -> None:
        """drive_file у диссертации, mime карта пуста → не удалось
        определить формат, hard fail (без формата ответственно сказать
        «это .docx» нельзя)."""
        diss_file = "https://drive.google.com/file/d/diss/view"
        parsed = self._good_parsed(dissertation_url=diss_file)
        result, cells = run_stage3(parsed=parsed, link_mime_types={})
        cells_map = {c.column_key: c for c in cells}
        self.assertTrue(cells_map["dissertation_url"].strikethrough)
        self.assertFalse(result.passed)

    def test_no_mime_check_when_link_mime_types_is_none(self) -> None:
        """Если caller не делал MIME prefetch (None), drive_file для
        ЛКБ/публикации не считается ошибкой — это режим «без сети»
        (тесты, dry-run без Drive API)."""
        parsed = self._good_parsed()
        result, cells = run_stage3(parsed=parsed, link_mime_types=None)
        for cell in cells:
            self.assertFalse(cell.strikethrough, msg=cell.column_key)
        self.assertEqual(result.issues, [])
        self.assertTrue(result.passed)


def _metrics(
    *,
    pdf_pages: int | None = None,
    approx_pages: int = 87,
    sources_count: int | None = 42,
    formatting_compliance: bool | None = True,
    font_size_14_ratio: float | None = 1.0,
    times_new_roman_ratio: float | None = 1.0,
    single_spacing_ratio: float | None = 1.0,
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
        font_size_14_ratio=font_size_14_ratio,
        times_new_roman_ratio=times_new_roman_ratio,
        single_spacing_ratio=single_spacing_ratio,
    )


class Stage4Tests(unittest.TestCase):
    """Unit-тесты содержательного разбора диссертации (handoff §3-§4)."""

    def test_skipped_when_metrics_none(self) -> None:
        result = run_stage4(dissertation_metrics=None)
        self.assertFalse(result.executed)
        self.assertFalse(result.passed)
        self.assertEqual(result.skipped_reason, "не удалось получить метрики диссертации")
        self.assertEqual(result.issues, [])
        self.assertIsNone(result.pages_total)
        self.assertIsNone(result.sources_count)
        self.assertIsNone(result.compliance)
        self.assertEqual(build_stage4_cells(result), [])

    def test_full_pass_with_compliant_dissertation(self) -> None:
        m = _metrics(approx_pages=87, sources_count=42, formatting_compliance=True)
        result = run_stage4(dissertation_metrics=m)
        self.assertTrue(result.executed)
        self.assertTrue(result.passed)
        self.assertIsNone(result.skipped_reason)
        self.assertEqual(result.pages_total, 87)
        self.assertEqual(result.sources_count, 42)
        self.assertTrue(result.compliance)
        self.assertEqual(result.issues, [])

    def test_pdf_pages_preferred_over_approx(self) -> None:
        m = _metrics(pdf_pages=120, approx_pages=87)
        result = run_stage4(dissertation_metrics=m)
        self.assertEqual(result.pages_total, 120)

    def test_compliance_false_warning_with_details_passed_remains_true(self) -> None:
        """handoff §8.3 + §8.5: warning + детали в скобках, passed=True."""
        m = _metrics(
            formatting_compliance=False,
            times_new_roman_ratio=0.87,
            font_size_14_ratio=0.92,
            single_spacing_ratio=0.80,
        )
        result = run_stage4(dissertation_metrics=m)
        self.assertTrue(result.executed)
        self.assertTrue(result.passed)
        self.assertFalse(result.compliance)
        self.assertEqual(len(result.issues), 1)
        msg = result.issues[0]
        self.assertIn("оформление не соответствует требованиям", msg)
        self.assertIn("TNR 87%", msg)
        self.assertIn("14pt 92%", msg)
        self.assertIn("single 80%", msg)

    def test_compliance_unknown_no_issue(self) -> None:
        """formatting_compliance=None → ни warning, ни passed=False."""
        m = _metrics(
            formatting_compliance=None,
            times_new_roman_ratio=None,
            font_size_14_ratio=None,
            single_spacing_ratio=None,
        )
        result = run_stage4(dissertation_metrics=m)
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])
        cells = {c.column_key: c.value for c in build_stage4_cells(result)}
        self.assertEqual(cells["compliance"], COMPLIANCE_TEXT_UNKNOWN)

    def test_compliance_text_mapping(self) -> None:
        self.assertEqual(compliance_to_text(True), COMPLIANCE_TEXT_YES)
        self.assertEqual(compliance_to_text(False), COMPLIANCE_TEXT_NO)
        self.assertEqual(compliance_to_text(None), COMPLIANCE_TEXT_UNKNOWN)

    def test_build_stage4_cells_full(self) -> None:
        m = _metrics(approx_pages=87, sources_count=42, formatting_compliance=True)
        result = run_stage4(dissertation_metrics=m)
        cells = build_stage4_cells(result)
        self.assertEqual(
            cells,
            [
                Stage4CellUpdate(column_key="pages_total", value="87"),
                Stage4CellUpdate(column_key="sources_count", value="42"),
                Stage4CellUpdate(column_key="compliance", value=COMPLIANCE_TEXT_YES),
            ],
        )

    def test_build_stage4_cells_with_missing_numerics(self) -> None:
        """Если pages/sources None — пишем пустую строку, не ломаемся."""
        m = _metrics(
            approx_pages=0,  # _resolve_pages_total → None
            sources_count=None,
            formatting_compliance=False,
            times_new_roman_ratio=None,
            font_size_14_ratio=None,
            single_spacing_ratio=None,
        )
        result = run_stage4(dissertation_metrics=m)
        cells = {c.column_key: c.value for c in build_stage4_cells(result)}
        self.assertEqual(cells["pages_total"], "")
        self.assertEqual(cells["sources_count"], "")
        self.assertEqual(cells["compliance"], COMPLIANCE_TEXT_NO)


class RunRowPipelineTests(unittest.TestCase):
    def test_stops_at_stage1_when_report_document_has_no_marker(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        wrong_doc = _doc_with_text("Магистерский проект: оглавление")
        report = run_row_pipeline(
            form,
            report_document=wrong_doc,
            url_probe=("yes", "yes"),
            parsed_report=_parsed(),
            row_number=2,
        )
        self.assertEqual(report.stopped_at, "stage1")
        self.assertIn(REPORT_URL_WRONG_TARGET_MESSAGE, report.stage1.issues)
        self.assertFalse(report.stage2.executed)
        self.assertFalse(report.stage3.executed)

    def test_stops_at_stage2_when_link_not_accessible(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт магистранта")
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "no"),
            parsed_report=_parsed(),
            row_number=3,
        )
        self.assertEqual(report.stopped_at, "stage2")
        self.assertIn("Ссылка не открыта", report.stage2.issues)
        self.assertFalse(report.stage3.executed)

    def test_full_pass_with_all_links(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт")
        diss = "https://docs.google.com/document/d/diss/edit"
        parsed = _parsed(
            dissertation_url=diss,
            lkb_url="https://drive.google.com/file/d/lkb/view",
            project_folder_url="https://drive.google.com/drive/folders/proj",
            publication_url="https://drive.google.com/file/d/pub/view",
        )
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=parsed,
        )
        self.assertIsNone(report.stopped_at)
        self.assertTrue(report.stage1.passed)
        self.assertTrue(report.stage2.passed)
        self.assertTrue(report.stage3.passed)
        self.assertEqual(report.all_issues(), [])

    def test_fio_and_phone_errors_do_not_block_later_stages(self) -> None:
        form = UserForm(
            fio="ТОО Viamedis Kosshy", phone="abc", report_url=REPORT_URL
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=_parsed(
                dissertation_url="https://docs.google.com/document/d/diss/edit"
            ),
        )
        self.assertIn(FIO_INVALID_MESSAGE, report.stage1.issues)
        self.assertIn(PHONE_INVALID_MESSAGE, report.stage1.issues)
        self.assertTrue(report.stage2.executed)
        self.assertTrue(report.stage3.executed)
        self.assertIsNone(report.stopped_at)

    def test_stage4_runs_when_stage3_passes_and_metrics_provided(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт")
        diss = "https://docs.google.com/document/d/diss/edit"
        parsed = _parsed(
            dissertation_url=diss,
            lkb_url="https://drive.google.com/file/d/lkb/view",
            project_folder_url="https://drive.google.com/drive/folders/proj",
            publication_url="https://drive.google.com/file/d/pub/view",
        )
        metrics = _metrics(approx_pages=100, sources_count=50, formatting_compliance=True)
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=parsed,
            dissertation_metrics=metrics,
        )
        self.assertIsNone(report.stopped_at)
        self.assertTrue(report.stage3.passed)
        self.assertTrue(report.stage4.executed)
        self.assertTrue(report.stage4.passed)
        self.assertEqual(report.stage4.pages_total, 100)
        self.assertEqual(report.stage4.sources_count, 50)
        cells = {c.column_key: c.value for c in report.stage4_cells}
        self.assertEqual(cells["pages_total"], "100")
        self.assertEqual(cells["sources_count"], "50")
        self.assertEqual(cells["compliance"], COMPLIANCE_TEXT_YES)

    def test_stage4_skipped_when_stage3_hard_fails(self) -> None:
        """Диссертация = папка → Stage 3 hard fail → Stage 4 не запускается."""
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://drive.google.com/drive/folders/diss-as-folder",
            lkb_url="https://drive.google.com/file/d/lkb/view",
            project_folder_url="https://drive.google.com/drive/folders/proj",
            publication_url="https://drive.google.com/file/d/pub/view",
        )
        metrics = _metrics()  # передали бы — но Stage 4 не должна их трогать
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=parsed,
            dissertation_metrics=metrics,
            link_mime_types={
                "https://drive.google.com/file/d/lkb/view": PDF_MIME,
                "https://drive.google.com/file/d/pub/view": PDF_MIME,
            },
        )
        self.assertEqual(report.stopped_at, "stage3")
        self.assertFalse(report.stage4.executed)
        self.assertEqual(report.stage4_cells, [])

    def test_stage4_skipped_when_metrics_missing_but_stage3_passed(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт")
        diss = "https://docs.google.com/document/d/diss/edit"
        parsed = _parsed(dissertation_url=diss)
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=parsed,
            dissertation_metrics=None,
        )
        self.assertIsNone(report.stopped_at)
        self.assertTrue(report.stage3.passed)
        self.assertFalse(report.stage4.executed)
        self.assertEqual(
            report.stage4.skipped_reason, "не удалось получить метрики диссертации"
        )
        self.assertEqual(report.stage4_cells, [])

    def test_stage4_compliance_warning_in_all_issues(self) -> None:
        form = UserForm(fio=VALID_FIO, phone=VALID_PHONE, report_url=REPORT_URL)
        good_doc = _doc_with_text("Промежуточный отчёт")
        diss = "https://docs.google.com/document/d/diss/edit"
        parsed = _parsed(dissertation_url=diss)
        metrics = _metrics(
            formatting_compliance=False,
            times_new_roman_ratio=0.50,
            font_size_14_ratio=0.60,
            single_spacing_ratio=0.70,
        )
        report = run_row_pipeline(
            form,
            report_document=good_doc,
            url_probe=("yes", "yes"),
            parsed_report=parsed,
            dissertation_metrics=metrics,
        )
        self.assertTrue(report.stage4.executed)
        self.assertTrue(report.stage4.passed)  # warning, не блокирует
        self.assertTrue(
            any(
                "оформление не соответствует требованиям" in i
                for i in report.all_issues()
            )
        )

    def test_spravka_lines_formatting(self) -> None:
        form = UserForm(fio=VALID_FIO, phone="abc", report_url=REPORT_URL)
        report = run_row_pipeline(form, row_number=12)
        lines = report.spravka_lines()
        self.assertEqual(lines[0], f"Магистрант: {VALID_FIO}")
        self.assertEqual(lines[1], "Строка в листе «Регистрация»: 12")
        self.assertIn(f"- {PHONE_INVALID_MESSAGE}", lines)


if __name__ == "__main__":
    unittest.main()
