"""Async-хендлеры Telegram-бота: команды и сбор анкеты по missing-полям."""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import logging
from typing import Awaitable, Callable, List, Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
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
from magister_checking.row_check_cli import (
    RowLocator,
    format_report,
    run_row_check,
)

logger = logging.getLogger("magistrcheckbot")
_REGISTRATION_SHEETS_LOCK = asyncio.Lock()

ASK_FIELD, ASK_CONFIRM, BIND_ASK_FIO, BIND_CONFIRM, PROJECT_CARD_ASK_TARGET = range(5)

USER_DATA_FORM_KEY = "form_data"
USER_DATA_PENDING_KEY = "pending_fields"
USER_DATA_CURRENT_KEY = "current_field"
USER_DATA_BIND_ROW_KEY = "bind_candidate_row"

CONFIG_BOT_DATA_KEY = "bot_config"
ADMIN_PROJECT_CARD_BUTTON = "Сформировать карточку проекта"

HELP_REPLY_TEXT = (
    "Команды бота:\n\n"
    "/start — регистрация, привязка к строке таблицы или продолжение анкеты\n"
    "/recheck — повторить проверку отчёта (если вы уже в таблице)\n"
    "/cancel — прервать текущий диалог\n"
    "/admin — панель администратора (только для telegram_id из листа "
    f"«{ADMINS_WORKSHEET_NAME}»)\n"
    "/project_card — сформировать PDF-карточку проекта (только админы)\n\n"
    f"В анкете поле можно пропустить: отправьте {SKIP_TOKEN} или /skip.\n\n"
    "Подсказка: меню команд (кнопка рядом с полем ввода) подтягивается "
    "после перезапуска бота."
)


def default_bot_commands() -> list[BotCommand]:
    """Команды для ``BotFather`` / меню Telegram (короткие описания ≤256 симв.)."""

    return [
        BotCommand("start", "Регистрация или продолжить анкету"),
        BotCommand("help", "Список команд и подсказки"),
        BotCommand("recheck", "Повторить проверку отчёта"),
        BotCommand("cancel", "Прервать текущий диалог"),
        BotCommand("admin", "Панель администратора"),
        BotCommand("project_card", "PDF-карточка проекта (админы)"),
    ]


def _format_user_visible_exc(exc: BaseException, *, limit: int = 480) -> str:
    """Одна короткая строка для пользователя без многострочного traceback."""

    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    first = text.split("\n", 1)[0]
    if len(first) > limit:
        return first[: limit - 1] + "…"
    return first


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


async def _call_blocking(func, /, *args, **kwargs):
    """Выполняет блокирующий I/O в рабочем потоке, не стопоря event loop."""

    return await asyncio.to_thread(func, *args, **kwargs)


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
        f"Если какое-то поле хотите заполнить позже, отправьте {SKIP_TOKEN} или /skip.\n\n"
        "/help — список команд, /cancel — прервать диалог."
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
    worksheet = await _call_blocking(get_worksheet, cfg)
    loaded = await _call_blocking(load_user, worksheet, row_number)
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
    worksheet = await _call_blocking(get_worksheet, cfg)

    user_form = _get_user_form(context)
    _set_telegram_identity(user_form, update)

    existing_row = await _call_blocking(
        find_row_by_telegram_id, worksheet, user_form.telegram_id
    )
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
        f"Если такой записи ещё нет, отправьте {SKIP_TOKEN} или /skip — пройдём регистрацию с нуля.\n\n"
        "/help — список команд, /cancel — прервать диалог."
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
            f"Кратко: {_format_user_visible_exc(exc)}\n\n"
            "Подробности — в логе бота (для администратора).",
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
    worksheet = await _call_blocking(get_worksheet, cfg)
    user_form = _get_user_form(context)

    raw = update.message.text or ""
    fio = normalize_text(raw)
    if not fio:
        return await _start_new_registration(update, context)

    user_form.fio = fio
    matches = await _call_blocking(find_rows_by_fio, worksheet, fio)
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
    candidate = await _call_blocking(load_user, worksheet, row_number)

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
    async with _REGISTRATION_SHEETS_LOCK:
        worksheet = await _call_blocking(get_worksheet, cfg)
        candidate = await _call_blocking(load_user, worksheet, row_number)
        current_telegram_id = (candidate.telegram_id or "").strip()
        requested_telegram_id = (user_form.telegram_id or "").strip()
        if current_telegram_id and current_telegram_id != requested_telegram_id:
            context.user_data[USER_DATA_BIND_ROW_KEY] = None
            await update.message.reply_text(
                "Пока вы подтверждали привязку, эта строка уже была занята другим Telegram-аккаунтом.\n\n"
                "Чтобы не повредить запись, запускаю обычную регистрацию с нуля."
            )
            return await _start_new_registration(update, context)

        await _call_blocking(
            attach_telegram_to_row,
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
    _refresh_status(user_form)
    _record_action(user_form, "confirmed_save")
    extra_values: dict[str, str] = {}
    if user_form.report_url:
        try:
            extra_values.update(build_sheet_enrichment(cfg, user_form))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось обогатить строку отчёта для telegram_id=%s",
                user_form.telegram_id or "?",
            )

    async with _REGISTRATION_SHEETS_LOCK:
        worksheet = await _call_blocking(get_worksheet, cfg)
        existing_row = await _call_blocking(
            find_row_by_telegram_id, worksheet, user_form.telegram_id
        )
        save_extra_values = dict(extra_values)
        if not existing_row:
            save_extra_values["timestamp"] = _registration_timestamp()

        if save_extra_values:
            row_num = await _call_blocking(
                upsert_user_with_extras,
                worksheet,
                user_form,
                extra_values=save_extra_values,
            )
        else:
            row_num = await _call_blocking(upsert_user, worksheet, user_form)
        try:
            await _call_blocking(sync_registration_dashboard, cfg)
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
            "Спасибо. Регистрация завершена.\n\n"
            "Чтобы запустить проверку вашего отчёта прямо сейчас — "
            "нажмите кнопку «🔄 Перепроверить» ниже или отправьте /recheck.",
            reply_markup=build_recheck_keyboard(),
        )

    return ConversationHandler.END


