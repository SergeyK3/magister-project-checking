"""Асинхронные тесты Telegram-хендлеров без сети и без токена."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from telegram import InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import BadRequest

from magister_checking.bot import handlers
from magister_checking.bot.handlers import (
    ADMIN_PROJECT_CARD_BUTTON,
    ADMIN_STATS_BUTTON,
    ADMIN_STUDENT_MESSAGE_BULK_BUTTON,
    ADMIN_STUDENT_MESSAGE_BUTTON,
    ADMIN_SUPERVISOR_MESSAGE_BUTTON,
    ASK_CONFIRM,
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    CLAIM_ASK_FIO,
    PROJECT_CARD_ASK_TARGET,
    RECHECK_BUTTON_LABEL,
    RECHECK_CALLBACK_DATA,
    ROLE_PICK,
    ROLE_MENU_SPRAVKA_BUTTON,
    SUPERVISOR_STATUS_BUTTON,
    SUPERVISOR_MSG_ASK_TARGET,
    SUPERVISOR_MSG_CONFIRM,
    help_reply_for_user,
    admin_menu,
    admin_recheck_pending_receive,
    admin_stats,
    admin_sync_dashboard,
    ask_confirm,
    build_recheck_keyboard,
    cancel,
    confirm_bind,
    default_bot_commands,
    help_command,
    project_card_receive_target,
    project_card_start,
    receive_bind_fio,
    receive_field,
    recheck,
    recheck_button,
    skip_bind,
    skip_field,
    spravka_choose,
    spravka_start,
    start,
    start_role_callback,
    supervisor_menu_status,
)
from magister_checking.bot.supervisor_lists import (
    supervisor_unregistered_from_magistrants_registration_report,
)
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.models import SHEET_HEADER, UserForm
from tests.bot.test_sheets_repo import FakeSpreadsheet, FakeWorksheet


class _FakeContext:
    """Мини-замена ContextTypes.DEFAULT_TYPE."""

    def __init__(self, worksheet: FakeWorksheet) -> None:
        self.bot_data: dict = {
            handlers.CONFIG_BOT_DATA_KEY: MagicMock(
                worksheet_name="Регистрация",
                magistrants_worksheet_name="",
                telegram_join_group_chat_id=None,
                telegram_join_group_title="Магистр аттестация КОЗМ",
                telegram_join_group_invite_link="",
            )
        }
        self.user_data: dict = {}
        self.chat_data: dict = {}
        self.args: list[str] = []
        self.bot = MagicMock()
        self.bot.approve_chat_join_request = AsyncMock()
        self.bot.send_message = AsyncMock()
        self.application = MagicMock()
        self.application.chat_data = {}
        self._worksheet = worksheet


def _make_update(
    *,
    user_id: int = 111,
    username: str = "ivanov",
    first_name: str = "Иван",
    last_name: str = "Иванов",
    text: str = "",
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = first_name
    update.effective_user.last_name = last_name
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.effective_message = update.message
    chat = MagicMock()
    chat.type = ChatType.PRIVATE
    update.effective_chat = chat
    return update


def _make_join_request_update(
    *,
    user_id: int = 111,
    chat_id: int = -100123,
    title: str = "Магистр аттестация КОЗМ",
) -> MagicMock:
    update = MagicMock()
    request = MagicMock()
    request.from_user.id = user_id
    request.chat.id = chat_id
    request.chat.title = title
    update.chat_join_request = request
    update.effective_message = None
    return update


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_worksheet(worksheet: FakeWorksheet):
    return patch(
        "magister_checking.bot.handlers.get_worksheet",
        return_value=worksheet,
    )


def _patch_dashboard_sync():
    return patch("magister_checking.bot.handlers.sync_registration_dashboard")


def _patch_admin_check(value: bool):
    return patch("magister_checking.bot.handlers.is_admin_telegram_id", return_value=value)


def _patch_supervisor_check(value: bool):
    return patch(
        "magister_checking.bot.handlers.is_supervisor_telegram_id", return_value=value
    )


class StartHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        handlers.clear_telegram_role_cache()

    def test_start_unknown_user_offers_role_picker(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws), _patch_admin_check(False), _patch_supervisor_check(
            False
        ):
            state = _run(start(update, ctx))

        self.assertEqual(state, ROLE_PICK)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.telegram_id, "111")
        update.message.reply_text.assert_awaited()
        km = update.message.reply_text.await_args.kwargs.get("reply_markup")
        self.assertIsNotNone(km)
        self.assertEqual(len(km.inline_keyboard), 3)

    def test_start_admin_shows_admin_role_menu(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws), _patch_admin_check(True), _patch_supervisor_check(
            False
        ):
            state = _run(start(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(update.message.reply_text.await_count, 2)
        menu_call = update.message.reply_text.await_args_list[-1]
        self.assertIn("Панель администратора", menu_call.args[0])
        km = menu_call.kwargs.get("reply_markup")
        self.assertIsNotNone(km)
        buttons = [getattr(row[0], "text", row[0]) for row in km.keyboard]
        self.assertEqual(buttons[0], ROLE_MENU_SPRAVKA_BUTTON)
        self.assertEqual(buttons[1], ADMIN_STUDENT_MESSAGE_BUTTON)
        self.assertEqual(buttons[2], ADMIN_STUDENT_MESSAGE_BULK_BUTTON)
        self.assertEqual(buttons[3], ADMIN_SUPERVISOR_MESSAGE_BUTTON)
        self.assertIn(ADMIN_PROJECT_CARD_BUTTON, buttons)
        self.assertIn(ADMIN_STATS_BUTTON, buttons)

    def test_start_supervisor_shows_supervisor_role_menu(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws), _patch_admin_check(False), _patch_supervisor_check(
            True
        ):
            state = _run(start(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(update.message.reply_text.await_count, 2)
        menu_call = update.message.reply_text.await_args_list[-1]
        self.assertIn("Панель научного руководителя", menu_call.args[0])
        km = menu_call.kwargs.get("reply_markup")
        self.assertIsNotNone(km)
        buttons = [getattr(row[0], "text", row[0]) for row in km.keyboard]
        self.assertIn(SUPERVISOR_STATUS_BUTTON, buttons)
        self.assertIn(ROLE_MENU_SPRAVKA_BUTTON, buttons)

    def test_role_cache_hit_skips_duplicate_google_lookup(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        cfg = MagicMock()
        cfg.spreadsheet_id = "sheet123"
        cfg.worksheet_name = "Регистрация"

        with patch(
            "magister_checking.bot.handlers.find_row_by_telegram_id",
            return_value=None,
        ) as m_find, _patch_admin_check(False) as m_admin, _patch_supervisor_check(
            False
        ) as m_supervisor:
            first = handlers._resolve_telegram_role(cfg, ws, "111")
            second = handlers._resolve_telegram_role(cfg, ws, "111")

        self.assertEqual(first, ("unknown", None, None))
        self.assertEqual(second, ("unknown", None, None))
        self.assertEqual(m_find.call_count, 1)
        self.assertEqual(m_admin.call_count, 1)
        self.assertEqual(m_supervisor.call_count, 1)

    def test_role_cache_expiration_falls_back_to_sheets(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        cfg = MagicMock()
        cfg.spreadsheet_id = "sheet123"
        cfg.worksheet_name = "Регистрация"
        handlers._store_telegram_role(cfg, "111", role="unknown")
        key = handlers._telegram_role_cache_key(cfg, "111")
        handlers._TELEGRAM_ROLE_CACHE[key] = handlers._TelegramRoleCacheEntry(
            role="unknown",
            row_number=None,
            expires_at=0.0,
        )

        loaded = UserForm(telegram_id="111", fio="Иванов И.И.")
        with patch(
            "magister_checking.bot.handlers.find_row_by_telegram_id",
            return_value=2,
        ) as m_find, patch(
            "magister_checking.bot.handlers.load_user",
            return_value=loaded,
        ) as m_load:
            role, row_number, loaded_user = handlers._resolve_telegram_role(
                cfg, ws, "111", load_student=True
            )

        self.assertEqual(role, "student")
        self.assertEqual(row_number, 2)
        self.assertIs(loaded_user, loaded)
        m_find.assert_called_once()
        m_load.assert_called_once_with(ws, 2)

    def test_start_reuses_loaded_student_row_inside_flow(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), ["111"] + [""] * 14])
        ctx = _FakeContext(ws)
        update = _make_update()
        loaded = UserForm(telegram_id="111", fio="Иванов И.И.")

        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.find_row_by_telegram_id",
            return_value=2,
        ), patch(
            "magister_checking.bot.handlers.load_user",
            return_value=loaded,
        ) as m_load:
            state = _run(start(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertIs(ctx.user_data[handlers.USER_DATA_FORM_KEY], loaded)
        m_load.assert_called_once_with(ws, 2)

    def test_supervisor_status_button_uses_one_shot_pending_target(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text=SUPERVISOR_STATUS_BUTTON)

        def close_task(coro):
            coro.close()

        with patch(
            "magister_checking.bot.handlers.asyncio.create_task",
            side_effect=close_task,
        ):
            _run(supervisor_menu_status(update, ctx))

        self.assertTrue(ctx.user_data[handlers.USER_DATA_SUPERVISOR_STATUS_PENDING])
        update.message.reply_text.assert_awaited_once()

        target_update = _make_update(text="Иванов Иван")
        with _patch_supervisor_check(True), patch(
            "magister_checking.bot.handlers._supervisor_student_status_by_fio",
            new_callable=AsyncMock,
        ) as m_status:
            _run(admin_recheck_pending_receive(target_update, ctx))

        self.assertNotIn(handlers.USER_DATA_SUPERVISOR_STATUS_PENDING, ctx.user_data)
        m_status.assert_awaited_once()
        self.assertEqual(m_status.await_args.args[2], "Иванов Иван")

    def test_supervisor_status_command_without_args_waits_for_fio(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.args = []
        update = _make_update(text="/status")

        def close_task(coro):
            coro.close()

        with _patch_supervisor_check(True), patch(
            "magister_checking.bot.handlers.asyncio.create_task",
            side_effect=close_task,
        ) as create_task:
            _run(handlers.status_command(update, ctx))

        self.assertTrue(ctx.user_data[handlers.USER_DATA_SUPERVISOR_STATUS_PENDING])
        self.assertEqual(handlers.SUPERVISOR_STATUS_INPUT_TIMEOUT_SECONDS, 30.0)
        create_task.assert_called_once()
        update.message.reply_text.assert_awaited_once_with(
            handlers.SUPERVISOR_STATUS_PROMPT_TEXT
        )

    def test_status_admin_with_fio_bypasses_supervisor_filter(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = "222"
        row[SHEET_HEADER.index("fio")] = "Уразаев Дамир Серикбаевич"
        row[SHEET_HEADER.index("phone")] = "+77001234567"
        reg_ws = FakeWorksheet([list(SHEET_HEADER), row])
        mag_ws = FakeWorksheet(
            [
                [
                    "№",
                    "ФИО магистранта",
                    "Спец",
                    "Телефон",
                    "Научный руководитель (ссылка)",
                    "Регистрация",
                ],
                [
                    "1",
                    "Уразаев Дамир Серикбаевич",
                    "",
                    "8 700 123 45 67",
                    "Другой Научрук",
                    "зарегистрирован",
                ],
            ]
        )
        ctx = _FakeContext(reg_ws)
        ctx.args = ["Уразаев", "Дамир", "Серикбаевич"]
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = (
            "Магистранты"
        )
        update = _make_update(user_id=999, text="/status Уразаев Дамир Серикбаевич")
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        with _patch_admin_check(True), _patch_supervisor_check(False), _patch_worksheet(
            reg_ws
        ), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            _run(handlers.status_command(update, ctx))

        m_do.assert_awaited_once()
        self.assertEqual(m_do.await_args.kwargs["row_number_override"], 2)
        self.assertTrue(m_do.await_args.kwargs["attach_recheck_keyboard"])
        self.assertEqual(m_do.await_args.kwargs["history_source"], "admin_status")

    def test_status_falls_back_to_unique_registration_fio_when_phone_differs(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = "815191575"
        row[SHEET_HEADER.index("fio")] = "Жарлыкпаева Лаура Зинатоллакызы"
        row[SHEET_HEADER.index("phone")] = "+77022576571"
        reg_ws = FakeWorksheet([list(SHEET_HEADER), row])
        mag_ws = FakeWorksheet(
            [
                [
                    "№",
                    "ФИО магистранта",
                    "Спец",
                    "Телефон",
                    "Научный руководитель (ссылка)",
                    "Регистрация",
                ],
                [
                    "1",
                    "Жарлыкпаева Лаура Зинатоллакызы",
                    "",
                    "+77078054115",
                    "Другой Научрук",
                    "зарегистрирован",
                ],
            ]
        )
        ctx = _FakeContext(reg_ws)
        ctx.args = ["Жарлыкпаева", "Лаура", "Зинатоллакызы"]
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = (
            "Магистранты"
        )
        update = _make_update(
            user_id=999, text="/status Жарлыкпаева Лаура Зинатоллакызы"
        )
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        with _patch_admin_check(True), _patch_supervisor_check(False), _patch_worksheet(
            reg_ws
        ), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            _run(handlers.status_command(update, ctx))

        m_do.assert_awaited_once()
        self.assertEqual(m_do.await_args.kwargs["row_number_override"], 2)
        self.assertEqual(m_do.await_args.kwargs["history_source"], "admin_status")

    def test_status_supervisor_existing_fio_outside_scope_mentions_admin_sheet(
        self,
    ) -> None:
        reg_ws = FakeWorksheet([list(SHEET_HEADER)])
        mag_ws = FakeWorksheet(
            [
                [
                    "№",
                    "ФИО магистранта",
                    "Спец",
                    "Телефон",
                    "Научный руководитель (ссылка)",
                    "Регистрация",
                ],
                [
                    "1",
                    "Уразаев Дамир Серикбаевич",
                    "",
                    "8 700 123 45 67",
                    "Другой Научрук",
                    "зарегистрирован",
                ],
            ]
        )
        ctx = _FakeContext(reg_ws)
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = (
            "Магистранты"
        )
        update = _make_update(text="/status Уразаев Дамир Серикбаевич")
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        def close_task(coro):
            coro.close()

        with _patch_admin_check(False), _patch_worksheet(reg_ws), patch(
            "magister_checking.bot.handlers.get_supervisor_fio_for_telegram_id",
            return_value="Петров П.П.",
        ), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do, patch(
            "magister_checking.bot.handlers.asyncio.create_task",
            side_effect=close_task,
        ) as create_task:
            _run(
                handlers._supervisor_student_status_by_fio(
                    update, ctx, "Уразаев Дамир Серикбаевич"
                )
            )

        m_do.assert_not_awaited()
        create_task.assert_called_once()
        reply = update.message.reply_text.await_args_list[0].args[0]
        self.assertIn("есть в листе «Магистранты»", reply)
        self.assertIn("Администраторы", reply)
        self.assertIn("через 60 секунд", update.message.reply_text.await_args.args[0])

    def test_status_auto_retry_repeats_once_without_rescheduling(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/status Уразаев Дамир Серикбаевич")

        with patch(
            "magister_checking.bot.handlers.asyncio.sleep", new_callable=AsyncMock
        ) as sleep, patch(
            "magister_checking.bot.handlers._supervisor_student_status_by_fio",
            new_callable=AsyncMock,
        ) as m_status:
            _run(
                handlers._status_auto_retry_task(
                    update, ctx, "Уразаев Дамир Серикбаевич"
                )
            )

        sleep.assert_awaited_once_with(handlers.ADMIN_STATUS_AUTO_RETRY_SECONDS)
        m_status.assert_awaited_once()
        self.assertEqual(m_status.await_args.args[2], "Уразаев Дамир Серикбаевич")
        self.assertFalse(m_status.await_args.kwargs["schedule_admin_retry"])

    def test_supervisor_status_pending_expires_after_short_timeout(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_STATUS_PENDING] = True
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_STATUS_PENDING_AT] = (
            handlers.time.time()
            - handlers.SUPERVISOR_STATUS_INPUT_TIMEOUT_SECONDS
            - 1
        )
        update = _make_update(text="Иванов Иван")

        with _patch_supervisor_check(True), patch(
            "magister_checking.bot.handlers._supervisor_student_status_by_fio",
            new_callable=AsyncMock,
        ) as m_status:
            _run(admin_recheck_pending_receive(update, ctx))

        self.assertNotIn(handlers.USER_DATA_SUPERVISOR_STATUS_PENDING, ctx.user_data)
        m_status.assert_not_awaited()
        update.message.reply_text.assert_awaited_once_with(
            handlers.SUPERVISOR_STATUS_TIMEOUT_TEXT
        )

    def test_start_existing_user_with_missing_only_asks_missing(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                [
                    "111",  # telegram_id
                    "ivanov",
                    "Иван",
                    "Иванов",
                    "Иванов И.И.",  # fio
                    "М-101",  # group_name
                    "ООО",  # workplace
                    "",  # position MISSING
                    "+7",  # phone
                    "Петров",  # supervisor
                    "",  # report_url MISSING
                    "", "", "", "PARTIAL", "answered_phone",
                ],
            ]
        )
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws), _patch_admin_check(False), _patch_supervisor_check(
            False
        ):
            state = _run(start(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов И.И.")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "position")
        pending = ctx.user_data[handlers.USER_DATA_PENDING_KEY]
        self.assertEqual(pending, ["report_url"])

    def test_start_role_pick_admin_sets_claim_state(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(callback_data="start:pick:admin")
        state = _run(start_role_callback(update, ctx))
        self.assertEqual(state, CLAIM_ASK_FIO)
        self.assertEqual(
            ctx.user_data[handlers.USER_DATA_CLAIM_TARGET_KEY], "admin"
        )
        update.callback_query.message.reply_text.assert_awaited()

    def test_start_role_mag_new_asks_fio(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(callback_data="start:mag:new")
        with _patch_worksheet(ws):
            state = _run(start_role_callback(update, ctx))
        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "fio")

    def test_start_role_mag_bind_sends_bind_flow(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(callback_data="start:mag:bind")
        state = _run(start_role_callback(update, ctx))
        self.assertEqual(state, BIND_ASK_FIO)
        self.assertEqual(
            ctx.user_data[handlers.USER_DATA_FORM_KEY].last_action, "ask_bind_fio"
        )


class ReceiveFieldTests(unittest.TestCase):
    def test_advances_through_fields_and_skip_token(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(telegram_id="111")
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = ["group_name", "workplace"]
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "fio"

        update_fio = _make_update(text="Иванов И.И.")
        with _patch_worksheet(ws):
            state = _run(receive_field(update_fio, ctx))
        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")

        form: UserForm = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов И.И.")
        self.assertEqual(form.last_action, "ask_group_name")

        update_skip = _make_update(text="-")
        with _patch_worksheet(ws):
            state = _run(receive_field(update_skip, ctx))
        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(form.group_name, "")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "workplace")

    def test_report_url_triggers_check(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)

        # Сценарий: пользователь уже на этапе report_url
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="X",
            group_name="X",
            workplace="X",
            position="X",
            phone="X",
            supervisor="X",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = []
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "report_url"

        update = _make_update(text="https://docs.google.com/document/d/abc/edit")
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ) as mock_check, _patch_worksheet(ws):
            state = _run(receive_field(update, ctx))

        mock_check.assert_called_once_with("https://docs.google.com/document/d/abc/edit")
        self.assertEqual(state, ASK_CONFIRM)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url_valid, "yes")
        self.assertEqual(form.report_url_accessible, "yes")
        self.assertEqual(form.fill_status, "REGISTERED")
        sent_texts = [call.args[0] for call in update.message.reply_text.await_args_list]
        self.assertIn("Сейчас я покажу данные", sent_texts[0])

    def test_report_url_folder_keeps_user_on_field_for_retry(
        self,
    ) -> None:
        """Магистрант прислал ссылку на папку Drive вместо документа.

        Бот должен:
        1) ответить ему сообщением «исправьте, это папка» с приглашением
           прислать правильную ссылку прямо здесь;
        2) записать сообщение об ошибке в ``report_url_valid`` (колонка
           «Проверка ссылки» — для админа);
        3) очистить ``report_url_accessible`` (HTTP-проба не делалась);
        4) **не звонить** ``check_report_url`` (без сетевой пробы);
        5) **не двигать** диалог: остаёмся в ``ASK_FIELD`` с тем же
           ``current_field == "report_url"``, чтобы следующее сообщение
           пользователя ушло в ту же ветку валидации.
        """

        from magister_checking.bot.validation import (
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="X",
            group_name="X",
            workplace="X",
            position="X",
            phone="X",
            supervisor="X",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = []
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "report_url"

        folder_url = "https://drive.google.com/drive/u/0/folders/1AbCdEf"
        update = _make_update(text=folder_url)
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ) as mock_check, _patch_worksheet(ws):
            state = _run(receive_field(update, ctx))

        mock_check.assert_not_called()
        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(
            ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "report_url"
        )
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url, folder_url)
        self.assertEqual(
            form.report_url_valid, REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
        )
        self.assertEqual(form.report_url_accessible, "")
        sent_texts = [
            call.args[0] for call in update.message.reply_text.await_args_list
        ]
        self.assertEqual(len(sent_texts), 1)
        self.assertIn(REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE, sent_texts[0])
        self.assertIn("в ответ на это сообщение", sent_texts[0])
        self.assertIn("/skip", sent_texts[0])

    def test_report_url_folder_then_valid_url_fixes_and_advances(self) -> None:
        """Двухшаговый сценарий: папка → потом валидный документ.

        Первый ввод (папка) кладёт предупреждение в ``report_url_valid`` и
        оставляет диалог на том же поле. Второй ввод (валидный URL Google
        Doc) должен:
        - перезаписать ``report_url`` новой ссылкой;
        - заменить сообщение-предупреждение на ``"yes"`` в
          ``report_url_valid`` и проставить ``report_url_accessible == "yes"``;
        - выслать короткое подтверждение «Ссылка принята. Продолжаю
          регистрацию.»;
        - продвинуть сценарий дальше (нет других missing-полей —
          выходим в ``ASK_CONFIRM`` со сводкой).
        """

        from magister_checking.bot.validation import (
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="X",
            group_name="X",
            workplace="X",
            position="X",
            phone="X",
            supervisor="X",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = []
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "report_url"

        folder_url = "https://drive.google.com/drive/u/0/folders/1AbCdEf"
        update_folder = _make_update(text=folder_url)
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ) as mock_check_first, _patch_worksheet(ws):
            state_after_folder = _run(receive_field(update_folder, ctx))

        mock_check_first.assert_not_called()
        self.assertEqual(state_after_folder, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(
            form.report_url_valid, REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
        )

        doc_url = "https://docs.google.com/document/d/abc/edit"
        update_doc = _make_update(text=doc_url)
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ) as mock_check_second, _patch_worksheet(ws):
            state_after_doc = _run(receive_field(update_doc, ctx))

        mock_check_second.assert_called_once_with(doc_url)
        self.assertEqual(state_after_doc, ASK_CONFIRM)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url, doc_url)
        self.assertEqual(form.report_url_valid, "yes")
        self.assertEqual(form.report_url_accessible, "yes")
        self.assertEqual(form.fill_status, "REGISTERED")

        sent_texts = [
            call.args[0]
            for call in update_doc.message.reply_text.await_args_list
        ]
        self.assertTrue(
            any("Ссылка принята" in text for text in sent_texts),
            f"Ожидаемое подтверждение не найдено в: {sent_texts!r}",
        )
        self.assertTrue(
            any("Сейчас я покажу данные" in text for text in sent_texts),
            f"Ожидался переход к review-prompt, фактические сообщения: {sent_texts!r}",
        )

    def test_report_url_folder_followed_by_another_folder_keeps_user_stuck(
        self,
    ) -> None:
        """Если магистрант прислал ещё одну папку — снова просим документ.

        Диалог не должен «провалиться» к следующему шагу даже после
        нескольких подряд неверных вводов; ``check_report_url`` (HTTP-проба)
        не должна звониться ни на одной итерации.
        """

        from magister_checking.bot.validation import (
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="X",
            group_name="X",
            workplace="X",
            position="X",
            phone="X",
            supervisor="X",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = []
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "report_url"

        first_url = "https://drive.google.com/drive/u/0/folders/AAA"
        second_url = "https://drive.google.com/drive/folders/BBB"
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ) as mock_check, _patch_worksheet(ws):
            state_first = _run(receive_field(_make_update(text=first_url), ctx))
            state_second = _run(receive_field(_make_update(text=second_url), ctx))

        mock_check.assert_not_called()
        self.assertEqual(state_first, ASK_FIELD)
        self.assertEqual(state_second, ASK_FIELD)
        self.assertEqual(
            ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "report_url"
        )
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url, second_url)
        self.assertEqual(
            form.report_url_valid, REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
        )
        self.assertEqual(form.report_url_accessible, "")

    def test_report_url_folder_then_skip_keeps_warning_and_advances(self) -> None:
        """После папки магистрант может выйти через /skip — предупреждение остаётся.

        Сохранённый ранее текст в ``report_url_valid`` (для админа) не
        должен теряться при пропуске: ячейка по-прежнему объясняет, почему
        ссылка не валидна. Сам URL папки тоже остаётся (магистрант ничего
        нового не прислал)."""

        from magister_checking.bot.validation import (
            REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
        )

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="X",
            group_name="X",
            workplace="X",
            position="X",
            phone="X",
            supervisor="X",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = []
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "report_url"

        folder_url = "https://drive.google.com/drive/folders/AAA"
        with patch(
            "magister_checking.bot.handlers.check_report_url",
            return_value=("yes", "yes"),
        ), _patch_worksheet(ws):
            state_after_folder = _run(
                receive_field(_make_update(text=folder_url), ctx)
            )
        self.assertEqual(state_after_folder, ASK_FIELD)

        with _patch_worksheet(ws):
            state_after_skip = _run(skip_field(_make_update(text="/skip"), ctx))

        self.assertEqual(state_after_skip, ASK_CONFIRM)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url, folder_url)
        self.assertEqual(
            form.report_url_valid, REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
        )
        self.assertEqual(form.report_url_accessible, "")

    def test_skip_token_keeps_existing_value_during_recheck(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(
            telegram_id="111",
            fio="Иванов И.И.",
        )
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = ["workplace"]
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "fio"

        update = _make_update(text="-")
        with _patch_worksheet(ws):
            state = _run(receive_field(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов И.И.")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "workplace")


class SkipFieldTests(unittest.TestCase):
    def test_skip_keeps_existing_field_and_advances(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(telegram_id="111", fio="Old")
        ctx.user_data[handlers.USER_DATA_PENDING_KEY] = ["group_name"]
        ctx.user_data[handlers.USER_DATA_CURRENT_KEY] = "fio"

        update = _make_update(text="/skip")
        with _patch_worksheet(ws):
            state = _run(skip_field(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Old")
        self.assertEqual(form.last_action, "ask_group_name")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")

    def test_skip_without_active_field_ends(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/skip")
        state = _run(skip_field(update, ctx))
        self.assertEqual(state, ConversationHandler.END)


class AskConfirmTests(unittest.TestCase):
    def _prepare_form(self, ctx: _FakeContext) -> UserForm:
        form = UserForm(
            telegram_id="111",
            telegram_username="ivanov",
            fio="Иванов",
            group_name="М-101",
            workplace="ООО",
            position="Инженер",
            phone="+7",
            supervisor="Петров",
            report_url="https://docs.google.com/document/d/abc/edit",
            report_url_valid="yes",
            report_url_accessible="yes",
            fill_status="REGISTERED",
        )
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = form
        return form

    def test_yes_saves_to_sheet(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="да")
        with patch(
            "magister_checking.bot.handlers.build_sheet_enrichment",
            return_value={},
        ), patch(
            "magister_checking.bot.handlers._registration_timestamp",
            return_value="20.04.2026 9:15:00",
        ), _patch_dashboard_sync() as mock_dashboard, _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(len(ws.rows), 2)
        self.assertEqual(ws.rows[1][0], "111")
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.last_action, "confirmed_save")
        mock_dashboard.assert_called_once()

        final_call = update.message.reply_text.await_args_list[-1]
        self.assertIn("Регистрация завершена", final_call.args[0])
        markup = final_call.kwargs.get("reply_markup")
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, RECHECK_BUTTON_LABEL)
        self.assertEqual(button.callback_data, f"{RECHECK_CALLBACK_DATA}:2")

    def test_yes_saves_with_report_enrichment_when_available(self) -> None:
        ws = FakeWorksheet(
            [
                [
                    "Отметка времени",
                    "ФИО",
                    "Группа",
                    "Ссылка на промежуточный отчет",
                    "Проверка ссылки",
                    "Доступ открыт",
                    "Ссылка на ЛКБ",
                    "Число страниц",
                ]
            ]
        )
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="да")
        with patch(
            "magister_checking.bot.handlers.build_sheet_enrichment",
            return_value={"lkb_url": "https://drive.google.com/file/d/lkb/view", "pages_total": "87"},
        ), patch(
            "magister_checking.bot.handlers._registration_timestamp",
            return_value="20.04.2026 9:15:00",
        ) as mock_enrich, _patch_dashboard_sync(), _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        mock_enrich.assert_called_once()
        self.assertEqual(ws.rows[1][0], "20.04.2026 9:15:00")
        self.assertEqual(ws.rows[1][1], "Иванов")
        self.assertEqual(ws.rows[1][6], "https://drive.google.com/file/d/lkb/view")
        self.assertEqual(ws.rows[1][7], "87")

    def test_yes_saves_with_report_enrichment_placeholders(self) -> None:
        ws = FakeWorksheet(
            [
                [
                    "Отметка времени",
                    "ФИО",
                    "Группа",
                    "Ссылка на папку 1",
                    "Ссылка на ЛКБ",
                    "Ссылка на диссер",
                ]
            ]
        )
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="да")
        with patch(
            "magister_checking.bot.handlers.build_sheet_enrichment",
            return_value={
                "project_folder_url": "url отсутствует",
                "lkb_url": "url отсутствует",
                "dissertation_url": "url недоступен",
            },
        ), patch(
            "magister_checking.bot.handlers._registration_timestamp",
            return_value="20.04.2026 9:15:00",
        ), _patch_dashboard_sync(), _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(ws.rows[1][0], "20.04.2026 9:15:00")
        self.assertEqual(ws.rows[1][3], "url отсутствует")
        self.assertEqual(ws.rows[1][4], "url отсутствует")
        self.assertEqual(ws.rows[1][5], "url недоступен")

    def test_no_requests_correction(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="нет")
        with _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(len(ws.rows), 1)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.last_action, "ask_fio")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "fio")

    def test_invalid_answer_keeps_state(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="возможно")
        state = _run(ask_confirm(update, ctx))
        self.assertEqual(state, ASK_CONFIRM)


class BindFlowTests(unittest.TestCase):
    def _row(self, *, fio: str, telegram_id: str = "", group: str = "") -> list:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = telegram_id
        row[SHEET_HEADER.index("fio")] = fio
        row[SHEET_HEADER.index("group_name")] = group
        return row

    def _start_in_bind_state(self, ws: FakeWorksheet) -> _FakeContext:
        ctx = _FakeContext(ws)
        update = _make_update()
        with (
            _patch_worksheet(ws),
            _patch_admin_check(False),
            _patch_supervisor_check(False),
        ):
            state = _run(start(update, ctx))
        self.assertEqual(state, ROLE_PICK)
        cb = _make_callback_update(callback_data="start:mag:bind")
        with (
            _patch_worksheet(ws),
            _patch_admin_check(False),
            _patch_supervisor_check(False),
        ):
            state = _run(start_role_callback(cb, ctx))
        self.assertEqual(state, BIND_ASK_FIO)
        return ctx

    def test_skip_bind_starts_new_registration(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = self._start_in_bind_state(ws)

        update = _make_update(text="/skip")
        with _patch_worksheet(ws):
            state = _run(skip_bind(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "fio")

    def test_unknown_fio_falls_back_to_new_registration(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(fio="Сидоров С.С.")])
        ctx = self._start_in_bind_state(ws)

        update = _make_update(text="Неизвестный И.И.")
        with _patch_worksheet(ws):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_FORM_KEY].fio, "Неизвестный И.И.")

    def test_unknown_registration_fio_uses_magistrants_sheet_prefill(self) -> None:
        reg_ws = FakeWorksheet([list(SHEET_HEADER)])
        mag_ws = FakeWorksheet(
            [
                [
                    "№",
                    "ФИО магистранта",
                    "Спец",
                    "Телефон",
                    "Научный руководитель (ссылка)",
                    "Регистрация",
                ],
                [
                    "1",
                    "Иванов Иван Иванович",
                    "",
                    "8 999 000-00-00",
                    "Петров П.П.",
                    "нет",
                ],
            ]
        )
        ctx = self._start_in_bind_state(reg_ws)
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = "Магистранты"
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        update = _make_update(text="Иванов Иван Иванович")
        with _patch_worksheet(reg_ws), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов Иван Иванович")
        self.assertEqual(form.phone, "+79990000000")
        self.assertEqual(form.supervisor, "Петров П.П.")
        self.assertEqual(form.telegram_id, "111")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")
        first_message = update.message.reply_text.await_args_list[0].args[0]
        self.assertIn("«Магистранты»", first_message)

    def test_single_match_asks_for_confirmation(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(fio="Иванов И.И.", group="М-101")])
        ctx = self._start_in_bind_state(ws)

        update = _make_update(text="иванов  и.и.")
        with _patch_worksheet(ws):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, BIND_CONFIRM)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_BIND_ROW_KEY], 2)

    def test_confirm_yes_attaches_telegram_and_resumes(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(fio="Иванов И.И.", group="М-101")])
        ctx = self._start_in_bind_state(ws)

        with _patch_worksheet(ws):
            _run(receive_bind_fio(_make_update(text="Иванов И.И."), ctx))

        update = _make_update(text="да")
        with _patch_worksheet(ws):
            state = _run(confirm_bind(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("telegram_id")], "111")
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("telegram_username")], "ivanov")
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов И.И.")
        self.assertEqual(form.group_name, "М-101")
        self.assertEqual(form.last_action, "ask_workplace")

    def test_confirm_yes_does_not_overwrite_row_taken_during_confirmation(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(fio="Иванов И.И.", group="М-101")])
        ctx = self._start_in_bind_state(ws)

        with _patch_worksheet(ws):
            _run(receive_bind_fio(_make_update(text="Иванов И.И."), ctx))

        ws.rows[1][SHEET_HEADER.index("telegram_id")] = "999"

        update = _make_update(text="да")
        with _patch_worksheet(ws):
            state = _run(confirm_bind(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("telegram_id")], "999")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")
        sent_messages = [call.args[0] for call in update.message.reply_text.await_args_list]
        self.assertIn("эта строка уже была занята", sent_messages[0])

    def test_confirm_no_starts_new_registration(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(fio="Иванов И.И.")])
        ctx = self._start_in_bind_state(ws)

        with _patch_worksheet(ws):
            _run(receive_bind_fio(_make_update(text="Иванов И.И."), ctx))

        update = _make_update(text="нет")
        with _patch_worksheet(ws):
            state = _run(confirm_bind(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("telegram_id")], "")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_BIND_ROW_KEY], None)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")

    def test_already_bound_to_other_user_falls_back(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                self._row(fio="Иванов И.И.", telegram_id="999"),
            ]
        )
        ctx = self._start_in_bind_state(ws)

        update = _make_update(text="Иванов И.И.")
        with _patch_worksheet(ws):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ws.rows[1][SHEET_HEADER.index("telegram_id")], "999")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_FORM_KEY].last_action, "ask_group_name")

    def test_multiple_matches_fall_back_to_new_registration(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                self._row(fio="Иванов И.И."),
                self._row(fio="иванов и.и."),
            ]
        )
        ctx = self._start_in_bind_state(ws)

        update = _make_update(text="Иванов И.И.")
        with _patch_worksheet(ws):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "group_name")


class CancelTests(unittest.TestCase):
    def test_cancel_records_action_and_ends(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(telegram_id="111")

        update = _make_update(text="/cancel")
        state = _run(cancel(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.last_action, "cancelled")
        cancel_text = update.message.reply_text.await_args.args[0]
        self.assertIn("/help", cancel_text)
        self.assertIn("/start", cancel_text)


class HelpAndCommandsTests(unittest.TestCase):
    def test_default_bot_commands_lists_core_slugs(self) -> None:
        cmds = default_bot_commands()
        slugs = [c.command for c in cmds]
        self.assertEqual(
            slugs,
            [
                "start",
                "status",
                "unreg",
                "reg_list",
                "student_message",
                "student_message_bulk",
                "supervisor_message",
                "about",
            ],
        )

    def test_help_command_replies_with_text(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/help")
        update.effective_message = update.message
        with _patch_admin_check(False), _patch_supervisor_check(False):
            _run(help_command(update, ctx))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("магистрант", text)
        self.assertIn("/start", text)
        self.assertIn("/spravka", text)
        self.assertIn("/about", text)
        self.assertIn("/cancel", text)
        self.assertNotIn("/recheck", text)
        self.assertNotIn("/stats", text)
        self.assertNotIn("/admin", text)

    def test_help_command_admin_includes_privileged_slugs(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/help")
        update.effective_message = update.message
        with _patch_admin_check(True), _patch_supervisor_check(False):
            _run(help_command(update, ctx))
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("администратор", text)
        self.assertIn("/spravka", text)
        self.assertIn("/about", text)
        self.assertNotIn("/stats", text)
        self.assertNotIn("/admin", text)

    def test_help_reply_for_user_routing(self) -> None:
        self.assertIn("администратор", help_reply_for_user(is_admin=True))
        self.assertNotIn("/stats", help_reply_for_user(is_admin=False))
        supervisor_help = help_reply_for_user(is_admin=False, is_supervisor=True)
        self.assertIn("научный руководитель", supervisor_help.lower())
        self.assertIn("/unreg", supervisor_help)
        self.assertIn("/reg_list", supervisor_help)
        self.assertIn("/about", supervisor_help)
        self.assertIn("администратор", help_reply_for_user(is_admin=True, is_supervisor=True))


class GroupJoinRequestTests(unittest.TestCase):
    def test_join_request_approves_whitelisted_user(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_join_request_update(user_id=222, chat_id=-100777)
        with patch(
            "magister_checking.bot.handlers._group_join_whitelist_hit",
            return_value=True,
        ):
            _run(handlers.group_join_request(update, ctx))

        ctx.bot.approve_chat_join_request.assert_awaited_once_with(
            chat_id=-100777,
            user_id=222,
        )
        ctx.bot.send_message.assert_awaited_once()
        self.assertEqual(ctx.chat_data, {})

    def test_join_request_stores_pending_when_not_whitelisted(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_join_request_update(user_id=333, chat_id=-100777)
        with patch(
            "magister_checking.bot.handlers._group_join_whitelist_hit",
            return_value=False,
        ):
            _run(handlers.group_join_request(update, ctx))

        ctx.bot.approve_chat_join_request.assert_not_awaited()
        pending = ctx.chat_data[handlers.CHAT_DATA_PENDING_GROUP_JOINS]
        self.assertEqual(pending["333"]["chat_id"], -100777)
        ctx.bot.send_message.assert_awaited_once()

    def test_pending_join_approved_after_registration(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.application.chat_data = {
            -100777: {
                handlers.CHAT_DATA_PENDING_GROUP_JOINS: {
                    "444": {"chat_id": -100777, "requested_at": 1}
                }
            }
        }
        update = _make_update(user_id=444, text="/start")
        with patch(
            "magister_checking.bot.handlers._group_join_whitelist_hit",
            return_value=True,
        ):
            _run(
                handlers._try_approve_pending_group_join(
                    update,
                    ctx,
                    telegram_id="444",
                )
            )

        ctx.bot.approve_chat_join_request.assert_awaited_once_with(
            chat_id=-100777,
            user_id=444,
        )
        self.assertEqual(
            ctx.application.chat_data[-100777][handlers.CHAT_DATA_PENDING_GROUP_JOINS],
            {},
        )

    def test_invite_link_sent_when_configured(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].telegram_join_group_invite_link = (
            "https://t.me/+testInvite"
        )
        update = _make_update(user_id=555, text="да")

        _run(handlers._send_group_invite_link_if_configured(update, ctx))

        text = update.message.reply_text.await_args.args[0]
        self.assertIn("https://t.me/+testInvite", text)
        self.assertIn("подайте заявку", text)


class SpravkaHandlerTests(unittest.TestCase):
    """/spravka — canonical check flow plus admin formats."""

    def test_spravka_start_non_admin_runs_canonical_retry(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/spravka")
        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            state = _run(spravka_start(update, ctx))
        self.assertEqual(state, ConversationHandler.END)
        m_do.assert_awaited_once()
        self.assertFalse(m_do.call_args.kwargs.get("only_if_changed"))
        self.assertEqual(m_do.call_args.kwargs.get("report_trigger"), "spravka")

    def test_spravka_start_admin_without_target_sends_menu(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/spravka")
        with _patch_admin_check(True):
            state = _run(spravka_start(update, ctx))
        self.assertEqual(state, handlers.SPRAVKA_MENU)
        km = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(len(km.inline_keyboard), 3)
        self.assertEqual(
            km.inline_keyboard[0][0].callback_data, handlers.SPRAVKA_CALLBACK_TELEGRAM
        )
        self.assertEqual(
            km.inline_keyboard[1][0].callback_data, handlers.SPRAVKA_CALLBACK_COMMISSION
        )
        self.assertEqual(
            km.inline_keyboard[2][0].callback_data, handlers.SPRAVKA_CALLBACK_PDF
        )

    def test_spravka_start_admin_with_target_runs_retry_for_row(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = "222"
        row[SHEET_HEADER.index("fio")] = "Иванов И.И."
        row[SHEET_HEADER.index("report_url")] = (
            "https://docs.google.com/document/d/r/edit"
        )
        ws = FakeWorksheet([list(SHEET_HEADER), row])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/справка 2")
        with _patch_admin_check(True), _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            state = _run(spravka_start(update, ctx))
        self.assertEqual(state, ConversationHandler.END)
        m_do.assert_awaited_once()
        self.assertEqual(m_do.call_args.kwargs.get("row_number_override"), 2)
        self.assertFalse(m_do.call_args.kwargs.get("only_if_changed"))
        self.assertEqual(m_do.call_args.kwargs.get("report_trigger"), "spravka")

    def test_spravka_start_non_admin_with_target_rejected(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/справка 2")
        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            state = _run(spravka_start(update, ctx))
        self.assertEqual(state, ConversationHandler.END)
        m_do.assert_not_awaited()
        self.assertIn(
            "только администраторы", update.message.reply_text.await_args.args[0]
        )

    def test_spravka_pdf_callback_denies_non_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(
            user_id=111, callback_data=handlers.SPRAVKA_CALLBACK_PDF
        )
        with _patch_admin_check(False), _patch_worksheet(ws):
            state = _run(spravka_choose(update, ctx))
        self.assertEqual(state, handlers.SPRAVKA_MENU)
        update.callback_query.answer.assert_awaited_once()
        self.assertTrue(update.callback_query.answer.await_args.kwargs.get("show_alert"))

    def test_spravka_telegram_non_admin_runs_dorecheck(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(
            user_id=111, callback_data=handlers.SPRAVKA_CALLBACK_TELEGRAM
        )
        with _patch_admin_check(False), _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as m_do:
            state = _run(spravka_choose(update, ctx))
        self.assertEqual(state, ConversationHandler.END)
        m_do.assert_awaited_once()
        self.assertTrue(m_do.call_args.kwargs.get("skip_status_message"))
        self.assertFalse(m_do.call_args.kwargs.get("only_if_changed"))
        self.assertEqual(m_do.call_args.kwargs.get("report_trigger"), "spravka")

    def test_spravka_pdf_admin_goes_to_ask_target(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(
            user_id=111, callback_data=handlers.SPRAVKA_CALLBACK_PDF
        )
        with _patch_admin_check(True), _patch_worksheet(ws):
            state = _run(spravka_choose(update, ctx))
        self.assertEqual(state, handlers.SPRAVKA_ASK_TARGET)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_SPRAVKA_MODE], "pdf")


class AdminStatsTests(unittest.TestCase):
    def test_admin_stats_denies_non_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/stats")

        with _patch_admin_check(False):
            _run(admin_stats(update, ctx))

        self.assertIn("только администраторам", update.message.reply_text.await_args.args[0])
        self.assertIsNone(update.message.reply_text.await_args.kwargs.get("parse_mode"))

    def test_admin_stats_sends_html_summary(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "", "", "", "Иванов", "М1", "ООО", "инж", "+7", "Петров", "https://x", "yes", "yes", "REGISTERED", "ok"],
            ]
        )
        ctx = _FakeContext(ws)
        update = _make_update(text="/stats")

        with _patch_admin_check(True), _patch_worksheet(ws):
            _run(admin_stats(update, ctx))

        update.message.reply_text.assert_awaited_once()
        self.assertEqual(update.message.reply_text.await_args.kwargs.get("parse_mode"), "HTML")
        body = update.message.reply_text.await_args.args[0]
        self.assertIn("Сводка", body)
        self.assertIn("Всего регистраций: 1", body)

    def test_ops_row_denies_non_admin_without_collecting(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.args = ["2"]
        update = _make_update(text="/ops_row 2")

        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.collect_ops_row_diagnostics"
        ) as collect:
            _run(handlers.ops_row(update, ctx))

        collect.assert_not_called()
        self.assertIn("только администраторам", update.message.reply_text.await_args.args[0])

    def test_ops_row_admin_sends_html_diagnostics(self) -> None:
        from magister_checking.bot.ops_diagnostics import OpsRowDiagnostics

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.args = ["2"]
        update = _make_update(text="/ops_row 2")
        diag = OpsRowDiagnostics(row_number=2, fio="Иванов И.И.", fill_status="OK")

        with _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.collect_ops_row_diagnostics",
            return_value=diag,
        ) as collect:
            _run(handlers.ops_row(update, ctx))

        collect.assert_called_once()
        self.assertEqual(collect.call_args.args[1], 2)
        self.assertEqual(update.message.reply_text.await_args.kwargs.get("parse_mode"), "HTML")
        body = update.message.reply_text.await_args.args[0]
        self.assertIn("Ops row 2", body)
        self.assertIn("Иванов", body)

    def test_ops_row_admin_requires_numeric_data_row(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.args = ["1"]
        update = _make_update(text="/ops_row 1")

        with _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.collect_ops_row_diagnostics"
        ) as collect:
            _run(handlers.ops_row(update, ctx))

        collect.assert_not_called()
        self.assertIn("2 или больше", update.message.reply_text.await_args.args[0])

    def test_admin_sync_dashboard_calls_sync(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/sync_dashboard")

        with _patch_admin_check(True), _patch_worksheet(ws), _patch_dashboard_sync() as m:
            _run(admin_sync_dashboard(update, ctx))

        m.assert_called_once()
        self.assertIn("обновлён", update.message.reply_text.await_args.args[0].lower())

    def test_admin_sync_dashboard_denies_non_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/sync_dashboard")

        with _patch_admin_check(False), _patch_dashboard_sync() as m:
            _run(admin_sync_dashboard(update, ctx))

        self.assertIn("только администраторам", update.message.reply_text.await_args.args[0])
        m.assert_not_called()


class AdminProjectCardTests(unittest.TestCase):
    def test_admin_menu_shows_button_for_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/admin")

        with _patch_admin_check(True):
            state = _run(admin_menu(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        sent_text = update.message.reply_text.await_args.args[0]
        self.assertIn("Панель администратора", sent_text)
        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        buttons = [getattr(row[0], "text", row[0]) for row in reply_markup.keyboard]
        self.assertEqual(buttons[0], ROLE_MENU_SPRAVKA_BUTTON)
        self.assertEqual(buttons[1], ADMIN_STUDENT_MESSAGE_BUTTON)
        self.assertEqual(buttons[2], ADMIN_STUDENT_MESSAGE_BULK_BUTTON)
        self.assertEqual(buttons[3], ADMIN_SUPERVISOR_MESSAGE_BUTTON)
        self.assertIn(ADMIN_PROJECT_CARD_BUTTON, buttons)
        self.assertIn(ADMIN_STATS_BUTTON, buttons)

    def test_project_card_start_denies_non_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/project_card")

        with _patch_admin_check(False):
            state = _run(project_card_start(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertIn("только администраторам", update.message.reply_text.await_args.args[0])

    def test_project_card_start_prompts_target_for_admin(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/project_card")

        with _patch_admin_check(True):
            state = _run(project_card_start(update, ctx))

        self.assertEqual(state, PROJECT_CARD_ASK_TARGET)
        self.assertIn("Введите номер строки", update.message.reply_text.await_args.args[0])

    def test_project_card_receive_target_by_row_generates_pdf(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "ivanov", "Иван", "Иванов", "Иванов И.И.", "М-101", "", "", "", "", "https://docs.google.com/document/d/abc/edit", "yes", "yes", "REGISTERED", "confirmed_save"],
            ]
        )
        ctx = _FakeContext(ws)
        update = _make_update(text="2")

        with _patch_admin_check(True), _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.generate_project_card_pdf",
            return_value=MagicMock(
                row_number=2,
                pdf_name="Карточка проекта - Иванов И.И..pdf",
                pdf_bytes=b"%PDF-1.4 test",
            ),
        ) as mock_generate:
            state = _run(project_card_receive_target(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        mock_generate.assert_called_once()
        sent_messages = [call.args[0] for call in update.message.reply_text.await_args_list]
        self.assertIn("Формирую карточку проекта", sent_messages[0])
        self.assertEqual(update.message.reply_document.await_count, 1)
        self.assertIn(
            "Карточка проекта сформирована",
            update.message.reply_document.await_args.kwargs["caption"],
        )

    def test_project_card_receive_target_requests_clarification_on_duplicate_fio(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "", "", "", "Иванов И.И."] + [""] * 10,
                ["222", "", "", "", "иванов  и.и."] + [""] * 10,
            ]
        )
        ctx = _FakeContext(ws)
        update = _make_update(text="Иванов И.И.")

        with _patch_admin_check(True), _patch_worksheet(ws):
            state = _run(project_card_receive_target(update, ctx))

        self.assertEqual(state, PROJECT_CARD_ASK_TARGET)
        self.assertIn("несколько строк", update.message.reply_text.await_args.args[0])

    def test_project_card_receive_target_user_message_on_generation_error(self) -> None:
        ws = FakeWorksheet(
            [
                list(SHEET_HEADER),
                ["111", "ivanov", "Иван", "Иванов", "Иванов И.И.", "М-101", "", "", "", "", "https://docs.google.com/document/d/abc/edit", "yes", "yes", "REGISTERED", "confirmed_save"],
            ]
        )
        ctx = _FakeContext(ws)
        update = _make_update(text="2")

        class Boom(Exception):
            pass

        boom = Boom("первая строка\nвторая строка не для пользователя")

        with _patch_admin_check(True), _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.generate_project_card_pdf",
            side_effect=boom,
        ):
            state = _run(project_card_receive_target(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        last = update.message.reply_text.await_args.args[0]
        self.assertIn("Не удалось сформировать", last)
        self.assertIn("первая строка", last)
        self.assertNotIn("вторая строка", last)


class AdminBulkStudentMessageTests(unittest.TestCase):
    def test_single_message_standard_template_sends_without_confirm_question(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = "222"
        row[SHEET_HEADER.index("fio")] = "Иванов И.И."
        ws = FakeWorksheet([list(SHEET_HEADER), row])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_STUDENT_REMINDER_ROW] = 2
        ctx.user_data[handlers.USER_DATA_STUDENT_REMINDER_FIO] = "Иванов И.И."
        update = _make_callback_update(callback_data="admstu:std")

        with _patch_admin_check(True), _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers._deliver_reminder_text_and_snapshot",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ) as deliver:
            state = _run(handlers.student_reminder_pick_template(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        deliver.assert_awaited_once()
        sent_texts = [
            call.args[0]
            for call in update.callback_query.message.reply_text.await_args_list
        ]
        self.assertTrue(any("Сообщение отправлено" in text for text in sent_texts))
        self.assertFalse(any("Предпросмотр" in text for text in sent_texts))
        self.assertFalse(
            any("Отправить это сообщение" in text for text in sent_texts)
        )

    def test_bulk_message_registration_flow_asks_sheet_rows_and_text(self) -> None:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = "222"
        row[SHEET_HEADER.index("fio")] = "Иванов И.И."
        ws = FakeWorksheet([list(SHEET_HEADER), row])
        ctx = _FakeContext(ws)

        with _patch_admin_check(True):
            state = _run(
                handlers.student_message_bulk_start(
                    _make_update(text="/student_message_bulk"), ctx
                )
            )
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_ASK_ROWS)

        with _patch_admin_check(True), _patch_worksheet(ws):
            state = _run(
                handlers.student_reminder_bulk_receive_rows(
                    _make_update(text="Регистрация"), ctx
                )
            )
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_ASK_ROWS)

        rows_update = _make_update(text="2")
        with _patch_admin_check(True), _patch_worksheet(ws):
            state = _run(handlers.student_reminder_bulk_receive_rows(rows_update, ctx))
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_ASK_ROWS)
        self.assertIn(
            "Пришлите текст сообщения",
            rows_update.message.reply_text.await_args.args[0],
        )

        text_update = _make_update(text="Здравствуйте, {fio}! Проверьте справку.")
        with _patch_admin_check(True):
            state = _run(handlers.student_reminder_bulk_receive_rows(text_update, ctx))
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_CONFIRM)
        preview = text_update.message.reply_text.await_args.args[0]
        self.assertIn("Будет отправлено 1 сообщений", preview)
        self.assertIn("Здравствуйте, Иванов И.И.!", preview)

    def test_bulk_message_magistrants_flow_uses_magistrants_sheet_rows(self) -> None:
        reg_ws = FakeWorksheet([list(SHEET_HEADER)])
        mag_ws = FakeWorksheet(
            [
                ["fio", "telegram_id"],
                ["Петров П.П.", "333"],
            ]
        )
        ctx = _FakeContext(reg_ws)
        ctx.bot_data[
            handlers.CONFIG_BOT_DATA_KEY
        ].magistrants_worksheet_name = "Магистранты"
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        with _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ):
            state = _run(
                handlers.student_reminder_bulk_receive_rows(
                    _make_update(text="Магистранты"), ctx
                )
            )
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_ASK_ROWS)

        rows_update = _make_update(text="2")
        with _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ):
            state = _run(handlers.student_reminder_bulk_receive_rows(rows_update, ctx))
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_ASK_ROWS)
        self.assertIn(
            "Лист: «Магистранты»",
            rows_update.message.reply_text.await_args.args[0],
        )

        text_update = _make_update(text="Добрый день, {fio}.")
        with _patch_admin_check(True):
            state = _run(handlers.student_reminder_bulk_receive_rows(text_update, ctx))
        self.assertEqual(state, handlers.STUDENT_MSG_BULK_CONFIRM)
        self.assertFalse(
            ctx.user_data[handlers.USER_DATA_STUDENT_BULK_ENTRIES][0]["attach_snapshot"]
        )
        self.assertIn(
            "Добрый день, Петров П.П.",
            text_update.message.reply_text.await_args.args[0],
        )


class AdminSupervisorMessageTests(unittest.TestCase):
    def test_report_filters_magistrants_by_supervisor_and_registration_no(self) -> None:
        mag_header = [
            "ФИО магистранта",
            "Группа",
            "Место работы",
            "Должность",
            "Телефон",
            "Научный руков",
            "Регистрация",
        ]
        mag_ws = FakeWorksheet(
            [
                mag_header,
                ["Иванов Иван", "ОЗ-1", "", "", "8 999 000-00-00", "Петров П.П.", "нет"],
                ["Сидоров Сидор", "ОЗ-1", "", "", "8 999 000-00-01", "Петров П.П.", "да"],
                ["Алиев Али", "ОЗ-2", "", "", "8 999 000-00-02", "Смирнов С.С.", "нет"],
            ]
        )
        reg_ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(reg_ws)
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = "Магистранты"
        spreadsheet = FakeSpreadsheet({"Магистранты": mag_ws})

        with patch(
            "magister_checking.bot.supervisor_lists.get_spreadsheet",
            return_value=spreadsheet,
        ):
            chunks, err = supervisor_unregistered_from_magistrants_registration_report(
                ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY],
                supervisor_fio="Петров Петр Петрович",
            )

        self.assertIsNone(err)
        text = "\n".join(chunks)
        self.assertIn("Иванов Иван", text)
        self.assertIn("+79990000000", text)
        self.assertNotIn("Сидоров Сидор", text)
        self.assertNotIn("Алиев Али", text)

    def test_start_prompts_for_supervisor_target(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)

        with _patch_admin_check(True):
            state = _run(
                handlers.supervisor_message_start(
                    _make_update(text="/supervisor_message"), ctx
                )
            )

        self.assertEqual(state, SUPERVISOR_MSG_ASK_TARGET)

    def test_receive_target_previews_message_for_supervisor_row(self) -> None:
        sup_ws = FakeWorksheet(
            [
                ["fio", "telegram_id"],
                ["Петров Петр Петрович", "444"],
            ]
        )
        mag_ws = FakeWorksheet(
            [
                ["ФИО магистранта", "Телефон", "Научный руков", "Регистрация"],
                ["Иванов Иван", "8 999 000-00-00", "Петров П.П.", "нет"],
            ]
        )
        reg_ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(reg_ws)
        ctx.bot_data[handlers.CONFIG_BOT_DATA_KEY].magistrants_worksheet_name = "Магистранты"
        spreadsheet = FakeSpreadsheet({"научрук": sup_ws, "Магистранты": mag_ws})
        update = _make_update(text="2")

        with _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.get_spreadsheet",
            return_value=spreadsheet,
        ), patch(
            "magister_checking.bot.supervisor_lists.get_spreadsheet",
            return_value=spreadsheet,
        ):
            state = _run(handlers.supervisor_message_receive_target(update, ctx))

        self.assertEqual(state, SUPERVISOR_MSG_CONFIRM)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_SUPERVISOR_MESSAGE_CHAT_ID], 444)
        preview = update.message.reply_text.await_args.args[0]
        self.assertIn("Петров Петр Петрович", preview)
        self.assertIn("Иванов Иван", preview)
        self.assertIn("Отправить?", preview)

    def test_confirm_sends_chunks_to_supervisor(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_MESSAGE_ROW] = 2
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_MESSAGE_FIO] = "Петров Петр"
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_MESSAGE_CHAT_ID] = 444
        ctx.user_data[handlers.USER_DATA_SUPERVISOR_MESSAGE_CHUNKS] = ["Текст"]
        update = _make_callback_update(callback_data="admsupmsg:send")

        with _patch_admin_check(True):
            state = _run(handlers.supervisor_message_confirm_callback(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        ctx.bot.send_message.assert_awaited_once()
        self.assertIn(
            "Сообщение научруку отправлено",
            update.callback_query.message.reply_text.await_args.args[0],
        )


class RecheckHandlerTests(unittest.TestCase):
    """Команда /recheck — повторная проверка магистрантом своей строки."""

    def _row(
        self,
        *,
        telegram_id: str = "111",
        fio: str = "Иванов И.И.",
        report_url: str = "https://docs.google.com/document/d/r/edit",
    ) -> list[str]:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = telegram_id
        row[SHEET_HEADER.index("fio")] = fio
        row[SHEET_HEADER.index("report_url")] = report_url
        return row

    def test_recheck_unknown_telegram_id_replies_start(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/recheck")

        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck(update, ctx))

        run.assert_not_called()
        msg = update.message.reply_text.await_args_list[-1].args[0]
        self.assertIn("/start", msg)

    def test_recheck_admin_without_registration_row_gets_target_hint(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/recheck")

        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck(update, ctx))

        run.assert_not_called()
        msg = update.message.reply_text.await_args_list[-1].args[0]
        self.assertIn("/spravka", msg)
        self.assertNotIn("/recheck", msg)
        self.assertIn("следующим сообщением", msg)
        self.assertNotIn("не привязан", msg)
        self.assertNotIn("Сначала пройдите регистрацию", msg)
        self.assertTrue(ctx.user_data.get("admin_recheck_pending"))

    def test_recheck_admin_followup_plain_text_runs_pipeline(self) -> None:
        ws = FakeWorksheet(
            [list(SHEET_HEADER), self._row(telegram_id="222", fio="Петров П.П.")]
        )
        ctx = _FakeContext(ws)
        ctx.user_data["admin_recheck_pending"] = True
        ctx.user_data["admin_recheck_only_if_changed"] = False
        update = _make_update(user_id=999, text="2")

        fake_report = RowCheckReport(fio="Петров П.П.", row_number=2)
        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Петров П.П."), {}),
        ):
            _run(admin_recheck_pending_receive(update, ctx))

        run.assert_called_once()
        locator = run.call_args.args[1]
        self.assertEqual(locator.row_number, 2)
        self.assertIsNone(ctx.user_data.get("admin_recheck_pending"))

    def test_recheck_admin_with_target_row_runs_pipeline(self) -> None:
        ws = FakeWorksheet(
            [list(SHEET_HEADER), self._row(telegram_id="222", fio="Петров П.П.")]
        )
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/recheck 2")

        fake_report = RowCheckReport(fio="Петров П.П.", row_number=2)
        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Петров П.П."), {}),
        ):
            _run(recheck(update, ctx))

        run.assert_called_once()
        locator = run.call_args.args[1]
        self.assertEqual(locator.row_number, 2)

    def test_recheck_non_admin_with_target_rejected(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="222")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck 2")

        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck(update, ctx))

        run.assert_not_called()
        msg = update.message.reply_text.await_args_list[-1].args[0]
        self.assertIn("только администраторы", msg)

    def test_recheck_runs_full_pipeline_for_known_user(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck")

        fake_report = RowCheckReport(fio="Иванов И.И.", row_number=2)
        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Иванов И.И."), {}),
        ):
            _run(recheck(update, ctx))

        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["only_if_changed"])
        self.assertTrue(kwargs["apply"])
        self.assertEqual(kwargs["history_source"], "bot")
        locator = run.call_args.args[1]
        self.assertEqual(locator.row_number, 2)

        sent = [c.args[0] for c in update.message.reply_text.await_args_list]
        self.assertTrue(any("полная проверка" in m for m in sent))
        self.assertTrue(any("Иванов И.И." in m for m in sent))

        final_markup = update.message.reply_text.await_args_list[-1].kwargs.get(
            "reply_markup"
        )
        self.assertIsInstance(final_markup, InlineKeyboardMarkup)
        self.assertEqual(
            final_markup.inline_keyboard[0][0].callback_data, f"{RECHECK_CALLBACK_DATA}:2"
        )

    def test_recheck_quick_passes_only_if_changed(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck quick")

        fake_report = RowCheckReport(
            fio="Иванов И.И.", row_number=2, unchanged=True
        )
        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run:
            _run(recheck(update, ctx))

        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["only_if_changed"])
        sent = [c.args[0] for c in update.message.reply_text.await_args_list]
        self.assertTrue(any("быстрый режим" in m for m in sent))
        self.assertFalse(any("--only-if-changed" in m for m in sent))

    def test_recheck_swallows_pipeline_exception_and_replies(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck")

        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check",
            side_effect=RuntimeError("drive boom"),
        ):
            _run(recheck(update, ctx))

        last_call = update.message.reply_text.await_args_list[-1]
        self.assertIn("Не удалось выполнить", last_call.args[0])
        self.assertIn("drive boom", last_call.args[0])
        retry_markup = last_call.kwargs.get("reply_markup")
        self.assertIsInstance(retry_markup, InlineKeyboardMarkup)
        self.assertEqual(
            retry_markup.inline_keyboard[0][0].callback_data, f"{RECHECK_CALLBACK_DATA}:2"
        )


def _make_callback_update(
    *,
    user_id: int = 111,
    callback_data: str = RECHECK_CALLBACK_DATA,
) -> MagicMock:
    """Update без message — только callback_query (нажатие inline-кнопки).

    ``callback_query.message`` мокается с ``reply_text`` для асинхронных
    ответов через ``_send_recheck_reply``.
    """

    update = MagicMock()
    update.message = None
    update.effective_user.id = user_id
    update.effective_user.username = "ivanov"
    update.effective_user.first_name = "Иван"
    update.effective_user.last_name = "Иванов"
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.delete = AsyncMock()
    query.message.reply_text = AsyncMock()
    update.callback_query = query
    chat = MagicMock()
    chat.type = ChatType.PRIVATE
    update.effective_chat = chat
    return update


class RecheckButtonTests(unittest.TestCase):
    """Кнопка «🔄 Перепроверить» — inline callback под отчётом/регистрацией."""

    def _row(self, *, telegram_id: str = "111") -> list[str]:
        row = [""] * len(SHEET_HEADER)
        row[SHEET_HEADER.index("telegram_id")] = telegram_id
        row[SHEET_HEADER.index("fio")] = "Иванов И.И."
        row[SHEET_HEADER.index("report_url")] = (
            "https://docs.google.com/document/d/r/edit"
        )
        return row

    def test_keyboard_payload_matches_callback_pattern(self) -> None:
        markup = build_recheck_keyboard()
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, RECHECK_BUTTON_LABEL)
        self.assertEqual(button.callback_data, RECHECK_CALLBACK_DATA)
        markup_row = build_recheck_keyboard(42)
        self.assertEqual(
            markup_row.inline_keyboard[0][0].callback_data,
            f"{RECHECK_CALLBACK_DATA}:42",
        )

    def test_button_acks_query_and_strips_keyboard_then_runs_full_pipeline(
        self,
    ) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_callback_update(user_id=111)

        fake_report = RowCheckReport(fio="Иванов И.И.", row_number=2)
        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Иванов И.И."), {}),
        ):
            _run(recheck_button(update, ctx))

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_reply_markup.assert_awaited_once_with(
            reply_markup=None
        )
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["only_if_changed"])
        self.assertTrue(kwargs["apply"])
        self.assertEqual(kwargs["history_source"], "bot")

        replies = update.callback_query.message.reply_text.await_args_list
        self.assertTrue(any("полная проверка" in c.args[0] for c in replies))
        self.assertTrue(any("Иванов И.И." in c.args[0] for c in replies))
        final_markup = replies[-1].kwargs.get("reply_markup")
        self.assertIsInstance(final_markup, InlineKeyboardMarkup)
        self.assertEqual(
            final_markup.inline_keyboard[0][0].callback_data, f"{RECHECK_CALLBACK_DATA}:2"
        )

    def test_button_unknown_telegram_id_replies_start_without_running_pipeline(
        self,
    ) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(user_id=999)

        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck_button(update, ctx))

        run.assert_not_called()
        update.callback_query.answer.assert_awaited_once()
        msg = update.callback_query.message.reply_text.await_args_list[-1].args[0]
        self.assertIn("/start", msg)

    def test_button_swallows_edit_message_badrequest(self) -> None:
        """Если кнопка не редактируется (старое сообщение / нет прав) — не падаем."""

        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_callback_update(user_id=111)
        update.callback_query.edit_message_reply_markup.side_effect = BadRequest(
            "Message can't be edited"
        )

        fake_report = RowCheckReport(fio="Иванов И.И.", row_number=2)
        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Иванов И.И."), {}),
        ):
            _run(recheck_button(update, ctx))

        run.assert_called_once()
        replies = update.callback_query.message.reply_text.await_args_list
        self.assertTrue(any("Иванов И.И." in c.args[0] for c in replies))

    def test_button_pipeline_exception_attaches_retry_keyboard(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_callback_update(user_id=111)

        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check",
            side_effect=RuntimeError("docs api 500"),
        ):
            _run(recheck_button(update, ctx))

        last_call = update.callback_query.message.reply_text.await_args_list[-1]
        self.assertIn("Не удалось выполнить", last_call.args[0])
        self.assertIn("docs api 500", last_call.args[0])
        retry_markup = last_call.kwargs.get("reply_markup")
        self.assertIsInstance(retry_markup, InlineKeyboardMarkup)
        self.assertEqual(
            retry_markup.inline_keyboard[0][0].callback_data, f"{RECHECK_CALLBACK_DATA}:2"
        )

    def test_admin_button_embedded_row_targets_same_line(self) -> None:
        """Админ без строки в таблице: callback ``recheck:full:N`` задаёт строку без повторного ввода."""

        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(
            user_id=999, callback_data=f"{RECHECK_CALLBACK_DATA}:7"
        )
        fake_report = RowCheckReport(fio="Студент", row_number=7)
        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run, patch(
            "magister_checking.bot.handlers.load_user_enrichment_for_row",
            return_value=(UserForm(fio="Студент"), {}),
        ):
            _run(recheck_button(update, ctx))

        run.assert_called_once()
        self.assertEqual(run.call_args.args[1].row_number, 7)


if __name__ == "__main__":
    unittest.main()
