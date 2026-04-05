"""
Извлечение текста и гиперссылок из ответа Google Docs API (documents.get).

Обходит параграфы и вложенные таблицы (ячейки содержат тот же формат content[]).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class HyperlinkRecord:
    """Внешняя ссылка из textRun (поле link.url)."""

    url: str
    anchor_text: str
    context_path: str


def extract_plain_text(document: dict[str, Any]) -> str:
    """Весь видимый текст документа в порядке следования в API (включая ячейки таблиц)."""
    parts: list[str] = []
    content = document.get("body", {}).get("content", [])
    _append_text_from_content(content, parts)
    return "".join(parts)


def iter_hyperlinks(document: dict[str, Any]) -> Iterator[HyperlinkRecord]:
    """Итерация по внешним URL в textRun (link.url). Внутренние headingId/bookmarkId пропускаются."""
    content = document.get("body", {}).get("content", [])
    yield from _iter_hyperlinks_in_content(content, "body")


def _append_text_from_content(content: list[dict[str, Any]], parts: list[str]) -> None:
    for element in content:
        if "paragraph" in element:
            _append_paragraph_plain_text(element["paragraph"], parts)
        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    nested = cell.get("content", [])
                    _append_text_from_content(nested, parts)
        # sectionBreak, tableOfContents — без текстового содержимого в body


def _append_paragraph_plain_text(paragraph: dict[str, Any], parts: list[str]) -> None:
    for pe in paragraph.get("elements", []):
        tr = pe.get("textRun")
        if tr and "content" in tr:
            parts.append(tr["content"])
        # inlineObjectElement (рисунки и т.д.) — текст из content API не даёт


def _iter_hyperlinks_in_content(
    content: list[dict[str, Any]], path: str
) -> Iterator[HyperlinkRecord]:
    for element in content:
        if "paragraph" in element:
            yield from _iter_paragraph_hyperlinks(element["paragraph"], path)
        elif "table" in element:
            table = element["table"]
            for ri, row in enumerate(table.get("tableRows", [])):
                for ci, cell in enumerate(row.get("tableCells", [])):
                    cell_path = f"{path}/table[{ri},{ci}]"
                    nested = cell.get("content", [])
                    yield from _iter_hyperlinks_in_content(nested, cell_path)


def _iter_paragraph_hyperlinks(
    paragraph: dict[str, Any], path: str
) -> Iterator[HyperlinkRecord]:
    for pe in paragraph.get("elements", []):
        tr = pe.get("textRun")
        if not tr:
            continue
        text = tr.get("content", "")
        style = tr.get("textStyle") or {}
        link = style.get("link")
        if not link:
            continue
        url = link.get("url")
        if url:
            yield HyperlinkRecord(url=url, anchor_text=text, context_path=path)
