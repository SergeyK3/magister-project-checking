"""Tests for intermediate report parsing (summary table fields)."""

import unittest

from magister_checking.report_parser import parse_intermediate_report


def _p(text: str, url: str | None = None) -> dict:
    tr: dict = {"content": text, "textStyle": {}}
    if url:
        tr["textStyle"] = {"link": {"url": url}}
    return {"paragraph": {"elements": [{"textRun": tr}]}}


class TestReportParser(unittest.TestCase):
    def test_extracts_workplace_job_supervisor_and_report_link(self) -> None:
        doc_url = "https://docs.google.com/document/d/1vf8CLxg9mzQFdZw51uxIlR5w3P9KM-XhO_-8PgW22ec/edit"
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {"content": [_p("Место работы")]},
                                        {"content": [_p("не работает")]},
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {"content": [_p("Должность")]},
                                        {"content": [_p("Исследователь")]},
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {"content": [_p("Научный руководитель")]},
                                        {"content": [_p("Ким Сергей Васильевич, проф.")]},
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {"content": [_p("Ссылка на настоящий документ")]},
                                        {"content": [_p("открыть", doc_url)]},
                                    ]
                                },
                            ]
                        }
                    }
                ]
            }
        }
        out = parse_intermediate_report(doc)
        self.assertEqual(out.workplace, "не работает")
        self.assertEqual(out.job_title, "Исследователь")
        self.assertEqual(out.supervisor, "Ким Сергей Васильевич, проф.")
        self.assertEqual(out.report_doc_url, doc_url)

    def test_supervisor_in_same_cell_after_colon(self) -> None:
        """Как в части шаблонов: подпись и ФИО руководителя в одной ячейке."""
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                _p(
                                                    "Научный руководитель: Ким Сергей Васильевич, проф."
                                                )
                                            ]
                                        },
                                    ]
                                },
                            ]
                        }
                    }
                ]
            }
        }
        out = parse_intermediate_report(doc)
        self.assertEqual(out.supervisor, "Ким Сергей Васильевич, проф.")


def _doc_from_paragraphs(paragraphs: list[tuple[str, str | None]]) -> dict:
    """Документ из последовательности параграфов: (text, optional_link).

    Реальный Google Docs API возвращает в последнем textRun-е каждого
    параграфа завершающий ``\\n``; ``extract_plain_text`` не добавляет
    разделители сам, так что без этого ``\\n`` соседние параграфы в plain
    text слипаются и regex'ы ловят URL вместе с текстом следующей строки.
    Хелпер дописывает ``\\n``, если его нет, чтобы фикстура повторяла
    реальный формат API.
    """
    return {
        "body": {
            "content": [
                _p(text if text.endswith("\n") else text + "\n", link)
                for text, link in paragraphs
            ]
        }
    }


