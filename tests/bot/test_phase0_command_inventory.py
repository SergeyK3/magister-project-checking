"""Phase 0 characterization tests for Telegram command/routing inventory."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram.ext import CommandHandler, ConversationHandler, MessageHandler

from magister_checking.bot import handlers as h
from magister_checking.bot.app import (
    RUSSIAN_EXIT_COMMAND_PATTERN,
    RUSSIAN_SPRAVKA_COMMAND_PATTERN,
    RUSSIAN_START_COMMAND_PATTERN,
    build_application,
)
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


def _command_tuple(handler: CommandHandler) -> tuple[tuple[str, ...], str]:
    return (tuple(sorted(handler.commands)), handler.callback.__name__)


def _command_inventory(items: list[object]) -> list[tuple[tuple[str, ...], str]]:
    return [_command_tuple(item) for item in items if isinstance(item, CommandHandler)]


class Phase0CommandInventoryTests(unittest.TestCase):
    def test_bot_command_menu_exact_inventory(self) -> None:
        self.assertEqual(
            [(c.command, c.description) for c in h.default_bot_commands()],
            [
                ("start", "Запуск и регистрация"),
                ("status", "Проверить магистранта"),
                ("unreg", "Кто не зарегистрировался"),
                ("reg_list", "Кто зарегистрировался"),
                ("student_message", "Сообщение магистранту"),
                ("student_message_bulk", "Групповое напоминание"),
                ("supervisor_message", "Сообщение научруку"),
                ("about", "О проекте"),
            ],
        )

    def test_current_fsm_state_numbers_are_frozen(self) -> None:
        self.assertEqual(
            {
                "ASK_FIELD": h.ASK_FIELD,
                "ASK_CONFIRM": h.ASK_CONFIRM,
                "BIND_ASK_FIO": h.BIND_ASK_FIO,
                "BIND_CONFIRM": h.BIND_CONFIRM,
                "PROJECT_CARD_ASK_TARGET": h.PROJECT_CARD_ASK_TARGET,
                "SPRAVKA_MENU": h.SPRAVKA_MENU,
                "SPRAVKA_ASK_TARGET": h.SPRAVKA_ASK_TARGET,
                "ROLE_PICK": h.ROLE_PICK,
                "CLAIM_ASK_FIO": h.CLAIM_ASK_FIO,
                "CLAIM_CONFIRM": h.CLAIM_CONFIRM,
                "STUDENT_MSG_ASK_TARGET": h.STUDENT_MSG_ASK_TARGET,
                "STUDENT_MSG_PICK_KIND": h.STUDENT_MSG_PICK_KIND,
                "STUDENT_MSG_ASK_EXTRA": h.STUDENT_MSG_ASK_EXTRA,
                "STUDENT_MSG_ASK_CUSTOM": h.STUDENT_MSG_ASK_CUSTOM,
                "STUDENT_MSG_CONFIRM": h.STUDENT_MSG_CONFIRM,
                "STUDENT_MSG_BULK_ASK_ROWS": h.STUDENT_MSG_BULK_ASK_ROWS,
                "STUDENT_MSG_BULK_CONFIRM": h.STUDENT_MSG_BULK_CONFIRM,
                "PIN_VERIFY_INPUT": h.PIN_VERIFY_INPUT,
                "SUPERVISOR_MSG_ASK_TARGET": h.SUPERVISOR_MSG_ASK_TARGET,
                "SUPERVISOR_MSG_CONFIRM": h.SUPERVISOR_MSG_CONFIRM,
            },
            {
                "ASK_FIELD": 0,
                "ASK_CONFIRM": 1,
                "BIND_ASK_FIO": 2,
                "BIND_CONFIRM": 3,
                "PROJECT_CARD_ASK_TARGET": 4,
                "SPRAVKA_MENU": 5,
                "SPRAVKA_ASK_TARGET": 6,
                "ROLE_PICK": 7,
                "CLAIM_ASK_FIO": 8,
                "CLAIM_CONFIRM": 9,
                "STUDENT_MSG_ASK_TARGET": 10,
                "STUDENT_MSG_PICK_KIND": 11,
                "STUDENT_MSG_ASK_EXTRA": 12,
                "STUDENT_MSG_ASK_CUSTOM": 13,
                "STUDENT_MSG_CONFIRM": 14,
                "STUDENT_MSG_BULK_ASK_ROWS": 15,
                "STUDENT_MSG_BULK_CONFIRM": 16,
                "PIN_VERIFY_INPUT": 17,
                "SUPERVISOR_MSG_ASK_TARGET": 18,
                "SUPERVISOR_MSG_CONFIRM": 19,
            },
        )

    def test_application_top_level_command_routing_inventory(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))

        self.assertEqual(
            _command_inventory(app.handlers.get(-1, [])),
            [(("help",), "help_command")],
        )
        self.assertEqual(
            _command_inventory(app.handlers.get(0, [])),
            [
                (("start",), "group_start_use_private_chat"),
                (("admin",), "admin_menu"),
                (("stats",), "admin_stats"),
                (("ops_row",), "ops_row"),
                (("sync_dashboard",), "admin_sync_dashboard"),
                (("sync_magistrants",), "admin_sync_magistrants"),
                (("status",), "status_command"),
                (("about",), "about_command"),
                (("unreg",), "supervisor_unregistered_list_command"),
                (("reg_list",), "supervisor_registered_list_command"),
                (("recheck",), "recheck"),
            ],
        )

    def test_conversation_command_routing_inventory(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))
        conv = next(
            item
            for item in app.handlers.get(0, [])
            if isinstance(item, ConversationHandler)
        )

        self.assertEqual(
            _command_inventory(conv.entry_points),
            [
                (("cancel",), "cancel"),
                (("start",), "start"),
                (("register",), "register_command"),
                (("project_card",), "project_card_start"),
                (("student_message",), "student_reminder_start"),
                (("student_message_bulk",), "student_message_bulk_start"),
                (("supervisor_message",), "supervisor_message_start"),
                (("spravka",), "spravka_start"),
                (("about",), "about_command"),
                (("status",), "status_command"),
            ],
        )
        self.assertEqual(
            [
                item.callback.__name__
                for item in conv.entry_points
                if isinstance(item, MessageHandler)
            ][:2],
            ["cancel", "start"],
        )
        self.assertEqual(
            sorted(conv.states.keys()),
            sorted(
                [
                h.ASK_FIELD,
                h.ASK_CONFIRM,
                h.BIND_ASK_FIO,
                h.BIND_CONFIRM,
                h.CLAIM_ASK_FIO,
                h.CLAIM_CONFIRM,
                h.PIN_VERIFY_INPUT,
                h.PROJECT_CARD_ASK_TARGET,
                h.ROLE_PICK,
                h.SPRAVKA_ASK_TARGET,
                h.SPRAVKA_MENU,
                h.STUDENT_MSG_ASK_CUSTOM,
                h.STUDENT_MSG_ASK_EXTRA,
                h.STUDENT_MSG_ASK_TARGET,
                h.STUDENT_MSG_BULK_ASK_ROWS,
                h.STUDENT_MSG_BULK_CONFIRM,
                h.STUDENT_MSG_CONFIRM,
                h.STUDENT_MSG_PICK_KIND,
                h.SUPERVISOR_MSG_ASK_TARGET,
                h.SUPERVISOR_MSG_CONFIRM,
                ]
            ),
        )
        self.assertEqual(
            _command_inventory(conv.fallbacks),
            [
                (("cancel",), "cancel"),
                (("start",), "start"),
                (("register",), "register_command"),
                (("project_card",), "project_card_start"),
                (("student_message",), "student_reminder_start"),
                (("student_message_bulk",), "student_message_bulk_start"),
                (("supervisor_message",), "supervisor_message_start"),
                (("spravka",), "spravka_start"),
                (("about",), "about_command"),
                (("status",), "status_command"),
            ],
        )
        self.assertEqual(
            [
                item.callback.__name__
                for item in conv.fallbacks
                if isinstance(item, MessageHandler)
            ][:3],
            ["cancel", "start", "spravka_start"],
        )
        self.assertEqual(
            _command_inventory(conv.states[h.ASK_FIELD]),
            [(("skip",), "skip_field")],
        )
        self.assertEqual(
            _command_inventory(conv.states[h.BIND_ASK_FIO]),
            [(("skip",), "skip_bind")],
        )

    def test_json_snapshot_handler_stays_readonly_side_channel(self) -> None:
        with TemporaryDirectory() as tmp:
            app = build_application(_make_config(Path(tmp) / "state.pickle"))

        group_1_message_handlers = [
            item for item in app.handlers.get(1, []) if isinstance(item, MessageHandler)
        ]
        self.assertEqual(len(group_1_message_handlers), 1)
        self.assertIs(group_1_message_handlers[0].callback, h.on_project_snapshot_json_file)

    def test_public_russian_command_alias_patterns_are_registered(self) -> None:
        self.assertEqual(RUSSIAN_START_COMMAND_PATTERN, r"^/старт(?:@\w+)?(?:\s|$)")
        self.assertEqual(RUSSIAN_SPRAVKA_COMMAND_PATTERN, r"^/справка(?:@\w+)?(?:\s|$)")
        self.assertEqual(RUSSIAN_EXIT_COMMAND_PATTERN, r"^/выход(?:@\w+)?(?:\s|$)")


if __name__ == "__main__":
    unittest.main()
