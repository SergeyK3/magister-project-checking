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
    attach_telegram_to_row,
    find_row_by_telegram_id,
    find_rows_by_fio,
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

ASK_FIELD, ASK_CONFIRM, BIND_ASK_FIO, BIND_CONFIRM = range(4)

USER_DATA_FORM_KEY = "form_data"
USER_DATA_PENDING_KEY = "pending_fields"
USER_DATA_CURRENT_KEY = "current_field"
USER_DATA_BIND_ROW_KEY = "bind_candidate_row"

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


async def _start_new_registration(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Запускает обычный сценарий регистрации с нуля."""

    user_form = _get_user_form(context)
    _record_action(user_form, "start_new")
    await update.message.reply_text(
        "Здравствуйте.\n\n"
        "Бот поможет зарегистрироваться для промежуточной аттестации магистрантов.\n"
        "Я последовательно задам вопросы и сохраню данные в таблицу.\n\n"
        f"Если какое-то поле хотите заполнить позже, отправьте {SKIP_TOKEN} или /skip."
    )
    _set_pending_fields(context, list(REQUIRED_FIELDS))
    return await _prompt_next(update, context)


async def _resume_registration_from_row(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    row_number: int,
    action: str,
) -> int:
    """Переключает диалог в режим продолжения регистрации по существующей строке."""

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)

    loaded = load_user(worksheet, row_number)
    context.user_data[USER_DATA_FORM_KEY] = loaded
    _set_telegram_identity(loaded, update)
    _record_action(loaded, action)

    missing = get_missing_fields(loaded)
    if missing:
        await update.message.reply_text(
            "Продолжим заполнение.\n\n"
            f"Незаполненные поля: {', '.join(missing)}"
        )
        _set_pending_fields(context, get_missing_field_keys(loaded))
    else:
        await update.message.reply_text(
            "Все поля уже заполнены. Можно обновить данные.\n\n"
            "Пройдём поля заново — оставляйте текущее значение, "
            f"отправляя {SKIP_TOKEN}, либо вводите новое."
        )
        _set_pending_fields(context, list(REQUIRED_FIELDS))

    return await _prompt_next(update, context)


def _format_row_summary(form: UserForm) -> str:
    """Короткое описание найденной анкеты для подтверждения привязки."""

    return (
        f"ФИО: {form.fio or '—'}\n"
        f"Группа: {form.group_name or '—'}\n"
        f"Место работы: {form.workplace or '—'}\n"
        f"Должность: {form.position or '—'}\n"
        f"Научный руководитель: {form.supervisor or '—'}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start: ищет запись по Telegram ID или предлагает привязку по ФИО."""

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)

    user_form = _get_user_form(context)
    _set_telegram_identity(user_form, update)

    existing_row = find_row_by_telegram_id(worksheet, user_form.telegram_id)
    if existing_row:
        await update.message.reply_text("Вы уже есть в таблице.")
        return await _resume_registration_from_row(
            update,
            context,
            row_number=existing_row,
            action="start_returning",
        )

    await update.message.reply_text(
        "Здравствуйте.\n\n"
        "Если вы уже отправляли промежуточный отчёт через форму или старосту, "
        "введите ФИО ровно так, как в форме — я найду вашу запись и привяжу к ней этот аккаунт.\n\n"
        f"Если такой записи ещё нет, отправьте {SKIP_TOKEN} или /skip — пройдём регистрацию с нуля."
    )
    _record_action(user_form, "ask_bind_fio")
    return BIND_ASK_FIO


async def skip_bind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /skip на этапе привязки — сразу запускает новую регистрацию."""

    user_form = _get_user_form(context)
    _record_action(user_form, "bind_skipped")
    return await _start_new_registration(update, context)


async def receive_bind_fio(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Обрабатывает ответ пользователя с ФИО для попытки привязки."""

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)
    user_form = _get_user_form(context)

    raw = update.message.text or ""
    fio = normalize_text(raw)
    if not fio:
        return await _start_new_registration(update, context)

    matches = find_rows_by_fio(worksheet, fio)
    if not matches:
        await update.message.reply_text(
            "Не нашёл записи с таким ФИО. Пройдём регистрацию с нуля."
        )
        return await _start_new_registration(update, context)

    if len(matches) > 1:
        _record_action(user_form, "bind_multiple_matches")
        await update.message.reply_text(
            "Нашёл несколько записей с таким ФИО. Обратитесь к администратору, "
            "чтобы он подсказал, какую строку привязать. Пока запускаю обычную регистрацию."
        )
        return await _start_new_registration(update, context)

    row_number = matches[0]
    candidate = load_user(worksheet, row_number)

    if candidate.telegram_id and candidate.telegram_id != user_form.telegram_id:
        _record_action(user_form, "bind_already_taken")
        await update.message.reply_text(
            "К этой записи уже привязан другой Telegram-аккаунт. "
            "Если это ошибка, обратитесь к администратору. Пока запускаю обычную регистрацию."
        )
        return await _start_new_registration(update, context)

    context.user_data[USER_DATA_BIND_ROW_KEY] = row_number
    _record_action(user_form, "bind_confirm_pending")
    await update.message.reply_text(
        "Нашёл запись:\n\n"
        f"{_format_row_summary(candidate)}\n\n"
        "Это вы? Ответьте: да / нет"
    )
    return BIND_CONFIRM


async def confirm_bind(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Подтверждение привязки telegram_id к найденной строке."""

    answer = (update.message.text or "").strip().lower()
    user_form = _get_user_form(context)

    if answer not in {"да", "нет", "yes", "no"}:
        await update.message.reply_text("Введите: да или нет")
        return BIND_CONFIRM

    row_number = context.user_data.get(USER_DATA_BIND_ROW_KEY)
    if answer in {"нет", "no"} or not row_number:
        context.user_data[USER_DATA_BIND_ROW_KEY] = None
        await update.message.reply_text("Хорошо, пройдём регистрацию с нуля.")
        return await _start_new_registration(update, context)

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)
    attach_telegram_to_row(
        worksheet,
        row_number,
        telegram_id=user_form.telegram_id,
        telegram_username=user_form.telegram_username,
        telegram_first_name=user_form.telegram_first_name,
        telegram_last_name=user_form.telegram_last_name,
    )
    context.user_data[USER_DATA_BIND_ROW_KEY] = None

    await update.message.reply_text(
        f"Привязал ваш Telegram к строке {row_number}."
    )
    return await _resume_registration_from_row(
        update,
        context,
        row_number=row_number,
        action="bind_attached",
    )


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
    "BIND_ASK_FIO",
    "BIND_CONFIRM",
    "CONFIG_BOT_DATA_KEY",
    "USER_DATA_BIND_ROW_KEY",
    "USER_DATA_FORM_KEY",
    "USER_DATA_PENDING_KEY",
    "USER_DATA_CURRENT_KEY",
    "ask_confirm",
    "cancel",
    "confirm_bind",
    "receive_bind_fio",
    "receive_field",
    "skip_bind",
    "skip_field",
    "start",
]
