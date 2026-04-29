"""Тесты CLI send-message."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from magister_checking import cli


class SendMessageCliTests(unittest.TestCase):
    def _msg_file(self, text: str = "Привет!\nСтрока 2.") -> Path:
        fd, raw = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        path = Path(raw)
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_send_without_second_flag_returns_2(self) -> None:
        path = self._msg_file()
        with patch("magister_checking.bot.config.load_config", return_value=MagicMock()):
            code = cli.main(
                ["send-message", "--message-file", str(path), "--telegram-id", "999", "--send"]
            )
        self.assertEqual(code, 2)

    def test_dry_run_stdout_contains_id_and_body(self) -> None:
        path = self._msg_file("preview body")
        out = io.StringIO()
        with patch("magister_checking.bot.config.load_config", return_value=MagicMock()):
            with redirect_stdout(out):
                code = cli.main(
                    ["send-message", "--message-file", str(path), "--telegram-id", "424242"]
                )
        self.assertEqual(code, 0)
        s = out.getvalue()
        self.assertIn("424242", s)
        self.assertIn("preview body", s)
        self.assertIn("dry-run", s.lower())

    def test_row_with_empty_telegram_id_returns_1(self) -> None:
        path = self._msg_file()
        with patch("magister_checking.bot.config.load_config", return_value=MagicMock()), patch(
            "magister_checking.bot.sheets_repo.get_worksheet", return_value=MagicMock()
        ), patch(
            "magister_checking.bot.sheets_repo.get_telegram_id_at_row", return_value=""
        ):
            code = cli.main(["send-message", "--message-file", str(path), "--row", "3"])
        self.assertEqual(code, 1)

    def test_row_less_than_2_returns_2(self) -> None:
        path = self._msg_file()
        with patch("magister_checking.bot.config.load_config", return_value=MagicMock()):
            code = cli.main(["send-message", "--message-file", str(path), "--row", "1"])
        self.assertEqual(code, 2)

    def test_rows_dry_run_unique_chat_ids(self) -> None:
        path = self._msg_file("групповой текст")

        cfg = MagicMock()

        rows_map = {
            5: "90001",
            7: "90002",
            9: "90003",
            10: "90004",
        }

        def _tid(ws: object, row: int) -> str:
            return rows_map[row]

        out = io.StringIO()
        with patch("magister_checking.bot.config.load_config", return_value=cfg):
            with patch("magister_checking.bot.sheets_repo.get_worksheet", return_value=MagicMock()):
                with patch(
                    "magister_checking.bot.sheets_repo.get_telegram_id_at_row",
                    side_effect=_tid,
                ):
                    with redirect_stdout(out):
                        code = cli.main(
                            [
                                "send-message",
                                "--message-file",
                                str(path),
                                "--rows",
                                "5",
                                "7",
                                "9",
                                "10",
                            ]
                        )

        self.assertEqual(code, 0)
        s = out.getvalue()
        self.assertIn("Получателей: 4", s)
        self.assertIn("90001", s)
        self.assertIn("групповой текст", s)

    def test_rows_duplicate_telegrams_deduplicated(self) -> None:
        path = self._msg_file("x")

        cfg = MagicMock()
        rows_map = {5: "111", 10: "111"}

        def _tid(ws: object, row: int) -> str:
            return rows_map[row]

        out = io.StringIO()
        with patch("magister_checking.bot.config.load_config", return_value=cfg):
            with patch("magister_checking.bot.sheets_repo.get_worksheet", return_value=MagicMock()):
                with patch(
                    "magister_checking.bot.sheets_repo.get_telegram_id_at_row",
                    side_effect=_tid,
                ):
                    with redirect_stdout(out):
                        code = cli.main(
                            [
                                "send-message",
                                "--message-file",
                                str(path),
                                "--rows",
                                "5",
                                "10",
                            ]
                        )

        self.assertEqual(code, 0)
        s = out.getvalue()
        self.assertIn("Получателей: 1", s)


if __name__ == "__main__":
    unittest.main()
