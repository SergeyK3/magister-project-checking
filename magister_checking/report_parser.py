"""Разбор промежуточного отчёта магистранта (таблицы + эвристики по подписям строк)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from magister_checking.docs_extract import HyperlinkRecord
from magister_checking.docs_extract import extract_plain_text
from magister_checking.docs_tables import extract_tables


def _is_docs_url(url: str) -> bool:
    u = url.lower()
    return "document/d/" in u or "docs.google.com/document" in u


def _first_doc_link(links: list[HyperlinkRecord]) -> str | None:
    for h in links:
        if _is_docs_url(h.url):
            return h.url
    return None


def _join_value_cell_texts(value_cells: list[Any]) -> str:
    parts: list[str] = []
    for c in value_cells:
        t = (c.text or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts).strip()


def _label_key(primary_cell: str) -> str:
    """Текст подписи до «:» / тире — одна ячейка может содержать «Подпись: значение»."""
    t = (primary_cell or "").strip()
    if not t:
        return ""
    for sep in (":", "—", "–", "‑"):
        if sep in t:
            t = t.split(sep, 1)[0]
            break
    return t.lower().strip()


def _row_field_value(row: list[Any], value_cells: list[Any]) -> str:
    """Значение из ячеек справа от подписи или после «:» в первой ячейке (как в шаблоне отчёта)."""
    v = _join_value_cell_texts(value_cells)
    if v:
        return v
    first = (row[0].text or "").strip()
    for sep in (":", "—", "–", "‑"):
        if sep in first:
            _left, right = first.split(sep, 1)
            r = right.strip()
            if r:
                return r
    return ""


@dataclass
class ParsedReport:
    """Поля, извлечённые из отчёта (Прил. 1, эвристики).

    workplace, job_title, supervisor, report_doc_url — для 7-колоночной сводной таблицы.
    """

    lkb_status: str  # да / нет / ?
    lkb_url: str | None
    dissertation_url: str | None
    review_article_url: str | None
    review_article_note: str
    results_article_url: str | None
    project_folder_url: str | None = None
    workplace: str = ""
    job_title: str = ""
    supervisor: str = ""
    report_doc_url: str | None = None
    declared_pages_total: int | None = None
    declared_pages_review: int | None = None
    declared_sources_review: int | None = None
    declared_formatting_ok: bool | None = None
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


_DOC_URL_IN_ROW = re.compile(
    r"https://docs\.google\.com/document/d/[a-zA-Z0-9_-]+(?:/[^\s\)\]\"]*)?",
    re.IGNORECASE,
)

_LINE_WORKPLACE = re.compile(r"(?im)^\s*место\s+работы\s*[:\-–—]\s*(.*)\s*$")
_LINE_JOB = re.compile(r"(?im)^\s*должност[ььи]\s*[:\-–—]\s*(.*)\s*$")
_LINE_SUPERVISOR = re.compile(r"(?im)^\s*научн\w*\s+руководител\w*\s*[:\-–—]\s*(.*)\s*$")
_LINE_DISS = re.compile(r"(?im)^\s*диссертац\w*\s*[:\-–—]\s*(.*)\s*$")
_LINE_LKB = re.compile(
    r"(?im)^\s*(наличие\s+заключ\w*.*лкб|лкб|заключ\w*.*лкб|лкб.*биоэтик|заключ\w*.*биоэтик)\b.*[:\-–—]\s*(.*)\s*$"
)
_LINE_REVIEW_ART = re.compile(r"(?im)^\s*стать\w*.*обзор\w*.*\s*[:\-–—]\s*(.*)\s*$")
_LINE_RESULTS_ART = re.compile(r"(?im)^\s*(подготовлен\w*\s+стать\w*.*результат\w*|стать\w*.*результат\w*)\s*[:\-–—]\s*(.*)\s*$")
_LINE_PAGES_TOTAL = re.compile(
    r"(?im)^\s*в\s*т\.?\s*ч\.?\s*всег\w*\s+страниц\w*.*[:\-–—]\s*(\d+)\s*[,.;]?\s*$"
)
_LINE_PAGES_REVIEW = re.compile(
    r"(?im)^\s*в\s*т\.?\s*ч\.?\s*обзор\w*.*страниц\w*.*[:\-–—]\s*(\d+)\s*[,.;]?\s*$"
)
_LINE_SOURCES_REVIEW = re.compile(
    r"(?im)^\s*в\s*т\.?\s*ч\.?\s*.*источник\w*.*[:\-–—]\s*(\d+)\s*[,.;]?\s*$"
)
_DRIVE_URL = re.compile(r"https?://drive\.google\.com/[^\s\)\]\"]+", re.IGNORECASE)
_DRIVE_FOLDER_URL = re.compile(r"https?://drive\.google\.com/drive/folders/[a-zA-Z0-9_-]+", re.IGNORECASE)


def _fill_from_plain_text(out: ParsedReport, document: dict[str, Any]) -> None:
    """
    В части шаблонов «Промежуточный отчёт» поля идут не таблицей, а обычными абзацами:
      Место работы: ...
      Должность: ...
      Научный руководитель: ...
    Поэтому добавляем fallback-разбор по plain text всего документа.
    """
    try:
        plain = extract_plain_text(document)
    except Exception:  # noqa: BLE001
        return

    # Нормализуем переносы строк и убираем повторяющиеся пробелы.
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in plain.splitlines()]
    lines = [ln for ln in lines if ln]

    def _pick(pattern: re.Pattern[str]) -> str | None:
        for i, ln in enumerate(lines):
            m = pattern.match(ln)
            if not m:
                continue
            val = (m.group(1) or "").strip()
            if val:
                return val
            # Если после двоеточия пусто — пробуем следующую строку.
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt:
                    return nxt
        return None

    if not out.workplace:
        v = _pick(_LINE_WORKPLACE)
        if v:
            out.workplace = v
    if not out.job_title:
        v = _pick(_LINE_JOB)
        if v:
            out.job_title = v
    if not out.supervisor:
        v = _pick(_LINE_SUPERVISOR)
        if v:
            out.supervisor = v

    if out.declared_pages_total is None:
        v = _pick(_LINE_PAGES_TOTAL)
        if v and v.isdigit():
            out.declared_pages_total = int(v)
    if out.declared_pages_review is None:
        v = _pick(_LINE_PAGES_REVIEW)
        if v and v.isdigit():
            out.declared_pages_review = int(v)
    if out.declared_sources_review is None:
        v = _pick(_LINE_SOURCES_REVIEW)
        if v and v.isdigit():
            out.declared_sources_review = int(v)

    if out.declared_formatting_ok is None:
        for ln in lines:
            low = ln.lower()
            if "times new roman" in low or "кегл" in low or "межстроч" in low:
                if re.search(r"(?i)\bда\b", ln) and not re.search(r"(?i)\bнет\b", ln):
                    out.declared_formatting_ok = True
                elif re.search(r"(?i)\bнет\b", ln):
                    out.declared_formatting_ok = False
                break

    # Ссылки и «да/нет» по ключевым строкам (часто это абзацы, не таблицы)
    plain_full = "\n".join(lines)
    if not out.dissertation_url:
        # более точечно: строка «Диссертация: ...»
        for ln in lines:
            if _LINE_DISS.match(ln):
                mm = _DOC_URL_IN_ROW.search(ln)
                if mm:
                    out.dissertation_url = mm.group(0).rstrip(".,;)")
                    break

    if out.lkb_url is None:
        # В реальных отчётах строка может выглядеть как:
        # "Наличие заключение ЛКБ (локальной комиссии по биоэтике): https://drive.google.com/file/..."
        # Поэтому делаем максимально надёжно: если в строке есть (лкб/биоэтик) и drive-ссылка — берём её,
        # иначе fallback на старую эвристику по _LINE_LKB.
        for i, ln in enumerate(lines):
            low = ln.lower()
            urlm = _DRIVE_URL.search(ln)
            if urlm and ("лкб" in low or "биоэтик" in low):
                out.lkb_url = urlm.group(0).rstrip(".,;)")
                break

    # Папка проекта (иногда там лежит файл статьи по обзору)
    if out.project_folder_url is None:
        for ln in lines:
            if "папк" in ln.lower():
                fm = _DRIVE_FOLDER_URL.search(ln)
                if fm:
                    out.project_folder_url = fm.group(0)
                    break
            m = _LINE_LKB.match(ln)
            if not m:
                continue
            # Ссылка может быть на этой же строке или на следующей.
            urlm = _DRIVE_URL.search(ln)
            if not urlm and i + 1 < len(lines):
                urlm = _DRIVE_URL.search(lines[i + 1])
            if urlm:
                out.lkb_url = urlm.group(0).rstrip(".,;)")
                break

    if out.lkb_status == "?":
        if out.lkb_url:
            out.lkb_status = "да"

    # Статьи: если есть docs-link в строке — считаем ссылкой; если «вставить ссылку» — оставляем пусто
    if not out.review_article_url:
        for ln in lines:
            if _LINE_REVIEW_ART.match(ln):
                mm = _DOC_URL_IN_ROW.search(ln)
                if mm:
                    out.review_article_url = mm.group(0).rstrip(".,;)")
                break

    if not out.results_article_url:
        for ln in lines:
            if _LINE_RESULTS_ART.match(ln):
                mm = _DOC_URL_IN_ROW.search(ln)
                if mm:
                    out.results_article_url = mm.group(0).rstrip(".,;)")
                break


def parse_intermediate_report(document: dict[str, Any]) -> ParsedReport:
    out = ParsedReport(
        lkb_status="?",
        lkb_url=None,
        dissertation_url=None,
        review_article_url=None,
        review_article_note="",
        results_article_url=None,
        workplace="",
        job_title="",
        supervisor="",
        report_doc_url=None,
        declared_pages_total=None,
        declared_pages_review=None,
        declared_sources_review=None,
        declared_formatting_ok=None,
    )

    for table in extract_tables(document):
        for row in table:
            if not row:
                continue
            label_cell = (row[0].text or "").strip()
            if not label_cell:
                continue
            label_key = _label_key(label_cell)
            if not label_key:
                continue
            label = label_cell.lower()
            value_cells = row[1:] if len(row) > 1 else []
            row_links: list[HyperlinkRecord] = []
            row_text = "".join(c.text for c in row)
            for c in row:
                row_links.extend(c.links)
            doc_link = _first_doc_link(row_links)

            if (
                "место" in label_key
                and "работ" in label_key
                and "настоящ" not in label_key
            ):
                v = _row_field_value(row, value_cells)
                if v:
                    out.workplace = v
            elif "должност" in label_key:
                v = _row_field_value(row, value_cells)
                if v:
                    out.job_title = v
            elif ("научн" in label_key or "науч" in label_key) and "руковод" in label_key:
                v = _row_field_value(row, value_cells)
                if v:
                    out.supervisor = v
            elif ("ссыл" in label_key) and (
                "настоящ" in label_key or "этот" in label_key or "текущ" in label_key
            ) and ("документ" in label_key or "отч" in label_key):
                u = doc_link
                if not u:
                    m = _DOC_URL_IN_ROW.search(row_text)
                    if m:
                        u = m.group(0).rstrip(".,;)")
                if u:
                    out.report_doc_url = u

            matched_rule = None
            for rule_name, kws in _ROW_RULES:
                if all(k in label for k in kws):
                    matched_rule = rule_name
                    break
            if not matched_rule:
                if any(k in label for k in ("лкб", "биоэтик")):
                    matched_rule = "lkb"
                elif "диссертац" in label or (
                    "магистерск" in label and "проект" in label
                ):
                    matched_rule = "dissertation"
                elif "обзор" in label and "стать" in label:
                    matched_rule = "review_article"
                elif "результат" in label and "стать" in label:
                    matched_rule = "results_article"

            if not matched_rule:
                continue

            out.raw_labels_hit.append(label[:80])

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

    # Fallback: если поля для сводной не найдены таблицей, ищем в абзацах.
    if not out.workplace or not out.job_title or not out.supervisor:
        _fill_from_plain_text(out, document)

    return out


def _note_from_text(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t[:200] if t else ""
