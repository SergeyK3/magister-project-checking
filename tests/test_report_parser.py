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


if __name__ == "__main__":
    unittest.main()
