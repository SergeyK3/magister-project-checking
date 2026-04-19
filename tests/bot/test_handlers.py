"""Асинхронные тесты Telegram-хендлеров без сети и без токена."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from magister_checking.bot import handlers
from magister_checking.bot.handlers import (
    ASK_CONFIRM,
    ASK_FIELD,
    ask_confirm,
    cancel,
    receive_field,
    skip_field,
    start,
)
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
    return update


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_worksheet(worksheet: FakeWorksheet):
    return patch(
        "magister_checking.bot.handlers.get_worksheet",
        return_value=worksheet,
    )


class StartHandlerTests(unittest.TestCase):
    def test_start_new_user_inits_pending_and_asks_first_field(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update()

        with _patch_worksheet(ws):
            state = _run(start(update, ctx))

        self.assertEqual(state, ASK_FIELD)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "fio")
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.telegram_id, "111")
        self.assertEqual(form.telegram_username, "ivanov")
        self.assertEqual(form.last_action, "ask_fio")
        self.assertEqual(update.message.reply_text.call_count, 2)

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
        update = _make_update()

        with _patch_worksheet(ws):
            _run(start(update, ctx))

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
            return_value=("yes", "yes", "yes"),
        ) as mock_check, _patch_worksheet(ws):
            state = _run(receive_field(update, ctx))

        mock_check.assert_called_once_with("https://docs.google.com/document/d/abc/edit")
        self.assertEqual(state, ASK_CONFIRM)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.report_url_valid, "yes")
        self.assertEqual(form.report_url_accessible, "yes")
        self.assertEqual(form.report_url_public_guess, "yes")
        self.assertEqual(form.fill_status, "REGISTERED")


class SkipFieldTests(unittest.TestCase):
    def test_skip_clears_field_and_advances(self) -> None:
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
        self.assertEqual(form.fio, "")
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
            report_url_public_guess="yes",
            fill_status="REGISTERED",
        )
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = form
        return form

    def test_yes_saves_to_sheet(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="да")
        with _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(len(ws.rows), 2)
        self.assertEqual(ws.rows[1][0], "111")
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.last_action, "confirmed_save")

    def test_no_cancels(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="нет")
        with _patch_worksheet(ws):
            state = _run(ask_confirm(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(len(ws.rows), 1)
        form = ctx.user_data[handlers.USER_DATA_FORM_KEY]
        self.assertEqual(form.last_action, "cancelled_save")

    def test_invalid_answer_keeps_state(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        self._prepare_form(ctx)

        update = _make_update(text="возможно")
        state = _run(ask_confirm(update, ctx))
        self.assertEqual(state, ASK_CONFIRM)


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


if __name__ == "__main__":
    unittest.main()
