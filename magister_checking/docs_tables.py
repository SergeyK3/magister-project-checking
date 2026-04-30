"""Извлечение таблиц Google Docs: текст и ссылки по ячейкам."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from magister_checking.docs_extract import HyperlinkRecord, table_cell_content_blocks


@dataclass
class TableCell:
    text: str
    links: list[HyperlinkRecord] = field(default_factory=list)


def extract_tables(document: dict[str, Any]) -> list[list[list[TableCell]]]:
    """Все таблицы документа по порядку; каждая — список строк, строка — список ячеек."""
    tables: list[list[list[TableCell]]] = []
    for element in document.get("body", {}).get("content", []):
        if "table" not in element:
            continue
        rows_out: list[list[TableCell]] = []
        for ri, row in enumerate(element["table"].get("tableRows", [])):
            row_cells: list[TableCell] = []
            for ci, cell in enumerate(row.get("tableCells", [])):
                path = f"table[{ri},{ci}]"
                row_cells.append(_extract_cell(cell, path))
            rows_out.append(row_cells)
        tables.append(rows_out)
    return tables


def _extract_cell(cell: dict[str, Any], path: str) -> TableCell:
    parts: list[str] = []
    links: list[HyperlinkRecord] = []
    _fill_from_content(table_cell_content_blocks(cell), path, parts, links)
    return TableCell(text="".join(parts), links=links)


def _fill_from_content(
    content: list[dict[str, Any]],
    path: str,
    parts: list[str],
    links: list[HyperlinkRecord],
) -> None:
    for element in content:
        if "paragraph" in element:
            _fill_paragraph(element["paragraph"], path, parts, links)
        elif "table" in element:
            table = element["table"]
            for ri, row in enumerate(table.get("tableRows", [])):
                for ci, nested_cell in enumerate(row.get("tableCells", [])):
                    nested_path = f"{path}/nested[{ri},{ci}]"
                    _fill_from_content(
                        table_cell_content_blocks(nested_cell), nested_path, parts, links
                    )


def _fill_paragraph(
    paragraph: dict[str, Any],
    path: str,
    parts: list[str],
    links: list[HyperlinkRecord],
) -> None:
    for pe in paragraph.get("elements", []):
        # Smart chip / rich link (Google Docs "link cards") are not represented as textRun.link.url.
        if "richLink" in pe:
            rl = pe.get("richLink") or {}
            props = rl.get("richLinkProperties") or {}
            title = str(props.get("title") or "")
            uri = props.get("uri")
            if title:
                parts.append(title)
            if uri:
                links.append(
                    HyperlinkRecord(
                        url=str(uri),
                        anchor_text=title,
                        context_path=path,
                    )
                )
            continue
        tr = pe.get("textRun")
        if not tr:
            continue
        if "content" in tr:
            parts.append(tr["content"])
        style = tr.get("textStyle") or {}
        link = style.get("link")
        if link and link.get("url"):
            links.append(
                HyperlinkRecord(
                    url=link["url"],
                    anchor_text=tr.get("content", ""),
                    context_path=path,
                )
            )
