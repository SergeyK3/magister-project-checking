"""Async-хендлеры Telegram-бота: команды и сбор анкеты по missing-полям."""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import logging
import re
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
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from magister_checking.bot.config import BotConfig
from magister_checking.bot.google_api_errors import (
    GOOGLE_SHEETS_RATE_LIMIT_ADMIN_NOTE,
    GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE,
    is_google_sheets_rate_limit,
)
from magister_checking.bot.models import (
    FIELD_LABELS,
    FIELD_PROMPTS,
    FillStatus,
    REQUIRED_FIELDS,
    SHEET_HEADER,
    UserForm,
    compute_fill_status,
    get_missing_field_keys,
    get_missing_fields,
)
from magister_checking.bot.phone_normalize import normalize_phone_ru_kz
from magister_checking.bot.pin_verify import (
    PIN_LEN_MAX,
    PIN_LEN_MIN,
    PIN_MAX_ATTEMPTS,
    PIN_TTL_SEC,
    PinVerifyResult,
    clear_challenge,
    issue_pin_challenge,
    verify_pin_challenge,
)
from magister_checking.bot.report_enrichment import build_sheet_enrichment
from magister_checking.bot.sheets_repo import (
    ADMINS_WORKSHEET_NAME,
    SUPERVISORS_WORKSHEET_NAME,
    attach_telegram_to_row,
    find_row_by_telegram_id,
    find_rows_by_fio,
    get_spreadsheet,
    get_optional_worksheet,
    get_telegram_id_at_row,
    get_worksheet,
    get_supervisor_fio_for_telegram_id,
    fio_text_from_worksheet_row,
    phone_text_from_worksheet_row,
    is_admin_telegram_id,
    is_supervisor_telegram_id,
    magistrants_sheet_column_indices,
    build_dashboard_rows,
    format_dashboard_telegram_message,
    load_row_values,
    load_user,
    normalize_fio,
    registration_students_by_fio_phone,
    set_row_fill_status,
    supervisor_name_matches,
    sync_registration_dashboard,
    sync_magistrants_registration_status,
    upsert_user,
    upsert_user_with_extras,
)
from magister_checking.bot.validation import (
    REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE,
    SKIP_TOKEN,
    check_report_url,
    check_report_url_target_kind,
    normalize_fio_user_input,
    normalize_text,
)
from magister_checking.project_card_pipeline import generate_project_card_pdf
from magister_checking.project_snapshot import project_snapshot_from_json_str
from magister_checking.snapshot_render import (
    render_commission_telegram_html,
    render_spravka_telegram_html,
)
from magister_checking.broadcast import send_broadcast
from magister_checking.drive_latest_snapshot import (
    download_drive_file_bytes,
    pick_latest_snapshot_for_row,
    wrap_commission_html_for_browser,
)
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.student_notify_text import build_standard_reminder
from magister_checking.bot.supervisor_lists import (
    supervisor_registered_report,
    supervisor_unregistered_report,
)
from magister_checking.row_check_cli import (
    RowLocator,
    format_report,
    load_user_enrichment_for_row,
    run_row_check,
)

logger = logging.getLogger("magistrcheckbot")
_REGISTRATION_SHEETS_LOCK = asyncio.Lock()


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == ChatType.PRIVATE


async def group_start_use_private_chat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """В группах и супергруппах регистрация не ведётся — ответ в тот же чат с подсказкой."""

    msg = update.effective_message
    if msg is None:
        return
    me = await context.bot.get_me()
    uname = f"@{me.username}" if me.username else "бота"
    await msg.reply_text(
        f"Регистрация только в личном чате. Откройте диалог с {uname} и нажмите /start."
    )

ASK_FIELD, ASK_CONFIRM, BIND_ASK_FIO, BIND_CONFIRM, PROJECT_CARD_ASK_TARGET = range(5)
SPRAVKA_MENU, SPRAVKA_ASK_TARGET = 5, 6
ROLE_PICK, CLAIM_ASK_FIO, CLAIM_CONFIRM = 7, 8, 9
(
    STUDENT_MSG_ASK_TARGET,
    STUDENT_MSG_PICK_KIND,
    STUDENT_MSG_ASK_EXTRA,
    STUDENT_MSG_ASK_CUSTOM,
    STUDENT_MSG_CONFIRM,
    STUDENT_MSG_BULK_ASK_ROWS,
    STUDENT_MSG_BULK_CONFIRM,
) = (10, 11, 12, 13, 14, 15, 16)
PIN_VERIFY_INPUT = 17

ADMSTU_CALLBACK_TEMPLATE_PATTERN = r"^admstu:(std|stdex|cust)$"
ADMSTU_CALLBACK_CONFIRM_PATTERN = r"^admstu:(send|cancel)$"
ADMSTUB_CALLBACK_CONFIRM_PATTERN = r"^admstub:(send|cancel)$"

USER_DATA_FORM_KEY = "form_data"
USER_DATA_PENDING_KEY = "pending_fields"
USER_DATA_CURRENT_KEY = "current_field"
USER_DATA_BIND_ROW_KEY = "bind_candidate_row"
USER_DATA_CLAIM_TARGET_KEY = "claim_target"
USER_DATA_CLAIM_ROW_KEY = "claim_candidate_row"
USER_DATA_SPRAVKA_MODE = "spravka_mode"
USER_DATA_ADMIN_RECHECK_PENDING = "admin_recheck_pending"
USER_DATA_ADMIN_RECHECK_ONLY_IF_CHANGED = "admin_recheck_only_if_changed"
USER_DATA_STUDENT_REMINDER_ROW = "student_reminder_row"
USER_DATA_STUDENT_REMINDER_FIO = "student_reminder_fio"
USER_DATA_STUDENT_REMINDER_DRAFT = "student_reminder_draft"
USER_DATA_STUDENT_BULK_ENTRIES = "student_reminder_bulk_entries"
USER_DATA_PIN_CONTEXT_KEY = "pin_verify_context"
USER_DATA_PIN_REGISTER_EXTRA_KEY = "pin_verify_register_extra"

ADMIN_PROJECT_CARD_BUTTON = "Сформировать карточку проекта"
ADMIN_STUDENT_MESSAGE_BUTTON = "Сообщение магистранту"
ADMIN_STUDENT_MESSAGE_BULK_BUTTON = "Групповое напоминание по строкам"
BULK_STUDENT_REMINDER_MAX_ROWS = 40

CONFIG_BOT_DATA_KEY = "bot_config"

HELP_REPLY_TEXT_STUDENT = (
    "Команды бота (магистрант):\n\n"
    "/start — регистрация: привязка к строке в таблице «Регистрация» или "
    "продолжение анкеты. Числовой Telegram ID подставляется из вашего "
    "аккаунта автоматически.\n"
    "/register — первичная регистрация (анкета с нуля).\n"
    "/status — кратко: ваша строка в «Регистрация» (если уже привязаны).\n"
    "/recheck — повторить проверку промежуточного отчёта (когда вы уже в таблице)\n"
    "/cancel — прервать текущий диалог\n"
    "/spravka — краткий отчёт по проверке по вашей строке (как после /recheck)\n"
    "/help — эта справка\n\n"
    "При привязке к строке или перед сохранением анкеты бот может попросить одноразовый "
    "код по телефону из таблицы (см. лог бота в учебном контуре).\n\n"
    f"В анкете поле можно пропустить: отправьте {SKIP_TOKEN} или /skip.\n\n"
    "Меню команд (кнопка у поля ввода) подтягивается после перезапуска бота.\n\n"
    "Полные материалы для комиссии и проверка чужой строки — у администраторов; "
    "при необходимости обратитесь к куратору."
)

HELP_REPLY_TEXT_ADMIN = (
    "Команды бота (администратор):\n\n"
    "/start — регистрация, привязка к строке таблицы или продолжение анкеты\n"
    "/recheck — повторить проверку отчёта: своя строка; либо укажите цель в той же "
    "команде — номер строки листа «Регистрация» или ФИО (если вашего telegram_id нет "
    "в таблице, без номера/ФИО запуск нечего привязать)\n"
    "/cancel — прервать текущий диалог\n"
    "/admin — панель администратора (только для telegram_id из листа "
    f"«{ADMINS_WORKSHEET_NAME}»)\n"
    "/project_card — сформировать PDF-карточку проекта (только админы)\n"
    "/student_message — сообщение одному магистранту из шаблона (только админы)\n"
    "/student_message_bulk — стандартное напоминание по списку номеров строк (админы)\n"
    "/spravka — кратко для магистранта, полный текст для комиссии в чате, PDF, "
    "либо вложенный JSON с Drive в человекочитаемый вид (режимы «чужой строки» "
    "и JSON — у админа)\n"
    "/stats — краткая сводка по регистрациям, как в листе «Dashboard»\n"
    "/sync_dashboard — обновить лист «Dashboard» в Google Sheets\n"
    "/sync_magistrants — обновить лист магистрантов (колонка «Регистрация», телефоны +7)\n"
    "/unreg ФИО научрука — превью того же списка, что научрук видит по /unreg без аргументов "
    "(только админы; достаточно фамилии, если однозначно)\n"
    "/reg_list ФИО научрука — превью /reg_list для указанного научрука (только админы)\n\n"
    f"В анкете поле можно пропустить: отправьте {SKIP_TOKEN} или /skip.\n\n"
    "Меню команд (кнопка у поля ввода) подтягивается после перезапуска бота."
)

HELP_REPLY_TEXT_SUPERVISOR = (
    "Команды бота (научный руководитель):\n\n"
    "/start — при первом входе: выбор роли и привязка к строке в листе "
    f"«{SUPERVISORS_WORKSHEET_NAME}» по ФИО (как в таблице), если ещё не привязаны\n"
    "/unreg — список ваших магистрантов без регистрации в боте (лист «Магистранты»)\n"
    "/reg_list — список зарегистрированных с кратким статусом\n"
    "/status ФИО подопечного — полная проверка промежуточного отчёта и статус проекта по строке «Регистрация» "
    "(как админская /recheck по этому магистранту; до минуты, результаты пишутся в лист и «История проверок»)\n"
    "/help — эта справка\n"
    "/cancel — прервать текущий диалог (если бот ожидает ввод)\n\n"
    "Проверка отчётов и анкета — в сценарии магистранта (/register, /start)."
)

# Обратная совместимость: раньше был один объединённый текст.
HELP_REPLY_TEXT = HELP_REPLY_TEXT_ADMIN


def help_reply_for_user(
    *, is_admin: bool, is_supervisor: bool = False
) -> str:
    """Текст /help: приоритет админ → научрук → магистрант."""

    if is_admin:
        return HELP_REPLY_TEXT_ADMIN
    if is_supervisor:
        return HELP_REPLY_TEXT_SUPERVISOR
    return HELP_REPLY_TEXT_STUDENT


