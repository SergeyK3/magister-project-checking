"""Tests for docs_extract (tables + hyperlinks)."""

import unittest

from magister_checking.docs_extract import HyperlinkRecord, extract_plain_text, iter_hyperlinks


def _doc_with_table_and_link() -> dict:
    return {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {
                                    "content": "Intro ",
                                    "textStyle": {},
                                }
                            },
                            {
                                "textRun": {
                                    "content": "click",
                                    "textStyle": {
                                        "link": {
                                            "url": "https://example.com/doc",
                                        }
                                    },
                                }
                            },
                        ]
                    }
                },
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {
                                        "content": [
                                            {
                                                "paragraph": {
                                                    "elements": [
                                                        {
                                                            "textRun": {
                                                                "content": "A1",
                                                                "textStyle": {},
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    },
                                    {
                                        "content": [
                                            {
                                                "paragraph": {
                                                    "elements": [
                                                        {
                                                            "textRun": {
                                                                "content": "B1",
                                                                "textStyle": {
                                                                    "link": {
                                                                        "url": "https://drive.google.com/file/d/xyz"
                                                                    }
                                                                },
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    },
                                ]
                            }
                        ]
                    }
                },
            ]
        }
    }


class TestDocsExtract(unittest.TestCase):
    def test_plain_text_includes_table_cells(self) -> None:
        doc = _doc_with_table_and_link()
        text = extract_plain_text(doc)
        self.assertIn("Intro ", text)
        self.assertIn("click", text)
        self.assertIn("A1", text)
        self.assertIn("B1", text)
        self.assertEqual(text.index("Intro "), 0)

    def test_hyperlinks_paragraph_and_table(self) -> None:
        doc = _doc_with_table_and_link()
        links = list(iter_hyperlinks(doc))
        self.assertEqual(len(links), 2)
        self.assertEqual(
            links[0],
            HyperlinkRecord(
                url="https://example.com/doc",
                anchor_text="click",
                context_path="body",
            ),
        )
        self.assertEqual(
            links[1].url,
            "https://drive.google.com/file/d/xyz",
        )
        self.assertEqual(links[1].anchor_text, "B1")
        self.assertEqual(links[1].context_path, "body/table[0,1]")

    def test_internal_link_skipped(self) -> None:
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Section",
                                        "textStyle": {
                                            "link": {"headingId": "h.abc"},
                                        },
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
        self.assertEqual(list(iter_hyperlinks(doc)), [])


if __name__ == "__main__":
    unittest.main()
