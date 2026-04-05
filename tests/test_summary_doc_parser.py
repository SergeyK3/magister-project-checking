"""Tests for summary_doc_parser."""

import unittest

from magister_checking.summary_doc_parser import parse_summary_document


def _p(text: str, url: str | None = None) -> dict:
    tr: dict = {"content": text, "textStyle": {}}
    if url:
        tr["textStyle"] = {"link": {"url": url}}
    return {"paragraph": {"elements": [{"textRun": tr}]}}


class TestSummaryDocParser(unittest.TestCase):
    def test_parses_name_and_report_link(self) -> None:
        report = "https://docs.google.com/document/d/REPORT_ID/edit"
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {"content": [_p("№")]},
                                        {"content": [_p("ФИО")]},
                                        {
                                            "content": [
                                                _p("Промежуточный отчёт (ссылка)")
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {"content": [_p("1")]},
                                        {"content": [_p("Иванова А.А.")]},
                                        {"content": [_p("открыть", report)]},
                                    ]
                                },
                            ]
                        }
                    }
                ]
            }
        }
        rows = parse_summary_document(doc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Иванова А.А.")
        self.assertEqual(rows[0].report_url, report)


if __name__ == "__main__":
    unittest.main()
