"""Role-claim helper UI and formatting kept out of handler routing code."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from magister_checking.bot.models import UserForm


def _start_role_keyboard_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Магистрант", callback_data="start:pick:mag")],
            [InlineKeyboardButton("Научный руководитель", callback_data="start:pick:sup")],
            [InlineKeyboardButton("Администратор", callback_data="start:pick:admin")],
        ]
    )


def _start_role_keyboard_mag() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Новая регистрация", callback_data="start:mag:new")],
            [
                InlineKeyboardButton(
                    "Уже есть запись (по ФИО)", callback_data="start:mag:bind"
                )
            ],
        ]
    )


def _format_row_summary(form: UserForm) -> str:
    """Короткое описание найденной анкеты для подтверждения привязки."""

    return (
        f"ФИО: {form.fio or '—'}\n"
        f"Группа: {form.group_name or '—'}\n"
        f"Место работы: {form.workplace or '—'}\n"
        f"Должность: {form.position or '—'}\n"
        f"Научный руководитель: {form.supervisor or '—'}"
    )
