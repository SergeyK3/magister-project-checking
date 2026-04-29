"""Tests for ``magister_checking.formatting_rules``.

Покрытие:
- ``load_formatting_rules`` подставляет дефолты при отсутствующих env;
  принимает запятые как десятичные разделители; игнорирует мусор.
- ``evaluate_formatting_compliance`` точно реплицирует диагноз для трёх
  visually-проверенных кейсов: Камзебаева, Сулейменова, Мараджапова.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from magister_checking.dissertation_metrics import DissertationMetrics
from magister_checking.formatting_rules import (
    FormattingRules,
    evaluate_formatting_compliance,
    load_formatting_rules,
    position_human_ru,
)


def _metrics(
    *,
    tnr: float | None = 1.0,
    size: float | None = 1.0,
    spacing: float | None = 1.0,
    margins: dict[str, float] | None = None,
    margins_secondary: list[dict[str, float]] | None = None,
    numbering_present: bool | None = True,
    numbering_position: str | None = "bottom-right",
    sections_with_footer: int | None = None,
    sections_total: int | None = None,
    bibliography_heading_warning: str | None = None,
) -> DissertationMetrics:
    return DissertationMetrics(
        approx_pages=60,
        pdf_pages=None,
        sources_count=40,
        review_pages=None,
        review_sources_count=None,
        has_literature_review=True,
        has_results=True,
        has_discussion=True,
        formatting_compliance=None,
        font_size_14_ratio=size,
        times_new_roman_ratio=tnr,
        single_spacing_ratio=spacing,
        page_margins_cm=margins,
        page_margins_secondary_cm=margins_secondary or [],
        page_numbering_present=numbering_present,
        page_numbering_position=numbering_position,
        page_numbering_sections_with_footer=sections_with_footer,
        page_numbering_sections_total=sections_total,
        bibliography_heading_warning=bibliography_heading_warning,
    )


class LoadFormattingRulesTests(unittest.TestCase):
    def test_defaults_when_env_empty(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            rules = load_formatting_rules()
        self.assertEqual(rules.font_family, "Times New Roman")
        self.assertEqual(rules.font_size_pt, 14.0)
        self.assertEqual(rules.line_spacing, "single")
        self.assertEqual(rules.margins_cm, {"top": 2.0, "bottom": 1.0, "left": 3.0, "right": 1.0})
        self.assertEqual(rules.page_numbering_position, "bottom-right")
        self.assertEqual(rules.ratio_threshold, 0.95)
        self.assertEqual(rules.margin_tolerance_cm, 0.2)

    def test_env_overrides_with_comma_decimal(self) -> None:
        env = {
            "FORMATTING_FONT_FAMILY": "Arial",
            "FORMATTING_FONT_SIZE_PT": "12",
            "FORMATTING_MARGIN_TOP_CM": "2,5",
            "FORMATTING_MARGIN_TOLERANCE_CM": "0,1",
            "FORMATTING_PAGE_NUMBERING_POSITION": "bottom-center",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            rules = load_formatting_rules()
        self.assertEqual(rules.font_family, "Arial")
        self.assertEqual(rules.font_size_pt, 12.0)
        self.assertEqual(rules.margin_top_cm, 2.5)
        self.assertEqual(rules.margin_tolerance_cm, 0.1)
        self.assertEqual(rules.page_numbering_position, "bottom-center")

    def test_invalid_position_falls_back_to_default(self) -> None:
        with mock.patch.dict(
            os.environ, {"FORMATTING_PAGE_NUMBERING_POSITION": "diagonal"}, clear=True
        ):
            rules = load_formatting_rules()
        self.assertEqual(rules.page_numbering_position, "bottom-right")


class EvaluateFormattingComplianceTests(unittest.TestCase):
    """Реальные кейсы из handoff (25.04.2026), все три visually verified."""

    def setUp(self) -> None:
        self.rules = FormattingRules()  # дефолты методички

    def test_full_compliance_returns_short_text(self) -> None:
        """Все метрики идеальны → compliance=True, cell-текст = «соответствует»."""

        m = _metrics(
            margins={"top": 2.0, "bottom": 1.0, "left": 3.0, "right": 1.0},
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertTrue(report.compliance)
        self.assertEqual(report.text, "соответствует")

    def test_kamzebayeva_case(self) -> None:
        """Камзебаева row 2 — поля сильно мимо, нумерация bottom-left, 1/9 секций."""

        m = _metrics(
            tnr=1.0,
            size=0.939,
            spacing=0.991,
            margins={"top": 1.83, "bottom": 2.12, "left": 1.75, "right": 0.75},
            margins_secondary=[
                {"top": 1.83, "bottom": 0.49, "left": 1.75, "right": 0.75},
                {"top": 2.5, "bottom": 2.12, "left": 1.75, "right": 0.75},
            ],
            numbering_present=True,
            numbering_position="bottom-left",
            sections_with_footer=1,
            sections_total=9,
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertFalse(report.compliance)
        self.assertIn("не соответствует", report.text)
        self.assertIn("1,83", report.text)  # фактическое верхнее
        self.assertIn("0,75", report.text)  # фактическое правое
        self.assertIn("внизу слева", report.text)
        self.assertIn("внизу справа", report.text)  # требование
        self.assertIn("1 из 9", report.text)
        self.assertIn("в Google Docs может не отображаться", report.text)

    def test_suleymenova_case(self) -> None:
        """Сулейменова row 6 — TNR=0%, right=1.5, нумерация bottom-center, 1/1."""

        m = _metrics(
            tnr=0.0,
            size=0.999,
            spacing=1.0,
            margins={"top": 2.0, "bottom": 2.0, "left": 3.0, "right": 1.5},
            numbering_present=True,
            numbering_position="bottom-center",
            sections_with_footer=1,
            sections_total=1,
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertFalse(report.compliance)
        # 1/1 секций — coverage warning не должен сработать.
        self.assertNotIn("в Google Docs может не отображаться", report.text)
        self.assertIn("шрифт «Times New Roman»: 0%", report.text)
        self.assertIn("внизу по центру", report.text)

    def test_maradzhapova_case(self) -> None:
        """Мараджапова row 17 — bottom=2.0 mismatch, 84% размер, нумерация по центру."""

        m = _metrics(
            tnr=0.999,
            size=0.843,
            spacing=1.0,
            margins={"top": 2.0, "bottom": 2.0, "left": 3.0, "right": 1.0},
            numbering_present=True,
            numbering_position="bottom-center",
            sections_with_footer=3,
            sections_total=7,
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertFalse(report.compliance)
        # У Мараджаповой visually нумерация есть на каждой странице
        # (Google Docs наследует footer1 для sectPr[1,2] и footer4 для
        # sectPr[5,6]), но coverage 3/7 < 50% — мы выводим warning,
        # не блокируем по нему. Блокирует только position.
        self.assertIn("3 из 7 секций", report.text)
        self.assertIn("кегль 14 pt: 84%", report.text)

    def test_missing_numbering_blocks_compliance(self) -> None:
        m = _metrics(
            margins={"top": 2.0, "bottom": 1.0, "left": 3.0, "right": 1.0},
            numbering_present=False,
            numbering_position=None,
            sections_with_footer=0,
            sections_total=5,
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertFalse(report.compliance)
        self.assertIn("нумерация страниц: отсутствует", report.text)

    def test_no_metrics_returns_none(self) -> None:
        m = _metrics(
            tnr=None,
            size=None,
            spacing=None,
            margins=None,
            numbering_present=None,
        )
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertIsNone(report.compliance)
        self.assertEqual(report.text, "—")

    def test_bibliography_wrong_heading_blocks_compliance(self) -> None:
        warn = (
            "Заголовок списка литературы: по методичке используйте «СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ» "
            "(в документе указано «СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ»)."
        )
        m = _metrics(bibliography_heading_warning=warn)
        report = evaluate_formatting_compliance(m, self.rules)
        self.assertFalse(report.compliance)
        self.assertIn("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", report.text)
        self.assertIn("не соответствует", report.text)

    def test_position_human_ru(self) -> None:
        self.assertEqual(position_human_ru("bottom-right"), "внизу справа")
        self.assertEqual(position_human_ru("bottom-center"), "внизу по центру")
        self.assertEqual(position_human_ru("bottom-left"), "внизу слева")


if __name__ == "__main__":
    unittest.main()
