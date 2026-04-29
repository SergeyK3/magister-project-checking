"""Tests for dissertation metrics and formatting analysis."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from docx import Document  # type: ignore[import-untyped]

from magister_checking.dissertation_metrics import (
    _docx_bibliography_word_list_count,
    _dominant_margins,
    _estimate_sources_count,
    _gdoc_collect_section_margins,
    _gdoc_page_numbering_info,
    _jc_to_horizontal,
    _twips_to_cm,
    analyze_dissertation,
    analyze_docx_bytes,
    bibliography_heading_issue_note,
)


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

    def test_google_doc_uses_list_header_spectrum_or_kazakh_and_max_index(self) -> None:
        """Заголовки «СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ» / каз. раздел: индекс = max(n)."""
        gizatova = _document(
            [
                _paragraph("Текст…\n"),
                _paragraph("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ\n"),
                _paragraph("59. Книга девятьсот пятидесят девятая\n"),
                _paragraph("60. Книга шестьдесятая\n"),
                _paragraph("61. Последняя запись\n"),
            ]
        )
        self.assertEqual(analyze_dissertation(gizatova).sources_count, 61)

        kz = _document(
            [
                _paragraph("Текст…\n"),
                _paragraph("ПАЙДАЛАНЫЛҒАН ӘДЕБИЕТТЕР\n"),
                _paragraph("1. Бірінші\n"),
                _paragraph("5. Соңғы нөмір\n"),
            ]
        )
        self.assertEqual(analyze_dissertation(kz).sources_count, 5)

    def test_maradzhapova_bibliography_glued_block_and_parenthesis_index(self) -> None:
        """Мараджапова: как в макете отчёта (см. test_maradzhapova_layout_publication_on_next_line) —
        плотная вёрстка; плюс нумерация `1) …` в отдельных абзацах.

        1) Один длинный параграф: после «СПИСОК ЛИТЕРАТУРЫ» без перевода абзаца сразу
        идут «1. … 2. …» — в API это одна склейка; счётчик = max (после glued).
        2) Заголовок «Использованная литература» + пункты 1)…25) — max = 25.
        """
        # Склейка: нет `\\n` между заголовком раздела и первым пунктом (всё в одном textRun-потоке).
        glued = _document(
            [
                _paragraph("Введение и основной текст…\n"),
                _paragraph(
                    "СПИСОК ЛИТЕРАТУРЫ 1. Первоисточник 2. Второй 40. "
                    "Сороковой 42. Последний в списке\n"
                ),
            ]
        )
        self.assertEqual(analyze_dissertation(glued).sources_count, 42)

        paren_numbered = _document(
            [
                _paragraph("…\n"),
                _paragraph("Использованная литература\n"),
                _paragraph("1) Книга первая\n"),
                _paragraph("2) Книга вторая\n"),
                _paragraph("25) Книга двадцать пятая\n"),
            ]
        )
        self.assertEqual(analyze_dissertation(paren_numbered).sources_count, 25)

    def test_bibliography_clips_post_list_foreign_domestic_percent_block(self) -> None:
        """После списка — сводка «зарубежн./отечеств. … %»; дальше не считаем (шум 85+)."""
        with_noise = _document(
            [
                _paragraph("Введение…\n"),
                _paragraph(
                    "СПИСОК ЛИТЕРАТУРЫ 1. Книга 41. Сорок первая 42. "
                    "Тулепбаева последняя\n\n"
                    "Зарубежных источников 48% и отечественных источников 52%\n"
                    "85. Ложный нумерованный блок после сводки\n"
                ),
            ]
        )
        self.assertEqual(analyze_dissertation(with_noise).sources_count, 42)

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


def _build_docx_bytes(paragraphs: list[str]) -> bytes:
    """Собирает .docx в памяти из набора абзацев (без Word-нумерации)."""

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class AnalyzeDocxBytesBibliographyTests(unittest.TestCase):
    """Stage 4: подсчёт источников в .docx через analyze_docx_bytes.

    Покрывает приоритет сигналов handoff §dissertation_metrics:
    word-list → реальные «N. …» в начале абзацев → URL-абзацы в окне
    [маркер библиографии … «Приложение»).
    """

    def test_unnumbered_bibliography_uses_url_paragraph_count(self) -> None:
        """Тананова: библиография — сплошной текст без «1. …», у каждой записи URL.

        Реальный кейс (handoff): 43 записи без любой нумерации; в тексте
        мелькают «pp.453-459.», «196–201.», «15:194.» — text-эвристика
        ловила их как индексы и возвращала шум вместо 43. URL-абзац в
        окне библиографии — устойчивый сигнал «1 запись = 1 URL».
        """

        bib_entries = [
            f"Иванов И.И. Статья {i}. Журнал. 2020. С.10-20. https://example.com/a{i}"
            for i in range(1, 44)
        ]
        paragraphs = [
            "Введение и основной текст диссертации…",
            "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
            *bib_entries,
            "ПРИЛОЖЕНИЕ А",
            "Дополнительные таблицы и рисунки.",
        ]
        metrics = analyze_docx_bytes(_build_docx_bytes(paragraphs))
        self.assertEqual(metrics.sources_count, 43)

    def test_url_paragraph_count_clipped_at_appendix(self) -> None:
        """Граница «Приложение» обрезает хвост: URL-ссылки в приложении не считаются."""

        bib_entries = [
            f"Источник {i}. URL: https://src/{i}" for i in range(1, 6)
        ]
        appendix_with_urls = [
            f"Дополнительная ссылка {i}: https://annex/{i}" for i in range(1, 11)
        ]
        paragraphs = [
            "Текст…",
            "Список литературы",
            *bib_entries,
            "Приложение А",
            *appendix_with_urls,
        ]
        metrics = analyze_docx_bytes(_build_docx_bytes(paragraphs))
        self.assertEqual(metrics.sources_count, 5)

    def test_line_numbered_bibliography_uses_text_indices(self) -> None:
        """«1. … 45.» в начале абзацев → max(n.) = 45, URL-fallback не вмешивается."""

        bib_entries = [f"{i}. Источник номер {i}. URL: https://x/{i}" for i in range(1, 46)]
        paragraphs = [
            "Введение…",
            "Список литературы",
            *bib_entries,
            "Приложение",
        ]
        metrics = analyze_docx_bytes(_build_docx_bytes(paragraphs))
        self.assertEqual(metrics.sources_count, 45)

    def test_short_url_run_does_not_trigger_fallback(self) -> None:
        """В библиографии всего 2 URL-абзаца — fallback не сработает (порог < 3).

        Защита от ложного «sources=2», когда библиография по факту пустая,
        а пара URL — это случайные ссылки в комментариях.
        """

        paragraphs = [
            "Текст…",
            "Список литературы",
            "Случайный комментарий с https://a",
            "Ещё один комментарий https://b",
        ]
        metrics = analyze_docx_bytes(_build_docx_bytes(paragraphs))
        self.assertIsNone(metrics.sources_count)


class DocxBibliographyWordListMergeTests(unittest.TestCase):
    """`_docx_bibliography_word_list_count` суммирует подряд идущие numPr-блоки.

    Реальный кейс — Камзебаева (row 2): библиография разделена в Word на
    два независимых нумерованных списка (kz numId=3 — 42 пункта, en
    numId=4 — 19 пунктов), идущих подряд без пустых строк. Per-numPr
    максимум возвращал 42 — отображалось в листе как `sources_count = 42`,
    хотя визуально это один сквозной список из 61 источника.
    """

    @staticmethod
    def _patch_records(records: list[tuple[str, tuple[str, str] | None]]):
        return mock.patch(
            "magister_checking.dissertation_metrics._docx_paragraph_records",
            return_value=records,
        )

    def test_merges_adjacent_numpr_runs_with_different_numids(self) -> None:
        records: list[tuple[str, tuple[str, str] | None]] = [
            ("ПАЙДАЛАНЫЛҒАН ӘДЕБИЕТТЕР", None),
            *[(f"kz entry {i}", ("3", "0")) for i in range(42)],
            *[(f"en entry {i}", ("4", "0")) for i in range(19)],
            ("Қосымша А", None),
            ("Содержимое приложения", None),
        ]
        with self._patch_records(records):
            self.assertEqual(_docx_bibliography_word_list_count(object()), 61)

    def test_single_numid_run_unchanged(self) -> None:
        """Документ с одним numId — стрик равен per-numPr-максимуму."""

        records: list[tuple[str, tuple[str, str] | None]] = [
            ("Список литературы", None),
            *[(f"src {i}", ("3", "0")) for i in range(106)],
            ("Приложение А", None),
        ]
        with self._patch_records(records):
            self.assertEqual(_docx_bibliography_word_list_count(object()), 106)

    def test_break_in_numpr_resets_streak(self) -> None:
        """Длинный gap (>1 абзаца без numPr) разрывает стрик."""

        records: list[tuple[str, tuple[str, str] | None]] = [
            ("Список литературы", None),
            *[(f"first {i}", ("3", "0")) for i in range(20)],
            ("разрывный текст 1", None),
            ("разрывный текст 2", None),
            *[(f"second {i}", ("4", "0")) for i in range(15)],
        ]
        with self._patch_records(records):
            self.assertEqual(_docx_bibliography_word_list_count(object()), 20)

    def test_only_first_streak_after_marker_is_used(self) -> None:
        """Точная регрессия по Камзебаевой: kz=42 + en=19 → 61.

        После 61-го пункта идут 4 текстовые строки приложения
        (Қосымша А + заголовки анкеты), затем нумерованные блоки самой
        анкеты с собственными numId — их учитывать нельзя, иначе вернётся
        70 (длина блока анкеты), а реальная библиография — 61.
        """

        records: list[tuple[str, tuple[str, str] | None]] = [
            ("ПАЙДАЛАНЫЛҒАН ӘДЕБИЕТТЕР", None),
            *[(f"kz entry {i}", ("3", "0")) for i in range(42)],
            *[(f"en entry {i}", ("4", "0")) for i in range(19)],
            ("Қосымша А", None),
            ("Сауалнама", None),
            ("Зерттеу тақырыбы", None),
            ("1-бөлім. Жалпы мәліметтер", None),
            *[(f"q {i}", ("5", "0")) for i in range(35)],
            *[(f"opt {i}", ("5", "1")) for i in range(35)],
        ]
        with self._patch_records(records):
            self.assertEqual(_docx_bibliography_word_list_count(object()), 61)

    def test_no_numpr_returns_none(self) -> None:
        records: list[tuple[str, tuple[str, str] | None]] = [
            ("Список литературы", None),
            ("обычный текст", None),
            ("ещё текст", None),
        ]
        with self._patch_records(records):
            self.assertIsNone(_docx_bibliography_word_list_count(object()))


class DocxPagesSanityCheckTests(unittest.TestCase):
    """`analyze_docx_bytes` не доверяет заведомо заниженному `<Pages>` из docProps.

    Реальный кейс — Камзебаева (row 2): docx экспортирован из Google Docs,
    в `docProps/app.xml` зашит `<Pages>1</Pages>` и не пересчитан, при том
    что plain-текст ≈ 151 162 символа (~68 страниц по эвристике 2200 ch/page).
    Sanity-check переключается на оценку, если она в 5+ раз больше
    значения метаданных.
    """

    def test_underestimating_app_xml_falls_back_to_char_estimate(self) -> None:
        # ~400000 символов non-space → ~180+ страниц по 2200/стр.
        paragraphs = ["Очень длинный абзац " * 200] * 200
        docx = _build_docx_bytes(paragraphs)
        with mock.patch(
            "magister_checking.dissertation_metrics._docx_page_count",
            return_value=1,
        ):
            metrics = analyze_docx_bytes(docx)
        self.assertGreater(metrics.approx_pages, 50)

    def test_consistent_app_xml_is_trusted(self) -> None:
        """Если <Pages> близко к оценке — берём метаданные, не оценку."""

        paragraphs = ["Короткий абзац " * 5] * 30
        docx = _build_docx_bytes(paragraphs)
        with mock.patch(
            "magister_checking.dissertation_metrics._docx_page_count",
            return_value=2,
        ):
            metrics = analyze_docx_bytes(docx)
        self.assertEqual(metrics.approx_pages, 2)

    def test_missing_app_xml_uses_char_estimate(self) -> None:
        """Если <Pages> вообще нет (None) — ведём себя как раньше."""

        paragraphs = ["Текст " * 100] * 50
        docx = _build_docx_bytes(paragraphs)
        with mock.patch(
            "magister_checking.dissertation_metrics._docx_page_count",
            return_value=None,
        ):
            metrics = analyze_docx_bytes(docx)
        self.assertGreaterEqual(metrics.approx_pages, 1)


class DocxMarginsAndNumberingTests(unittest.TestCase):
    """Парсеры полей и нумерации для DOCX (handoff §formatting v2).

    Покрытие:
    - twips→см с реальным делителем 567 (1 cm = 1440/2.54 twips, ровно).
    - ``_dominant_margins`` возвращает моду + остальные комбинации
      в порядке встречи (детерминизм для отчёта по multi-section).
    - ``_jc_to_horizontal`` корректно мапит OOXML jc-значения (включая
      исторические синонимы start/end/both).
    """

    def test_twips_to_cm_uses_exact_567(self) -> None:
        # Камзебаева row 2 — 7 секций имеют top=1037 twips → ровно 1.83 см.
        self.assertEqual(_twips_to_cm("1037"), 1.83)
        # Сулейменова row 6 — top=1134 twips = 2.0 см.
        self.assertEqual(_twips_to_cm("1134"), 2.0)
        # Мараджапова row 17 — left=1701 twips = 3.0 см.
        self.assertEqual(_twips_to_cm("1701"), 3.0)
        self.assertIsNone(_twips_to_cm(None))
        self.assertIsNone(_twips_to_cm("not-a-number"))

    def test_dominant_margins_picks_mode_and_keeps_others(self) -> None:
        """Реплика реального паттерна Камзебаевой: 7 одинаковых, 2 уникальных."""

        kamzebayeva = [
            {"top": 1.83, "bottom": 0.49, "left": 1.75, "right": 0.75},  # титул
            *(
                [{"top": 1.83, "bottom": 2.12, "left": 1.75, "right": 0.75}] * 7
            ),
            {"top": 2.5, "bottom": 2.12, "left": 1.75, "right": 0.75},  # приложения
        ]
        dominant, secondary = _dominant_margins(kamzebayeva)
        self.assertEqual(
            dominant, {"top": 1.83, "bottom": 2.12, "left": 1.75, "right": 0.75}
        )
        # Уникальные комбинации в порядке встречи (без моды).
        self.assertEqual(
            secondary,
            [
                {"top": 1.83, "bottom": 0.49, "left": 1.75, "right": 0.75},
                {"top": 2.5, "bottom": 2.12, "left": 1.75, "right": 0.75},
            ],
        )

    def test_dominant_margins_empty(self) -> None:
        self.assertEqual(_dominant_margins([]), (None, []))

    def test_jc_to_horizontal_handles_aliases(self) -> None:
        self.assertEqual(_jc_to_horizontal("left"), "left")
        self.assertEqual(_jc_to_horizontal("start"), "left")
        self.assertEqual(_jc_to_horizontal("right"), "right")
        self.assertEqual(_jc_to_horizontal("end"), "right")
        self.assertEqual(_jc_to_horizontal("center"), "center")
        self.assertEqual(_jc_to_horizontal("centre"), "center")
        # `both` — выравнивание по ширине; для footer-PAGE визуально это
        # тоже левый край (нет переносов в одной строке с PAGE).
        self.assertEqual(_jc_to_horizontal("both"), "left")
        self.assertIsNone(_jc_to_horizontal(None))
        self.assertIsNone(_jc_to_horizontal(""))


class GoogleDocMarginsAndNumberingTests(unittest.TestCase):
    """Парсеры полей и нумерации для Google Doc (Docs API)."""

    @staticmethod
    def _pt(magnitude: float) -> dict:
        return {"magnitude": magnitude, "unit": "PT"}

    def test_collect_default_document_style_margins(self) -> None:
        """``documentStyle.margin*`` без секций → 1 запись (default)."""

        # 56.6929 pt ≈ 2.0 см (1 cm = 28.346 pt).
        doc = {
            "documentStyle": {
                "marginTop": self._pt(56.6929),
                "marginBottom": self._pt(28.3464),
                "marginLeft": self._pt(85.0394),
                "marginRight": self._pt(28.3464),
            },
            "body": {"content": []},
        }
        margins = _gdoc_collect_section_margins(doc)
        self.assertEqual(len(margins), 1)
        self.assertEqual(
            margins[0], {"top": 2.0, "bottom": 1.0, "left": 3.0, "right": 1.0}
        )

    def test_collect_section_break_overrides_default(self) -> None:
        """Если ``sectionBreak.sectionStyle`` задаёт margins — добавляется к списку."""

        doc = {
            "documentStyle": {
                "marginTop": self._pt(56.6929),
                "marginBottom": self._pt(28.3464),
                "marginLeft": self._pt(85.0394),
                "marginRight": self._pt(28.3464),
            },
            "body": {
                "content": [
                    {
                        "sectionBreak": {
                            "sectionStyle": {
                                "marginTop": self._pt(28.3464),
                                "marginBottom": self._pt(28.3464),
                                "marginLeft": self._pt(85.0394),
                                "marginRight": self._pt(28.3464),
                            }
                        }
                    }
                ]
            },
        }
        margins = _gdoc_collect_section_margins(doc)
        self.assertEqual(len(margins), 2)

    def test_page_numbering_present_via_autotext(self) -> None:
        """Footer с PAGE_NUMBER + alignment=END → present=True, position=bottom-right."""

        doc = {
            "footers": {
                "kix.f1": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {"autoText": {"type": "PAGE_NUMBER"}}
                                ],
                                "paragraphStyle": {"alignment": "END"},
                            }
                        }
                    ]
                }
            }
        }
        info = _gdoc_page_numbering_info(doc)
        self.assertTrue(info["present"])
        self.assertEqual(info["position"], "bottom-right")
        # Coverage не имеет смысла для GDoc — None.
        self.assertIsNone(info["sections_with_footer"])
        self.assertIsNone(info["sections_total"])

    def test_page_numbering_absent_when_no_footers(self) -> None:
        info = _gdoc_page_numbering_info({"body": {"content": []}})
        self.assertFalse(info["present"])
        self.assertIsNone(info["position"])

    def test_page_numbering_absent_when_footer_has_no_page_field(self) -> None:
        """Footer есть, но без autoText.PAGE_NUMBER — нумерации нет."""

        doc = {
            "footers": {
                "kix.f1": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {"textRun": {"content": "просто текст"}}
                                ],
                                "paragraphStyle": {"alignment": "CENTER"},
                            }
                        }
                    ]
                }
            }
        }
        info = _gdoc_page_numbering_info(doc)
        self.assertFalse(info["present"])
        self.assertIsNone(info["position"])

    def test_page_numbering_alignment_start_maps_to_left(self) -> None:
        doc = {
            "footers": {
                "kix.f1": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {"autoText": {"type": "PAGE_NUMBER"}}
                                ],
                                "paragraphStyle": {"alignment": "START"},
                            }
                        }
                    ]
                }
            }
        }
        info = _gdoc_page_numbering_info(doc)
        self.assertEqual(info["position"], "bottom-left")


class WrongBibliographyHeadingBaikyatalovTests(unittest.TestCase):
    """Якорь «СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ» + предупреждение по методичке."""

    def test_genitive_heading_counts_numbered_sources(self) -> None:
        plain = (
            "Введение\n\nСПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ\n\n"
            + "\n".join(f"{i}. Источник {i}" for i in range(1, 80))
        )
        self.assertEqual(_estimate_sources_count(plain), 79)

    def test_genitive_heading_sets_issue_note_without_approved_phrase(self) -> None:
        plain = "СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ\n1. A\n"
        msg = bibliography_heading_issue_note(plain)
        self.assertIsNotNone(msg)
        self.assertIn("ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", msg)

    def test_approved_heading_only_no_issue_note(self) -> None:
        plain = "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ\n1. A\n"
        self.assertIsNone(bibliography_heading_issue_note(plain))


if __name__ == "__main__":
    unittest.main()
