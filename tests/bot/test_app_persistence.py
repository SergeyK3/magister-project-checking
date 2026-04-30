"""Тесты PicklePersistence: build_application подключает хранилище состояний."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ConversationHandler, MessageHandler, PicklePersistence

from magister_checking.bot.app import _post_init, build_application
from magister_checking.bot.handlers import on_project_snapshot_json_file
from magister_checking.bot.config import BotConfig


def _make_config(persistence_file: Path) -> BotConfig:
    return BotConfig(
        telegram_bot_token="123:ABCdefGHIjklMNOpqrstUVwxyz1234567890",
        spreadsheet_id="sheet123",
        worksheet_name="Регистрация",
        project_card_output_folder_url="",
        google_service_account_json=Path("credentials/unused.json"),
        log_level=20,
        persistence_file=persistence_file,
        project_snapshot_output_folder_urls=(),
        magistrants_worksheet_name="",
    )


class BuildApplicationPersistenceTests(unittest.TestCase):
    def test_application_wired_with_pickle_persistence(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "nested" / "state.pickle"
            app = build_application(_make_config(state_path))

            self.assertIsInstance(app.persistence, PicklePersistence)
            self.assertEqual(Path(app.persistence.filepath), state_path)
            self.assertTrue(
                state_path.parent.is_dir(),
                msg="build_application должен создать каталог для pickle-файла",
            )

    def test_conversation_handler_is_persistent_and_named(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))

            conv_handlers = [
                h
                for handlers in app.handlers.values()
                for h in handlers
                if isinstance(h, ConversationHandler)
            ]
            self.assertTrue(conv_handlers, "ConversationHandler не зарегистрирован")
            conv = conv_handlers[0]
            self.assertTrue(getattr(conv, "persistent", False))
            self.assertEqual(getattr(conv, "name", None), "registration")

    def test_error_handler_registered(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))
            self.assertTrue(
                getattr(app, "error_handlers", ()),
                msg="PTB Application должен иметь error_handlers после B3",
            )

    def test_help_handler_high_priority_group(self) -> None:
        """C1: /help в group=-1, чтобы справка не терялась внутри диалога."""

        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))
            handlers_m1 = app.handlers.get(-1, [])
            self.assertEqual(len(handlers_m1), 1)
            self.assertIn("help", handlers_m1[0].commands)

    def test_json_snapshot_message_handler_in_group_1(self) -> None:
        """В group=1: приём .json (project snapshot) админом — см. build_application."""

        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))
            in_g1 = app.handlers.get(1, [])
            self.assertTrue(in_g1, "ожидается хотя бы один handler в group=1")
            mh = [h for h in in_g1 if isinstance(h, MessageHandler)]
            self.assertTrue(mh, "в group=1 ожидается MessageHandler")
            self.assertIs(mh[0].callback, on_project_snapshot_json_file)

    def test_post_init_registers_bot_commands(self) -> None:
        """C1: при старте вызывается set_my_commands."""

        app = MagicMock()
        app.bot.set_my_commands = AsyncMock()
        asyncio.run(_post_init(app))
        app.bot.set_my_commands.assert_awaited_once()
        cmds = app.bot.set_my_commands.await_args.args[0]
        self.assertTrue(any(getattr(c, "command", None) == "help" for c in cmds))


if __name__ == "__main__":
    unittest.main()