def default_bot_commands() -> list[BotCommand]:
    """Команды для ``BotFather`` / меню Telegram (короткие описания ≤256 симв.)."""

    return [
        BotCommand("start", "Регистрация или продолжить анкету"),
        BotCommand("help", "Список команд и подсказки"),
        BotCommand("recheck", "Повторить проверку отчёта"),
        BotCommand("cancel", "Прервать текущий диалог"),
        BotCommand("admin", "Панель администратора"),
        BotCommand("project_card", "PDF-карточка проекта (админы)"),
        BotCommand("student_message", "Сообщение магистранту (админы)"),
        BotCommand(
            "student_message_bulk",
            "Стандартное напоминание списку строк (админы)",
        ),
        BotCommand("spravka", "Справка: магистр., комиссия, PDF, JSON→текст"),
        BotCommand("stats", "Сводка Dashboard в чат (админы)"),
        BotCommand("sync_dashboard", "Обновить лист Dashboard (админы)"),
        BotCommand("sync_magistrants", "Синхронизация листа магистрантов (админы)"),
        BotCommand("register", "Первичная регистрация (магистрант)"),
        BotCommand("status", "Статус магистранта / научрука"),
        BotCommand(
            "unreg",
            "Незарегистрированные (научрук); админ: ФИО научрука — превью",
        ),
        BotCommand(
            "reg_list",
            "Зарегистрированные (научрук); админ: ФИО научрука — превью",
        ),
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


def _user_message_for_api_failure(
    exc: BaseException, *, audience: str = "student", limit: int = 480
) -> str:
    """Текст ошибки API для чата: при 429 — дружелюбное объяснение про лимит Google."""

    if is_google_sheets_rate_limit(exc):
        return (
            GOOGLE_SHEETS_RATE_LIMIT_ADMIN_NOTE
            if audience == "admin"
            else GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE
        )
    return _format_user_visible_exc(exc, limit=limit)


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
        [
            [ADMIN_PROJECT_CARD_BUTTON],
            [ADMIN_STUDENT_MESSAGE_BUTTON],
            [ADMIN_STUDENT_MESSAGE_BULK_BUTTON],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _clear_student_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        USER_DATA_STUDENT_REMINDER_ROW,
        USER_DATA_STUDENT_REMINDER_FIO,
        USER_DATA_STUDENT_REMINDER_DRAFT,
        USER_DATA_STUDENT_BULK_ENTRIES,
    ):
        context.user_data.pop(key, None)


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


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tg_user = update.effective_user
    if tg_user is None:
        return False
    return is_admin_telegram_id(_bot_config(context), str(tg_user.id))


def _is_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tg_user = update.effective_user
    if tg_user is None:
        return False
    return is_supervisor_telegram_id(_bot_config(context), str(tg_user.id))


def _message_for_bot_reply(update: Update):
    """Сообщение чата для ``reply_text`` (команда / callback-запрос)."""

    if update.message is not None:
        return update.message
    if update.callback_query is not None and update.callback_query.message is not None:
        return update.callback_query.message
    return update.effective_message


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
    """Обновляет ``fill_status`` в памяти: анкета и сохранённые итоги проверки §12."""

    base = compute_fill_status(user_form)
    raw = (user_form.fill_status or "").strip()
    try:
        current_enum = FillStatus(raw) if raw else None
    except ValueError:
        current_enum = None

    post_check = {
        FillStatus.OK,
        FillStatus.NEED_FIX,
        FillStatus.ERROR,
        FillStatus.CHECKING,
    }

    if base in (FillStatus.NEW, FillStatus.PARTIAL):
        user_form.fill_status = base.value
        return

    if current_enum in post_check:
        user_form.fill_status = current_enum.value
        return

    user_form.fill_status = FillStatus.REGISTERED.value


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


async def _deliver_reminder_text_and_snapshot(
    bot,
    cfg: BotConfig,
    *,
    row_no: int,
    chat_id_target: int,
    message_text: str,
) -> tuple[bool, str]:
    """Текст в личку и при возможности HTML по снимку на Drive.

    При успешной отправке текста второй элемент — текст для администратора
    (обычно хвост с пояснением про вложение).
    При ошибке доставки текста второй элемент — строка ошибки Telegram.
    """

    result = await send_broadcast(
        bot,
        [str(chat_id_target)],
        message_text,
        sleep_between=0.0,
    )
    if not result.sent:
        reason = result.failed[0][1] if result.failed else "неизвестная ошибка"
        return False, str(reason)

    attach_note = ""
    pick = await _call_blocking(pick_latest_snapshot_for_row, cfg, row_no)
    if pick is not None:
        try:
            raw_bytes = await _call_blocking(
                download_drive_file_bytes,
                cfg,
                pick.file_id,
            )
            snap = await _call_blocking(
                project_snapshot_from_json_str,
                raw_bytes.decode("utf-8"),
            )
            html_page = wrap_commission_html_for_browser(
                render_commission_telegram_html(snap)
            )
            caption = (
                "Снимок последней сохранённой проверки (откройте HTML в браузере — все ссылки активны). "
                f"На Drive: «{pick.name}» ({pick.modified_time[:10]})."
            )
            if len(caption) > 1024:
                caption = caption[:1021] + "…"
            await bot.send_document(
                chat_id=chat_id_target,
                document=InputFile(
                    io.BytesIO(html_page.encode("utf-8")),
                    filename=f"proverka_stroka_{row_no}.html",
                ),
                caption=caption,
            )
            attach_note = "\n\nВложена HTML-справка по последнему JSON-снимку на Drive."
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "student_reminder: вложение по снимку для строки %s: %s",
                row_no,
                exc,
                exc_info=True,
            )
            attach_note = (
                "\n\nВложение снимка не добавлено — см. лог бота (Drive/JSON/размер)."
            )
    else:
        attach_note = (
            "\n\nСнимок для вложения не найден в папках Drive (задайте "
            "PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS и сохраняйте снимки после проверки/карточки)."
        )
    return True, attach_note


_TELEGRAM_SPRAVKA_MAX = 4000
"""Запас под лимит 4096 символов одного ``sendMessage`` (справка, отчёты)."""

SPRAVKA_CALLBACK_TELEGRAM = "spravka:telegram"
SPRAVKA_CALLBACK_PDF = "spravka:pdf"
SPRAVKA_CALLBACK_COMMISSION = "spravka:commission"

# Макс. размер JSON-файла project snapshot, принимаемого в чат (байты)
_SNAPSHOT_JSON_MAX_BYTES = 1_500_000


def _iter_telegram_spravka_chunks(
    text: str, max_len: int = _TELEGRAM_SPRAVKA_MAX
) -> list[str]:
    if len(text) <= max_len:
        return [text]
    out: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            cut = max_len
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return out


