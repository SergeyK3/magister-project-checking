import logging
import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List, Tuple

import gspread
import requests
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
GOOGLE_SERVICE_ACCOUNT_JSON = r"PATH_TO_SERVICE_ACCOUNT_JSON"
SPREADSHEET_ID = "16gpZSZgKBcbf8Z9LZvcYKT1lUPG-URuo9C6K9BRPDHU"
WORKSHEET_NAME = "Регистрация"

LOG_LEVEL = logging.INFO

# Если пользователь вводит "-" — поле считается пропущенным.
SKIP_TOKEN = "-"

# =========================
# ЛОГИРОВАНИЕ
# =========================

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("magistrcheckbot")


# =========================
# СОСТОЯНИЯ ДИАЛОГА
# =========================

(
    ASK_FIO,
    ASK_GROUP,
    ASK_WORKPLACE,
    ASK_POSITION,
    ASK_PHONE,
    ASK_SUPERVISOR,
    ASK_REPORT_URL,
    ASK_CONFIRM,
) = range(8)


# =========================
# МОДЕЛЬ ДАННЫХ
# =========================

@dataclass
class UserForm:
    telegram_id: str = ""
    telegram_username: str = ""
    telegram_first_name: str = ""
    telegram_last_name: str = ""
    fio: str = ""
    group_name: str = ""
    workplace: str = ""
    position: str = ""
    phone: str = ""
    supervisor: str = ""
    report_url: str = ""
    report_url_valid: str = ""
    report_url_accessible: str = ""
    report_url_public_guess: str = ""
    fill_status: str = ""
    last_action: str = ""


# =========================
# GOOGLE SHEETS
# =========================

def get_gspread_client() -> gspread.Client:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_JSON,
        scopes=scopes,
    )
    return gspread.authorize(creds)


def get_worksheet():
    client = get_gspread_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    return worksheet


def ensure_header(worksheet) -> None:
    expected_header = [
        "telegram_id",
        "telegram_username",
        "telegram_first_name",
        "telegram_last_name",
        "fio",
        "group_name",
        "workplace",
        "position",
        "phone",
        "supervisor",
        "report_url",
        "report_url_valid",
        "report_url_accessible",
        "report_url_public_guess",
        "fill_status",
        "last_action",
    ]

    current = worksheet.row_values(1)
    if current != expected_header:
        worksheet.update("A1:P1", [expected_header])


def find_row_by_telegram_id(worksheet, telegram_id: str) -> Optional[int]:
    values = worksheet.col_values(1)
    for idx, cell_value in enumerate(values, start=1):
        if idx == 1:
            continue
        if str(cell_value).strip() == str(telegram_id).strip():
            return idx
    return None


def load_user_from_sheet(worksheet, row_number: int) -> UserForm:
    row = worksheet.row_values(row_number)
    row += [""] * (16 - len(row))

    return UserForm(
        telegram_id=row[0],
        telegram_username=row[1],
        telegram_first_name=row[2],
        telegram_last_name=row[3],
        fio=row[4],
        group_name=row[5],
        workplace=row[6],
        position=row[7],
        phone=row[8],
        supervisor=row[9],
        report_url=row[10],
        report_url_valid=row[11],
        report_url_accessible=row[12],
        report_url_public_guess=row[13],
        fill_status=row[14],
        last_action=row[15],
    )


def save_user_to_sheet(worksheet, user: UserForm) -> int:
    ensure_header(worksheet)
    row_data = [[
        user.telegram_id,
        user.telegram_username,
        user.telegram_first_name,
        user.telegram_last_name,
        user.fio,
        user.group_name,
        user.workplace,
        user.position,
        user.phone,
        user.supervisor,
        user.report_url,
        user.report_url_valid,
        user.report_url_accessible,
        user.report_url_public_guess,
        user.fill_status,
        user.last_action,
    ]]

    existing_row = find_row_by_telegram_id(worksheet, user.telegram_id)
    if existing_row:
        worksheet.update(f"A{existing_row}:P{existing_row}", row_data)
        return existing_row

    worksheet.append_rows(row_data, value_input_option="USER_ENTERED")
    return worksheet.row_count


# =========================
# ВАЛИДАЦИЯ
# =========================

def normalize_text(value: str) -> str:
    value = value.strip()
    if value == SKIP_TOKEN:
        return ""
    return value


def is_valid_url(url: str) -> bool:
    pattern = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
    return bool(pattern.match(url.strip()))


def is_probably_public_google_doc_response(text: str) -> str:
    """
    Очень грубая первичная эвристика.
    Не финальная проверка прав доступа.
    """
    lowered = text.lower()

    deny_markers = [
        "you need access",
        "нужен доступ",
        "request access",
        "запросить доступ",
        "sign in",
        "войти",
    ]
    for marker in deny_markers:
        if marker in lowered:
            return "no"

    ok_markers = [
        "docs.google.com",
        "google docs",
        "google drive",
    ]
    for marker in ok_markers:
        if marker in lowered:
            return "yes"

    return "unknown"


