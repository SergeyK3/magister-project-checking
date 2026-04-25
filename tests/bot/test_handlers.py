"""Асинхронные тесты Telegram-хендлеров без сети и без токена."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest

from magister_checking.bot import handlers
from magister_checking.bot.handlers import (
    ADMIN_PROJECT_CARD_BUTTON,
    ASK_CONFIRM,
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    PROJECT_CARD_ASK_TARGET,
    RECHECK_BUTTON_LABEL,
    RECHECK_CALLBACK_DATA,
    admin_menu,
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
    start,
)
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.models import SHEET_HEADER, UserForm
from tests.bot.test_sheets_repo import FakeWorksheet


class _FakeContext:
    """Мини-замена ContextTypes.DEFAULT_TYPE."""

    def __init__(self, worksheet: FakeWorksheet) -> None:
        self.bot_data: dict = {handlers.CONFIG_BOT_DATA_KEY: MagicMock()}
        self.user_data: dict = {}
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


class StartHandlerTests(unittest.TestCase):
    def test_start_unknown_user_offers_binding(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws):
            state = _run(start(update, ctx))

        self.assertEqual(state, BIND_ASK_FIO)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.telegram_id, "111")
        self.assertEqual(form.last_action, "ask_bind_fio")

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

        with _patch_worksheet(ws):
            state = _run(start(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.fio, "Иванов И.И.")
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "position")
        pending = ctx.user_data[handlers.USER_DATA_PENDING_KEY]
        self.assertEqual(pending, ["report_url"])


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
        self.assertEqual(button.callback_data, RECHECK_CALLBACK_DATA)

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
        with _patch_worksheet(ws):
            state = _run(start(update, ctx))
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
            ["start", "help", "recheck", "cancel", "admin", "project_card"],
        )

    def test_help_command_replies_with_text(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/help")
        update.effective_message = update.message
        _run(help_command(update, ctx))
        update.message.reply_text.assert_awaited_once()
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("/start", text)
        self.assertIn("/recheck", text)


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
        self.assertEqual(reply_markup.keyboard[0][0].text, ADMIN_PROJECT_CARD_BUTTON)

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
        self.assertIn("Кратко:", last)
        self.assertIn("первая строка", last)
        self.assertNotIn("вторая строка", last)


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

        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck(update, ctx))

        run.assert_not_called()
        msg = update.message.reply_text.await_args_list[-1].args[0]
        self.assertIn("/start", msg)

    def test_recheck_runs_full_pipeline_for_known_user(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck")

        fake_report = RowCheckReport(fio="Иванов И.И.", row_number=2)
        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run:
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
            final_markup.inline_keyboard[0][0].callback_data, RECHECK_CALLBACK_DATA
        )

    def test_recheck_quick_passes_only_if_changed(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck quick")

        fake_report = RowCheckReport(
            fio="Иванов И.И.", row_number=2, unchanged=True
        )
        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check",
            return_value=fake_report,
        ) as run:
            _run(recheck(update, ctx))

        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["only_if_changed"])
        sent = [c.args[0] for c in update.message.reply_text.await_args_list]
        self.assertTrue(any("быстрый режим" in m for m in sent))
        self.assertTrue(any("--only-if-changed" in m for m in sent))

    def test_recheck_swallows_pipeline_exception_and_replies(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), self._row(telegram_id="111")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck")

        with _patch_worksheet(ws), patch(
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
            retry_markup.inline_keyboard[0][0].callback_data, RECHECK_CALLBACK_DATA
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
    query.message.reply_text = AsyncMock()
    update.callback_query = query
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
        ) as run:
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
            final_markup.inline_keyboard[0][0].callback_data, RECHECK_CALLBACK_DATA
        )

    def test_button_unknown_telegram_id_replies_start_without_running_pipeline(
        self,
    ) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(user_id=999)

        with _patch_worksheet(ws), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run:
            _run(recheck_button(update, ctx))

        run.assert_not_called()
        update.callback_query.answer.assert_awaited_once()
        msg = update.callback_query.message.reply_text.await_args.args[0]
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
        ) as run:
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
            retry_markup.inline_keyboard[0][0].callback_data, RECHECK_CALLBACK_DATA
        )


if __name__ == "__main__":
    unittest.main()
