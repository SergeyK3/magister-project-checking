"""Хендлеры напоминания магистранту (admstu)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import unittest

from telegram import CallbackQuery, Message, Update, User
from telegram.ext import ConversationHandler

from magister_checking.bot.handlers import (
    student_reminder_confirm_callback,
)


def _run(coro):
    return asyncio.run(coro)


class StudentReminderConfirmTests(unittest.TestCase):
    def test_cancel_clears_and_ends(self) -> None:
        ctx = MagicMock()
        ctx.user_data = {"student_reminder_row": 5, "student_reminder_draft": "x"}
        q = AsyncMock(spec=CallbackQuery)
        q.data = "admstu:cancel"
        q.answer = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message.reply_text = AsyncMock()
        q.message.chat_id = 1
        upd = MagicMock(spec=Update)
        upd.callback_query = q
        upd.effective_user = User(id=1, first_name="A", is_bot=False)

        with patch(
            "magister_checking.bot.handlers.is_admin_telegram_id",
            return_value=True,
        ), patch(
            "magister_checking.bot.handlers._bot_config",
            return_value=MagicMock(),
        ):
            rc = _run(student_reminder_confirm_callback(upd, ctx))

        self.assertEqual(rc, ConversationHandler.END)
        self.assertFalse(ctx.user_data.get("student_reminder_row"))
        q.answer.assert_awaited()
        q.message.reply_text.assert_awaited()


if __name__ == "__main__":
    unittest.main()
