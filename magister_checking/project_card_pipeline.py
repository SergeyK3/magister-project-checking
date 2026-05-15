"""Генерация PDF-карточки проекта для отправки в Telegram."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.report_enrichment import build_sheet_enrichment
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.sheets_repo import (
    get_spreadsheet,
    load_user,
    save_user_to_row_with_extras,
    sync_registration_dashboard,
)
from magister_checking.project_snapshot import build_project_snapshot
from magister_checking.snapshot_drive import try_upload_project_snapshot_json
from magister_checking.snapshot_render import render_commission_plaintext

_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN_X = 50
_MARGIN_Y = 50
_FONT_SIZE = 11
_LINE_HEIGHT = 16
_DEFAULT_FONT_NAME = "Helvetica"
_UNICODE_FONT_NAME = "ProjectCardUnicode"
# Токены с пробелами в URL в карточке не переносим словами — см. обёртку строк.
_LINK_IN_TEXT_RE = re.compile(r"https?://\S+")
_PUNCT_AFTER_URL = frozenset(".,;:!?)]}'\"«»")


def _strip_url_trailing_punct(url: str) -> str:
    """Сохранённый якорь ссылки без хвостовой пунктуации после URL в тексте."""

    U = url
    while len(U) > 1 and U[-1] in _PUNCT_AFTER_URL:
        U = U[:-1]
    return U


def _draw_wrapped_physical_line_with_links(
    pdf: canvas.Canvas,
    *,
    x0: float,
    y: float,
    line: str,
    font_name: str,
    font_size: int,
) -> None:
    """Рисует одну уже уложенную по ширине строку: русский текст чёрным, ``https://…`` синим + URI."""

    line = _pdf_safe_text(line)
    if not line:
        return

    if _LINK_IN_TEXT_RE.search(line) is None:
        pdf.setFillColorRGB(0.0, 0.0, 0.0)
        pdf.drawString(x0, y, line)
        return

    black = (0.0, 0.0, 0.0)
    link_blue = (0.06, 0.27, 0.65)

    pdf.setFillColorRGB(*black)
    x = x0
    last = 0
    for m in _LINK_IN_TEXT_RE.finditer(line):
        prefix = line[last : m.start()]
        if prefix:
            pdf.drawString(x, y, prefix)
            x += pdfmetrics.stringWidth(prefix, font_name, font_size)

        visible = m.group(0)
        pdf.setFillColorRGB(*link_blue)
        pdf.drawString(x, y, visible)
        sw = pdfmetrics.stringWidth(visible, font_name, font_size)
        href = _strip_url_trailing_punct(visible)
        if href.startswith(("http://", "https://")):
            pdf.linkURL(
                href,
                (x, y - 3.0, x + sw, y + font_size + 4.0),
                relative=0,
            )
        pdf.setFillColorRGB(*black)
        x += sw
        last = m.end()

    suffix = line[last:]
    if suffix:
        pdf.drawString(x, y, suffix)


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


def _split_long_word(word: str, *, font_name: str, font_size: int, max_width: float) -> list[str]:
    word = _pdf_safe_text(word)
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


def _pdf_safe_text(value: object) -> str:
    """ReportLab ждёт str; из Sheets теоретически могут прийти числа или bytes."""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _wrap_line(line: str, *, font_name: str, font_size: int, max_width: float) -> list[str]:
    line = _pdf_safe_text(line)
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
    body_text = _pdf_safe_text(body_text)
    title = _pdf_safe_text(title).replace("\x00", "")
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
            _draw_wrapped_physical_line_with_links(
                pdf,
                x0=_MARGIN_X,
                y=y,
                line=_pdf_safe_text(line),
                font_name=font_name,
                font_size=_FONT_SIZE,
            )
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

    base_name = _sanitize_filename(
        f"Карточка проекта - {user.fio or f'строка {row_number}'}",
        fallback=f"Карточка проекта - строка {row_number}",
    )
    pdf_name = f"{base_name}.pdf"
    snapshot = build_project_snapshot(
        user=user,
        report=RowCheckReport(
            fio=user.fio or "",
            row_number=row_number,
        ),
        extra_values=extra_values,
        fill_status=user.fill_status or "",
        trigger="manual_regenerate",
    )
    body_text = render_commission_plaintext(snapshot)
    try_upload_project_snapshot_json(config, snapshot)
    pdf_bytes = _render_pdf(
        title=base_name,
        body_text=body_text,
    )
    if not pdf_bytes:
        raise RuntimeError("Не удалось сформировать PDF-карточку проекта.")

    return ProjectCardResult(
        row_number=row_number,
        pdf_name=pdf_name,
        pdf_bytes=pdf_bytes,
    )
