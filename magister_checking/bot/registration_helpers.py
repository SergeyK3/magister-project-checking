"""Pure registration/help helpers shared by Telegram handlers."""

from __future__ import annotations

from datetime import datetime

from telegram import BotCommand, Update

from magister_checking.bot.models import FillStatus, UserForm, compute_fill_status
from magister_checking.bot.sheets_repo import SUPERVISORS_WORKSHEET_NAME
from magister_checking.bot.validation import SKIP_TOKEN


def _student_help_text(*, require_phone_pin: bool) -> str:
    pin_note = (
        "При привязке к строке или перед сохранением анкеты бот может попросить одноразовый "
        "код по телефону из таблицы (см. лог бота в учебном контуре; SMS не отправляется).\n\n"
        if require_phone_pin
        else ""
    )
    return (
        "Команды бота (магистрант):\n\n"
        "/start — регистрация: привязка к строке в таблице «Регистрация» или "
        "продолжение анкеты. Числовой Telegram ID подставляется из вашего "
        "аккаунта автоматически.\n"
        "/справка — запустить проверку проекта и получить краткую справку "
        "(если клиент Telegram не принимает кириллицу, используйте /spravka).\n"
        "/help — эта справка\n\n"
        f"{pin_note}"
        f"В анкете поле можно пропустить: отправьте {SKIP_TOKEN} или /skip.\n"
        "Если нужно прервать текущий диалог, отправьте /выход.\n\n"
        "Меню команд (кнопка у поля ввода) подтягивается после перезапуска бота.\n\n"
        "Полные материалы для комиссии и проверка чужой строки — у администраторов; "
        "при необходимости обратитесь к куратору."
    )


HELP_REPLY_TEXT_STUDENT = _student_help_text(require_phone_pin=False)

HELP_REPLY_TEXT_ADMIN = (
    "Команды бота (администратор):\n\n"
    "/start — регистрация, привязка к строке таблицы или продолжение анкеты\n"
    "/справка — кратко для магистранта, полный текст для комиссии в чате, PDF, "
    "либо вложенный JSON с Drive в человекочитаемый вид (режимы «чужой строки» "
    "и JSON — у админа; латинский alias — /spravka)\n"
    "/help — эта справка\n\n"
    f"В анкете поле можно пропустить: отправьте {SKIP_TOKEN} или /skip.\n"
    "Если нужно прервать текущий диалог, отправьте /выход.\n\n"
    "Меню команд (кнопка у поля ввода) подтягивается после перезапуска бота."
)

HELP_REPLY_TEXT_SUPERVISOR = (
    "Команды бота (научный руководитель):\n\n"
    "/start — при первом входе: выбор роли и привязка к строке в листе "
    f"«{SUPERVISORS_WORKSHEET_NAME}» по ФИО (как в таблице), если ещё не привязаны\n"
    "/справка — проверка проекта и справка по вашей строке; латинский alias — /spravka\n"
    "/help — эта справка\n"
    "/выход — прервать текущий диалог (если бот ожидает ввод)\n\n"
    "Проверка отчётов и анкета — в сценарии магистранта (/start)."
)

# Обратная совместимость: раньше был один объединённый текст.
HELP_REPLY_TEXT = HELP_REPLY_TEXT_ADMIN


def help_reply_for_user(
    *,
    is_admin: bool,
    is_supervisor: bool = False,
    require_phone_pin: bool = False,
) -> str:
    """Текст /help: приоритет админ -> научрук -> магистрант."""

    if is_admin:
        return HELP_REPLY_TEXT_ADMIN
    if is_supervisor:
        return HELP_REPLY_TEXT_SUPERVISOR
    return _student_help_text(require_phone_pin=require_phone_pin)


def default_bot_commands() -> list[BotCommand]:
    """Команды для ``BotFather`` / меню Telegram (короткие описания <=256 симв.)."""

    return [
        BotCommand("start", "Запуск и регистрация"),
        BotCommand("spravka", "Справка и проверка проекта"),
        BotCommand("help", "Подсказки по работе с ботом"),
    ]


def _set_telegram_identity(user_form: UserForm, update: Update) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    user_form.telegram_id = str(tg_user.id)
    user_form.telegram_username = tg_user.username or ""
    user_form.telegram_first_name = tg_user.first_name or ""
    user_form.telegram_last_name = tg_user.last_name or ""


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


def _is_skip_text(raw: str) -> bool:
    stripped = (raw or "").strip()
    return stripped in {SKIP_TOKEN, "/skip"}


_TELEGRAM_TEXT_SOFT_LIMIT = 3900


def _split_text_chunks(text: str, limit: int = _TELEGRAM_TEXT_SOFT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
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
