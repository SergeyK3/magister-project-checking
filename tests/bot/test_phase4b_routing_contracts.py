"""Phase 4B characterization tests for Telegram routing contracts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
)

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


def _build_test_application():
    tmp = TemporaryDirectory()
    app = build_application(_make_config(Path(tmp.name) / "state.pickle"))
    return app, tmp


def _callback_name(handler: object) -> str:
    return handler.callback.__name__  # type: ignore[attr-defined]


def _command_route(handler: CommandHandler) -> tuple[tuple[str, ...], str]:
    return tuple(sorted(handler.commands)), _callback_name(handler)


def _message_routes(handlers: list[object]) -> list[str]:
    return [_callback_name(item) for item in handlers if isinstance(item, MessageHandler)]


def _command_routes(handlers: list[object]) -> list[tuple[tuple[str, ...], str]]:
    return [_command_route(item) for item in handlers if isinstance(item, CommandHandler)]


def _callback_routes(handlers: list[object]) -> list[tuple[str, str]]:
    return [
        (_pattern_text(item.pattern), _callback_name(item))
        for item in handlers
        if isinstance(item, CallbackQueryHandler)
    ]


def _pattern_text(pattern: object) -> str:
    if isinstance(pattern, re.Pattern):
        return pattern.pattern
    return str(pattern)


class Phase4BRoutingContractTests(unittest.TestCase):
    def test_single_persistent_registration_conversation_contract(self) -> None:
        app, tmp = _build_test_application()
        try:
            conversations = [
                item
                for group_handlers in app.handlers.values()
                for item in group_handlers
                if isinstance(item, ConversationHandler)
            ]

            self.assertEqual(len(conversations), 1)
            conv = conversations[0]
            self.assertEqual(conv.name, "registration")
            self.assertTrue(conv.persistent)
            self.assertTrue(conv.allow_reentry)
            self.assertFalse(conv.per_message)
            self.assertEqual(
                sorted(conv.states),
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
        finally:
            tmp.cleanup()

    def test_top_level_routes_keep_public_legacy_and_side_channel_order(self) -> None:
        app, tmp = _build_test_application()
        try:
            self.assertEqual(
                _command_routes(app.handlers.get(-1, [])),
                [(("help",), "help_command")],
            )

            group_zero = app.handlers.get(0, [])
            self.assertEqual(
                [type(item).__name__ for item in group_zero],
                [
                    "CommandHandler",
                    "ChatJoinRequestHandler",
                    "ConversationHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "CommandHandler",
                    "MessageHandler",
                    "CallbackQueryHandler",
                ],
            )
            self.assertEqual(
                _command_routes(group_zero),
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
            self.assertEqual(
                _message_routes(group_zero),
                ["admin_recheck_pending_receive"],
            )
            self.assertEqual(
                _callback_routes(group_zero),
                [(h.RECHECK_CALLBACK_PATTERN, "recheck_button")],
            )

            group_one_messages = [
                item for item in app.handlers.get(1, []) if isinstance(item, MessageHandler)
            ]
            self.assertEqual(len(group_one_messages), 1)
            self.assertIs(group_one_messages[0].callback, h.on_project_snapshot_json_file)
        finally:
            tmp.cleanup()

    def test_conversation_entry_and_fallback_routes_are_additive(self) -> None:
        app, tmp = _build_test_application()
        try:
            conv = next(
                item
                for item in app.handlers.get(0, [])
                if isinstance(item, ConversationHandler)
            )

            self.assertEqual(
                _message_routes(conv.entry_points),
                [
                    "cancel",
                    "start",
                    "spravka_start",
                    "project_card_start",
                    "student_reminder_start",
                    "student_message_bulk_start",
                    "supervisor_message_start",
                    "role_menu_spravka",
                    "role_menu_register",
                    "role_menu_status",
                    "role_menu_help",
                    "role_menu_about",
                    "supervisor_menu_status",
                    "supervisor_unregistered_list_command",
                    "supervisor_registered_list_command",
                    "admin_stats",
                ],
            )
            self.assertEqual(
                _command_routes(conv.entry_points),
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
                _message_routes(conv.fallbacks),
                [
                    "cancel",
                    "start",
                    "spravka_start",
                    "project_card_start",
                    "student_reminder_start",
                    "student_message_bulk_start",
                    "supervisor_message_start",
                    "role_menu_spravka",
                    "role_menu_register",
                    "role_menu_status",
                    "role_menu_help",
                    "role_menu_about",
                    "supervisor_menu_status",
                    "supervisor_unregistered_list_command",
                    "supervisor_registered_list_command",
                    "admin_stats",
                ],
            )
            self.assertEqual(
                _command_routes(conv.fallbacks),
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
        finally:
            tmp.cleanup()

    def test_conversation_state_routes_freeze_callback_namespaces(self) -> None:
        app, tmp = _build_test_application()
        try:
            conv = next(
                item
                for item in app.handlers.get(0, [])
                if isinstance(item, ConversationHandler)
            )

            self.assertEqual(
                _callback_routes(conv.states[h.ROLE_PICK]),
                [(r"^start:", "start_role_callback")],
            )
            self.assertEqual(
                _command_routes(conv.states[h.BIND_ASK_FIO]),
                [(("skip",), "skip_bind")],
            )
            self.assertEqual(
                _message_routes(conv.states[h.BIND_ASK_FIO]),
                ["receive_bind_fio"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.BIND_CONFIRM]),
                ["confirm_bind"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.CLAIM_ASK_FIO]),
                ["receive_claim_fio"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.CLAIM_CONFIRM]),
                ["confirm_claim"],
            )
            self.assertEqual(
                _command_routes(conv.states[h.ASK_FIELD]),
                [(("skip",), "skip_field")],
            )
            self.assertEqual(_message_routes(conv.states[h.ASK_FIELD]), ["receive_field"])
            self.assertEqual(_message_routes(conv.states[h.ASK_CONFIRM]), ["ask_confirm"])
            self.assertEqual(
                _message_routes(conv.states[h.PIN_VERIFY_INPUT]),
                ["receive_pin_input"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.PROJECT_CARD_ASK_TARGET]),
                ["project_card_receive_target"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.STUDENT_MSG_ASK_TARGET]),
                ["student_reminder_receive_target"],
            )
            self.assertEqual(
                _callback_routes(conv.states[h.STUDENT_MSG_PICK_KIND]),
                [(h.ADMSTU_CALLBACK_TEMPLATE_PATTERN, "student_reminder_pick_template")],
            )
            self.assertEqual(
                _message_routes(conv.states[h.STUDENT_MSG_ASK_EXTRA]),
                ["student_reminder_receive_extra"],
            )
            self.assertEqual(
                _message_routes(conv.states[h.STUDENT_MSG_ASK_CUSTOM]),
                ["student_reminder_receive_custom"],
            )
            self.assertEqual(
                _callback_routes(conv.states[h.STUDENT_MSG_CONFIRM]),
                [(h.ADMSTU_CALLBACK_CONFIRM_PATTERN, "student_reminder_confirm_callback")],
            )
            self.assertEqual(
                _message_routes(conv.states[h.STUDENT_MSG_BULK_ASK_ROWS]),
                ["student_reminder_bulk_receive_rows"],
            )
            self.assertEqual(
                _callback_routes(conv.states[h.STUDENT_MSG_BULK_CONFIRM]),
                [(h.ADMSTUB_CALLBACK_CONFIRM_PATTERN, "student_reminder_bulk_confirm_callback")],
            )
            self.assertEqual(
                _message_routes(conv.states[h.SUPERVISOR_MSG_ASK_TARGET]),
                ["supervisor_message_receive_target"],
            )
            self.assertEqual(
                _callback_routes(conv.states[h.SUPERVISOR_MSG_CONFIRM]),
                [(h.ADMSUPMSG_CALLBACK_CONFIRM_PATTERN, "supervisor_message_confirm_callback")],
            )
            self.assertEqual(
                _callback_routes(conv.states[h.SPRAVKA_MENU]),
                [(r"^spravka:(telegram|pdf|commission)$", "spravka_choose")],
            )
            self.assertEqual(
                _message_routes(conv.states[h.SPRAVKA_ASK_TARGET]),
                ["spravka_receive_target"],
            )
        finally:
            tmp.cleanup()

    def test_public_russian_alias_patterns_are_contractual_additions(self) -> None:
        self.assertEqual(RUSSIAN_START_COMMAND_PATTERN, r"^/старт(?:@\w+)?(?:\s|$)")
        self.assertEqual(RUSSIAN_SPRAVKA_COMMAND_PATTERN, r"^/справка(?:@\w+)?(?:\s|$)")
        self.assertEqual(RUSSIAN_EXIT_COMMAND_PATTERN, r"^/выход(?:@\w+)?(?:\s|$)")


if __name__ == "__main__":
    unittest.main()
