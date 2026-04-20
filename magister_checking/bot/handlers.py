"""Async-хендлеры Telegram-бота: команды и сбор анкеты по missing-полям."""

from __future__ import annotations

from datetime import datetime
import io
import logging
from typing import Awaitable, Callable, List, Optional

from telegram import InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
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
from magister_checking.bot.report_enrichment import build_sheet_enrichment
from magister_checking.bot.sheets_repo import (
    ADMINS_WORKSHEET_NAME,
    attach_telegram_to_row,
    find_row_by_telegram_id,
    find_rows_by_fio,
    get_worksheet,
    is_admin_telegram_id,
    load_row_values,
    load_user,
    sync_registration_dashboard,
    upsert_user,
    upsert_user_with_extras,
)
from magister_checking.bot.validation import (
    SKIP_TOKEN,
    check_report_url,
    normalize_text,
)
from magister_checking.project_card_pipeline import generate_project_card_pdf

logger = logging.getLogger("magistrcheckbot")

ASK_FIELD, ASK_CONFIRM, BIND_ASK_FIO, BIND_CONFIRM, PROJECT_CARD_ASK_TARGET = range(5)

USER_DATA_FORM_KEY = "form_data"
USER_DATA_PENDING_KEY = "pending_fields"
USER_DATA_CURRENT_KEY = "current_field"
USER_DATA_BIND_ROW_KEY = "bind_candidate_row"

CONFIG_BOT_DATA_KEY = "bot_config"
ADMIN_PROJECT_CARD_BUTTON = "Сформировать карточку проекта"


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


def _admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[ADMIN_PROJECT_CARD_BUTTON]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tg_user = update.effective_user
    if tg_user is None:
        return False
    return is_admin_telegram_id(_bot_config(context), str(tg_user.id))


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
        "",
        f"Статус заполнения: {user_form.fill_status or '—'}",
        "",
        "Подтвердите сохранение: да / нет",
    ]
    return "\n".join(lines)


def _review_prompt_text() -> str:
    return (
        "Сейчас я покажу данные, которые будут сохранены в таблицу.\n\n"
        "Пожалуйста, внимательно проверьте их.\n"
        "Если всё верно, напишите: Да.\n"
        f"Если есть ошибка, пройдём вопросы заново: для верных ответов отправляйте {SKIP_TOKEN}, "
        "а неверный ответ перепишите."
    )


def _registration_timestamp() -> str:
    now = datetime.now()
    return f"{now.day:02d}.{now.month:02d}.{now.year} {now.hour}:{now.minute:02d}:{now.second:02d}"


def _is_skip_text(raw: str) -> bool:
    stripped = (raw or "").strip()
    return stripped in {SKIP_TOKEN, "/skip"}


def _resolve_project_card_target_row(
    worksheet,
    raw_target: str,
) -> tuple[int | None, str | None]:
    target = normalize_text(raw_target)
    if not target:
        return None, "Введите номер строки или ФИО магистранта."

    if target.isdigit():
        row_number = int(target)
        if row_number < 2:
            return None, "Укажите номер строки данных, начиная со 2-й."
        row = load_row_values(worksheet, row_number)
        if not any(str(value).strip() for value in row):
            return None, f"Строка {row_number} пуста или не найдена."
        return row_number, None

    matches = find_rows_by_fio(worksheet, target)
    if not matches:
        return None, "Не нашёл магистранта с таким ФИО. Введите ФИО точнее или номер строки."
    if len(matches) > 1:
        return None, (
            "Нашёл несколько строк с таким ФИО. "
            f"Уточните номер строки: {', '.join(str(row) for row in matches)}"
        )
    return matches[0], None


