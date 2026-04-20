"""Tests for dissertation metrics and formatting analysis."""

from __future__ import annotations

import unittest

from magister_checking.dissertation_metrics import analyze_dissertation


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


def _document(content: list[dict]) -> dict:
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
        "body": {"content": content},
    }


class DissertationMetricsTests(unittest.TestCase):
    def test_google_doc_metrics_use_dissertation_bibliography_and_formatting(self) -> None:
        doc = _document(
            [
                _paragraph("Основной текст диссертации.\n"),
                _paragraph("Литература\n"),
                _paragraph("1. Первый источник\n"),
                _paragraph("2. Второй источник\n"),
            ]
        )

        metrics = analyze_dissertation(doc)

        self.assertEqual(metrics.sources_count, 2)
        self.assertTrue(metrics.formatting_compliance)
        self.assertEqual(metrics.font_size_14_ratio, 1.0)
        self.assertEqual(metrics.times_new_roman_ratio, 1.0)
        self.assertEqual(metrics.single_spacing_ratio, 1.0)

    def test_google_doc_metrics_fail_when_font_and_spacing_do_not_meet_threshold(self) -> None:
        doc = _document(
            [
                _paragraph("Очень длинный корректный абзац " * 10 + "\n"),
                _paragraph(
                    "Очень длинный некорректный абзац " * 10 + "\n",
                    font_family="Arial",
                    font_size=12,
                    line_spacing=150.0,
                ),
                _paragraph("Литература\n"),
                _paragraph("1. Источник\n"),
            ]
        )

        metrics = analyze_dissertation(doc)

        self.assertFalse(metrics.formatting_compliance)
        self.assertLess(metrics.font_size_14_ratio or 0.0, 0.95)
        self.assertLess(metrics.times_new_roman_ratio or 0.0, 0.95)
        self.assertLess(metrics.single_spacing_ratio or 0.0, 0.95)


if __name__ == "__main__":
    unittest.main()
