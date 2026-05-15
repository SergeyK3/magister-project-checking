"""Tests for docs_tables."""

import unittest

from magister_checking.docs_tables import extract_tables


def _cell_paragraph(text: str, url: str | None = None) -> dict:
    tr: dict = {"content": text, "textStyle": {}}
    if url:
        tr["textStyle"] = {"link": {"url": url}}
    return {"paragraph": {"elements": [{"textRun": tr}]}}


class TestExtractTables(unittest.TestCase):
    def test_one_table_two_rows(self) -> None:
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {"content": [_cell_paragraph("A")]},
                                        {"content": [_cell_paragraph("B")]},
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                _cell_paragraph(
                                                    "link",
                                                    "https://docs.google.com/document/d/xx/edit",
                                                )
                                            ]
                                        },
                                        {"content": [_cell_paragraph("Y")]},
                                    ]
                                },
                            ]
                        }
                    }
                ]
            }
        }
        tables = extract_tables(doc)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0][0].text, "A")
        self.assertEqual(len(tables[0][1][0].links), 1)
        self.assertIn("document/d/xx", tables[0][1][0].links[0].url)

    def test_table_cell_content_null_does_not_raise(self) -> None:
        """Google Docs API может отдать ``\"content\": null`` вместо []."""
        doc = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {"tableCells": [{"content": None}]},
                            ]
                        }
                    }
                ]
            }
        }
        tables = extract_tables(doc)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0][0].text, "")


if __name__ == "__main__":
    unittest.main()