async def _prompt_next(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Спрашивает следующее missing-поле или переходит к подтверждению."""

    next_field = _pop_next_field(context)
    user_form = _get_user_form(context)

    if next_field is None:
        _refresh_status(user_form)
        _record_action(user_form, "show_summary")
        await update.message.reply_text(_review_prompt_text())
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
    _set_pending_fields(context, get_missing_field_keys(user_form))
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


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает администратору кнопку запуска формирования карточки."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Панель администратора.\n\n"
        "Нажмите кнопку ниже или используйте /project_card, чтобы сформировать PDF-карточку проекта.",
        reply_markup=_admin_keyboard(),
    )
    return ConversationHandler.END


async def project_card_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускает сценарий формирования карточки проекта для администратора."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите номер строки листа `Регистрация` или ФИО магистранта.\n\n"
        "Я обновлю данные по отчету и диссертации, сформирую PDF-карточку и пришлю файл.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PROJECT_CARD_ASK_TARGET


async def project_card_receive_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Обрабатывает цель для формирования PDF-карточки проекта."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)
    target = update.message.text or ""
    row_number, error_message = _resolve_project_card_target_row(worksheet, target)
    if error_message:
        await update.message.reply_text(error_message)
        return PROJECT_CARD_ASK_TARGET

    assert row_number is not None
    await update.message.reply_text(
        f"Формирую карточку проекта для строки {row_number}. Это может занять до минуты."
    )
    try:
        result = generate_project_card_pdf(config=cfg, row_number=row_number)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось сформировать карточку проекта для строки %s", row_number)
        await update.message.reply_text(
            "Не удалось сформировать карточку проекта.\n\n"
            f"Причина: {exc}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_document(
        document=InputFile(io.BytesIO(result.pdf_bytes), filename=result.pdf_name),
        caption=(
            "Карточка проекта сформирована.\n\n"
            f"Строка: {result.row_number}\n"
            f"Файл: {result.pdf_name}"
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


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

    user_form.fio = fio
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
    if _is_skip_text(raw):
        value = getattr(user_form, field_key)
    else:
        value = normalize_text(raw)
        setattr(user_form, field_key, value)

    if field_key == "report_url":
        if _is_skip_text(raw) and getattr(user_form, field_key):
            pass
        elif value:
            valid, accessible = check_report_url(value)
            user_form.report_url_valid = valid
            user_form.report_url_accessible = accessible
        else:
            user_form.report_url_valid = ""
            user_form.report_url_accessible = ""

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
    if field_key == "report_url":
        if not getattr(user_form, field_key):
            user_form.report_url_valid = ""
            user_form.report_url_accessible = ""

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
        _record_action(user_form, "requested_correction")
        await update.message.reply_text(
            "Хорошо, давайте исправим данные.\n\n"
            f"Сейчас я пройду все поля заново. Для верных значений отправляйте {SKIP_TOKEN}, "
            "а неверный ответ перепишите."
        )
        _set_pending_fields(context, list(REQUIRED_FIELDS))
        return await _prompt_next(update, context)

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)
    existing_row = find_row_by_telegram_id(worksheet, user_form.telegram_id)

    _refresh_status(user_form)
    _record_action(user_form, "confirmed_save")
    extra_values: dict[str, str] = {}
    if not existing_row:
        extra_values["timestamp"] = _registration_timestamp()
    if user_form.report_url:
        try:
            extra_values.update(build_sheet_enrichment(cfg, user_form))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось обогатить строку отчёта для telegram_id=%s",
                user_form.telegram_id or "?",
            )

    row_num = (
        upsert_user_with_extras(worksheet, user_form, extra_values=extra_values)
        if extra_values
        else upsert_user(worksheet, user_form)
    )
    try:
        sync_registration_dashboard(cfg)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось обновить Dashboard для spreadsheet_id=%s",
            cfg.spreadsheet_id,
        )

    missing = get_missing_fields(user_form)
    if missing:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            f"Ещё не заполнено: {', '.join(missing)}\n\n"
            "Спасибо. Регистрация сохранена.\n"
            "Позже вы можете снова нажать /start и продолжить."
        )
    else:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            "Спасибо. Регистрация завершена."
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
    "ADMIN_PROJECT_CARD_BUTTON",
    "ASK_FIELD",
    "ASK_CONFIRM",
    "BIND_ASK_FIO",
    "BIND_CONFIRM",
    "CONFIG_BOT_DATA_KEY",
    "PROJECT_CARD_ASK_TARGET",
    "USER_DATA_BIND_ROW_KEY",
    "USER_DATA_FORM_KEY",
    "USER_DATA_PENDING_KEY",
    "USER_DATA_CURRENT_KEY",
    "admin_menu",
    "ask_confirm",
    "cancel",
    "confirm_bind",
    "project_card_receive_target",
    "project_card_start",
    "receive_bind_fio",
    "receive_field",
    "skip_bind",
    "skip_field",
    "start",
]
