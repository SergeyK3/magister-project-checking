"""Тесты алертов B3: формат сообщения и вызов send_message."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from telegram import Chat, Message, Update, User
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from magister_checking.bot.app import build_application
from magister_checking.bot.config import BotConfig
from magister_checking.bot.error_alerts import format_handler_error_html, on_handler_error
from magister_checking.bot.handlers import CONFIG_BOT_DATA_KEY


def _minimal_config(*, alert: tuple[int, ...] = (999,)) -> BotConfig:
    return BotConfig(
        telegram_bot_token="123:ABCdefGHIjklMNOpqrstUVwxyz1234567890",
        spreadsheet_id="sheet123",
        worksheet_name="Регистрация",
        project_card_output_folder_url="",
        google_service_account_json=Path("credentials/unused.json"),
        log_level=20,
        persistence_file=Path("state/x.pickle"),
        alert_chat_ids=alert,
        project_snapshot_output_folder_urls=(),
    )


class FormatHandlerErrorHtmlTests(unittest.TestCase):
    def test_includes_exception_name_and_traceback(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.error = exc
            html_out = format_handler_error_html(MagicMock(), ctx)
        self.assertIn("ValueError", html_out)
        self.assertIn("boom", html_out)
        self.assertIn("<pre>", html_out)
        self.assertIn("magistrcheckbot", html_out)

    def test_includes_user_and_chat_from_update(self) -> None:
        try:
            raise RuntimeError("x")
        except RuntimeError as exc:
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.error = exc
            chat = Chat(id=-100, type="group")
            user = User(id=42, first_name="a", is_bot=False)
            msg = Message(
                message_id=1,
                date=datetime.now(tz=timezone.utc),
                chat=chat,
                from_user=user,
            )
            upd = Update(update_id=1, message=msg)
            html_out = format_handler_error_html(upd, ctx)
        self.assertIn("user_id=42", html_out)
        self.assertIn("chat_id=-100", html_out)


class OnHandlerErrorTests(unittest.TestCase):
    def test_skips_send_when_no_alert_chats(self) -> None:
        async def _run() -> None:
            cfg = _minimal_config(alert=())
            application = build_application(cfg)
            send_mock = AsyncMock()
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.application = application
            ctx.bot = MagicMock()
            ctx.bot.send_message = send_mock
            ctx.error = RuntimeError("fail")
            self.assertEqual(application.bot_data.get(CONFIG_BOT_DATA_KEY), cfg)
            await on_handler_error(MagicMock(), ctx)
            send_mock.assert_not_awaited()

        asyncio.run(_run())

    def test_sends_to_all_configured_chats(self) -> None:
        async def _run() -> None:
            cfg = _minimal_config(alert=(111, 222))
            application = build_application(cfg)
            send_mock = AsyncMock()
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.application = application
            ctx.bot = MagicMock()
            ctx.bot.send_message = send_mock
            ctx.error = ValueError("oops")
            await on_handler_error(MagicMock(), ctx)
            self.assertEqual(send_mock.await_count, 2)
            chats = [c.kwargs["chat_id"] for c in send_mock.await_args_list]
            self.assertEqual(chats, [111, 222])

        asyncio.run(_run())

    def test_google_sheets_429_skips_alerts(self) -> None:
        """Временный лимит Google (429) — только warning, без рассылки админам."""

        async def _run() -> None:
            cfg = _minimal_config(alert=(111,))
            application = build_application(cfg)
            send_mock = AsyncMock()
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.application = application
            ctx.bot = MagicMock()
            ctx.bot.send_message = send_mock
            ctx.error = RuntimeError("APIError: [429]: Quota exceeded")
            await on_handler_error(MagicMock(), ctx)
            send_mock.assert_not_awaited()

        asyncio.run(_run())

    def test_polling_network_error_skips_alerts(self) -> None:
        """PTB передаёт update=None при ошибке get_updates; не спамим алертами."""

        async def _run() -> None:
            cfg = _minimal_config(alert=(111,))
            application = build_application(cfg)
            send_mock = AsyncMock()
            ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
            ctx.application = application
            ctx.bot = MagicMock()
            ctx.bot.send_message = send_mock
            ctx.error = NetworkError("httpx.ConnectError: [Errno 11001] getaddrinfo failed")
            await on_handler_error(None, ctx)
            send_mock.assert_not_awaited()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
