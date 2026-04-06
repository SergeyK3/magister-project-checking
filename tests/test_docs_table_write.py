"""Tests for docs_table_write span helpers."""

import unittest

from magister_checking.docs_table_write import (
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


if __name__ == "__main__":
    unittest.main()
