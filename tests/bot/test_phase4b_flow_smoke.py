"""Phase 4B smoke coverage for stabilized bot routing flows."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from magister_checking.bot import handlers
from magister_checking.bot.handlers import (
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    RECHECK_CALLBACK_DATA,
    ROLE_PICK,
    recheck,
    recheck_button,
    receive_bind_fio,
    spravka_start,
    start,
    start_role_callback,
)
from magister_checking.bot.models import SHEET_HEADER, UserForm
from tests.bot.test_handlers import (
    _FakeContext,
    _make_callback_update,
    _make_update,
    _patch_admin_check,
    _patch_supervisor_check,
    _patch_worksheet,
    _run,
)
from tests.bot.test_sheets_repo import FakeWorksheet


def _registration_row(
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


class Phase4BStartAndRoleSmokeTests(unittest.TestCase):
    def test_start_unknown_user_routes_to_role_picker(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/start")

        with _patch_worksheet(ws), _patch_admin_check(False), _patch_supervisor_check(
            False
        ):
            state = _run(start(update, ctx))

        self.assertEqual(state, ROLE_PICK)
        self.assertEqual(
            ctx.user_data[handlers.USER_DATA_FORM_KEY].telegram_id,
            "111",
        )
        update.message.reply_text.assert_awaited_once()

    def test_role_selection_routes_bind_and_new_paths(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])

        bind_ctx = _FakeContext(ws)
        bind_update = _make_callback_update(callback_data="start:mag:bind")
        bind_state = _run(start_role_callback(bind_update, bind_ctx))
        self.assertEqual(bind_state, BIND_ASK_FIO)
        self.assertEqual(
            bind_ctx.user_data[handlers.USER_DATA_FORM_KEY].last_action,
            "ask_bind_fio",
        )

        new_ctx = _FakeContext(ws)
        new_update = _make_callback_update(callback_data="start:mag:new")
        with _patch_worksheet(ws):
            new_state = _run(start_role_callback(new_update, new_ctx))
        self.assertEqual(new_state, ASK_FIELD)
        self.assertEqual(new_ctx.user_data[handlers.USER_DATA_CURRENT_KEY], "fio")

    def test_bind_fio_routes_single_match_to_confirmation(self) -> None:
        ws = FakeWorksheet(
            [list(SHEET_HEADER), _registration_row(telegram_id="", fio="Иванов И.И.")]
        )
        ctx = _FakeContext(ws)
        ctx.user_data[handlers.USER_DATA_FORM_KEY] = UserForm(telegram_id="111")
        update = _make_update(text="иванов  и.и.")

        with _patch_worksheet(ws):
            state = _run(receive_bind_fio(update, ctx))

        self.assertEqual(state, BIND_CONFIRM)
        self.assertEqual(ctx.user_data[handlers.USER_DATA_BIND_ROW_KEY], 2)


class Phase4BSpravkaAndRetrySmokeTests(unittest.TestCase):
    def test_spravka_non_admin_dispatches_canonical_retry(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(text="/справка")

        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as do_recheck:
            state = _run(spravka_start(update, ctx))

        self.assertEqual(state, ConversationHandler.END)
        do_recheck.assert_awaited_once()
        self.assertTrue(do_recheck.call_args.kwargs["only_if_changed"])
        self.assertEqual(do_recheck.call_args.kwargs["report_trigger"], "spravka")

    def test_spravka_admin_without_target_routes_to_format_menu(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/справка")

        with _patch_admin_check(True):
            state = _run(spravka_start(update, ctx))

        self.assertEqual(state, handlers.SPRAVKA_MENU)
        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        callback_data = [
            row[0].callback_data for row in reply_markup.inline_keyboard
        ]
        self.assertEqual(
            callback_data,
            [
                handlers.SPRAVKA_CALLBACK_TELEGRAM,
                handlers.SPRAVKA_CALLBACK_COMMISSION,
                handlers.SPRAVKA_CALLBACK_PDF,
            ],
        )

    def test_recheck_command_dispatch_contract_for_registered_user(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), _registration_row()])
        ctx = _FakeContext(ws)
        update = _make_update(text="/recheck")

        with _patch_worksheet(ws), _patch_admin_check(False), patch(
            "magister_checking.bot.handlers.run_row_check"
        ) as run_row_check, patch(
            "magister_checking.bot.handlers._format_spravka_text_from_recheck",
            return_value="smoke",
        ):
            _run(recheck(update, ctx))

        run_row_check.assert_called_once()
        locator = run_row_check.call_args.args[1]
        self.assertEqual(locator.row_number, 2)
        self.assertEqual(
            run_row_check.call_args.kwargs,
            {
                "skip_http": False,
                "apply": True,
                "only_if_changed": False,
                "history_source": "bot",
                "trigger": "recheck",
            },
        )

    def test_recheck_button_dispatches_full_retry_without_admin_row_override(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_callback_update(callback_data=f"{RECHECK_CALLBACK_DATA}:7")

        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as do_recheck:
            _run(recheck_button(update, ctx))

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_reply_markup.assert_awaited_once_with(
            reply_markup=None
        )
        do_recheck.assert_awaited_once()
        self.assertFalse(do_recheck.call_args.kwargs["only_if_changed"])
        self.assertIsNone(do_recheck.call_args.kwargs["row_number_override"])


class Phase4BAdminRetryRoutingSmokeTests(unittest.TestCase):
    def test_admin_recheck_without_target_sets_pending_route(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER)])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/recheck")

        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as do_recheck:
            _run(recheck(update, ctx))

        do_recheck.assert_not_awaited()
        self.assertTrue(ctx.user_data["admin_recheck_pending"])
        self.assertFalse(ctx.user_data["admin_recheck_only_if_changed"])

    def test_admin_recheck_with_target_routes_to_row_override(self) -> None:
        ws = FakeWorksheet(
            [list(SHEET_HEADER), _registration_row(telegram_id="222", fio="Петров П.П.")]
        )
        ctx = _FakeContext(ws)
        update = _make_update(user_id=999, text="/recheck 2")

        with _patch_worksheet(ws), _patch_admin_check(True), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as do_recheck:
            _run(recheck(update, ctx))

        do_recheck.assert_awaited_once()
        self.assertEqual(do_recheck.call_args.kwargs["row_number_override"], 2)
        self.assertFalse(do_recheck.call_args.kwargs["only_if_changed"])

    def test_non_admin_recheck_with_target_is_rejected_before_retry(self) -> None:
        ws = FakeWorksheet([list(SHEET_HEADER), _registration_row(telegram_id="222")])
        ctx = _FakeContext(ws)
        update = _make_update(user_id=111, text="/recheck 2")

        with _patch_admin_check(False), patch(
            "magister_checking.bot.handlers._do_recheck", new_callable=AsyncMock
        ) as do_recheck:
            _run(recheck(update, ctx))

        do_recheck.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
