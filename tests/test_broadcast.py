"""Тесты broadcast-логики: коллекторы ID, send_broadcast, форматтеры."""

from __future__ import annotations

import asyncio
import os
import pickle
import tempfile
import unittest
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from magister_checking.broadcast import (
    BroadcastResult,
    collect_chat_ids_from_persistence,
    format_dry_run_preview,
    format_send_summary,
    merge_dedup,
    send_broadcast,
)
from magister_checking.bot.sheets_repo import (
    SHEET_HEADER,
    list_registered_telegram_ids,
)
from tests.bot.test_sheets_repo import FakeWorksheet


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class MergeDedupTests(unittest.TestCase):
    def test_keeps_first_occurrence_order(self) -> None:
        self.assertEqual(
            merge_dedup(["1", "2", "3"], ["2", "4", "1", "5"]),
            ["1", "2", "3", "4", "5"],
        )

    def test_strips_whitespace_and_drops_empties(self) -> None:
        self.assertEqual(
            merge_dedup(["  111  ", "", " ", "222"], ["222 ", "333"]),
            ["111", "222", "333"],
        )

    def test_empty_inputs(self) -> None:
        self.assertEqual(merge_dedup(), [])
        self.assertEqual(merge_dedup([], [], []), [])


class CollectFromPersistenceTests(unittest.TestCase):
    def _new_tmp_path(self, suffix: str = ".pickle") -> Path:
        # Windows: mkstemp оставляет fd открытым → файл блокируется до os.close.
        # Сразу закрываем дескриптор, оставляем только путь, файл удалим в cleanup.
        fd, raw = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        path = Path(raw)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def _write_pickle(self, payload: object) -> Path:
        path = self._new_tmp_path()
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
        return path

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(
            collect_chat_ids_from_persistence(Path("/nonexistent/path.pickle")),
            [],
        )

    def test_collects_chat_data_and_user_data_with_dedup(self) -> None:
        path = self._write_pickle(
            {
                "chat_data": {111: {"foo": 1}, 222: {"bar": 2}},
                "user_data": {222: {"baz": 3}, 333: {"qux": 4}},
            }
        )
        ids = collect_chat_ids_from_persistence(path)
        self.assertEqual(ids, ["111", "222", "333"])

    def test_corrupt_pickle_returns_empty(self) -> None:
        path = self._new_tmp_path()
        path.write_bytes(b"not a real pickle")
        self.assertEqual(collect_chat_ids_from_persistence(path), [])

    def test_pickle_with_non_dict_returns_empty(self) -> None:
        path = self._write_pickle(["not a dict"])
        self.assertEqual(collect_chat_ids_from_persistence(path), [])

    def test_missing_sections_are_tolerated(self) -> None:
        path = self._write_pickle({"chat_data": {123: {}}})
        self.assertEqual(collect_chat_ids_from_persistence(path), ["123"])


class ListRegisteredTelegramIdsTests(unittest.TestCase):
    """Sanity-чек хелпера, который broadcast CLI вызывает на боевом листе."""

    def _row(self, telegram_id: str) -> List[str]:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = telegram_id
        return row

    def test_extracts_unique_non_empty_ids_skipping_header(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                self._row("111"),
                self._row("222"),
                self._row(""),
                self._row("111"),
                self._row(" 333 "),
            ]
        )
        self.assertEqual(list_registered_telegram_ids(ws), ["111", "222", "333"])

    def test_no_telegram_id_column_returns_empty(self) -> None:
        ws = FakeWorksheet([["fio", "group_name"], ["Иванов", "М-101"]])
        self.assertEqual(list_registered_telegram_ids(ws), [])


class SendBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def _bot_mock(self) -> MagicMock:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        return bot

    async def test_happy_path_sends_to_all_recipients(self) -> None:
        bot = self._bot_mock()
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        result = await send_broadcast(
            bot, ["111", "222"], "hello", sleep_between=0.1, sleep=fake_sleep
        )

        self.assertEqual(result.sent, ["111", "222"])
        self.assertEqual(result.failed, [])
        self.assertEqual(result.skipped_invalid, [])
        self.assertEqual(bot.send_message.await_count, 2)
        bot.send_message.assert_any_await(chat_id=111, text="hello")
        bot.send_message.assert_any_await(chat_id=222, text="hello")
        self.assertEqual(sleep_calls, [0.1, 0.1])

    async def test_forbidden_is_recorded_and_does_not_stop_loop(self) -> None:
        bot = self._bot_mock()
        bot.send_message.side_effect = [
            Forbidden("blocked"),
            None,
            BadRequest("chat not found"),
        ]

        async def fake_sleep(_: float) -> None:
            return None

        result = await send_broadcast(
            bot, ["1", "2", "3"], "msg", sleep_between=0.0, sleep=fake_sleep
        )

        self.assertEqual(result.sent, ["2"])
        self.assertEqual(len(result.failed), 2)
        self.assertEqual(result.failed[0][0], "1")
        self.assertIn("Forbidden", result.failed[0][1])
        self.assertEqual(result.failed[1][0], "3")
        self.assertIn("BadRequest", result.failed[1][1])

    async def test_retry_after_waits_then_retries_once(self) -> None:
        bot = self._bot_mock()
        bot.send_message.side_effect = [RetryAfter(2.5), None]
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        result = await send_broadcast(
            bot, ["999"], "msg", sleep_between=0.0, sleep=fake_sleep
        )

        self.assertEqual(result.sent, ["999"])
        self.assertEqual(result.failed, [])
        self.assertEqual(bot.send_message.await_count, 2)
        # Первый sleep — ожидание retry, минимум 1.0
        self.assertGreaterEqual(sleep_calls[0], 1.0)

    async def test_retry_after_failing_second_attempt_is_recorded(self) -> None:
        bot = self._bot_mock()
        bot.send_message.side_effect = [RetryAfter(1), Forbidden("still blocked")]

        async def fake_sleep(_: float) -> None:
            return None

        result = await send_broadcast(
            bot, ["999"], "msg", sleep_between=0.0, sleep=fake_sleep
        )

        self.assertEqual(result.sent, [])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0][0], "999")
        self.assertIn("Forbidden", result.failed[0][1])

    async def test_network_error_is_recorded_without_retry(self) -> None:
        bot = self._bot_mock()
        bot.send_message.side_effect = NetworkError("dns fail")

        async def fake_sleep(_: float) -> None:
            return None

        result = await send_broadcast(
            bot, ["111"], "msg", sleep_between=0.0, sleep=fake_sleep
        )

        self.assertEqual(result.sent, [])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertIn("NetworkError", result.failed[0][1])

    async def test_invalid_id_is_skipped_not_failed(self) -> None:
        bot = self._bot_mock()

        async def fake_sleep(_: float) -> None:
            return None

        result = await send_broadcast(
            bot,
            ["111", "abc", "  ", "222"],
            "msg",
            sleep_between=0.0,
            sleep=fake_sleep,
        )

        self.assertEqual(result.sent, ["111", "222"])
        self.assertEqual(result.failed, [])
        self.assertEqual(result.skipped_invalid, ["abc"])
        self.assertEqual(bot.send_message.await_count, 2)


class FormattersTests(unittest.TestCase):
    def test_dry_run_preview_includes_recipients_and_message(self) -> None:
        out = format_dry_run_preview(
            ["111", "222"], "Hi everyone", source_label="Регистрация (2 ID)"
        )
        self.assertIn("DRY-RUN", out)
        self.assertIn("Регистрация (2 ID)", out)
        self.assertIn("Получателей: 2", out)
        self.assertIn("  111", out)
        self.assertIn("  222", out)
        self.assertIn("Hi everyone", out)
        self.assertIn("--send", out)

    def test_dry_run_preview_handles_empty_list(self) -> None:
        out = format_dry_run_preview([], "msg", source_label="источник")
        self.assertIn("Получателей: 0", out)
        self.assertIn("(пусто)", out)

    def test_send_summary_renders_failures_and_invalid(self) -> None:
        result = BroadcastResult(
            sent=["111", "222"],
            failed=[("333", "Forbidden: blocked")],
            skipped_invalid=["abc"],
        )
        out = format_send_summary(result)
        self.assertIn("Отправлено успешно: 2", out)
        self.assertIn("Ошибки доставки:    1", out)
        self.assertIn("Пропущено (битые):  1", out)
        self.assertIn("333: Forbidden: blocked", out)
        self.assertIn("'abc'", out)


if __name__ == "__main__":
    unittest.main()
