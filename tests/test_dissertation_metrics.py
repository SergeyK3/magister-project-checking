"""Tests for dissertation metrics and formatting analysis."""

from __future__ import annotations

import io
import unittest

from docx import Document  # type: ignore[import-untyped]

from magister_checking.dissertation_metrics import (
    analyze_dissertation,
    analyze_docx_bytes,
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


if __name__ == "__main__":
    unittest.main()
