"""Юнит-тесты чистого конвейера проверки строки."""

from __future__ import annotations

import unittest

from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    DOCX_MIME,
    LINK_MISSING_VALUE,
    PDF_MIME,
    run_row_pipeline,
    run_stage2,
    run_stage3,
)
from magister_checking.bot.validation import (
    FIO_INVALID_MESSAGE,
    PHONE_INVALID_MESSAGE,
    REPORT_URL_WRONG_TARGET_MESSAGE,
)
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

    def test_spravka_lines_formatting(self) -> None:
        form = UserForm(fio=VALID_FIO, phone="abc", report_url=REPORT_URL)
        report = run_row_pipeline(form, row_number=12)
        lines = report.spravka_lines()
        self.assertEqual(lines[0], f"Магистрант: {VALID_FIO}")
        self.assertEqual(lines[1], "Строка в листе «Регистрация»: 12")
        self.assertIn(f"- {PHONE_INVALID_MESSAGE}", lines)


if __name__ == "__main__":
    unittest.main()
