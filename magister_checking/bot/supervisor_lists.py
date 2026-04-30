"""Тексты ответов научрука для /unreg и /reg_list (общая логика и CLI-превью)."""

from __future__ import annotations

from typing import List

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.phone_normalize import normalize_phone_ru_kz
from magister_checking.bot.sheets_repo import (
    SUPERVISORS_WORKSHEET_NAME,
    get_optional_worksheet,
    get_spreadsheet,
    get_supervisor_fio_for_telegram_id,
    get_worksheet,
    magistrants_sheet_column_indices,
    normalize_fio,
    registration_students_by_fio_phone,
    supervisor_name_matches,
)

_TELEGRAM_TEXT_SOFT_LIMIT = 3900

_UNREG_MSG_NO_UNREGISTERED = (
    "На текущий момент у вас нет незарегистрированных магистрантов"
)

_UNREG_SUPERVISOR_ACTION_HINT = (
    "\n\nПодсказка: перезвоните перечисленным магистрантам и попросите без "
    "промедления зарегистрироваться в боте (в личке с ботом — /start или /register) "
    "и вступить в группу Telegram «Магистр аттестация КОЗМ»."
)


def split_supervisor_message_chunks(
    text: str, limit: int = _TELEGRAM_TEXT_SOFT_LIMIT
) -> List[str]:
    """Те же отрезки длины, что у Telegram handler (мягкий лимит ~4k)."""

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
            buf = []
            size = 0
        buf.append(line)
        size += line_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _resolve_sup_fio_for_preview(
    config: BotConfig,
    telegram_id: str,
    *,
    supervisor_fio_override: str | None,
) -> tuple[str | None, str | None]:
    if supervisor_fio_override and supervisor_fio_override.strip():
        return supervisor_fio_override.strip(), None
    if not telegram_id.strip():
        return None, "Укажите непустой --telegram-id или --supervisor-fio."
    sup = get_supervisor_fio_for_telegram_id(config, telegram_id.strip())
    if not sup.strip():
        return None, (
            f"Не найдено ФИО научрука по telegram_id в листе «{SUPERVISORS_WORKSHEET_NAME}». "
            "Проверьте строку или используйте --supervisor-fio «как в научрук»."
        )
    return sup.strip(), None


def supervisor_unregistered_report(
    config: BotConfig,
    telegram_id: str,
    *,
    supervisor_fio_override: str | None = None,
) -> tuple[List[str], str | None]:
    """Сообщения как у /unreg: (чанки для отправки, ошибка)."""

    title = (config.magistrants_worksheet_name or "").strip()
    if not title:
        return [], "Мастер-лист магистрантов не настроен (MAGISTRANTS_WORKSHEET_NAME)."

    sup_fio, err = _resolve_sup_fio_for_preview(
        config, telegram_id, supervisor_fio_override=supervisor_fio_override
    )
    if err:
        return [], err

    spreadsheet = get_spreadsheet(config)
    mag_ws = get_optional_worksheet(spreadsheet, title)
    if mag_ws is None:
        return [], f"Лист «{title}» не найден."

    header = mag_ws.row_values(1)
    colmap = magistrants_sheet_column_indices(header)
    if colmap is None or "supervisor" not in colmap:
        return [], "В листе магистрантов нет колонки научного руководителя."

    reg_ws = get_worksheet(config)
    reg_map = registration_students_by_fio_phone(reg_ws)
    all_rows = mag_ws.get_all_values()
    fio_i = colmap["fio"]
    phone_i = colmap["phone"]
    sup_i = colmap["supervisor"]
    lines_body: List[str] = []
    for row in all_rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        w = max(len(header), len(row), fio_i + 1, phone_i + 1, sup_i + 1)
        padded = list(row) + [""] * (w - len(row))
        if not supervisor_name_matches(sup_fio, str(padded[sup_i] or "")):
            continue
        st_fio = str(padded[fio_i] or "").strip()
        raw_phone = str(padded[phone_i] or "").strip()
        fk = normalize_fio(st_fio)
        pk = normalize_phone_ru_kz(raw_phone)
        if fk and pk and (fk, pk) in reg_map:
            continue
        phone_disp = normalize_phone_ru_kz(raw_phone) or raw_phone
        lines_body.append(f"• {st_fio} — {phone_disp}")
    if not lines_body:
        return [_UNREG_MSG_NO_UNREGISTERED], None
    text = (
        "Не зарегистрированы в боте:\n\n"
        + "\n".join(lines_body)
        + _UNREG_SUPERVISOR_ACTION_HINT
    )
    return split_supervisor_message_chunks(text), None


def supervisor_registered_report(
    config: BotConfig,
    telegram_id: str,
    *,
    supervisor_fio_override: str | None = None,
) -> tuple[List[str], str | None]:
    """Сообщения как у /reg_list: (чанки, ошибка)."""

    title = (config.magistrants_worksheet_name or "").strip()
    if not title:
        return [], "Мастер-лист магистрантов не настроен (MAGISTRANTS_WORKSHEET_NAME)."

    sup_fio, err = _resolve_sup_fio_for_preview(
        config, telegram_id, supervisor_fio_override=supervisor_fio_override
    )
    if err:
        return [], err

    spreadsheet = get_spreadsheet(config)
    mag_ws = get_optional_worksheet(spreadsheet, title)
    if mag_ws is None:
        return [], f"Лист «{title}» не найден."

    header = mag_ws.row_values(1)
    colmap = magistrants_sheet_column_indices(header)
    if colmap is None or "supervisor" not in colmap:
        return [], "В листе магистрантов нет колонки научного руководителя."

    reg_ws = get_worksheet(config)
    reg_map = registration_students_by_fio_phone(reg_ws)
    all_rows = mag_ws.get_all_values()
    fio_i = colmap["fio"]
    phone_i = colmap["phone"]
    sup_i = colmap["supervisor"]
    lines_body: List[str] = []
    for row in all_rows[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        w = max(len(header), len(row), fio_i + 1, phone_i + 1, sup_i + 1)
        padded = list(row) + [""] * (w - len(row))
        if not supervisor_name_matches(sup_fio, str(padded[sup_i] or "")):
            continue
        st_fio = str(padded[fio_i] or "").strip()
        raw_phone = str(padded[phone_i] or "").strip()
        fk = normalize_fio(st_fio)
        pk = normalize_phone_ru_kz(raw_phone)
        key = (fk, pk) if fk and pk else None
        usr: UserForm | None = reg_map.get(key) if key else None
        if usr is None:
            continue
        lines_body.append(
            f"• {st_fio} — статус: {usr.fill_status or '—'}; "
            f"ссылка OK: {usr.report_url_valid or '—'}; доступ: {usr.report_url_accessible or '—'}"
        )
    if not lines_body:
        return ["Зарегистрированных под вашим руководством не найдено."], None
    text = "Зарегистрированы в боте:\n\n" + "\n".join(lines_body)
    return split_supervisor_message_chunks(text), None