RECHECK_QUICK_TOKENS = {"quick", "only-if-changed", "only_if_changed", "fast", "diff"}
"""Ключевые слова, после которых ``/recheck`` работает как ``--only-if-changed``.

По умолчанию ``/recheck`` запускает полный прогон (handoff §8 — diff_detection
режим «full by default»), но магистрант может написать ``/recheck quick``,
чтобы получить ответ «без изменений» без повторной нагрузки на Drive."""

RECHECK_BUTTON_LABEL = "🔄 Перепроверить"
RECHECK_CALLBACK_DATA = "recheck:full"
"""Callback payload кнопки «Перепроверить». Эквивалент ``/recheck`` без аргументов
(полный прогон). Узкий paттern регистрируется в ``app.build_application``."""


def _parse_recheck_only_if_changed(message_text: str) -> bool:
    """Распознаёт ``/recheck quick`` и эквиваленты — handoff §8 ``--only-if-changed``."""

    parts = (message_text or "").strip().split()
    if len(parts) < 2:
        return False
    return parts[1].strip().lower() in RECHECK_QUICK_TOKENS


def build_recheck_keyboard() -> InlineKeyboardMarkup:
    """Inline-кнопка под итоговым отчётом и финалом регистрации.

    Inline (а не Reply) — чтобы кнопка была привязана к конкретному сообщению
    и пропадала после нажатия (см. ``recheck_button``). Это снижает риск
    случайного двойного запуска тяжёлого пайплайна.
    """

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(RECHECK_BUTTON_LABEL, callback_data=RECHECK_CALLBACK_DATA)]]
    )