async def _reply_spravka_to_message(
    message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """Отправляет длинную справку чанками, ``reply_markup`` — только на последнем."""

    parts = _iter_telegram_spravka_chunks(text)
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        await message.reply_text(
            part,
            parse_mode=parse_mode,
            reply_markup=reply_markup if is_last else None,
        )


def _format_spravka_text_from_recheck(
    cfg: BotConfig,
    report: RowCheckReport,
    row_number: int,
    trigger: str,
    *,
    view: str = "student",
    as_html: bool = True,
) -> str:
    """Синхронная сборка текста справки (для ``asyncio.to_thread`` / общего пути /recheck)."""

    applied_effective = not report.unchanged
    if not report.unchanged:
        user_row, extra_values = load_user_enrichment_for_row(cfg, row_number)
        return format_report(
            report,
            applied=applied_effective,
            user=user_row,
            extra_values=extra_values,
            fill_status=None,
            trigger=trigger,
            view=view,
            as_html=as_html,
        )
    return format_report(
        report,
        applied=applied_effective,
        trigger=trigger,
        view=view,
        as_html=as_html,
    )


def _is_skip_text(raw: str) -> bool:
    stripped = (raw or "").strip()
    return stripped in {SKIP_TOKEN, "/skip"}


def _resolve_project_card_target_row(
    worksheet,
    raw_target: str,
) -> tuple[int | None, str | None]:
    target = normalize_fio_user_input(raw_target)
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

    msg = _message_for_bot_reply(update)
    if msg is None:
        return ConversationHandler.END

    next_field = _pop_next_field(context)
    user_form = _get_user_form(context)

    if next_field is None:
        _refresh_status(user_form)
        _record_action(user_form, "show_summary")
        await msg.reply_text(_review_prompt_text())
        await msg.reply_text(_summary_text(user_form))
        return ASK_CONFIRM

    _record_action(user_form, f"ask_{next_field}")
    prompt = FIELD_PROMPTS[next_field]
    hint = f"\n(чтобы пропустить — отправьте {SKIP_TOKEN} или /skip)"
    await msg.reply_text(prompt + hint)
    return ASK_FIELD


async def _start_new_registration(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Запускает обычный сценарий регистрации с нуля."""

    msg = _message_for_bot_reply(update)
    if msg is None:
        return ConversationHandler.END

    user_form = _get_user_form(context)
    _record_action(user_form, "start_new")
    await msg.reply_text(
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

    msg = _message_for_bot_reply(update)
    if msg is None:
        return ConversationHandler.END

    missing = get_missing_fields(loaded)
    if missing:
        await msg.reply_text(
            "Продолжим заполнение.\n\n"
            f"Незаполненные поля: {', '.join(missing)}"
        )
        _set_pending_fields(context, get_missing_field_keys(loaded))
    else:
        await msg.reply_text(
            "Все поля уже заполнены. Можно обновить данные.\n\n"
            "Пройдём поля заново — оставляйте текущее значение, "
            f"отправляя {SKIP_TOKEN}, либо вводите новое."
        )
        _set_pending_fields(context, list(REQUIRED_FIELDS))

    return await _prompt_next(update, context)


def _pin_challenge_prompt_text(pin_len: int) -> str:
    minutes = max(1, PIN_TTL_SEC // 60)
    return (
        "Подтвердите свой контактный телефон из таблицы: введите одноразовый "
        f"цифровой код из {pin_len} символов.\n\n"
        f"Код действует около {minutes} мин. Попробовать можно до {PIN_MAX_ATTEMPTS} раз.\n\n"
        "Если ошиблись диалогом — /cancel.\n\n"
        "В учебном контуре код также пишется в лог бота администратором (SMS нет)."
    )


async def _finalize_bind_attachment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    row_number: int,
) -> int:
    cfg = _bot_config(context)
    user_form = _get_user_form(context)
    msg = update.effective_message

    async with _REGISTRATION_SHEETS_LOCK:
        worksheet = await _call_blocking(get_worksheet, cfg)
        candidate = await _call_blocking(load_user, worksheet, row_number)
        current_telegram_id = (candidate.telegram_id or "").strip()
        requested_telegram_id = (user_form.telegram_id or "").strip()
        if current_telegram_id and current_telegram_id != requested_telegram_id:
            context.user_data[USER_DATA_BIND_ROW_KEY] = None
            if msg is not None:
                await msg.reply_text(
                    "Пока вы подтверждали, эта строка уже была занята другим Telegram-аккаунтом.\n\n"
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
        try:
            await _call_blocking(sync_registration_dashboard, cfg)
        except Exception as sync_exc:  # noqa: BLE001
            logger.exception(
                "Не удалось обновить Dashboard после привязки spreadsheet_id=%s",
                cfg.spreadsheet_id,
            )
            if is_google_sheets_rate_limit(sync_exc):
                logger.warning("Google Sheets rate limit после привязки (Dashboard)")
        try:
            await _call_blocking(sync_magistrants_registration_status, cfg)
        except Exception as mag_exc:  # noqa: BLE001
            logger.exception(
                "Не удалось обновить лист магистрантов после привязки spreadsheet_id=%s",
                cfg.spreadsheet_id,
            )
            if is_google_sheets_rate_limit(mag_exc):
                logger.warning("Google Sheets rate limit после привязки (магистранты)")
    context.user_data[USER_DATA_BIND_ROW_KEY] = None
    reply = msg or update.message
    if reply is not None:
        await reply.reply_text(f"Привязал ваш Telegram к строке {row_number}.")
    return await _resume_registration_from_row(
        update,
        context,
        row_number=row_number,
        action="bind_attached",
    )


async def _finalize_claim_attachment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    user_form = _get_user_form(context)
    msg = update.effective_message
    target = (context.user_data.get(USER_DATA_CLAIM_TARGET_KEY) or "").strip()
    if target not in {"admin", "supervisor"}:
        if msg is not None:
            await msg.reply_text("Сессия привязки сброшена. Нажмите /start.")
        return ConversationHandler.END

    row_number = context.user_data.get(USER_DATA_CLAIM_ROW_KEY)
    if not row_number:
        if msg is not None:
            await msg.reply_text("Сессия привязки сброшена. Нажмите /start.")
        return ConversationHandler.END

    cfg = _bot_config(context)
    wsheet, sheet_label = await _claim_worksheet_for_target(cfg, target)
    if wsheet is None:
        context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
        context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
        if msg is not None:
            await msg.reply_text("Лист таблицы недоступен. Позже: /start.")
        return ConversationHandler.END

    async with _REGISTRATION_SHEETS_LOCK:
        current = await _call_blocking(get_telegram_id_at_row, wsheet, int(row_number))
        req = (user_form.telegram_id or "").strip()
        if current and current != req:
            context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
            context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
            if msg is not None:
                await msg.reply_text(
                    "Пока вы подтверждали, эта строка уже была занята другим Telegram-аккаунтом. "
                    "Обратитесь к администратору."
                )
            return ConversationHandler.END

        await _call_blocking(
            attach_telegram_to_row,
            wsheet,
            int(row_number),
            telegram_id=user_form.telegram_id,
            telegram_username=user_form.telegram_username,
            telegram_first_name=user_form.telegram_first_name,
            telegram_last_name=user_form.telegram_last_name,
        )
    context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
    context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
    if target == "admin":
        assert msg is not None
        await msg.reply_text(
            f"Готово: ваш Telegram привязан к записи в «{sheet_label}». "
            "Панель: /admin. Справка: /help."
        )
    else:
        assert msg is not None
        await msg.reply_text(
            f"Готово: ваш Telegram привязан к записи в «{sheet_label}». Справка: /help."
        )
    return ConversationHandler.END


async def _finalize_registration_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    extra_values: dict[str, str],
) -> int:
    """Запись анкеты после «да» (сразу или после успешного PIN)."""

    msg = update.effective_message
    assert msg is not None

    cfg = _bot_config(context)
    user_form = _get_user_form(context)
    dashboard_rate_note = ""
    magistrants_rate_note = ""
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
        except Exception as sync_exc:  # noqa: BLE001
            logger.exception(
                "Не удалось обновить Dashboard для spreadsheet_id=%s",
                cfg.spreadsheet_id,
            )
            if is_google_sheets_rate_limit(sync_exc):
                dashboard_rate_note = "\n\n" + GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE
        try:
            await _call_blocking(sync_magistrants_registration_status, cfg)
        except Exception as mag_exc:  # noqa: BLE001
            logger.exception(
                "Не удалось обновить лист магистрантов для spreadsheet_id=%s",
                cfg.spreadsheet_id,
            )
            if is_google_sheets_rate_limit(mag_exc):
                magistrants_rate_note = "\n\n" + GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE

    missing = get_missing_fields(user_form)
    rate_notes = dashboard_rate_note + magistrants_rate_note
    if missing:
        await msg.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            f"Ещё не заполнено: {', '.join(missing)}\n\n"
            "Спасибо. Регистрация сохранена.\n"
            "Позже вы можете снова нажать /start и продолжить."
            + rate_notes
        )
    else:
        await msg.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Статус: {user_form.fill_status}\n"
            "Спасибо. Регистрация завершена.\n\n"
            "Чтобы запустить проверку вашего отчёта прямо сейчас — "
            "нажмите кнопку «🔄 Перепроверить» ниже или отправьте /recheck."
            + rate_notes,
            reply_markup=build_recheck_keyboard(row_num),
        )

    return ConversationHandler.END


async def receive_pin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    msg = update.effective_message
    if msg is None or msg.text is None:
        return PIN_VERIFY_INPUT

    ctx = context.user_data.get(USER_DATA_PIN_CONTEXT_KEY)
    if not isinstance(ctx, dict) or not ctx.get("kind"):
        await msg.reply_text("Подтверждение недоступно. Начните с /start.")
        await clear_challenge(_get_user_form(context).telegram_id or "")
        return ConversationHandler.END

    raw = msg.text.strip()
    digits_only = "".join(c for c in raw if c.isdigit())
    if len(digits_only) < PIN_LEN_MIN or len(digits_only) > PIN_LEN_MAX:
        await msg.reply_text(
            f"Нужно от {PIN_LEN_MIN} до {PIN_LEN_MAX} цифр подряд. Попробуйте ещё раз или /cancel."
        )
        return PIN_VERIFY_INPUT

    user_form = _get_user_form(context)
    telegram_key = user_form.telegram_id or ""
    result = await verify_pin_challenge(telegram_key, digits_only)

    if result == PinVerifyResult.WRONG:
        await msg.reply_text(
            "Код не подошёл. Проверьте цифры и отправьте снова или /cancel."
        )
        return PIN_VERIFY_INPUT

    if result == PinVerifyResult.EXPIRED:
        context.user_data.pop(USER_DATA_PIN_CONTEXT_KEY, None)
        context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
        await msg.reply_text("Время действия кода истекло. Начните с шага подтверждения заново (/start).")
        return ConversationHandler.END

    if result in {PinVerifyResult.LOCKED, PinVerifyResult.NO_CHALLENGE}:
        context.user_data.pop(USER_DATA_PIN_CONTEXT_KEY, None)
        context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
        await msg.reply_text(
            "Слишком много неверных попыток или сессия устарела. Начните сначала: /start."
        )
        return ConversationHandler.END

    kind = str(ctx.get("kind"))
    context.user_data.pop(USER_DATA_PIN_CONTEXT_KEY, None)

    if kind == "bind":
        row_number = context.user_data.get(USER_DATA_BIND_ROW_KEY)
        context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
        if not row_number:
            await msg.reply_text("Привязка сброшена. Нажмите /start.")
            return ConversationHandler.END
        return await _finalize_bind_attachment(
            update, context, row_number=int(row_number)
        )

    if kind == "claim":
        context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
        return await _finalize_claim_attachment(update, context)

    if kind == "register_save":
        extras = context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
        extra_values: dict[str, str]
        if isinstance(extras, dict):
            extra_values = {str(k): str(v) for k, v in extras.items()}
        else:
            extra_values = {}
        return await _finalize_registration_confirm(
            update, context, extra_values=extra_values
        )

    context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
    await msg.reply_text("Неизвестный сценарий. /start.")
    return ConversationHandler.END


def _format_row_summary(form: UserForm) -> str:
    """Короткое описание найденной анкеты для подтверждения привязки."""

    return (
        f"ФИО: {form.fio or '—'}\n"
        f"Группа: {form.group_name or '—'}\n"
        f"Место работы: {form.workplace or '—'}\n"
        f"Должность: {form.position or '—'}\n"
        f"Научный руководитель: {form.supervisor or '—'}"
    )


async def _claim_worksheet_for_target(
    config: BotConfig, target: str
):
    """Лист «Администраторы» или «научрук» для привязки по ФИО."""

    if target == "admin":
        name = ADMINS_WORKSHEET_NAME
    elif target == "supervisor":
        name = SUPERVISORS_WORKSHEET_NAME
    else:
        return None, None
    spreadsheet = await _call_blocking(get_spreadsheet, config)
    return await _call_blocking(get_optional_worksheet, spreadsheet, name), name


async def start_role_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Инлайн-кнопки после /start: роль и сценарий магистранта."""

    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    if not _is_private_chat(update):
        try:
            await query.answer("Регистрация только в личном чате с ботом.", show_alert=True)
        except BadRequest:
            pass
        return ConversationHandler.END
    data = (query.data or "").strip()
    user_form = _get_user_form(context)
    _set_telegram_identity(user_form, update)
    try:
        await query.answer()
    except BadRequest:
        pass
    if not data.startswith("start:"):
        return ConversationHandler.END

    msg = query.message
    if msg is None:
        return ConversationHandler.END

    if data == "start:pick:mag":
        await msg.reply_text(
            "Магистрант: выберите вариант.",
            reply_markup=_start_role_keyboard_mag(),
        )
        return ROLE_PICK

    if data == "start:mag:new":
        return await _start_new_registration(update, context)

    if data == "start:mag:bind":
        await msg.reply_text(
            "Здравствуйте.\n\n"
            "Если вы уже отправляли промежуточный отчёт через форму или старосту, "
            "введите ФИО ровно так, как в форме — я найду вашу запись и привяжу к ней "
            "этот аккаунт.\n\n"
            f"Если такой записи ещё нет, отправьте {SKIP_TOKEN} или /skip — пройдём "
            "регистрацию с нуля.\n\n"
            "/help — список команд, /cancel — прервать диалог."
        )
        _record_action(user_form, "ask_bind_fio")
        return BIND_ASK_FIO

    if data == "start:pick:admin":
        context.user_data[USER_DATA_CLAIM_TARGET_KEY] = "admin"
        context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
        await msg.reply_text(
            f"Введите ФИО точно так, как в листе «{ADMINS_WORKSHEET_NAME}» в таблице "
            "(там должна быть заранее строка с вашим ФИО). После поиска спрошу подтверждение."
        )
        return CLAIM_ASK_FIO

    if data == "start:pick:sup":
        context.user_data[USER_DATA_CLAIM_TARGET_KEY] = "supervisor"
        context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
        await msg.reply_text(
            f"Введите ФИО точно так, как в листе «{SUPERVISORS_WORKSHEET_NAME}» "
            "в таблице (там должна быть заранее строка с вашим ФИО). "
            "После поиска спрошу подтверждение."
        )
        return CLAIM_ASK_FIO

    return ROLE_PICK


async def receive_claim_fio(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """ФИО для привязки к строке на листе админов или научруков."""

    target = (context.user_data.get(USER_DATA_CLAIM_TARGET_KEY) or "").strip()
    if target not in {"admin", "supervisor"}:
        await update.message.reply_text("Сначала нажмите /start и выберите роль на кнопках.")
        return ConversationHandler.END

    cfg = _bot_config(context)
    wsheet, sheet_label = await _claim_worksheet_for_target(cfg, target)
    if wsheet is None:
        await update.message.reply_text(
            f"Не найден лист «{sheet_label}» в этой таблице. Обратитесь к администратору."
        )
        return ConversationHandler.END

    user_form = _get_user_form(context)
    raw = update.message.text or ""
    fio = normalize_fio_user_input(raw)
    if not fio:
        await update.message.reply_text("Введите непустое ФИО или /cancel.")
        return CLAIM_ASK_FIO

    matches = await _call_blocking(find_rows_by_fio, wsheet, fio)
    if not matches:
        await update.message.reply_text(
            f"Не нашёл строки с таким ФИО в «{sheet_label}». "
            "Проверьте написание или обратитесь к администратору."
        )
        return CLAIM_ASK_FIO

    if len(matches) > 1:
        await update.message.reply_text(
            "Нашёл несколько строк с таким ФИО. "
            f"Уточните, какая ваша, или введите номер строки: {', '.join(str(r) for r in matches)}"
        )
        return CLAIM_ASK_FIO

    row_number = matches[0]
    bound = await _call_blocking(get_telegram_id_at_row, wsheet, row_number)
    req = (user_form.telegram_id or "").strip()
    if bound and bound != req:
        await update.message.reply_text(
            "К этой записи уже привязан другой Telegram-аккаунт. "
            "Если это ошибка, обратитесь к администратору."
        )
        return ConversationHandler.END

    context.user_data[USER_DATA_CLAIM_ROW_KEY] = row_number
    display_fio = await _call_blocking(fio_text_from_worksheet_row, wsheet, row_number)
    if not display_fio:
        display_fio = fio
    _record_action(user_form, "claim_confirm_pending")
    await update.message.reply_text(
        f"Нашёл в «{sheet_label}»:\n\n"
        f"ФИО: {display_fio}\n\n"
        "Это вы? Ответьте: да / нет"
    )
    return CLAIM_CONFIRM


async def confirm_claim(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Подтверждение привязки telegram_id к листу админов / научруков."""

    answer = (update.message.text or "").strip().lower()
    user_form = _get_user_form(context)

    if answer not in {"да", "нет", "yes", "no"}:
        await update.message.reply_text("Введите: да или нет")
        return CLAIM_CONFIRM

    target = (context.user_data.get(USER_DATA_CLAIM_TARGET_KEY) or "").strip()
    if target not in {"admin", "supervisor"}:
        await update.message.reply_text("Сессия привязки сброшена. Нажмите /start.")
        return ConversationHandler.END

    row_number = context.user_data.get(USER_DATA_CLAIM_ROW_KEY)
    if answer in {"нет", "no"} or not row_number:
        context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
        await update.message.reply_text("Хорошо. Введите ФИО снова (как в таблице).")
        return CLAIM_ASK_FIO

    cfg = _bot_config(context)
    wsheet, sheet_label = await _claim_worksheet_for_target(cfg, target)
    if wsheet is None:
        context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
        context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
        await update.message.reply_text("Лист таблицы недоступен. Позже: /start.")
        return ConversationHandler.END

    async with _REGISTRATION_SHEETS_LOCK:
        current = await _call_blocking(get_telegram_id_at_row, wsheet, int(row_number))
        req = (user_form.telegram_id or "").strip()
        if current and current != req:
            context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
            context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
            await update.message.reply_text(
                "Пока вы подтверждали, эта строка уже была занята другим Telegram-аккаунтом. "
                "Обратитесь к администратору."
            )
            return ConversationHandler.END

    phone_canon = normalize_phone_ru_kz(
        (await _call_blocking(phone_text_from_worksheet_row, wsheet, int(row_number)) or "").strip()
    )
    if phone_canon:
        issued = await issue_pin_challenge(user_form.telegram_id or "", phone_canon)
        if issued is not None:
            _, pin_len = issued
            context.user_data[USER_DATA_PIN_CONTEXT_KEY] = {"kind": "claim"}
            await update.message.reply_text(_pin_challenge_prompt_text(pin_len))
            return PIN_VERIFY_INPUT

    logger.info(
        'claim telegram attach without PIN: missing normalized phone sheet_label=%s row=%s',
        sheet_label,
        row_number,
    )
    return await _finalize_claim_attachment(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start: регистрация, роли админ/научрук или выбор сценария магистранта."""

    if update.message is None:
        return ConversationHandler.END

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

    if await _call_blocking(
        is_admin_telegram_id, cfg, user_form.telegram_id or ""
    ):
        await update.message.reply_text(
            f"Здравствуйте. Вы в списке администраторов (лист «{ADMINS_WORKSHEET_NAME}»). "
            "Справка: /help, панель: /admin."
        )
        return ConversationHandler.END

    if await _call_blocking(
        is_supervisor_telegram_id, cfg, user_form.telegram_id or ""
    ):
        await update.message.reply_text(
            f"Здравствуйте. Вы в списке научных руководителей (лист "
            f"«{SUPERVISORS_WORKSHEET_NAME}»). Справка: /help."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Здравствуйте. Вы ещё не в таблице «Регистрация» и не привязаны к спискам "
        "администраторов / научруков.\n\n"
        "Кто вы? Выберите кнопку ниже.",
        reply_markup=_start_role_keyboard_main(),
    )
    return ROLE_PICK


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ: те же цифры, что на листе «Dashboard» (D1), без открытия таблицы."""

    if update.message is None:
        return
    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
        )
        return

    cfg = _bot_config(context)
    try:
        worksheet = await _call_blocking(get_worksheet, cfg)
        rows = await _call_blocking(build_dashboard_rows, worksheet)
        text = format_dashboard_telegram_message(rows)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось сформировать /stats")
        await update.message.reply_text(
            "Не удалось получить сводку.\n\n"
            f"{_user_message_for_api_failure(exc, audience='admin')}"
        )
        return

    await update.message.reply_text(text, parse_mode="HTML")


async def admin_sync_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ: пересчитать лист «Dashboard» (D2) — вручную, если меняли таблицу вне бота."""

    if update.message is None:
        return
    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
        )
        return

    cfg = _bot_config(context)
    try:
        await _call_blocking(sync_registration_dashboard, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось обновить Dashboard (sync)")
        await update.message.reply_text(
            "Не удалось обновить лист «Dashboard».\n\n"
            f"{_user_message_for_api_failure(exc, audience='admin')}"
        )
        return

    await update.message.reply_text("Лист «Dashboard» в таблице обновлён.")


async def admin_sync_magistrants(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ: синхронизировать колонки листа магистрантов с «Регистрация»."""

    if update.message is None:
        return
    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
        )
        return

    cfg = _bot_config(context)
    if not (cfg.magistrants_worksheet_name or "").strip():
        await update.message.reply_text(
            "В конфигурации не задан MAGISTRANTS_WORKSHEET_NAME — синхронизация отключена."
        )
        return
    try:
        await _call_blocking(sync_magistrants_registration_status, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось обновить лист магистрантов (sync)")
        await update.message.reply_text(
            "Не удалось обновить лист магистрантов.\n\n"
            f"{_user_message_for_api_failure(exc, audience='admin')}"
        )
        return

    await update.message.reply_text(
        f"Лист «{cfg.magistrants_worksheet_name}» обновлён (телефоны +7, колонка «Регистрация»)."
    )


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
        "• Карточка — кнопка ниже или /project_card (PDF).\n"
        "• Сообщение магистранту — вторая кнопка или /student_message (напоминание в личку).\n"
        "• Групповое стандартное напоминание — третья кнопка или /student_message_bulk "
        "(список номеров строк).",
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
            f"{_user_message_for_api_failure(exc, audience='admin')}\n\n"
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


async def student_reminder_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Админ: напоминание одному магистранту (шаблон или свой текст в личку)."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    _clear_student_reminder(context)
    await update.message.reply_text(
        "Введите номер строки листа `Регистрация` или ФИО магистранта.\n\n"
        "Я подставлю `telegram_id` из строки и пришлю в личку выбранный текст.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return STUDENT_MSG_ASK_TARGET


async def student_reminder_receive_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Строка/ФИО для напоминания магистранту."""

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
        return STUDENT_MSG_ASK_TARGET

    assert row_number is not None
    fio_label = (fio_text_from_worksheet_row(worksheet, row_number) or "").strip()

    context.user_data[USER_DATA_STUDENT_REMINDER_ROW] = row_number
    context.user_data[USER_DATA_STUDENT_REMINDER_FIO] = fio_label
    await update.message.reply_text(
        f"Строка {row_number}"
        + (f" ({fio_label})" if fio_label else "")
        + ".\n\nВыберите шаблон сообщения:",
        reply_markup=_student_reminder_template_keyboard(),
    )
    return STUDENT_MSG_PICK_KIND


async def student_reminder_pick_template(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Callback: std / stdex / cust."""

    query = update.callback_query
    if query is None or query.message is None:
        return STUDENT_MSG_PICK_KIND
    await query.answer()

    if not _is_admin(update, context):
        return ConversationHandler.END

    raw = query.data or ""
    m = re.match(ADMSTU_CALLBACK_TEMPLATE_PATTERN, raw)
    if not m:
        return STUDENT_MSG_PICK_KIND
    tag = m.group(1)

    fio_label = context.user_data.get(USER_DATA_STUDENT_REMINDER_FIO, "")

    if tag == "std":
        draft = build_standard_reminder(recipient_fio=fio_label, extra_lines=None)
        context.user_data[USER_DATA_STUDENT_REMINDER_DRAFT] = draft
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(_student_reminder_preview_text(draft))
        await query.message.reply_text(
            "Отправить это сообщение в личку магистранту?",
            reply_markup=_student_reminder_confirm_keyboard(),
        )
        return STUDENT_MSG_CONFIRM

    await query.edit_message_reply_markup(reply_markup=None)

    if tag == "stdex":
        await query.message.reply_text(
            "Пришлите до трёх строк замечаний (каждая — с новой строки).\n"
            "Пустая отправка или «-» только — без блока замечаний."
        )
        return STUDENT_MSG_ASK_EXTRA

    if tag == "cust":
        await query.message.reply_text("Пришлите полный текст сообщения одним сообщением:")
        return STUDENT_MSG_ASK_CUSTOM

    return STUDENT_MSG_PICK_KIND


async def student_reminder_receive_extra(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Строки замечаний к стандартному шаблону."""

    msg = update.message
    if msg is None:
        return STUDENT_MSG_ASK_EXTRA

    raw = normalize_text(msg.text or "")
    lines: List[str] = []
    if raw and raw.strip() not in {"", "-"}:
        for ln in (msg.text or "").splitlines():
            t = ln.strip()
            if t and t != "-":
                lines.append(t)
    lines = lines[:3]

    fio_label = context.user_data.get(USER_DATA_STUDENT_REMINDER_FIO, "")
    draft = build_standard_reminder(recipient_fio=fio_label, extra_lines=lines)
    context.user_data[USER_DATA_STUDENT_REMINDER_DRAFT] = draft

    await msg.reply_text(_student_reminder_preview_text(draft))
    await msg.reply_text(
        "Отправить это сообщение в личку магистранту?",
        reply_markup=_student_reminder_confirm_keyboard(),
    )
    return STUDENT_MSG_CONFIRM


async def student_reminder_receive_custom(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Пользовательский полный текст."""

    msg = update.message
    if msg is None:
        return STUDENT_MSG_ASK_CUSTOM

    draft = (msg.text or "").strip()
    if not draft:
        await msg.reply_text("Текст пустой. Пришлите сообщение текстом или /cancel.")
        return STUDENT_MSG_ASK_CUSTOM

    context.user_data[USER_DATA_STUDENT_REMINDER_DRAFT] = draft

    await msg.reply_text(_student_reminder_preview_text(draft))
    await msg.reply_text(
        "Отправить это сообщение в личку магистранту?",
        reply_markup=_student_reminder_confirm_keyboard(),
    )
    return STUDENT_MSG_CONFIRM


async def student_reminder_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Отправка или отмена."""

    query = update.callback_query
    if query is None or query.message is None:
        return STUDENT_MSG_CONFIRM
    await query.answer()

    if not _is_admin(update, context):
        return ConversationHandler.END

    raw = query.data or ""
    m = re.match(ADMSTU_CALLBACK_CONFIRM_PATTERN, raw)
    if not m:
        return STUDENT_MSG_CONFIRM
    decision = m.group(1)

    if decision == "cancel":
        _clear_student_reminder(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Отправка отменена.\n\nСнова — /student_message или панель /admin.",
            reply_markup=_admin_keyboard(),
        )
        return ConversationHandler.END

    row_no = context.user_data.get(USER_DATA_STUDENT_REMINDER_ROW)
    draft_t = context.user_data.get(USER_DATA_STUDENT_REMINDER_DRAFT)
    await query.edit_message_reply_markup(reply_markup=None)

    if row_no is None or not isinstance(row_no, int) or draft_t is None or not str(draft_t).strip():
        await query.message.reply_text(
            "Внутреннее состояние сценария сброшено. Начните снова: /student_message."
        )
        _clear_student_reminder(context)
        return ConversationHandler.END

    cfg = _bot_config(context)
    ws = get_worksheet(cfg)
    tg_raw = get_telegram_id_at_row(ws, row_no).strip()

    try:
        chat_id_target = int(tg_raw) if tg_raw else 0
    except ValueError:
        chat_id_target = 0

    if not chat_id_target:
        await query.message.reply_text(
            f"В строке {row_no} пустой или некорректный telegram_id — не к кому отправить.",
            reply_markup=_admin_keyboard(),
        )
        _clear_student_reminder(context)
        return ConversationHandler.END

    ok, info = await _deliver_reminder_text_and_snapshot(
        context.bot,
        cfg,
        row_no=row_no,
        chat_id_target=chat_id_target,
        message_text=str(draft_t),
    )

    if ok:
        await query.message.reply_text(
            f"Сообщение отправлено (chat_id={chat_id_target}, строка {row_no}).{info}",
            reply_markup=_admin_keyboard(),
        )
    else:
        await query.message.reply_text(
            f"Не удалось доставить: {info}",
            reply_markup=_admin_keyboard(),
        )

    _clear_student_reminder(context)
    return ConversationHandler.END


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
    """Разбор строки вида ``5 7 12`` или ``5,12,15`` — только целые номера строк (≥ 2)."""

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


async def student_message_bulk_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Групповое стандартное напоминание по списку номеров строк «Регистрация»."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    _clear_student_reminder(context)
    await update.message.reply_text(
        "Перечислите номера строк листа «Регистрация» (начиная со 2-й), по которым нужно "
        "разослать стандартное напоминание — у каждого подставится ФИО из своей строки.\n\n"
        "Можно через пробел, запятую или с новой строки. Пример:\n"
        "5 7 9 10   или   12,15,18\n\n"
        f"Не больше {BULK_STUDENT_REMINDER_MAX_ROWS} строк за раз.\n/cancel — отмена.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return STUDENT_MSG_BULK_ASK_ROWS


async def student_reminder_bulk_receive_rows(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Парсит список строк, показывает предпросмотр и кнопки Отправить/Отмена."""

    if not _is_admin(update, context):
        await update.message.reply_text(
            f"Команда доступна только администраторам из листа `{ADMINS_WORKSHEET_NAME}`.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    msg = update.message
    if msg is None:
        return STUDENT_MSG_BULK_ASK_ROWS

    idx, err = _parse_bulk_student_row_numbers(msg.text or "")
    if err:
        await msg.reply_text(err)
        return STUDENT_MSG_BULK_ASK_ROWS

    assert idx is not None
    if len(idx) > BULK_STUDENT_REMINDER_MAX_ROWS:
        await msg.reply_text(
            f"Слишком много строк ({len(idx)}). Максимум {BULK_STUDENT_REMINDER_MAX_ROWS} за один раз."
        )
        return STUDENT_MSG_BULK_ASK_ROWS

    bad_rows = [n for n in idx if n < 2]
    if bad_rows:
        await msg.reply_text(
            "Номер строки данных должен быть не меньше 2 (строка 1 — заголовок): "
            + ", ".join(str(x) for x in bad_rows)
        )
        return STUDENT_MSG_BULK_ASK_ROWS

    cfg = _bot_config(context)
    ws = get_worksheet(cfg)
    entries: list[dict] = []
    empty_tg: list[int] = []
    empty_row: list[int] = []

    for row_no in idx:
        row_vals = load_row_values(ws, row_no)
        if not any(str(v).strip() for v in row_vals):
            empty_row.append(row_no)
            continue
        tg_raw = (get_telegram_id_at_row(ws, row_no) or "").strip()
        if not tg_raw:
            empty_tg.append(row_no)
            continue
        try:
            chat_id = int(tg_raw)
        except ValueError:
            empty_tg.append(row_no)
            continue
        if not chat_id:
            empty_tg.append(row_no)
            continue
        fio = (fio_text_from_worksheet_row(ws, row_no) or "").strip()
        entries.append({"row": row_no, "chat_id": chat_id, "fio": fio})

    if empty_row:
        await msg.reply_text(
            "Пустые или отсутствующие строки в таблице: "
            + ", ".join(str(x) for x in empty_row)
        )
        return STUDENT_MSG_BULK_ASK_ROWS
    if empty_tg:
        await msg.reply_text(
            "Нет заполненного telegram_id в строках: "
            + ", ".join(str(x) for x in empty_tg)
        )
        return STUDENT_MSG_BULK_ASK_ROWS
    if not entries:
        await msg.reply_text("Некого добавить в рассылку.")
        return STUDENT_MSG_BULK_ASK_ROWS

    seen_chat: dict[int, int] = {}
    dedup_rows: list[dict] = []
    dup_notes: list[str] = []
    for e in entries:
        cid = int(e["chat_id"])
        rw = int(e["row"])
        if cid in seen_chat:
            dup_notes.append(
                f"Строка {rw} — тот же chat_id {cid}, что и строка {seen_chat[cid]} "
                "(дубликат получателя пропускается)."
            )
            continue
        seen_chat[cid] = rw
        dedup_rows.append(e)
    entries = dedup_rows

    if not entries:
        await msg.reply_text(
            "После объединения дубликатов chat_id некого рассылать — укажите разных получателей."
        )
        return STUDENT_MSG_BULK_ASK_ROWS

    context.user_data[USER_DATA_STUDENT_BULK_ENTRIES] = entries

    sample_fio = entries[0]["fio"]
    sample_draft = build_standard_reminder(recipient_fio=sample_fio, extra_lines=None)
    lines = [
        f"Будет отправлено {len(entries)} сообщений (стандартный текст, у каждого своё ФИО):",
        "",
    ]
    if dup_notes:
        lines.extend(dup_notes)
        lines.append("")
    show = entries[:35]
    for e in show:
        fn = e["fio"] or "—"
        lines.append(f"• стр. {e['row']} — {fn} → chat_id {e['chat_id']}")
    if len(entries) > 35:
        lines.append(f"… и ещё {len(entries) - 35}.")
    lines.extend(
        [
            "",
            "Пример текста для первой строки в списке:",
            "════════════",
            sample_draft[:3200] + ("…" if len(sample_draft) > 3200 else ""),
            "════════════",
            "",
            "Отправить всем?",
        ]
    )
    body = "\n".join(lines)
    if len(body) > 4000:
        body = body[:3990] + "…"

    await msg.reply_text(
        body,
        reply_markup=_student_reminder_bulk_confirm_keyboard(),
    )
    return STUDENT_MSG_BULK_CONFIRM


async def student_reminder_bulk_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Отправка или отмена группового стандартного напоминания."""

    query = update.callback_query
    if query is None or query.message is None:
        return STUDENT_MSG_BULK_CONFIRM
    await query.answer()

    if not _is_admin(update, context):
        return ConversationHandler.END

    raw = query.data or ""
    m = re.match(ADMSTUB_CALLBACK_CONFIRM_PATTERN, raw)
    if not m:
        return STUDENT_MSG_BULK_CONFIRM
    decision = m.group(1)

    if decision == "cancel":
        _clear_student_reminder(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Рассылка отменена.\n\nСнова — /student_message_bulk или панель /admin.",
            reply_markup=_admin_keyboard(),
        )
        return ConversationHandler.END

    entries = context.user_data.get(USER_DATA_STUDENT_BULK_ENTRIES)
    if not isinstance(entries, list) or not entries:
        await query.message.reply_text(
            "Состояние сценария устарело. Начните снова: /student_message_bulk."
        )
        _clear_student_reminder(context)
        return ConversationHandler.END

    await query.edit_message_reply_markup(reply_markup=None)

    cfg = _bot_config(context)
    summary_lines: list[str] = []
    delay = 0.06

    for i, ent in enumerate(entries):
        if i:
            await asyncio.sleep(delay)
        row_no = int(ent["row"])
        chat_id_target = int(ent["chat_id"])
        fio = str(ent.get("fio") or "")
        draft = build_standard_reminder(recipient_fio=fio, extra_lines=None)
        ok, info = await _deliver_reminder_text_and_snapshot(
            context.bot,
            cfg,
            row_no=row_no,
            chat_id_target=chat_id_target,
            message_text=draft,
        )
        summary_lines.append(_bulk_delivery_summary_line(row_no, ok, info))

    report = "Групповая рассылка завершена.\n\n" + "\n".join(summary_lines)
    if len(report) > 4000:
        report = report[:3990] + "…"

    await query.message.reply_text(report, reply_markup=_admin_keyboard())
    _clear_student_reminder(context)
    return ConversationHandler.END


async def spravka_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Меню выбора: короткий Telegram-текст справки или PDF для комиссии."""

    if update.message is None:
        return ConversationHandler.END
    text_lines = [
        "Выберите формат справки по проекту:\n",
        "• Кратко — для магистранта (как после /recheck: этапы, ссылки, диссертация).",
        "• Полный текст — для комиссии прямо в чат (без PDF; нужен номер строки или ФИО).",
        "• PDF — печатная карточка (строка или ФИО; только у админов).",
    ]
    if not _is_admin(update, context):
        text_lines.append("")
        text_lines.append(
            "Полный текст и PDF по чужой строке — только у администраторов. "
            "Свой вариант «как /recheck» — первая кнопка."
        )
    else:
        text_lines.append("")
        text_lines.append(
            "Администратор: варианты 2—3 с указанием строки; также можно в чат вложить "
            "снимок .json с Drive — бот пришлёт оба сформированных вида (магистрант и комиссия)."
        )
    rows = [
        [
            InlineKeyboardButton(
                "Кратко (магистрант)",
                callback_data=SPRAVKA_CALLBACK_TELEGRAM,
            )
        ],
    ]
    if _is_admin(update, context):
        rows.append(
            [
                InlineKeyboardButton(
                    "Полный текст (комиссия, чат)",
                    callback_data=SPRAVKA_CALLBACK_COMMISSION,
                )
            ],
        )
    rows.append(
        [
            InlineKeyboardButton(
                "PDF для комиссии",
                callback_data=SPRAVKA_CALLBACK_PDF,
            )
        ],
    )
    keyboard = InlineKeyboardMarkup(rows)
    await update.message.reply_text("\n".join(text_lines), reply_markup=keyboard)
    return SPRAVKA_MENU


async def spravka_choose(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Callback с inline-кнопок меню /spravka."""

    query = update.callback_query
    if query is None or not query.data:
        return SPRAVKA_MENU
    if not _is_private_chat(update):
        try:
            await query.answer("Только в личном чате с ботом.", show_alert=True)
        except BadRequest:
            pass
        return ConversationHandler.END
    data = query.data
    is_admin = _is_admin(update, context)

    if data == SPRAVKA_CALLBACK_PDF and not is_admin:
        await query.answer("PDF по строке/ФИО — только администраторам.", show_alert=True)
        return SPRAVKA_MENU

    if data == SPRAVKA_CALLBACK_COMMISSION and not is_admin:
        await query.answer(
            "Полный текст по строке/ФИО — только администраторам.",
            show_alert=True,
        )
        return SPRAVKA_MENU

    await query.answer()

    if data == SPRAVKA_CALLBACK_TELEGRAM and not is_admin:
        try:
            await query.edit_message_text(
                "Короткий текст: запускаю полную проверку вашей строки (до минуты)…",
                reply_markup=None,
            )
        except BadRequest:
            pass
        await _do_recheck(
            update, context, only_if_changed=False, skip_status_message=True
        )
        return ConversationHandler.END

    if data == SPRAVKA_CALLBACK_TELEGRAM and is_admin:
        context.user_data[USER_DATA_SPRAVKA_MODE] = "telegram"
        try:
            await query.edit_message_text(
                "Короткий текст. Введите номер строки (≥ 2) или ФИО магистранта.",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return SPRAVKA_ASK_TARGET

    if data == SPRAVKA_CALLBACK_PDF and is_admin:
        context.user_data[USER_DATA_SPRAVKA_MODE] = "pdf"
        try:
            await query.edit_message_text(
                "PDF для комиссии. Введите номер строки (≥ 2) или ФИО магистранта.",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return SPRAVKA_ASK_TARGET

    if data == SPRAVKA_CALLBACK_COMMISSION and is_admin:
        context.user_data[USER_DATA_SPRAVKA_MODE] = "commission"
        try:
            await query.edit_message_text(
                "Полный текст для комиссии (в чат). "
                "Введите номер строки (≥ 2) или ФИО магистранта.",
                reply_markup=None,
            )
        except BadRequest:
            pass
        return SPRAVKA_ASK_TARGET

    return SPRAVKA_MENU


async def spravka_receive_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Админ: строка/ФИО для режима, выбранного в /spravka (Telegram- или PDF-вид)."""

    if update.message is None or not _is_admin(update, context):
        if update.message is not None:
            await update.message.reply_text(
                f"Сценарий /spravka с указанием строки — только для администраторов "
                f"(лист «{ADMINS_WORKSHEET_NAME}»)."
            )
        return ConversationHandler.END

    mode = context.user_data.pop(USER_DATA_SPRAVKA_MODE, None)
    if mode not in ("telegram", "pdf", "commission"):
        if update.message is not None:
            await update.message.reply_text(
                "Сессия /spravka устарела. Начните снова: /spravka"
            )
        return ConversationHandler.END

    cfg = _bot_config(context)
    worksheet = get_worksheet(cfg)
    target = update.message.text or ""
    row_number, error_message = _resolve_project_card_target_row(worksheet, target)
    if error_message:
        await update.message.reply_text(error_message)
        return SPRAVKA_ASK_TARGET

    assert row_number is not None
    if mode == "pdf":
        await update.message.reply_text(
            f"Формирую PDF для строки {row_number} (до минуты)…"
        )
        try:
            result = generate_project_card_pdf(config=cfg, row_number=row_number)
        except Exception as exc:  # noqa: BLE001
            logger.exception("spravka PDF: не удалось для строки %s", row_number)
            await update.message.reply_text(
                "Не удалось сформировать PDF.\n\n"
                f"{_user_message_for_api_failure(exc, audience='admin')}\n\n"
                "Подробности — в логе бота.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return ConversationHandler.END

        await update.message.reply_document(
            document=InputFile(io.BytesIO(result.pdf_bytes), filename=result.pdf_name),
            caption=(
                "PDF (из /spravka).\n\n"
                f"Строка: {result.row_number}\n"
                f"Файл: {result.pdf_name}"
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    if mode == "telegram":
        await update.message.reply_text(
            f"Проверяю строку {row_number} (это может занять до минуты)…"
        )
    elif mode == "commission":
        await update.message.reply_text(
            f"Проверяю строку {row_number} — готовлю полный отчёт для комиссии в чат "
            f"(это может занять до минуты)…"
        )
    locator = RowLocator(row_number=row_number)
    try:
        report = await _call_blocking(
            run_row_check,
            cfg,
            locator,
            skip_http=False,
            apply=True,
            only_if_changed=False,
            history_source="bot",
        )
    except ValueError as exc:
        await _try_mark_recheck_error(cfg, row_number)
        await _reply_spravka_to_message(
            update.message, f"Ошибка: {exc}"
        )
        return ConversationHandler.END
    except Exception as exc:  # noqa: BLE001
        logger.exception("spravka telegram/commission: сбой row_check для строки %s", row_number)
        await _try_mark_recheck_error(cfg, row_number)
        await _reply_spravka_to_message(
            update.message,
            "Не удалось выполнить проверку.\n\n"
            f"{_user_message_for_api_failure(exc, audience='admin')}",
        )
        return ConversationHandler.END

    view = "commission" if mode == "commission" else "student"
    spravka_text = await _call_blocking(
        _format_spravka_text_from_recheck,
        cfg,
        report,
        row_number,
        "spravka",
        view=view,
        as_html=True,
    )
    await _reply_spravka_to_message(
        update.message,
        spravka_text,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def on_project_snapshot_json_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Админ: вложенный .json (снимок с Google Drive) → два человекочитаемых HTML-сообщения."""

    if update.message is None or update.message.document is None:
        return
    if not _is_admin(update, context):
        return
    doc = update.message.document
    fname = (doc.file_name or "").lower()
    if not fname.endswith(".json"):
        return
    if doc.file_size and doc.file_size > _SNAPSHOT_JSON_MAX_BYTES:
        await update.message.reply_text(
            f"JSON слишком большой (макс. {_SNAPSHOT_JSON_MAX_BYTES // 1_000_000} МБ)."
        )
        return
    try:
        tfile = await context.bot.get_file(doc.file_id)
        raw: bytes = await tfile.download_as_bytearray()  # type: ignore[assignment]
        text = raw.decode("utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        logger.exception("admin json snapshot: чтение вложения")
        await update.message.reply_text(
            f"Не удалось прочитать файл: {_format_user_visible_exc(exc)}"
        )
        return
    try:
        snap = await _call_blocking(project_snapshot_from_json_str, text)
    except (ValueError, TypeError) as exc:
        await update.message.reply_text(
            f"Файл не похож на project snapshot: {_format_user_visible_exc(exc)}"
        )
        return
    student = render_spravka_telegram_html(snap, applied=True)
    commission = render_commission_telegram_html(snap)
    await _reply_spravka_to_message(
        update.message,
        "📋 <b>Как для магистранта</b> (файл JSON)\n\n" + student,
        parse_mode=ParseMode.HTML,
    )
    await _reply_spravka_to_message(
        update.message,
        "📄 <b>Для комиссии</b> (файл JSON)\n\n" + commission,
        parse_mode=ParseMode.HTML,
    )


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
    fio = normalize_fio_user_input(raw)
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

    phone_canon = normalize_phone_ru_kz((candidate.phone or "").strip())
    if phone_canon:
        issued = await issue_pin_challenge(user_form.telegram_id or "", phone_canon)
        if issued is not None:
            _, pin_len = issued
            context.user_data[USER_DATA_PIN_CONTEXT_KEY] = {"kind": "bind"}
            await update.message.reply_text(_pin_challenge_prompt_text(pin_len))
            return PIN_VERIFY_INPUT

    logger.info(
        "bind telegram attach without PIN: missing normalized phone row=%s",
        row_number,
    )
    return await _finalize_bind_attachment(update, context, row_number=int(row_number))


async def receive_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Сохраняет ответ пользователя в текущее активное поле."""

    msg = update.effective_message
    field_key = _current_field(context)
    if not field_key:
        if msg:
            await msg.reply_text(
                "Не вижу активного вопроса. Нажмите /start, чтобы начать заново."
            )
        return ConversationHandler.END

    if msg is None or msg.text is None:
        if msg:
            await msg.reply_text(
                "Нужен текстовый ответ. Если открыта клавиатура с кнопками — "
                "используйте личный чат с ботом."
            )
        return ASK_FIELD

    user_form = _get_user_form(context)
    raw = msg.text or ""
    if _is_skip_text(raw):
        value = getattr(user_form, field_key)
    else:
        value = normalize_text(raw)
        setattr(user_form, field_key, value)

    if field_key == "report_url":
        if _is_skip_text(raw) and getattr(user_form, field_key):
            pass
        elif value:
            # Запоминаем, не ждали ли мы исправления папки — чтобы после
            # успешной валидации послать магистранту короткое «принято».
            previous_was_folder_warning = (
                user_form.report_url_valid == REPORT_URL_FOLDER_NOT_DOCUMENT_MESSAGE
            )
            # Сначала формальная проверка «папка vs документ» (без сети).
            # Если магистрант прислал ссылку на папку Drive, явно отвечаем
            # сообщением об ошибке и кладём текст в «Проверка ссылки», но
            # из ASK_FIELD не выходим: следующее сообщение пользователя в
            # этом же чате будет принято как новый URL для report_url
            # (правильная ссылка на документ), без перевыдачи всего prompt.
            target_msg = check_report_url_target_kind(value)
            if target_msg:
                user_form.report_url_valid = target_msg
                user_form.report_url_accessible = ""
                _refresh_status(user_form)
                _record_action(user_form, "report_url_folder_retry")
                await msg.reply_text(
                    target_msg
                    + "\n\nПришлите, пожалуйста, правильную ссылку на сам "
                    "документ промежуточного отчёта (Google Docs, DOCX или "
                    "PDF в Drive) прямо в ответ на это сообщение — я её проверю "
                    "и продолжу регистрацию.\n"
                    f"Чтобы пропустить этот шаг, отправьте {SKIP_TOKEN} или /skip."
                )
                return ASK_FIELD
            valid, accessible = check_report_url(value)
            user_form.report_url_valid = valid
            user_form.report_url_accessible = accessible
            if previous_was_folder_warning and valid == "yes":
                await msg.reply_text(
                    "Ссылка принята. Продолжаю регистрацию."
                )
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
    msg = update.effective_message
    if not field_key:
        if msg:
            await msg.reply_text(
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

    msg = update.effective_message
    if msg is None or msg.text is None:
        if msg:
            await msg.reply_text("Нужен текстовый ответ: да или нет.")
        return ASK_CONFIRM

    answer = (msg.text or "").strip().lower()
    user_form = _get_user_form(context)

    if answer not in {"да", "нет", "yes", "no"}:
        await msg.reply_text("Введите: да или нет")
        return ASK_CONFIRM

    if answer in {"нет", "no"}:
        _record_action(user_form, "requested_correction")
        await msg.reply_text(
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

    phone_canon = normalize_phone_ru_kz((user_form.phone or "").strip())
    if phone_canon:
        issued = await issue_pin_challenge(user_form.telegram_id or "", phone_canon)
        if issued is not None:
            _, pin_len = issued
            context.user_data[USER_DATA_PIN_CONTEXT_KEY] = {"kind": "register_save"}
            context.user_data[USER_DATA_PIN_REGISTER_EXTRA_KEY] = dict(extra_values)
            await msg.reply_text(_pin_challenge_prompt_text(pin_len))
            return PIN_VERIFY_INPUT

    logger.info(
        "registration save without PIN: no normalized phone telegram_id=%s",
        user_form.telegram_id or "?",
    )
    return await _finalize_registration_confirm(update, context, extra_values=extra_values)


RECHECK_QUICK_TOKENS = {"quick", "only-if-changed", "only_if_changed", "fast", "diff"}
"""Ключевые слова, после которых ``/recheck`` работает как ``--only-if-changed``.

По умолчанию ``/recheck`` запускает полный прогон (handoff §8 — diff_detection
режим «full by default»), но магистрант может написать ``/recheck quick``,
чтобы получить ответ «без изменений» без повторной нагрузки на Drive."""

RECHECK_BUTTON_LABEL = "🔄 Перепроверить"
RECHECK_CALLBACK_DATA = "recheck:full"
RECHECK_CALLBACK_PATTERN = r"^recheck:full(?::\d+)?$"
"""Шаблон callback: ``recheck:full`` или ``recheck:full:<номер строки>`` для админской
перепроверки без повторного ввода цели (кнопка под отчётом по той же строке)."""


def _parse_recheck_callback_row(callback_data: str | None) -> int | None:
    if not callback_data:
        return None
    parts = callback_data.strip().split(":")
    if len(parts) != 3 or parts[0] != "recheck" or parts[1] != "full":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _parse_recheck_command_parts(message_text: str) -> tuple[bool, str | None]:
    """Разбор текста команды ``/recheck``.

    Возвращает ``(only_if_changed, target)``, где ``target`` — номер строки или ФИО
    (всё, что осталось после удаления токенов ``quick`` / ``only-if-changed`` и т.д.).
    Только администраторы могут передать непустой ``target`` (см. ``recheck``).
    """

    parts = (message_text or "").strip().split()
    if len(parts) < 2:
        return False, None
    body = parts[1:]
    only_if_changed = False
    collected: list[str] = []
    for token in body:
        if token.strip().lower() in RECHECK_QUICK_TOKENS:
            only_if_changed = True
        else:
            collected.append(token)
    target = " ".join(collected).strip() or None
    return only_if_changed, target


def _clear_admin_recheck_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(USER_DATA_ADMIN_RECHECK_PENDING, None)
    context.user_data.pop(USER_DATA_ADMIN_RECHECK_ONLY_IF_CHANGED, None)


async def _prompt_admin_recheck_need_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    only_if_changed: bool,
) -> None:
    """Админ без строки в «Регистрация»: ждём цель в той же команде или следующим сообщением."""

    context.user_data[USER_DATA_ADMIN_RECHECK_PENDING] = True
    context.user_data[USER_DATA_ADMIN_RECHECK_ONLY_IF_CHANGED] = only_if_changed
    msg = (
        "Отправьте номер строки листа «Регистрация» или ФИО следующим сообщением "
        "(или одной командой: /recheck N или /recheck ФИО)."
    )
    if only_if_changed:
        msg += (
            "\n\nБыстрый режим уже включён: запись после проверки — только если входы "
            "менялись с прошлого прогона; или одной строкой: /recheck quick N."
        )
    if update.message is not None:
        await update.message.reply_text(msg)
    else:
        await _send_recheck_reply(update, msg)


async def admin_recheck_pending_receive(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Второй шаг админского /recheck: текст с номером строки или ФИО (не команда)."""

    if update.message is None or update.effective_user is None:
        return
    if not _is_private_chat(update):
        return
    if not context.user_data.get(USER_DATA_ADMIN_RECHECK_PENDING):
        return
    if not _is_admin(update, context):
        _clear_admin_recheck_pending(context)
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    cfg = _bot_config(context)
    worksheet = await _call_blocking(get_worksheet, cfg)
    row_number, err_msg = await _call_blocking(
        _resolve_project_card_target_row, worksheet, text
    )
    if err_msg:
        await update.message.reply_text(err_msg)
        return

    context.user_data.pop(USER_DATA_ADMIN_RECHECK_PENDING, None)
    only_if_changed = bool(
        context.user_data.pop(USER_DATA_ADMIN_RECHECK_ONLY_IF_CHANGED, False)
    )

    await _do_recheck(
        update,
        context,
        only_if_changed=only_if_changed,
        row_number_override=row_number,
    )


def build_recheck_keyboard(row_number: int | None = None) -> InlineKeyboardMarkup:
    """Inline-кнопка под итоговым отчётом и финалом регистрации.

    Inline (а не Reply) — чтобы кнопка была привязана к конкретному сообщению
    и пропадала после нажатия (см. ``recheck_button``). Это снижает риск
    случайного двойного запуска тяжёлого пайплайна.

    Если передан ``row_number``, в callback кладётся ``recheck:full:<row>``, чтобы
    администратор (без своей строки в «Регистрация») мог перепроверить ту же цель
    кнопкой, не вводя номер снова. Магистрант всё равно ищется по ``telegram_id``.
    """

    payload = (
        RECHECK_CALLBACK_DATA
        if row_number is None
        else f"{RECHECK_CALLBACK_DATA}:{row_number}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(RECHECK_BUTTON_LABEL, callback_data=payload)]]
    )


async def _try_mark_recheck_error(cfg: BotConfig, row_number: int) -> None:
    """После сбоя ``/recheck`` помечает строку ``ERROR`` (п.12 ТЗ), если колонка есть."""

    try:
        worksheet = await _call_blocking(get_worksheet, cfg)
        await _call_blocking(
            set_row_fill_status,
            worksheet,
            row_number,
            FillStatus.ERROR.value,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Не удалось записать fill_status=ERROR для строки %s", row_number
        )


async def _send_recheck_reply(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """Отправляет ответ магистранту вне зависимости от источника обновления.

    Для ``/recheck`` источник — обычное сообщение (``update.message``).
    Для нажатия кнопки — ``update.callback_query.message`` (само inline-сообщение,
    под которым висит кнопка). В обоих случаях reply уйдёт в нужный чат.
    Длинный текст бьётся на части; ``reply_markup`` (кнопка «Перепроверить``)
    цепляется к **последней** части. Если ни ``message``, ни
    ``callback_query.message`` нет — молча выходим.
    """

    parts = _iter_telegram_spravka_chunks(text)
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        km = reply_markup if is_last else None
        if update.message is not None:
            await update.message.reply_text(
                part, parse_mode=parse_mode, reply_markup=km
            )
        elif update.callback_query is not None and update.callback_query.message is not None:
            await update.callback_query.message.reply_text(
                part, parse_mode=parse_mode, reply_markup=km
            )
        else:
            return


def _recheck_reply_markup_after_check(
    row_number: int, *, attach_kb: bool
) -> InlineKeyboardMarkup | None:
    """Inline «Перепроверить» только если callback сможет подставить строку (см. ``recheck_button``)."""

    return build_recheck_keyboard(row_number) if attach_kb else None


async def _do_recheck(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    only_if_changed: bool,
    skip_status_message: bool = False,
    row_number_override: int | None = None,
    attach_recheck_keyboard: bool = True,
    history_source: str = "bot",
) -> None:
    """Общее тело /recheck: вызывается из ``recheck`` и ``recheck_button``.

    Поиск строки в листе ``Регистрация`` по умолчанию — строго по ``telegram_id``
    у вызывающего (ФИО как fallback не используем, чтобы случайный однофамилец не мог
    инициировать перезапись чужой строки). Администратор может передать номер строки
    или ФИО прямо в команде (см. ``recheck``): тогда используется ``row_number_override``.
    Научный руководитель не передаёт номер вручную: строка подопечного задаётся через
    ``/status ФИО`` (лист «Магистранты» + совпадение научрука).

    При ``attach_recheck_keyboard=False`` кнопку не показываем (ответ научруку: без прав
    администратора callback не передаёт номер строки в ``recheck_button``).

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
    if row_number_override is not None:
        row_number = row_number_override
    else:
        row_number = await _call_blocking(find_row_by_telegram_id, worksheet, telegram_id)
    if row_number is None:
        if _is_admin(update, context):
            await _prompt_admin_recheck_need_target(
                update, context, only_if_changed=only_if_changed
            )
        else:
            await _send_recheck_reply(
                update,
                "Не нашёл вашу строку в листе «Регистрация».\n\n"
                "Сначала пройдите регистрацию: /start",
            )
        return

    if not skip_status_message:
        mode_hint = (
            "быстрый режим (только если входы поменялись)"
            if only_if_changed
            else "полная проверка"
        )
        await _send_recheck_reply(
            update,
            f"Запускаю повторную проверку строки {row_number} ({mode_hint}).\n"
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
            history_source=history_source,
        )
    except ValueError as exc:
        await _try_mark_recheck_error(cfg, row_number)
        await _send_recheck_reply(
            update,
            f"Ошибка: {exc}",
            reply_markup=_recheck_reply_markup_after_check(
                row_number, attach_kb=attach_recheck_keyboard
            ),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Не удалось выполнить /recheck для telegram_id=%s row=%s",
            telegram_id,
            row_number,
        )
        await _try_mark_recheck_error(cfg, row_number)
        await _send_recheck_reply(
            update,
            "Не удалось выполнить повторную проверку.\n\n"
            f"{_user_message_for_api_failure(exc, audience='student')}",
            reply_markup=_recheck_reply_markup_after_check(
                row_number, attach_kb=attach_recheck_keyboard
            ),
        )
        return

    spravka_text = await _call_blocking(
        _format_spravka_text_from_recheck, cfg, report, row_number, "recheck"
    )
    await _send_recheck_reply(
        update,
        spravka_text,
        reply_markup=_recheck_reply_markup_after_check(
            row_number, attach_kb=attach_recheck_keyboard
        ),
        parse_mode=ParseMode.HTML,
    )


async def recheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда ``/recheck`` — повторная проверка строки «Регистрация».

    Магистрант: прогон своей строки (по ``telegram_id`` в таблице).

    Администратор без строки в таблице: укажите цель в команде —
    ``/recheck N`` или ``/recheck ФИО`` (и необязательно ``quick`` для
    ``only_if_changed``).

    По умолчанию выполняется полный прогон с ``apply=True``: lay результаты
    в J:R и пишет одну запись в «Историю проверок» с ``source="bot"``.
    ``/recheck quick`` у магистранта включает ``only_if_changed=True``.
    """

    if update.message is None or update.effective_user is None:
        return

    only_if_changed, admin_target = _parse_recheck_command_parts(
        update.message.text or ""
    )
    if admin_target and not _is_admin(update, context):
        await update.message.reply_text(
            "Указать в команде номер строки или ФИО могут только администраторы.\n\n"
            "Чтобы перепроверить свою анкету, отправьте /recheck без аргументов "
            "(или /recheck quick — быстрый режим, если данные в таблице не менялись)."
        )
        return

    cfg = _bot_config(context)
    telegram_id = str(update.effective_user.id)
    worksheet = await _call_blocking(get_worksheet, cfg)

    if admin_target:
        _clear_admin_recheck_pending(context)
        row_number_override, err_msg = await _call_blocking(
            _resolve_project_card_target_row, worksheet, admin_target
        )
        if err_msg:
            await update.message.reply_text(err_msg)
            return
    else:
        row_number_override = None

    if not _is_admin(update, context):
        _clear_admin_recheck_pending(context)

    row_by_tg = await _call_blocking(find_row_by_telegram_id, worksheet, telegram_id)

    if row_number_override is not None:
        _clear_admin_recheck_pending(context)
        await _do_recheck(
            update,
            context,
            only_if_changed=only_if_changed,
            row_number_override=row_number_override,
        )
        return

    if row_by_tg is not None:
        _clear_admin_recheck_pending(context)
        await _do_recheck(
            update,
            context,
            only_if_changed=only_if_changed,
            row_number_override=None,
        )
        return

    if _is_admin(update, context):
        await _prompt_admin_recheck_need_target(
            update, context, only_if_changed=only_if_changed
        )
        return

    await _do_recheck(
        update,
        context,
        only_if_changed=only_if_changed,
        row_number_override=None,
    )


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
    if not _is_private_chat(update):
        try:
            await query.answer("Только в личном чате с ботом.", show_alert=True)
        except BadRequest:
            pass
        return

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        # Сообщение слишком старое или нет прав на редактирование — не критично.
        pass

    row_cb = _parse_recheck_callback_row(query.data)
    row_override = (
        row_cb if (row_cb is not None and _is_admin(update, context)) else None
    )

    await _do_recheck(
        update, context, only_if_changed=False, row_number_override=row_override
    )


_TELEGRAM_TEXT_SOFT_LIMIT = 3900


def _split_text_chunks(text: str, limit: int = _TELEGRAM_TEXT_SOFT_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for line in lines:
        line_len = len(line) + 1
        if buf and size + line_len > limit:
            chunks.append("\n".join(buf))
            buf = [line]
            size = line_len
        else:
            buf.append(line)
            size += line_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _fio_query_matches_row_fio(fio_query_norm: str, row_fio_norm: str) -> bool:
    if not fio_query_norm or not row_fio_norm:
        return False
    if fio_query_norm in row_fio_norm or row_fio_norm in fio_query_norm:
        return True
    q_parts = fio_query_norm.split()
    r_parts = row_fio_norm.split()
    if not q_parts:
        return False
    for i, qp in enumerate(q_parts):
        if i >= len(r_parts):
            return False
        rp = r_parts[i]
        if not (rp.startswith(qp) or qp.startswith(rp) or qp in rp or rp in qp):
            return False
    return True


async def register_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Первичная регистрация магистранта (анкета с нуля)."""

    if update.message is None:
        return ConversationHandler.END
    cfg = _bot_config(context)
    user_form = _get_user_form(context)
    _set_telegram_identity(user_form, update)
    worksheet = await _call_blocking(get_worksheet, cfg)
    existing_row = await _call_blocking(
        find_row_by_telegram_id, worksheet, user_form.telegram_id
    )
    if existing_row:
        await update.message.reply_text("Вы уже есть в таблице. Продолжим анкету.")
        return await _resume_registration_from_row(
            update,
            context,
            row_number=existing_row,
            action="register_returning",
        )
    if await _call_blocking(is_admin_telegram_id, cfg, user_form.telegram_id or ""):
        await update.message.reply_text(
            "Команда /register для магистрантов. Администраторам: /start, /help."
        )
        return ConversationHandler.END
    if await _call_blocking(is_supervisor_telegram_id, cfg, user_form.telegram_id or ""):
        await update.message.reply_text(
            "Команда /register — для магистранта. Научруку: /help."
        )
        return ConversationHandler.END
    return await _start_new_registration(update, context)


async def student_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Краткий статус по строке «Регистрация» для текущего telegram_id."""

    if update.message is None:
        return
    cfg = _bot_config(context)
    tg = str(update.effective_user.id)
    worksheet = await _call_blocking(get_worksheet, cfg)
    row = await _call_blocking(find_row_by_telegram_id, worksheet, tg)
    if not row:
        await update.message.reply_text(
            "Вашего telegram_id нет в листе «Регистрация». Нажмите /start или /register."
        )
        return
    user = await _call_blocking(load_user, worksheet, row)
    lines = [
        f"Строка: {row}",
        f"ФИО: {user.fio or '—'}",
        f"Телефон: {user.phone or '—'}",
        f"Статус заполнения: {user.fill_status or '—'}",
        f"Научный руководитель: {user.supervisor or '—'}",
        f"Проверка ссылки: {user.report_url_valid or '—'}",
        f"Доступ открыт: {user.report_url_accessible or '—'}",
    ]
    await update.message.reply_text("\n".join(lines))


async def _supervisor_student_status_by_fio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fio_query_raw: str
) -> None:
    """Научрук: /status ФИО — полная проверка строки подопечного (как админ /recheck ФИО)."""

    assert update.message is not None
    cfg = _bot_config(context)
    tg = str(update.effective_user.id)
    title = (cfg.magistrants_worksheet_name or "").strip()
    if not title:
        await update.message.reply_text(
            "Мастер-лист магистрантов не настроен (MAGISTRANTS_WORKSHEET_NAME)."
        )
        return
    sup_fio = await _call_blocking(get_supervisor_fio_for_telegram_id, cfg, tg)
    if not sup_fio.strip():
        await update.message.reply_text(
            f"Не удалось найти ваше ФИО в листе «{SUPERVISORS_WORKSHEET_NAME}»."
        )
        return
    spreadsheet = await _call_blocking(get_spreadsheet, cfg)
    mag_ws = await _call_blocking(get_optional_worksheet, spreadsheet, title)
    if mag_ws is None:
        await update.message.reply_text(f"Лист «{title}» не найден.")
        return
    header = await _call_blocking(mag_ws.row_values, 1)
    colmap = magistrants_sheet_column_indices(header)
    if colmap is None or "supervisor" not in colmap:
        await update.message.reply_text(
            "В листе магистрантов нет колонки научного руководителя."
        )
        return
    reg_ws = await _call_blocking(get_worksheet, cfg)
    reg_map = await _call_blocking(registration_students_by_fio_phone, reg_ws)
    all_rows = await _call_blocking(mag_ws.get_all_values)
    needle = normalize_fio(fio_query_raw)
    if not needle:
        await update.message.reply_text("Укажите непустое ФИО.")
        return
    fio_i = colmap["fio"]
    phone_i = colmap["phone"]
    sup_i = colmap["supervisor"]
    matches: list[tuple[str, str, UserForm | None]] = []
    for row in all_rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        w = max(len(header), len(row), fio_i + 1, phone_i + 1, sup_i + 1)
        padded = list(row) + [""] * (w - len(row))
        cell_sup = str(padded[sup_i] or "")
        if not supervisor_name_matches(sup_fio, cell_sup):
            continue
        st_fio = str(padded[fio_i] or "").strip()
        fk = normalize_fio(st_fio)
        if not fk or not _fio_query_matches_row_fio(needle, fk):
            continue
        pk = normalize_phone_ru_kz(str(padded[phone_i] or ""))
        key = (fk, pk) if pk else None
        usr: UserForm | None = reg_map.get(key) if key else None
        matches.append((st_fio, str(padded[phone_i] or "").strip(), usr))

    if not matches:
        await update.message.reply_text(
            "Под вашим руководством не найден магистрант с таким ФИО (лист «Магистранты»)."
        )
        return
    if len(matches) > 1:
        preview = "\n".join(f"• {m[0]}" for m in matches[:15])
        more = f"\n… и ещё {len(matches) - 15}" if len(matches) > 15 else ""
        await update.message.reply_text(
            "Несколько совпадений — уточните ФИО:\n" + preview + more
        )
        return
    st_fio, phone_raw, usr = matches[0]
    if usr is None:
        await update.message.reply_text(
            f"{st_fio}\nТелефон: {phone_raw or '—'}\n\nВ боте не зарегистрирован (нет tg_id+ФИО+тел на «Регистрация»)."
        )
        return
    tg_st = (usr.telegram_id or "").strip()
    if not tg_st:
        await update.message.reply_text(
            f"{st_fio}\nВ строке «Регистрация» не заполнен telegram_id — проверку не запускаю."
        )
        return
    row_student = await _call_blocking(find_row_by_telegram_id, reg_ws, tg_st)
    if row_student is None:
        await update.message.reply_text(
            f"{st_fio}\nНе нашёл строку «Регистрация» по telegram_id магистранта."
        )
        return
    await _do_recheck(
        update,
        context,
        only_if_changed=False,
        row_number_override=row_student,
        attach_recheck_keyboard=False,
        history_source="supervisor_status",
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Научрук: /status ФИО — проверка строки подопечного; магистрант: своя строка (кратко)."""

    if update.message is None:
        return
    cfg = _bot_config(context)
    tg = str(update.effective_user.id)
    args = context.args or []
    if await _call_blocking(is_supervisor_telegram_id, cfg, tg):
        if not args:
            await update.message.reply_text(
                "Укажите ФИО магистранта с листа «Магистранты», например:\n/status Иванов Иван"
            )
            return
        await _supervisor_student_status_by_fio(
            update, context, " ".join(args)
        )
        return
    await student_status_command(update, context)


async def supervisor_unregistered_list_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.message is None:
        return
    cfg = _bot_config(context)
    args = context.args or []

    if _is_admin(update, context):
        if args:
            fio = " ".join(args).strip()
            chunks, err = await _call_blocking(
                supervisor_unregistered_report,
                cfg,
                "",
                supervisor_fio_override=fio,
            )
            if err:
                await update.message.reply_text(err)
                return
            for i, chunk in enumerate(chunks):
                prefix = (
                    f"Превью /unreg для научрука «{fio}»"
                    + (f" (часть {i + 1}/{len(chunks)})" if len(chunks) > 1 else "")
                    + ":\n\n"
                )
                await update.message.reply_text(prefix + chunk)
            return
        if not _is_supervisor(update, context):
            await update.message.reply_text(
                "Укажите ФИО научрука (как в «Магистрантах» / «научрук»), например:\n"
                "/unreg Иванов Иван Иванович\n"
                "или только фамилию, если она однозначна."
            )
            return

    if not _is_supervisor(update, context):
        await update.message.reply_text("Команда только для научных руководителей.")
        return
    tg = str(update.effective_user.id)
    chunks, err = await _call_blocking(supervisor_unregistered_report, cfg, tg)
    if err:
        await update.message.reply_text(err)
        return
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def supervisor_registered_list_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.message is None:
        return
    cfg = _bot_config(context)
    args = context.args or []

    if _is_admin(update, context):
        if args:
            fio = " ".join(args).strip()
            chunks, err = await _call_blocking(
                supervisor_registered_report,
                cfg,
                "",
                supervisor_fio_override=fio,
            )
            if err:
                await update.message.reply_text(err)
                return
            for i, chunk in enumerate(chunks):
                prefix = (
                    f"Превью /reg_list для научрука «{fio}»"
                    + (f" (часть {i + 1}/{len(chunks)})" if len(chunks) > 1 else "")
                    + ":\n\n"
                )
                await update.message.reply_text(prefix + chunk)
            return
        if not _is_supervisor(update, context):
            await update.message.reply_text(
                "Укажите ФИО научрука, например:\n"
                "/reg_list Иванов Иван Иванович"
            )
            return

    if not _is_supervisor(update, context):
        await update.message.reply_text("Команда только для научных руководителей.")
        return
    tg = str(update.effective_user.id)
    chunks, err = await _call_blocking(supervisor_registered_report, cfg, tg)
    if err:
        await update.message.reply_text(err)
        return
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel: прервать диалог."""

    user_form = _get_user_form(context)
    _record_action(user_form, "cancelled")
    await clear_challenge(user_form.telegram_id or "")
    context.user_data.pop(USER_DATA_PIN_CONTEXT_KEY, None)
    context.user_data.pop(USER_DATA_PIN_REGISTER_EXTRA_KEY, None)
    context.user_data[USER_DATA_CLAIM_TARGET_KEY] = None
    context.user_data[USER_DATA_CLAIM_ROW_KEY] = None
    context.user_data[USER_DATA_BIND_ROW_KEY] = None
    _clear_admin_recheck_pending(context)
    _clear_student_reminder(context)
    msg = update.message or (update.effective_message)
    if msg is not None:
        await msg.reply_text(
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
        is_ad = _is_admin(update, context)
        is_sup = _is_supervisor(update, context)
        await msg.reply_text(
            help_reply_for_user(is_admin=is_ad, is_supervisor=is_sup)
        )


__all__ = [
    "ADMSTUB_CALLBACK_CONFIRM_PATTERN",
    "ADMSTU_CALLBACK_CONFIRM_PATTERN",
    "ADMSTU_CALLBACK_TEMPLATE_PATTERN",
    "ADMIN_PROJECT_CARD_BUTTON",
    "ADMIN_STUDENT_MESSAGE_BUTTON",
    "ADMIN_STUDENT_MESSAGE_BULK_BUTTON",
    "ASK_FIELD",
    "ASK_CONFIRM",
    "BIND_ASK_FIO",
    "BIND_CONFIRM",
    "CLAIM_ASK_FIO",
    "CLAIM_CONFIRM",
    "CONFIG_BOT_DATA_KEY",
    "PROJECT_CARD_ASK_TARGET",
    "RECHECK_BUTTON_LABEL",
    "RECHECK_CALLBACK_DATA",
    "RECHECK_CALLBACK_PATTERN",
    "SPRAVKA_CALLBACK_COMMISSION",
    "USER_DATA_BIND_ROW_KEY",
    "USER_DATA_CLAIM_TARGET_KEY",
    "USER_DATA_CLAIM_ROW_KEY",
    "USER_DATA_FORM_KEY",
    "USER_DATA_PENDING_KEY",
    "USER_DATA_CURRENT_KEY",
    "USER_DATA_SPRAVKA_MODE",
    "SPRAVKA_ASK_TARGET",
    "SPRAVKA_CALLBACK_PDF",
    "SPRAVKA_CALLBACK_TELEGRAM",
    "SPRAVKA_MENU",
    "STUDENT_MSG_ASK_CUSTOM",
    "STUDENT_MSG_ASK_EXTRA",
    "STUDENT_MSG_ASK_TARGET",
    "STUDENT_MSG_BULK_ASK_ROWS",
    "STUDENT_MSG_BULK_CONFIRM",
    "STUDENT_MSG_CONFIRM",
    "STUDENT_MSG_PICK_KIND",
    "ROLE_PICK",
    "admin_recheck_pending_receive",
    "admin_menu",
    "admin_stats",
    "admin_sync_dashboard",
    "admin_sync_magistrants",
    "ask_confirm",
    "build_recheck_keyboard",
    "cancel",
    "confirm_bind",
    "confirm_claim",
    "default_bot_commands",
    "help_command",
    "help_reply_for_user",
    "HELP_REPLY_TEXT",
    "HELP_REPLY_TEXT_ADMIN",
    "HELP_REPLY_TEXT_STUDENT",
    "HELP_REPLY_TEXT_SUPERVISOR",
    "PIN_VERIFY_INPUT",
    "project_card_receive_target",
    "project_card_start",
    "receive_bind_fio",
    "receive_claim_fio",
    "receive_field",
    "receive_pin_input",
    "on_project_snapshot_json_file",
    "recheck",
    "recheck_button",
    "register_command",
    "skip_bind",
    "skip_field",
    "spravka_choose",
    "spravka_receive_target",
    "spravka_start",
    "start",
    "start_role_callback",
    "status_command",
    "student_message_bulk_start",
    "student_reminder_bulk_confirm_callback",
    "student_reminder_bulk_receive_rows",
    "student_reminder_confirm_callback",
    "student_reminder_receive_custom",
    "student_reminder_receive_extra",
    "student_reminder_receive_target",
    "student_reminder_pick_template",
    "student_reminder_start",
    "supervisor_registered_list_command",
    "supervisor_unregistered_list_command",
]
