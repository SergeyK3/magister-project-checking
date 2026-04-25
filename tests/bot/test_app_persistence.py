"""Тесты PicklePersistence: build_application подключает хранилище состояний."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram.ext import ConversationHandler, PicklePersistence

from magister_checking.bot.app import build_application
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


if __name__ == "__main__":
    unittest.main()