async def _send_recheck_reply(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Отправляет ответ магистранту вне зависимости от источника обновления.

    Для ``/recheck`` источник — обычное сообщение (``update.message``).
    Для нажатия кнопки — ``update.callback_query.message`` (само inline-сообщение,
    под которым висит кнопка). В обоих случаях reply уйдёт в нужный чат.
    Если ни того, ни другого нет (странный update без сообщения) — молча
    выходим, чтобы не падать на ``None``.
    """

    if update.message is not None:
        await update.message.reply_text(text, reply_markup=reply_markup)
        return
    callback = update.callback_query
    if callback is not None and callback.message is not None:
        await callback.message.reply_text(text, reply_markup=reply_markup)


async def _do_recheck(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    only_if_changed: bool,
) -> None:
    """Общее тело /recheck: вызывается из ``recheck`` и ``recheck_button``.

    Поиск строки в листе ``Регистрация`` идёт строго по ``telegram_id`` —
    ФИО как fallback не используем, чтобы случайный однофамилец не мог
    инициировать перезапись чужой строки. Если ``telegram_id`` не привязан
    к листу, бот предлагает пройти ``/start`` и кнопку не показывает
    (без строки нечего перепроверять).

    Кнопка «Перепроверить» прицепляется к финальному отчёту, а также к
    сообщениям об ошибках пайплайна — чтобы пользователь мог сразу повторить
    при сетевом сбое или временном падении Drive/Docs API.

    Тяжёлая часть (Sheets/Drive/Docs IO) уходит в worker-поток, чтобы не
    блокировать event loop, — стандартный приём бота (``_call_blocking``).
    """

    if update.effective_user is None:
        return

    cfg = _bot_config(context)
    telegram_id = str(update.effective_user.id)

    worksheet = await _call_blocking(get_worksheet, cfg)
    row_number = await _call_blocking(find_row_by_telegram_id, worksheet, telegram_id)
    if row_number is None:
        await _send_recheck_reply(
            update,
            "Не нашёл вашу строку в листе «Регистрация».\n\n"
            "Сначала пройдите регистрацию: /start",
        )
        return

    mode_hint = (
        "быстрый режим (только если входы поменялись)"
        if only_if_changed
        else "полная проверка"
    )
    await _send_recheck_reply(
        update,
        f"Запускаю повторную проверку вашей строки {row_number}: {mode_hint}.\n"
        "Это может занять до минуты.",
    )

    locator = RowLocator(row_number=row_number)
    try:
        report = await _call_blocking(
            run_row_check,
            cfg,
            locator,
            skip_http=False,
            apply=True,
            only_if_changed=only_if_changed,
            history_source="bot",
        )
    except ValueError as exc:
        await _send_recheck_reply(
            update,
            f"Ошибка: {exc}",
            reply_markup=build_recheck_keyboard(),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Не удалось выполнить /recheck для telegram_id=%s row=%s",
            telegram_id,
            row_number,
        )
        await _send_recheck_reply(
            update,
            "Не удалось выполнить повторную проверку.\n\n"
            f"Причина: {exc}",
            reply_markup=build_recheck_keyboard(),
        )
        return

    applied_effective = not report.unchanged
    await _send_recheck_reply(
        update,
        format_report(report, applied=applied_effective),
        reply_markup=build_recheck_keyboard(),
    )


async def recheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда ``/recheck`` — магистрант запускает свою проверку повторно.

    По умолчанию выполняется полный прогон с ``apply=True``: lay результаты
    в J:R и пишет одну запись в «Историю проверок» с ``source="bot"``.
    Если магистрант написал ``/recheck quick``, включается короткое
    замыкание ``only_if_changed=True`` (см. ``RECHECK_QUICK_TOKENS``).
    """

    if update.message is None or update.effective_user is None:
        return

    only_if_changed = _parse_recheck_only_if_changed(update.message.text or "")
    await _do_recheck(update, context, only_if_changed=only_if_changed)


async def recheck_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-кнопка «🔄 Перепроверить» под отчётом или финалом регистрации.

    Поведение эквивалентно ``/recheck`` без аргументов — полный прогон
    с ``apply=True``. Сценарий ``quick`` через кнопку не предлагаем: это
    расширило бы UI на две кнопки без явной пользы — quick-режим остаётся
    доступен текстом ``/recheck quick`` (handoff §8).

    Перед запуском пайплайна снимаем клавиатуру с исходного сообщения, чтобы
    повторное нажатие не запустило тяжёлый прогон второй раз. Если правка
    падает (старое сообщение, нет прав) — молча игнорируем: на пайплайн это
    не влияет, а ``BadRequest`` от Telegram при невозможности редактирования
    клавиатуры — нормальная ситуация.
    """

    query = update.callback_query
    if query is None:
        return

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        # Сообщение слишком старое или нет прав на редактирование — не критично.
        pass

    await _do_recheck(update, context, only_if_changed=False)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel: прервать диалог."""

    user_form = _get_user_form(context)
    _record_action(user_form, "cancelled")
    await update.message.reply_text(
        "Диалог остановлен.\n\n"
        "Снова начать: /start. Справка по командам: /help."
    )
    return ConversationHandler.END


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Команда /help: краткая справка (работает и вне диалога)."""

    msg = update.effective_message
    if msg is not None:
        await msg.reply_text(HELP_REPLY_TEXT)


__all__ = [
    "ADMIN_PROJECT_CARD_BUTTON",
    "ASK_FIELD",
    "ASK_CONFIRM",
    "BIND_ASK_FIO",
    "BIND_CONFIRM",
    "CONFIG_BOT_DATA_KEY",
    "PROJECT_CARD_ASK_TARGET",
    "RECHECK_BUTTON_LABEL",
    "RECHECK_CALLBACK_DATA",
    "USER_DATA_BIND_ROW_KEY",
    "USER_DATA_FORM_KEY",
    "USER_DATA_PENDING_KEY",
    "USER_DATA_CURRENT_KEY",
    "admin_menu",
    "ask_confirm",
    "build_recheck_keyboard",
    "cancel",
    "confirm_bind",
    "default_bot_commands",
    "help_command",
    "project_card_receive_target",
    "project_card_start",
    "receive_bind_fio",
    "receive_field",
    "recheck",
    "recheck_button",
    "skip_bind",
    "skip_field",
    "start",
]
