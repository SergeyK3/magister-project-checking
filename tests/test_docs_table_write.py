"""Tests for docs_table_write span helpers."""

import unittest

from magister_checking.docs_table_write import (
    find_best_summary_table,
    find_first_table,
    table_row_cell_spans,
)


class TestDocsTableWrite(unittest.TestCase):
    def test_find_first_table(self) -> None:
        doc = {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 2,
                        "paragraph": {"elements": []},
                    },
                    {
                        "startIndex": 2,
                        "endIndex": 100,
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                {
                                                    "startIndex": 3,
                                                    "endIndex": 10,
                                                    "paragraph": {
                                                        "elements": [
                                                            {
                                                                "startIndex": 4,
                                                                "endIndex": 9,
                                                                "textRun": {
                                                                    "content": "abc\n",
                                                                },
                                                            }
                                                        ]
                                                    },
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                    },
                ]
            }
        }
        t = find_first_table(doc)
        self.assertIsNotNone(t)
        spans = table_row_cell_spans(t, 0)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0], (4, 9))

    def test_find_best_summary_table_prefers_more_columns(self) -> None:
        cell = {
            "content": [
                {
                    "startIndex": 4,
                    "endIndex": 9,
                    "paragraph": {
                        "elements": [{"startIndex": 4, "endIndex": 9, "textRun": {"content": "x\n"}}]
                    },
                }
            ]
        }
        narrow = {"tableRows": [{"tableCells": [cell, cell, cell]}]}
        wide = {"tableRows": [{"tableCells": [cell] * 7}]}
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 2, "table": narrow},
                    {"startIndex": 2, "endIndex": 3, "table": wide},
                ]
            }
        }
        t = find_best_summary_table(doc)
        self.assertIs(t, wide)


if __name__ == "__main__":
    unittest.main()
