"""Async-хендлеры Telegram-бота: команды и сбор анкеты по missing-полям."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, List, Optional

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import (
    FIELD_LABELS,
    FIELD_PROMPTS,
    REQUIRED_FIELDS,
    SHEET_HEADER,
    UserForm,
    compute_fill_status,
    get_missing_field_keys,
    get_missing_fields,
)
from magister_checking.bot.sheets_repo import (
    find_row_by_telegram_id,
    get_worksheet,
    load_user,
    upsert_user,
)
from magister_checking.bot.validation import (
    SKIP_TOKEN,
    check_report_url,
    normalize_text,
)

logger = logging.getLogger("magistrcheckbot")

ASK_FIELD, ASK_CONFIRM = range(2)

USER_DATA_FORM_KEY = "form_data"
USER_DATA_PENDING_KEY = "pending_fields"
USER_DATA_CURRENT_KEY = "current_field"

CONFIG_BOT_DATA_KEY = "bot_config"


def _bot_config(context: ContextTypes.DEFAULT_TYPE) -> BotConfig:
    cfg = context.bot_data.get(CONFIG_BOT_DATA_KEY)
    if cfg is None:
        raise RuntimeError(
            "BotConfig не найден в context.bot_data — используйте build_application()"
        )
    return cfg


def _get_user_form(context: ContextTypes.DEFAULT_TYPE) -> UserForm:
    form = context.user_data.get(USER_DATA_FORM_KEY)
    if form is None:
        form = UserForm()
        context.user_data[USER_DATA_FORM_KEY] = form
    return form


def _set_telegram_identity(user_form: UserForm, update: Update) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    user_form.telegram_id = str(tg_user.id)
    user_form.telegram_username = tg_user.username or ""
    user_form.telegram_first_name = tg_user.first_name or ""
    user_form.telegram_last_name = tg_user.last_name or ""


def _set_pending_fields(
    context: ContextTypes.DEFAULT_TYPE, fields_to_ask: List[str]
) -> None:
    context.user_data[USER_DATA_PENDING_KEY] = list(fields_to_ask)


def _pop_next_field(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    pending: List[str] = context.user_data.get(USER_DATA_PENDING_KEY, [])
    if not pending:
        context.user_data[USER_DATA_CURRENT_KEY] = None
        return None
    next_field = pending.pop(0)
    context.user_data[USER_DATA_PENDING_KEY] = pending
    context.user_data[USER_DATA_CURRENT_KEY] = next_field
    return next_field


def _current_field(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return context.user_data.get(USER_DATA_CURRENT_KEY)


def _record_action(user_form: UserForm, action: str) -> None:
    user_form.last_action = action
    logger.debug("user %s -> %s", user_form.telegram_id or "?", action)


def _refresh_status(user_form: UserForm) -> None:
    user_form.fill_status = compute_fill_status(user_form).value


def _summary_text(user_form: UserForm) -> str:
    lines = [
        f"ФИО: {user_form.fio or '—'}",
        f"Группа: {user_form.group_name or '—'}",
        f"Место работы: {user_form.workplace or '—'}",
        f"Должность: {user_form.position or '—'}",
        f"Телефон: {user_form.phone or '—'}",
        f"Научный руководитель: {user_form.supervisor or '—'}",
        f"Ссылка на отчет: {user_form.report_url or '—'}",
        "",
        "Первичная проверка ссылки:",
        f"- формат URL: {user_form.report_url_valid or '—'}",
        f"- доступность: {user_form.report_url_accessible or '—'}",
        f"- предположительно открыт доступ: {user_form.report_url_public_guess or '—'}",
        "",
        f"Статус заполнения: {user_form.fill_status or '—'}",
        "",
        "Подтвердите сохранение: да / нет",
    ]
    return "\n".join(lines)


async def _prompt_next(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Спрашивает следующее missing-поле или переходит к подтверждению."""

    next_field = _pop_next_field(context)
    user_form = _get_user_form(context)

    if next_field is None:
        _refresh_status(user_form)
        _record_action(user_form, "show_summary")
        await update.message.reply_text(_summary_text(user_form))
        return ASK_CONFIRM

    _record_action(user_form, f"ask_{next_field}")
    prompt = FIELD_PROMPTS[next_field]
    hint = f"\n(чтобы пропустить — отправьте {SKIP_TOKEN} или /skip)"
    await update.message.reply_text(prompt + hint)
    return ASK_FIELD


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start: создаёт/находит запись и инициирует диалог."""

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)

    user_form = _get_user_form(context)
    _set_telegram_identity(user_form, update)

    existing_row = find_row_by_telegram_id(worksheet, user_form.telegram_id)
    if existing_row:
        loaded = load_user(worksheet, existing_row)
        context.user_data[USER_DATA_FORM_KEY] = loaded
        user_form = loaded
        _set_telegram_identity(user_form, update)
        _record_action(user_form, "start_returning")

        missing = get_missing_fields(user_form)
        if missing:
            await update.message.reply_text(
                "Вы уже есть в таблице. Продолжим заполнение.\n\n"
                f"Незаполненные поля: {', '.join(missing)}"
            )
            _set_pending_fields(context, get_missing_field_keys(user_form))
        else:
            await update.message.reply_text(
                "Вы уже зарегистрированы. Можно обновить данные.\n\n"
                "Сейчас пройдём поля заново — оставляйте текущее значение, "
                f"отправляя {SKIP_TOKEN}, либо вводите новое."
            )
            _set_pending_fields(context, list(REQUIRED_FIELDS))
    else:
        _record_action(user_form, "start_new")
        await update.message.reply_text(
            "Здравствуйте.\n\n"
            "Бот поможет зарегистрироваться для промежуточной аттестации магистрантов.\n"
            "Я последовательно задам вопросы и сохраню данные в таблицу.\n\n"
            f"Если какое-то поле хотите заполнить позже, отправьте {SKIP_TOKEN} или /skip."
        )
        _set_pending_fields(context, list(REQUIRED_FIELDS))

    return await _prompt_next(update, context)


async def receive_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Сохраняет ответ пользователя в текущее активное поле."""

    field_key = _current_field(context)
    if not field_key:
        await update.message.reply_text(
            "Не вижу активного вопроса. Нажмите /start, чтобы начать заново."
        )
        return ConversationHandler.END

    user_form = _get_user_form(context)
    raw = update.message.text or ""
    value = normalize_text(raw)
    setattr(user_form, field_key, value)

    if field_key == "report_url":
        if value:
            valid, accessible, public_guess = check_report_url(value)
            user_form.report_url_valid = valid
            user_form.report_url_accessible = accessible
            user_form.report_url_public_guess = public_guess
        else:
            user_form.report_url_valid = ""
            user_form.report_url_accessible = ""
            user_form.report_url_public_guess = ""

    _refresh_status(user_form)
    _record_action(user_form, f"answered_{field_key}")
    return await _prompt_next(update, context)


