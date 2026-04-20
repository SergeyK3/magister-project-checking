"""Генерация PDF-карточки проекта для отправки в Telegram."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.report_enrichment import build_sheet_enrichment
from magister_checking.bot.sheets_repo import (
    get_spreadsheet,
    load_user,
    save_user_to_row_with_extras,
    sync_registration_dashboard,
)

_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN_X = 50
_MARGIN_Y = 50
_FONT_SIZE = 11
_LINE_HEIGHT = 16
_DEFAULT_FONT_NAME = "Helvetica"
_UNICODE_FONT_NAME = "ProjectCardUnicode"

@dataclass(frozen=True)
class ProjectCardResult:
    row_number: int
    pdf_name: str
    pdf_bytes: bytes


def _sanitize_filename(value: str, *, fallback: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:120] or fallback


@lru_cache(maxsize=1)
def _project_card_font_name() -> str:
    """Регистрирует Unicode-шрифт для PDF, если он доступен в системе."""

    candidate_paths = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(_UNICODE_FONT_NAME, str(path)))
            return _UNICODE_FONT_NAME
        except Exception:  # noqa: BLE001
            continue
    return _DEFAULT_FONT_NAME


def _build_project_card_text(
    *,
    user: UserForm,
    extra_values: dict[str, str],
    generated_at: str,
    row_number: int,
) -> str:
    def v(name: str) -> str:
        return str(extra_values.get(name) or "—")

    lines = [
        "Карточка проекта магистранта",
        "",
        f"Дата формирования: {generated_at}",
        f"Строка в таблице: {row_number}",
        "",
        "Данные магистранта",
        f"ФИО: {user.fio or '—'}",
        f"Группа: {user.group_name or '—'}",
        f"Место работы: {user.workplace or '—'}",
        f"Должность: {user.position or '—'}",
        f"Телефон: {user.phone or '—'}",
        f"Научный руководитель: {user.supervisor or '—'}",
        "",
        "Ссылки",
        f"Промежуточный отчет: {user.report_url or '—'}",
        f"Папка проекта: {v('project_folder_url')}",
        f"Заключение ЛКБ: {v('lkb_url')}",
        f"Диссертация: {v('dissertation_url')}",
        "",
        "Данные по анализу диссертации",
        f"Число страниц: {v('pages_total')}",
        f"Число источников: {v('sources_count')}",
        f"Соответствие оформлению: {v('compliance')}",
        "",
        "Первичная проверка ссылки на отчет",
        f"Формат URL: {user.report_url_valid or '—'}",
        f"Доступ открыт: {user.report_url_accessible or '—'}",
        "",
        "Карточка сформирована автоматически на основе данных анкеты, промежуточного отчета и анализа диссертации.",
    ]
    return "\n".join(lines) + "\n"


def _split_long_word(word: str, *, font_name: str, font_size: int, max_width: float) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in word:
        trial = current + char
        if current and pdfmetrics.stringWidth(trial, font_name, font_size) > max_width:
            chunks.append(current)
            current = char
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks or [word]


def _wrap_line(line: str, *, font_name: str, font_size: int, max_width: float) -> list[str]:
    if not line.strip():
        return [""]

    words: list[str] = []
    for word in line.split():
        if pdfmetrics.stringWidth(word, font_name, font_size) <= max_width:
            words.append(word)
        else:
            words.extend(_split_long_word(word, font_name=font_name, font_size=font_size, max_width=max_width))

    wrapped: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if current and pdfmetrics.stringWidth(trial, font_name, font_size) > max_width:
            wrapped.append(current)
            current = word
        else:
            current = trial
    if current:
        wrapped.append(current)
    return wrapped or [""]


def _render_pdf(*, title: str, body_text: str) -> bytes:
    font_name = _project_card_font_name()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(title)
    pdf.setAuthor("magistrcheckbot")
    pdf.setFont(font_name, _FONT_SIZE)

    max_width = _PAGE_WIDTH - (2 * _MARGIN_X)
    y = _PAGE_HEIGHT - _MARGIN_Y
    for raw_line in body_text.splitlines():
        wrapped_lines = _wrap_line(
            raw_line,
            font_name=font_name,
            font_size=_FONT_SIZE,
            max_width=max_width,
        )
        for line in wrapped_lines:
            if y <= _MARGIN_Y:
                pdf.showPage()
                pdf.setFont(font_name, _FONT_SIZE)
                y = _PAGE_HEIGHT - _MARGIN_Y
            pdf.drawString(_MARGIN_X, y, line)
            y -= _LINE_HEIGHT
        if raw_line == "":
            y -= _LINE_HEIGHT // 3

    pdf.save()
    return buffer.getvalue()


def generate_project_card_pdf(
    *,
    config: BotConfig,
    row_number: int,
) -> ProjectCardResult:
    """Пересчитывает данные магистранта, обновляет таблицу и формирует PDF в памяти."""

    spreadsheet = get_spreadsheet(config)
    worksheet = spreadsheet.worksheet(config.worksheet_name)
    loaded = worksheet.row_values(row_number)
    if not any(str(value).strip() for value in loaded):
        raise ValueError(f"Строка {row_number} в листе регистрации пуста.")
    user = load_user(worksheet, row_number)
    extra_values = build_sheet_enrichment(config, user)
    save_user_to_row_with_extras(
        worksheet,
        row_number,
        user,
        extra_values=extra_values,
    )
    sync_registration_dashboard(config)

    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    base_name = _sanitize_filename(
        f"Карточка проекта - {user.fio or f'строка {row_number}'}",
        fallback=f"Карточка проекта - строка {row_number}",
    )
    pdf_name = f"{base_name}.pdf"
    pdf_bytes = _render_pdf(
        title=base_name,
        body_text=_build_project_card_text(
            user=user,
            extra_values=extra_values,
            generated_at=timestamp,
            row_number=row_number,
        ),
    )
    if not pdf_bytes:
        raise RuntimeError("Не удалось сформировать PDF-карточку проекта.")

    return ProjectCardResult(
        row_number=row_number,
        pdf_name=pdf_name,
        pdf_bytes=pdf_bytes,
    )
