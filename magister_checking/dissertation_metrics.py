"""Метрики по документу диссертации (заголовки, грубая оценка страниц и списка литературы)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from magister_checking.docs_extract import extract_plain_text


# Порядок важен: более длинные фразы раньше
_BIB_MARKERS = (
    "список литературы",
    "использованная литература",
    "библиографический список",
    "литература",
    "references",
)

_CHARS_PER_PAGE_RU = 2200


@dataclass
class DissertationMetrics:
    approx_pages: int
    sources_count: int | None
    has_literature_review: bool
    has_results: bool
    has_discussion: bool
    headings_found: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def iter_heading_texts(document: dict[str, Any]) -> Iterator[str]:
    """Тексты абзацев со стилем HEADING_* (включая ячейки таблиц)."""
    content = document.get("body", {}).get("content", [])
    yield from _headings_in_content(content)


def _headings_in_content(content: list[dict[str, Any]]) -> Iterator[str]:
    for element in content:
        if "paragraph" in element:
            p = element["paragraph"]
            st = p.get("paragraphStyle") or {}
            nst = st.get("namedStyleType") or ""
            if isinstance(nst, str) and nst.startswith("HEADING_"):
                t = _paragraph_text(p).strip()
                if t:
                    yield t
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _headings_in_content(cell.get("content", []))


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    parts: list[str] = []
    for pe in paragraph.get("elements", []):
        tr = pe.get("textRun")
        if tr and "content" in tr:
            parts.append(tr["content"])
    return "".join(parts)


def analyze_dissertation(document: dict[str, Any]) -> DissertationMetrics:
    plain = extract_plain_text(document)
    headings = list(iter_heading_texts(document))
    low = [h.lower() for h in headings]

    has_review = any(
        "обзор литературы" in h or "литературный обзор" in h for h in low
    )
    has_results = any("результат" in h for h in low)
    has_discussion = any(
        "обсуждение" in h or "вывод" in h for h in low
    )

    sources = _estimate_sources_count(plain)
    pages = max(1, len(plain) // _CHARS_PER_PAGE_RU)

    notes: list[str] = []
    if sources is None:
        notes.append("Число источников не оценено (маркер списка не найден).")

    return DissertationMetrics(
        approx_pages=pages,
        sources_count=sources,
        has_literature_review=has_review,
        has_results=has_results,
        has_discussion=has_discussion,
        headings_found=headings[:30],
        notes=notes,
    )


def _estimate_sources_count(plain: str) -> int | None:
    lower = plain.lower()
    best = -1
    for m in _BIB_MARKERS:
        pos = lower.rfind(m)
        if pos > best:
            best = pos
    if best < 0:
        return None
    tail = plain[best:]
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    n = 0
    for ln in lines[1:400]:
        if re.match(r"^\d+[\.\)]\s+\S", ln):
            n += 1
        elif re.match(r"^\[\d+\]\s*\S", ln):
            n += 1
    return n if n > 0 else None
