"""Admin menu and student-reminder formatting helpers."""

from __future__ import annotations

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

ADMSTU_CALLBACK_TEMPLATE_PATTERN = r"^admstu:(std|stdex|cust)$"
ADMSTU_CALLBACK_CONFIRM_PATTERN = r"^admstu:(send|cancel)$"
ADMSTUB_CALLBACK_CONFIRM_PATTERN = r"^admstub:(send|cancel)$"

ADMIN_PROJECT_CARD_BUTTON = "Сформировать карточку проекта"
ADMIN_STUDENT_MESSAGE_BUTTON = "Сообщение магистранту"
ADMIN_STUDENT_MESSAGE_BULK_BUTTON = "Групповое напоминание по строкам"
ROLE_MENU_SPRAVKA_BUTTON = "Справка / проверка"
ROLE_MENU_HELP_BUTTON = "Помощь"
ROLE_MENU_STATUS_BUTTON = "Проверить статус"
ROLE_MENU_REGISTER_BUTTON = "Продолжить регистрацию"
SUPERVISOR_STATUS_BUTTON = "Проверить магистранта"
SUPERVISOR_UNREGISTERED_BUTTON = "Кто не зарегистрировался"
SUPERVISOR_REGISTERED_BUTTON = "Кто зарегистрировался"
ADMIN_STATS_BUTTON = "Сводка"
BULK_STUDENT_REMINDER_MAX_ROWS = 40


def _admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [ROLE_MENU_SPRAVKA_BUTTON],
            [ADMIN_PROJECT_CARD_BUTTON],
            [ADMIN_STUDENT_MESSAGE_BUTTON],
            [ADMIN_STUDENT_MESSAGE_BULK_BUTTON],
            [ADMIN_STATS_BUTTON],
            [ROLE_MENU_HELP_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _supervisor_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [SUPERVISOR_STATUS_BUTTON],
            [SUPERVISOR_UNREGISTERED_BUTTON],
            [SUPERVISOR_REGISTERED_BUTTON],
            [ROLE_MENU_SPRAVKA_BUTTON],
            [ROLE_MENU_HELP_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _student_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [ROLE_MENU_SPRAVKA_BUTTON],
            [ROLE_MENU_REGISTER_BUTTON],
            [ROLE_MENU_STATUS_BUTTON],
            [ROLE_MENU_HELP_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _student_reminder_template_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Стандартное напоминание", callback_data="admstu:std")],
            [
                InlineKeyboardButton(
                    "Стандарт + замечания (до 3 строк)",
                    callback_data="admstu:stdex",
                )
            ],
            [InlineKeyboardButton("Только свой текст", callback_data="admstu:cust")],
        ]
    )


def _student_reminder_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Отправить", callback_data="admstu:send"),
                InlineKeyboardButton("Отмена", callback_data="admstu:cancel"),
            ]
        ]
    )


def _student_reminder_preview_text(draft: str) -> str:
    return (
        "Предпросмотр:\n"
        "════════════════════\n"
        f"{draft}\n"
        "════════════════════"
    )


def _student_reminder_bulk_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Отправить всем", callback_data="admstub:send"),
                InlineKeyboardButton("Отмена", callback_data="admstub:cancel"),
            ]
        ]
    )


def _parse_bulk_student_row_numbers(raw: str) -> tuple[list[int] | None, str | None]:
    """Разбор строки вида ``5 7 12`` или ``5,12,15`` — только целые номера строк (>= 2)."""

    parts = [p for p in re.split(r"[\s,;]+", (raw or "").strip()) if p.strip()]
    if not parts:
        return None, "Список номеров строк пустой. Пример: «5 7 9» или «12,15,20»."
    rows: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None, (
                "Укажите только номера строк (целые числа), через пробел, запятую или перевод строки. "
                f"Не понял фрагмент: {p!r}."
            )
        rows.append(int(p))
    out: list[int] = []
    seen: set[int] = set()
    for n in rows:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out, None


def _bulk_delivery_summary_line(row_no: int, ok: bool, detail: str) -> str:
    if not ok:
        return f"• стр. {row_no}: ошибка — {detail[:200]}"
    if "Вложена HTML-справка" in detail:
        return f"• стр. {row_no}: отправлено + HTML снимок"
    if "Вложение снимка не добавлено" in detail:
        return f"• стр. {row_no}: текст да, вложение снимка — ошибка (см. лог)"
    if "не найден в папках Drive" in detail:
        return f"• стр. {row_no}: текст да, снимка на Drive нет"
    return f"• стр. {row_no}: отправлено"