async def skip_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Команда /skip: пропустить текущее поле."""

    field_key = _current_field(context)
    if not field_key:
        await update.message.reply_text(
            "Сейчас нет активного вопроса. Нажмите /start, чтобы начать."
        )
        return ConversationHandler.END

    user_form = _get_user_form(context)
    setattr(user_form, field_key, "")
    if field_key == "report_url":
        user_form.report_url_valid = ""
        user_form.report_url_accessible = ""
        user_form.report_url_public_guess = ""

    _refresh_status(user_form)
    _record_action(user_form, f"skipped_{field_key}")
    return await _prompt_next(update, context)


async def ask_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Подтверждение сохранения и запись в Sheets."""

    answer = (update.message.text or "").strip().lower()
    user_form = _get_user_form(context)

    if answer not in {"да", "нет", "yes", "no"}:
        await update.message.reply_text("Введите: да или нет")
        return ASK_CONFIRM

    if answer in {"нет", "no"}:
        _record_action(user_form, "cancelled_save")
        await update.message.reply_text(
            "Сохранение отменено. Нажмите /start, чтобы начать заново."
        )
        return ConversationHandler.END

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)

    _refresh_status(user_form)
    _record_action(user_form, "confirmed_save")
    row_num = upsert_user(worksheet, user_form)

    missing = get_missing_fields(user_form)
    if missing:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            f"Ещё не заполнено: {', '.join(missing)}\n\n"
            "Позже вы можете снова нажать /start и продолжить."
        )
    else:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            "Регистрация завершена."
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel: прервать диалог."""

    user_form = _get_user_form(context)
    _record_action(user_form, "cancelled")
    await update.message.reply_text(
        "Диалог остановлен. Для нового запуска используйте /start"
    )
    return ConversationHandler.END


__all__ = [
    "ASK_FIELD",
    "ASK_CONFIRM",
    "CONFIG_BOT_DATA_KEY",
    "USER_DATA_FORM_KEY",
    "USER_DATA_PENDING_KEY",
    "USER_DATA_CURRENT_KEY",
    "ask_confirm",
    "cancel",
    "receive_field",
    "skip_field",
    "start",
]
