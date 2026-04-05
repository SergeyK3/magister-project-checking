"""Разбор промежуточного отчёта магистранта (таблицы + эвристики по подписям строк)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from magister_checking.docs_extract import HyperlinkRecord
from magister_checking.docs_tables import extract_tables


def _is_docs_url(url: str) -> bool:
    u = url.lower()
    return "document/d/" in u or "docs.google.com/document" in u


def _first_doc_link(links: list[HyperlinkRecord]) -> str | None:
    for h in links:
        if _is_docs_url(h.url):
            return h.url
    return None


@dataclass
class ParsedReport:
    """Поля, извлечённые из отчёта (Прил. 1, эвристики)."""

    lkb_status: str  # да / нет / ?
    lkb_url: str | None
    dissertation_url: str | None
    review_article_url: str | None
    review_article_note: str
    results_article_url: str | None
    raw_labels_hit: list[str] = field(default_factory=list)


# (attr_suffix, keyword tuples for label column — достаточно одного вхождения)
_ROW_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("lkb", ("лкб", "биоэтик", "локальн", "комисси")),
    ("dissertation", ("диссертац",)),
    ("review_article", ("обзор", "статья")),
    ("review_article_alt", ("статья", "обзор")),
    ("results_article", ("результат", "статья")),
    ("results_article_alt", ("статья", "результат")),
]


def parse_intermediate_report(document: dict[str, Any]) -> ParsedReport:
    out = ParsedReport(
        lkb_status="?",
        lkb_url=None,
        dissertation_url=None,
        review_article_url=None,
        review_article_note="",
        results_article_url=None,
    )

    for table in extract_tables(document):
        for row in table:
            if not row:
                continue
            label = row[0].text.lower().strip()
            if not label:
                continue
            value_cells = row[1:] if len(row) > 1 else row
            row_links: list[HyperlinkRecord] = []
            row_text = "".join(c.text for c in row)
            for c in value_cells:
                row_links.extend(c.links)

            matched_rule = None
            for rule_name, kws in _ROW_RULES:
                if all(k in label for k in kws):
                    matched_rule = rule_name
                    break
            if not matched_rule:
                if any(k in label for k in ("лкб", "биоэтик")):
                    matched_rule = "lkb"
                elif "диссертац" in label:
                    matched_rule = "dissertation"
                elif "обзор" in label and "стать" in label:
                    matched_rule = "review_article"
                elif "результат" in label and "стать" in label:
                    matched_rule = "results_article"

            if not matched_rule:
                continue

            out.raw_labels_hit.append(label[:80])
            doc_link = _first_doc_link(row_links)

            if matched_rule == "lkb":
                out.lkb_url = doc_link or out.lkb_url
                low_txt = row_text.lower()
                if re.search(r"\bнет\b", low_txt) and not re.search(r"\bесть\b", low_txt):
                    out.lkb_status = "нет"
                elif re.search(r"\bесть\b", low_txt) or doc_link:
                    out.lkb_status = "да"
                else:
                    out.lkb_status = "?"
            elif matched_rule == "dissertation":
                if doc_link:
                    out.dissertation_url = doc_link
            elif matched_rule in ("review_article", "review_article_alt"):
                if doc_link and not out.review_article_url:
                    out.review_article_url = doc_link
                if not out.review_article_note:
                    out.review_article_note = _note_from_text(row_text)
            elif matched_rule in ("results_article", "results_article_alt"):
                if doc_link:
                    out.results_article_url = doc_link

    if out.lkb_status == "?" and out.lkb_url:
        out.lkb_status = "да"

    return out


def _note_from_text(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t[:200] if t else ""
