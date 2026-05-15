"""Phase 4B persistence survival structural contracts."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram.ext import ConversationHandler, PersistenceInput, PicklePersistence

from magister_checking.bot.app import build_application
from magister_checking.bot.config import BotConfig
from magister_checking.bot.handlers import CONFIG_BOT_DATA_KEY


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


def _conversation_handlers(app: object) -> list[ConversationHandler]:
    return [
        handler
        for handlers in app.handlers.values()  # type: ignore[attr-defined]
        for handler in handlers
        if isinstance(handler, ConversationHandler)
    ]


def _registration_conversation_identity(
    conv: ConversationHandler,
) -> tuple[str, bool, bool, bool, bool]:
    return (
        conv.name,
        conv.persistent,
        conv.per_chat,
        conv.per_user,
        conv.per_message,
    )


class Phase4BPersistenceSurvivalTests(unittest.TestCase):
    def test_pickle_persistence_uses_configured_stable_filepath(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "nested" / "magistrcheckbot_state.pickle"
            app = build_application(_make_config(state_path))

            self.assertIsInstance(app.persistence, PicklePersistence)
            self.assertEqual(Path(app.persistence.filepath), state_path)
            self.assertTrue(state_path.parent.is_dir())

    def test_pickle_persistence_store_data_contract_is_minimal(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))

            self.assertEqual(
                app.persistence.store_data,
                PersistenceInput(
                    bot_data=False,
                    chat_data=True,
                    user_data=True,
                    callback_data=False,
                ),
            )
            self.assertFalse(app.persistence.store_data.bot_data)
            self.assertTrue(app.persistence.store_data.chat_data)
            self.assertTrue(app.persistence.store_data.user_data)
            self.assertFalse(app.persistence.store_data.callback_data)

    def test_runtime_bot_config_is_not_persisted_with_bot_data(self) -> None:
        with TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp) / "state.pickle")
            app = build_application(config)

            self.assertIs(app.bot_data[CONFIG_BOT_DATA_KEY], config)
            self.assertFalse(app.persistence.store_data.bot_data)

    def test_conversation_persistence_identity_survives_rebuild(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.pickle"
            app_before_restart = build_application(_make_config(state_path))
            app_after_restart = build_application(_make_config(state_path))

            before = _conversation_handlers(app_before_restart)
            after = _conversation_handlers(app_after_restart)

            self.assertEqual(len(before), 1)
            self.assertEqual(len(after), 1)
            self.assertEqual(
                _registration_conversation_identity(before[0]),
                ("registration", True, True, True, False),
            )
            self.assertEqual(
                _registration_conversation_identity(after[0]),
                _registration_conversation_identity(before[0]),
            )
            self.assertEqual(
                Path(app_after_restart.persistence.filepath),
                Path(app_before_restart.persistence.filepath),
            )


if __name__ == "__main__":
    unittest.main()
