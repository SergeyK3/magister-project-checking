"""Tests for report enrichment from dissertation data."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.bot.report_enrichment import (
    URL_MISSING,
    URL_UNAVAILABLE,
    build_sheet_enrichment,
)
from magister_checking.report_parser import ParsedReport


def _paragraph(
    text: str,
    *,
    font_family: str | None = None,
    font_size: float | None = None,
    line_spacing: float | None = None,
) -> dict:
    text_style: dict = {}
    if font_family:
        text_style["weightedFontFamily"] = {"fontFamily": font_family}
    if font_size is not None:
        text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
    paragraph_style: dict = {"namedStyleType": "NORMAL_TEXT"}
    if line_spacing is not None:
        paragraph_style["lineSpacing"] = line_spacing
    return {
        "paragraph": {
            "elements": [{"textRun": {"content": text, "textStyle": text_style}}],
            "paragraphStyle": paragraph_style,
        }
    }


def _dissertation_doc() -> dict:
    return {
        "namedStyles": {
            "styles": [
                {
                    "namedStyleType": "NORMAL_TEXT",
                    "textStyle": {
                        "weightedFontFamily": {"fontFamily": "Times New Roman"},
                        "fontSize": {"magnitude": 14, "unit": "PT"},
                    },
                    "paragraphStyle": {"lineSpacing": 100.0},
                }
            ]
        },
        "body": {
            "content": [
                _paragraph("Текст диссертации.\n"),
                _paragraph("Литература\n"),
                _paragraph("1. Первый источник\n"),
                _paragraph("2. Второй источник\n"),
            ]
        },
    }


class BuildSheetEnrichmentTests(unittest.TestCase):
    def test_enrichment_uses_dissertation_metrics_not_declared_report_values(self) -> None:
        config = MagicMock()
        user_form = UserForm(report_url="https://docs.google.com/document/d/report/edit")
        parsed = ParsedReport(
            lkb_status="да",
            lkb_url="https://drive.google.com/file/d/lkb/view",
            dissertation_url="https://docs.google.com/document/d/diss/edit",
            review_article_url=None,
            review_article_note="",
            results_article_url=None,
            project_folder_url="https://drive.google.com/drive/folders/project",
            declared_pages_total=999,
            declared_pages_review=111,
            declared_sources_review=777,
            declared_formatting_ok=False,
        )

        docs_service = MagicMock()
        report_req = MagicMock()
        report_req.execute.return_value = {"body": {"content": []}}
        diss_req = MagicMock()
        diss_req.execute.return_value = _dissertation_doc()
        docs_service.documents.return_value.get.side_effect = [report_req, diss_req]
        drive_service = MagicMock()

        with patch(
            "magister_checking.bot.report_enrichment._service_account_credentials",
            return_value=MagicMock(),
        ), patch(
            "magister_checking.bot.report_enrichment.build",
            side_effect=[docs_service, drive_service],
        ), patch(
            "magister_checking.bot.report_enrichment.resolve_report_google_doc_id",
            return_value="report-id",
        ), patch(
            "magister_checking.bot.report_enrichment.parse_intermediate_report",
            return_value=parsed,
        ), patch(
            "magister_checking.bot.report_enrichment.count_pdf_pages_via_drive_export",
            return_value=87,
        ):
            result = build_sheet_enrichment(config, user_form)

        self.assertEqual(result["project_folder_url"], "https://drive.google.com/drive/folders/project")
        self.assertEqual(result["lkb_url"], "https://drive.google.com/file/d/lkb/view")
        self.assertEqual(result["dissertation_url"], "https://docs.google.com/document/d/diss/edit")
        self.assertEqual(result["pages_total"], "87")
        self.assertEqual(result["sources_count"], "2")
        self.assertEqual(result["compliance"], "Соответствует")
        self.assertEqual(result["publication_url"], URL_MISSING)
        # У ``_dissertation_doc()`` нет ни «На тему», ни Heading 1 — тема пустая;
        # текст русский, поэтому язык должен определиться как «русский».
        self.assertEqual(result["dissertation_title"], "")
        self.assertEqual(result["dissertation_language"], "русский")


def _run_enrichment_with_parsed(
    parsed: ParsedReport,
    *,
    report_url: str = "https://docs.google.com/document/d/report/edit",
) -> dict[str, str]:
    config = MagicMock()
    user_form = UserForm(report_url=report_url)

    docs_service = MagicMock()
    report_req = MagicMock()
    report_req.execute.return_value = {"body": {"content": []}}
    docs_service.documents.return_value.get.return_value = report_req
    drive_service = MagicMock()

    with patch(
        "magister_checking.bot.report_enrichment._service_account_credentials",
        return_value=MagicMock(),
    ), patch(
        "magister_checking.bot.report_enrichment.build",
        side_effect=[docs_service, drive_service],
    ), patch(
        "magister_checking.bot.report_enrichment.resolve_report_google_doc_id",
        return_value="report-id",
    ), patch(
        "magister_checking.bot.report_enrichment.parse_intermediate_report",
        return_value=parsed,
    ):
        return build_sheet_enrichment(config, user_form)


def _parsed(**overrides: object) -> ParsedReport:
    defaults: dict[str, object] = {
        "lkb_status": "да",
        "lkb_url": None,
        "dissertation_url": None,
        "review_article_url": None,
        "review_article_note": "",
        "results_article_url": None,
    }
    defaults.update(overrides)
    return ParsedReport(**defaults)  # type: ignore[arg-type]


class DissertationMetaInEnrichmentTests(unittest.TestCase):
    """Поведение интеграции ``dissertation_meta`` в ``build_sheet_enrichment``.

    Проверяем оба источника темы/языка (Google Doc) и факт того, что
    результаты эвристик действительно проникают в выходной dict.
    """

    def _doc_with_topic(self, *, topic_paragraph: str, language_text: str) -> dict:
        return {
            "namedStyles": {
                "styles": [
                    {
                        "namedStyleType": "NORMAL_TEXT",
                        "textStyle": {
                            "weightedFontFamily": {"fontFamily": "Times New Roman"},
                            "fontSize": {"magnitude": 14, "unit": "PT"},
                        },
                        "paragraphStyle": {"lineSpacing": 100.0},
                    }
                ]
            },
            "body": {
                "content": [
                    _paragraph("Магистерская диссертация\n"),
                    _paragraph(topic_paragraph + "\n"),
                    _paragraph(language_text + "\n"),
                ]
            },
        }

    def _run(self, diss_doc: dict) -> dict[str, str]:
        config = MagicMock()
        user_form = UserForm(report_url="https://docs.google.com/document/d/report/edit")
        parsed = ParsedReport(
            lkb_status="да",
            lkb_url=None,
            dissertation_url="https://docs.google.com/document/d/diss/edit",
            review_article_url=None,
            review_article_note="",
            results_article_url=None,
        )
        docs_service = MagicMock()
        report_req = MagicMock()
        report_req.execute.return_value = {"body": {"content": []}}
        diss_req = MagicMock()
        diss_req.execute.return_value = diss_doc
        docs_service.documents.return_value.get.side_effect = [report_req, diss_req]
        drive_service = MagicMock()

        with patch(
            "magister_checking.bot.report_enrichment._service_account_credentials",
            return_value=MagicMock(),
        ), patch(
            "magister_checking.bot.report_enrichment.build",
            side_effect=[docs_service, drive_service],
        ), patch(
            "magister_checking.bot.report_enrichment.resolve_report_google_doc_id",
            return_value="report-id",
        ), patch(
            "magister_checking.bot.report_enrichment.parse_intermediate_report",
            return_value=parsed,
        ), patch(
            "magister_checking.bot.report_enrichment.count_pdf_pages_via_drive_export",
            return_value=42,
        ):
            return build_sheet_enrichment(config, user_form)

    def test_russian_topic_and_language(self) -> None:
        doc = self._doc_with_topic(
            topic_paragraph="На тему: Применение машинного обучения в медицине",
            language_text=(
                "Введение\n"
                + "Настоящая работа посвящена изучению методов обработки данных. " * 80
            ),
        )
        result = self._run(doc)
        self.assertEqual(
            result["dissertation_title"],
            "Применение машинного обучения в медицине",
        )
        self.assertEqual(result["dissertation_language"], "русский")

    def test_kazakh_language_via_intro(self) -> None:
        doc = self._doc_with_topic(
            topic_paragraph="На тему: Қазақ тіліндегі мәтінді жіктеу",
            language_text=(
                "Кіріспе\n"
                + (
                    "Қазіргі таңда үлкен деректерді өңдеу мәселелері өте өзекті "
                    "болып табылады, әңғқөұүһі жұмыста зерттеледі. "
                ) * 50
            ),
        )
        result = self._run(doc)
        self.assertEqual(
            result["dissertation_title"], "Қазақ тіліндегі мәтінді жіктеу"
        )
        self.assertEqual(result["dissertation_language"], "казахский")


class PublicationUrlTests(unittest.TestCase):
    def test_prefers_results_article_url(self) -> None:
        parsed = _parsed(
            results_article_url="https://docs.google.com/document/d/results/edit",
            review_article_url="https://docs.google.com/document/d/review/edit",
        )
        result = _run_enrichment_with_parsed(parsed)
        self.assertEqual(
            result["publication_url"], "https://docs.google.com/document/d/results/edit"
        )

    def test_falls_back_to_review_article_url(self) -> None:
        parsed = _parsed(
            results_article_url=None,
            review_article_url="https://docs.google.com/document/d/review/edit",
        )
        result = _run_enrichment_with_parsed(parsed)
        self.assertEqual(
            result["publication_url"], "https://docs.google.com/document/d/review/edit"
        )

    def test_missing_when_no_article_urls(self) -> None:
        parsed = _parsed(results_article_url=None, review_article_url=None)
        result = _run_enrichment_with_parsed(parsed)
        self.assertEqual(result["publication_url"], URL_MISSING)


class PlaceholderColumnsTests(unittest.TestCase):
    """Заглушки для колонок, которые наполняются в более поздних фазах."""

    def test_empty_report_url_returns_placeholders(self) -> None:
        config = MagicMock()
        user_form = UserForm(report_url="")
        result = build_sheet_enrichment(config, user_form)
        self.assertEqual(result["publication_url"], URL_MISSING)
        self.assertEqual(result["dissertation_title"], "")
        self.assertEqual(result["dissertation_language"], "")

    def test_report_fetch_failure_returns_unavailable(self) -> None:
        config = MagicMock()
        user_form = UserForm(report_url="https://docs.google.com/document/d/report/edit")
        with patch(
            "magister_checking.bot.report_enrichment._service_account_credentials",
            return_value=MagicMock(),
        ), patch(
            "magister_checking.bot.report_enrichment.build",
            side_effect=[MagicMock(), MagicMock()],
        ), patch(
            "magister_checking.bot.report_enrichment.resolve_report_google_doc_id",
            side_effect=RuntimeError("no access"),
        ):
            result = build_sheet_enrichment(config, user_form)
        self.assertEqual(result["publication_url"], URL_UNAVAILABLE)
        self.assertEqual(result["dissertation_title"], "")
        self.assertEqual(result["dissertation_language"], "")


if __name__ == "__main__":
    unittest.main()