def check_report_url(url: str) -> Tuple[str, str, str]:
    """
    Возвращает:
    (valid, accessible, public_guess)
    """
    if not url:
        return "", "", ""

    if not is_valid_url(url):
        return "no", "no", "no"

    try:
        response = requests.get(
            url,
            timeout=15,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        accessible = "yes" if response.status_code < 400 else "no"
        public_guess = is_probably_public_google_doc_response(response.text)
        return "yes", accessible, public_guess
    except requests.RequestException:
        return "yes", "no", "unknown"


def completion_status(user: UserForm) -> str:
    required_fields = [
        user.fio,
        user.group_name,
        user.workplace,
        user.position,
        user.phone,
        user.supervisor,
        user.report_url,
    ]
    return "complete" if all(required_fields) else "partial"


def get_missing_fields(user: UserForm) -> List[str]:
    missing = []

    if not user.fio:
        missing.append("ФИО")
    if not user.group_name:
        missing.append("Группа")
    if not user.workplace:
        missing.append("Место работы")
    if not user.position:
        missing.append("Должность")
    if not user.phone:
        missing.append("Телефон")
    if not user.supervisor:
        missing.append("Научный руководитель")
    if not user.report_url:
        missing.append("Ссылка на промежуточный отчет")

    return missing


# =========================
# ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ В CONTEXT
# =========================

def get_user_form(context: ContextTypes.DEFAULT_TYPE) -> UserForm:
    if "form_data" not in context.user_data:
        context.user_data["form_data"] = UserForm()
    return context.user_data["form_data"]


def set_telegram_identity(user_form: UserForm, update: Update) -> None:
    tg_user = update.effective_user
    user_form.telegram_id = str(tg_user.id)
    user_form.telegram_username = tg_user.username or ""
    user_form.telegram_first_name = tg_user.first_name or ""
    user_form.telegram_last_name = tg_user.last_name or ""


# =========================
# ХЕНДЛЕРЫ
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    worksheet = get_worksheet()
    ensure_header(worksheet)

    user_form = get_user_form(context)
    set_telegram_identity(user_form, update)

    existing_row = find_row_by_telegram_id(worksheet, user_form.telegram_id)
    if existing_row:
        user_form = load_user_from_sheet(worksheet, existing_row)
        context.user_data["form_data"] = user_form
        set_telegram_identity(user_form, update)

        missing = get_missing_fields(user_form)
        if missing:
            await update.message.reply_text(
                "Вы уже есть в таблице. Продолжим заполнение.\n\n"
                f"Незаполненные поля: {', '.join(missing)}"
            )
        else:
            await update.message.reply_text(
                "Вы уже зарегистрированы. Можно обновить данные.\n\n"
                "Если хотите, пройдем поля еще раз и при необходимости обновим запись."
            )
    else:
        await update.message.reply_text(
            "Здравствуйте.\n\n"
            "Бот поможет зарегистрироваться для промежуточной аттестации магистрантов.\n"
            "Я последовательно задам вопросы и сохраню данные в таблицу.\n\n"
            f"Если какое-то поле хотите заполнить позже, отправьте {SKIP_TOKEN}"
        )

    await update.message.reply_text("Введите ФИО магистранта:")
    return ASK_FIO


async def ask_fio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.fio = normalize_text(update.message.text)
    await update.message.reply_text("Введите группу:")
    return ASK_GROUP


async def ask_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.group_name = normalize_text(update.message.text)
    await update.message.reply_text("Введите место работы:")
    return ASK_WORKPLACE


async def ask_workplace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.workplace = normalize_text(update.message.text)
    await update.message.reply_text("Введите должность:")
    return ASK_POSITION


async def ask_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.position = normalize_text(update.message.text)
    await update.message.reply_text("Введите сотовый контактный телефон:")
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.phone = normalize_text(update.message.text)
    await update.message.reply_text("Введите ФИО научного руководителя:")
    return ASK_SUPERVISOR


async def ask_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.supervisor = normalize_text(update.message.text)
    await update.message.reply_text(
        "Введите ссылку на промежуточный отчет:"
    )
    return ASK_REPORT_URL


async def ask_report_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_form = get_user_form(context)
    user_form.report_url = normalize_text(update.message.text)

    if user_form.report_url:
        valid, accessible, public_guess = check_report_url(user_form.report_url)
        user_form.report_url_valid = valid
        user_form.report_url_accessible = accessible
        user_form.report_url_public_guess = public_guess
    else:
        user_form.report_url_valid = ""
        user_form.report_url_accessible = ""
        user_form.report_url_public_guess = ""

    user_form.fill_status = completion_status(user_form)
    user_form.last_action = "questionnaire_completed"

    summary = [
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
        f"Статус заполнения: {user_form.fill_status}",
        "",
        "Подтвердите сохранение: да / нет",
    ]

    await update.message.reply_text("\n".join(summary))
    return ASK_CONFIRM


async def ask_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    user_form = get_user_form(context)

    if answer not in {"да", "нет"}:
        await update.message.reply_text("Введите: да или нет")
        return ASK_CONFIRM

    if answer == "нет":
        await update.message.reply_text(
            "Сохранение отменено. Нажмите /start, чтобы начать заново."
        )
        return ConversationHandler.END

    worksheet = get_worksheet()
    row_num = save_user_to_sheet(worksheet, user_form)
    missing = get_missing_fields(user_form)

    if missing:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            f"Еще не заполнено: {', '.join(missing)}\n\n"
            "Позже вы можете снова нажать /start и продолжить."
        )
    else:
        await update.message.reply_text(
            "Данные сохранены.\n\n"
            f"Строка в таблице: {row_num}\n"
            "Регистрация завершена."
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Диалог остановлен. Для нового запуска используйте /start",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# =========================
# ЗАПУСК
# =========================

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fio)],
            ASK_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_group)],
            ASK_WORKPLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_workplace)],
            ASK_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_position)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_SUPERVISOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_supervisor)],
            ASK_REPORT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_report_url)],
            ASK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    logger.info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()