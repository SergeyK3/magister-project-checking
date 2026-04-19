"""Тесты вспомогательной логики пересборки детального Doc."""

import unittest

from magister_checking import docs_bootstrap as db


class TestFindParagraphForTitle(unittest.TestCase):
    def test_prefers_heading_over_trailing_empty_paragraph(self) -> None:
        """После H1 Docs может добавить пустой абзац в конец — не считать его «заголовком»."""
        content = [
            {
                "startIndex": 1,
                "endIndex": 10,
                "paragraph": {
                    "elements": [{"textRun": {"content": "Шахметов Азамат Думашевич\n"}}],
                },
            },
            {
                "startIndex": 10,
                "endIndex": 11,
                "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
            },
        ]
        el = db._find_last_top_level_paragraph_matching_title(
            content, "Шахметов Азамат Думашевич"
        )
        self.assertIsNotNone(el)
        assert el is not None
        self.assertEqual(el.get("startIndex"), 1)

    def test_last_duplicate_name_wins(self) -> None:
        content = [
            {
                "startIndex": 1,
                "endIndex": 5,
                "paragraph": {"elements": [{"textRun": {"content": "Сериков\n"}}]},
            },
            {
                "startIndex": 5,
                "endIndex": 9,
                "paragraph": {"elements": [{"textRun": {"content": "Сериков\n"}}]},
            },
        ]
        el = db._find_last_top_level_paragraph_matching_title(content, "Сериков")
        self.assertIsNotNone(el)
        assert el is not None
        self.assertEqual(el.get("startIndex"), 5)


if __name__ == "__main__":
    unittest.main()
