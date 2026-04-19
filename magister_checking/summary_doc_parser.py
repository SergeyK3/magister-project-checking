"""Разбор сводного Google Doc (Прил. 2): таблица со списком магистрантов и ссылками на отчёты."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from magister_checking.docs_extract import HyperlinkRecord
from magister_checking.docs_tables import TableCell, extract_tables
from magister_checking.drive_urls import is_google_drive_folder_url

_DOC_URL_IN_TEXT = re.compile(
    r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)(?:/[^\s\)\]\"]*)?",
    re.IGNORECASE,
)
_FOLDER_URL_IN_TEXT = re.compile(
    r"https://drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)(?:\?[^\s\)\]\"]*)?",
    re.IGNORECASE,
)


def _is_docs_url(url: str) -> bool:
    u = url.lower()
    return "document/d/" in u or "docs.google.com/document" in u


def _first_doc_link(links: list[HyperlinkRecord]) -> str | None:
    for h in links:
        if _is_docs_url(h.url):
            return h.url
    return None


def _first_folder_link(links: list[HyperlinkRecord]) -> str | None:
    for h in links:
        if is_google_drive_folder_url(h.url):
            return h.url
    return None


def _report_url_from_cell(cell: TableCell) -> str | None:
    """Гиперссылка в ячейке или URL как обычный текст (Doc или папка с отчётом)."""
    u = _first_doc_link(cell.links)
    if u:
        return u
    u = _first_folder_link(cell.links)
    if u:
        return u
    m = _DOC_URL_IN_TEXT.search(cell.text)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/edit"
    m = _FOLDER_URL_IN_TEXT.search(cell.text)
    if m:
        return f"https://drive.google.com/drive/folders/{m.group(1)}"
    return None


@dataclass
class SummaryStudentRow:
    """Одна строка сводной таблицы (как в тестовом Doc)."""

    name: str
    report_url: str | None
    group: str
    raw_cells: list[str]


def _pick_largest_table(document: dict[str, Any]) -> list[list] | None:
    tables = extract_tables(document)
    if not tables:
        return None
    return max(tables, key=lambda t: sum(len(r) for r in t) if t else 0)


def _header_indices(header_row: list) -> tuple[int | None, int | None, int | None]:
    """Индексы колонок: ФИО, группа, ссылка на отчёт."""
    texts = [c.text.lower().strip() for c in header_row]
    idx_name = None
    idx_group = None
    idx_report = None
    for i, h in enumerate(texts):
        if "фио" in h or "ф.и.о" in h:
            idx_name = i
        if "групп" in h:
            idx_group = i
        if any(
            x in h
            for x in (
                "промежуточн",
                "отчёт",
                "отчет",
                "ссылк",
            )
        ):
            idx_report = i
    return idx_name, idx_group, idx_report


def parse_summary_document(document: dict[str, Any]) -> list[SummaryStudentRow]:
    """
    Извлекает строки из наибольшей таблицы документа.
    Если колонка со ссылкой не найдена по заголовку, берётся последняя колонка с docs-link в строке.
    """
    table = _pick_largest_table(document)
    if not table or len(table) < 2:
        return []

    header = table[0]
    idx_name, idx_group, idx_report = _header_indices(header)
    ncols = max(len(r) for r in table)

    if idx_name is None:
        idx_name = 1 if ncols > 1 else 0
    if idx_group is None:
        idx_group = 2 if ncols > 2 else None

    rows_out: list[SummaryStudentRow] = []
    for row in table[1:]:
        if not row:
            continue
        cells_text = [c.text.strip() for c in row]
        name = row[idx_name].text.strip() if idx_name < len(row) else ""
        if not name or name.lower() in ("фио", "№", "номер", "n"):
            continue

        group = ""
        if idx_group is not None and idx_group < len(row):
            group = row[idx_group].text.strip()

        report_url: str | None = None
        if idx_report is not None and idx_report < len(row):
            report_url = _report_url_from_cell(row[idx_report])
        if not report_url:
            for cell in reversed(row):
                u = _report_url_from_cell(cell)
                if u:
                    report_url = u
                    break

        rows_out.append(
            SummaryStudentRow(
                name=name,
                report_url=report_url,
                group=group,
                raw_cells=cells_text,
            )
        )
    return rows_out
