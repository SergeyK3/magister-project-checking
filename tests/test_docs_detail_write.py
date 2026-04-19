"""Tests for H1 section planning in docs_detail_write."""

import unittest

from magister_checking.docs_detail_write import (
    collapse_duplicate_newlines_in_plain_text,
    plan_top_level_h1_sections,
)


class TestDocsDetailWrite(unittest.TestCase):
    def test_collapse_duplicate_newlines(self) -> None:
        self.assertEqual(
            collapse_duplicate_newlines_in_plain_text("a\n\n\nb"),
            "a\nb",
        )
        self.assertEqual(collapse_duplicate_newlines_in_plain_text("x"), "x")
        self.assertEqual(collapse_duplicate_newlines_in_plain_text(""), "")

    def test_plan_two_h1_sections(self) -> None:
        doc = {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 10,
                        "paragraph": {"elements": [{"textRun": {"content": "intro\n"}}]},
                    },
                    {
                        "startIndex": 10,
                        "endIndex": 30,
                        "paragraph": {
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                            "elements": [
                                {
                                    "startIndex": 11,
                                    "endIndex": 29,
                                    "textRun": {"content": "A\n"},
                                }
                            ],
                        },
                    },
                    {
                        "startIndex": 30,
                        "endIndex": 40,
                        "paragraph": {"elements": [{"textRun": {"content": "body1\n"}}]},
                    },
                    {
                        "startIndex": 40,
                        "endIndex": 60,
                        "paragraph": {
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                            "elements": [
                                {
                                    "startIndex": 41,
                                    "endIndex": 59,
                                    "textRun": {"content": "B\n"},
                                }
                            ],
                        },
                    },
                    {
                        "startIndex": 60,
                        "endIndex": 70,
                        "paragraph": {"elements": [{"textRun": {"content": "tail\n"}}]},
                    },
                ]
            }
        }
        secs = plan_top_level_h1_sections(doc)
        self.assertEqual(len(secs), 2)
        self.assertEqual(secs[0].body_start, 30)
        self.assertEqual(secs[0].body_end, 40)
        self.assertEqual(secs[1].body_start, 60)
        self.assertEqual(secs[1].body_end, 70)


if __name__ == "__main__":
    unittest.main()