class TestPlainTextLinkExtraction(unittest.TestCase):
    """Покрывает реальные шаблоны промежуточного отчёта (паражные, без таблицы).

    Гизатова: heading и URL в соседних параграфах для всех 4 ссылок;
    диссертация — same-line (одна).
    Мараджапова: heading и URL в одном параграфе (3 из 4); публикация —
    next-line.
    """

    PROJECT_FOLDER = "https://drive.google.com/drive/folders/PROJF"
    LKB_URL = "https://drive.google.com/file/d/LKB/view?usp=drive_link"
    DISS_URL = "https://docs.google.com/document/d/DISS/edit?usp=sharing"
    PUB_URL = "https://drive.google.com/file/d/PUB/view?usp=sharing"

    def test_gizatova_layout_heading_and_link_on_separate_paragraphs(self) -> None:
        """Все 4 ссылки на отдельных от заголовков параграфах."""
        doc = _doc_from_paragraphs(
            [
                ("Промежуточный отчёт магистранта", None),
                ("Папка \u201cМагистерский проект\u201d: ", None),
                (self.PROJECT_FOLDER, self.PROJECT_FOLDER),
                ("Наличие заключений ЛКБ (Локальной комиссии по биоэтике): ", None),
                (self.LKB_URL, self.LKB_URL),
                (
                    f"Диссертация: {self.DISS_URL}",
                    self.DISS_URL,
                ),
                (f"Публикации: {self.PUB_URL}", self.PUB_URL),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.project_folder_url, self.PROJECT_FOLDER)
        self.assertEqual(out.lkb_url, self.LKB_URL)
        self.assertEqual(out.dissertation_url, self.DISS_URL)
        self.assertEqual(out.publication_url, self.PUB_URL)

    def test_maradzhapova_layout_publication_on_next_line(self) -> None:
        """Project/LKB/Dissertation — same-line; публикация — следующая строка."""
        doc = _doc_from_paragraphs(
            [
                ("Промежуточный отчет магистранта", None),
                (
                    f"Папка \u201cМагистерский проект\u201d: {self.PROJECT_FOLDER}",
                    self.PROJECT_FOLDER,
                ),
                (
                    "Наличие заключение ЛКБ (локальной комиссии по биоэтике) ЕСТЬ: "
                    f"{self.LKB_URL}",
                    self.LKB_URL,
                ),
                (f"Диссертация: {self.DISS_URL}", self.DISS_URL),
                ("PDF публикации:", None),
                (self.PUB_URL, self.PUB_URL),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.project_folder_url, self.PROJECT_FOLDER)
        self.assertEqual(out.lkb_url, self.LKB_URL)
        self.assertEqual(out.dissertation_url, self.DISS_URL)
        self.assertEqual(out.publication_url, self.PUB_URL)

    def test_publication_url_recognised_after_pdf_prefix(self) -> None:
        """«PDF публикации:» (а не просто «Публикации:») тоже должен срабатывать."""
        doc = _doc_from_paragraphs(
            [("PDF публикации:", None), (self.PUB_URL, self.PUB_URL)]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.publication_url, self.PUB_URL)

    def test_no_link_when_heading_present_but_url_absent(self) -> None:
        """Только заголовок без следующей URL-строки → поле остаётся None."""
        doc = _doc_from_paragraphs(
            [
                ("Папка \u201cМагистерский проект\u201d: ", None),
                ("Заголовок без ссылки ниже", None),
                ("Соблюдение требований", None),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertIsNone(out.project_folder_url)
        self.assertIsNone(out.publication_url)

    def test_lkb_status_set_when_lkb_url_extracted_via_next_line(self) -> None:
        """Если lkb_url нашёлся через next-line, lkb_status тоже становится «да»."""
        doc = _doc_from_paragraphs(
            [
                ("Наличие заключений ЛКБ (Локальной комиссии по биоэтике): ", None),
                (self.LKB_URL, self.LKB_URL),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.lkb_url, self.LKB_URL)
        self.assertEqual(out.lkb_status, "да")

    def test_project_folder_extracts_any_url_kind(self) -> None:
        """Под заголовком «Папка \u201cМагистерский проект\u201d:» парсер
        теперь забирает ЛЮБУЮ http(s) ссылку, не только folder. Это нужно,
        чтобы Stage 3 мог отдельно пометить тип-несоответствие
        (например, магистрант случайно указал Google-документ): иначе
        парсер вернул бы None и Stage 3 решил бы, что ссылки нет вовсе.
        Кейс по мотивам Макишевой Г.Д.
        """
        wrong_kind_url = "https://docs.google.com/document/d/proj_doc/edit"
        doc = _doc_from_paragraphs(
            [
                ("Промежуточный отчёт магистранта", None),
                (
                    f"Папка \u201cМагистерский проект\u201d: {wrong_kind_url}",
                    wrong_kind_url,
                ),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.project_folder_url, wrong_kind_url)

    def test_dissertation_extracts_folder_url(self) -> None:
        """Под «Диссертация:» парсер забирает любую URL, включая folder.
        Stage 3 затем пометит folder для диссертации как hard-fail.
        Кейс по мотивам Досанова Б.А.
        """
        folder_url = "https://drive.google.com/drive/folders/diss_folder"
        doc = _doc_from_paragraphs(
            [
                ("Промежуточный отчёт магистранта", None),
                (f"Диссертация: {folder_url}", folder_url),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.dissertation_url, folder_url)

    def test_lkb_extracts_any_url_after_heading(self) -> None:
        """ЛКБ-ссылка — любого http(s) типа. Stage 3 проверит PDF mime отдельно."""
        doc_url_for_lkb = "https://docs.google.com/document/d/lkb_doc/edit"
        doc = _doc_from_paragraphs(
            [
                ("Наличие заключения ЛКБ:", None),
                (doc_url_for_lkb, doc_url_for_lkb),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.lkb_url, doc_url_for_lkb)

    def test_tananova_layout_dissertation_on_next_line(self) -> None:
        """Тананова: «Диссертация:» в одном абзаце, ссылка — в следующем.

        Реальный документ: paragraph[18] = «Диссертация:\\n», paragraph[19]
        = URL. Same-line ветка _DOC_URL_IN_ROW.search(ln) на пустой строке
        заголовка не находила ничего, и поле оставалось None.
        """
        doc = _doc_from_paragraphs(
            [
                ("Промежуточный отчет магистранта", None),
                ("Папка «Магистерский проект» ", None),
                (self.PROJECT_FOLDER, self.PROJECT_FOLDER),
                ("Наличие заключения ЛКБ:", None),
                (self.LKB_URL, self.LKB_URL),
                ("Диссертация:", None),
                (self.DISS_URL, self.DISS_URL),
            ]
        )
        out = parse_intermediate_report(doc)
        self.assertEqual(out.project_folder_url, self.PROJECT_FOLDER)
        self.assertEqual(out.lkb_url, self.LKB_URL)
        self.assertEqual(out.dissertation_url, self.DISS_URL)
        # Публикации в шаблоне Танановой нет — поле должно остаться None.
        self.assertIsNone(out.publication_url)


if __name__ == "__main__":
    unittest.main()
