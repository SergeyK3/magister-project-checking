"""Метрики по документу диссертации (заголовки, грубая оценка страниц и списка литературы)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from googleapiclient.http import MediaIoBaseDownload
from docx import Document  # type: ignore[import-untyped]

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
    pdf_pages: int | None
    sources_count: int | None
    review_pages: int | None
    review_sources_count: int | None
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
    review_pages, review_sources = _estimate_review_metrics(plain)
    pages = max(1, len(plain) // _CHARS_PER_PAGE_RU)

    notes: list[str] = []
    if sources is None:
        notes.append("Число источников не оценено (маркер списка не найден).")

    return DissertationMetrics(
        approx_pages=pages,
        pdf_pages=None,
        sources_count=sources,
        review_pages=review_pages,
        review_sources_count=review_sources,
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


def count_pdf_pages_via_drive_export(*, drive_service: Any, file_id: str) -> int | None:
    """
    Экспортирует Google Doc в PDF через Drive API и считает страницы по маркерам в PDF.
    Без внешних зависимостей.
    """
    try:
        req = drive_service.files().export(fileId=file_id, mimeType="application/pdf")
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        pdf = fh.getvalue()
        if not pdf:
            return None
        # Простейшая эвристика: /Type /Page встречается на каждую страницу,
        # а /Type /Pages — на корневой объект, его исключаем.
        pages = pdf.count(b"/Type /Page")
        pages -= pdf.count(b"/Type /Pages")
        return pages if pages > 0 else None
    except Exception:  # noqa: BLE001
        return None


def download_drive_file_bytes(*, drive_service: Any, file_id: str) -> bytes | None:
    """Скачивает файл с Google Drive (alt=media) в память."""
    try:
        req = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        data = fh.getvalue()
        return data if data else None
    except Exception:  # noqa: BLE001
        return None


def _docx_page_count(docx_bytes: bytes) -> int | None:
    """
    Страницы из docProps/app.xml (Word считает страницы при сохранении).
    Это не «рендер» как PDF, но обычно ближе к реальности, чем оценка по символам.
    """
    try:
        import zipfile
        from xml.etree import ElementTree as ET

        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            xml = zf.read("docProps/app.xml")
        root = ET.fromstring(xml)
        # <Pages> может быть в пространстве имён, поэтому ищем по локальному имени
        for el in root.iter():
            if el.tag.endswith("Pages") and (el.text or "").strip().isdigit():
                return int((el.text or "0").strip())
        return None
    except Exception:  # noqa: BLE001
        return None


def analyze_docx_bytes(docx_bytes: bytes) -> DissertationMetrics:
    """
    Анализ Word (.docx):
    - pages: из docProps/app.xml (если есть), иначе оценка по символам
    - review_pages/review_sources: по секции «Обзор литературы» (по heading/text)
    """
    doc = Document(io.BytesIO(docx_bytes))
    paras = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    plain = "\n".join(paras)

    # headings: по стилю Word (Heading 1/2/… или русское "Заголовок")
    headings: list[str] = []
    para_info: list[tuple[str, bool]] = []
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style_name = ""
        try:
            style_name = str(p.style.name or "")
        except Exception:  # noqa: BLE001
            style_name = ""
        is_heading = bool(re.search(r"(?i)\bheading\b", style_name) or "заголов" in style_name.lower())
        if is_heading:
            headings.append(text)
        para_info.append((text, is_heading))

    low_heads = [h.lower() for h in headings]
    has_review = any("обзор литературы" in h or "литературный обзор" in h for h in low_heads)
    has_results = any("результат" in h for h in low_heads)
    has_discussion = any("обсуждение" in h or "вывод" in h for h in low_heads)

    sources = _estimate_sources_count(plain)

    # pages_total
    pages_total = _docx_page_count(docx_bytes)
    approx_pages = pages_total if pages_total and pages_total > 0 else max(1, len(plain) // _CHARS_PER_PAGE_RU)

    # review section: find heading then accumulate until next heading
    review_text = ""
    start_idx = None
    for i, (t, is_h) in enumerate(para_info):
        if is_h and ("обзор литературы" in t.lower() or "литературный обзор" in t.lower()):
            start_idx = i + 1
            break
    if start_idx is not None:
        buf: list[str] = []
        for t, is_h in para_info[start_idx:]:
            if is_h:
                break
            buf.append(t)
        review_text = "\n".join(buf).strip()

    review_pages = None
    review_sources = None
    if review_text:
        # оценка страниц секции: пропорция по символам относительно общего текста и page_count
        if pages_total and pages_total > 0 and len(plain) > 0:
            ratio = len(review_text) / max(1, len(plain))
            review_pages = max(1, round(ratio * pages_total))
        else:
            review_pages = max(1, len(review_text) // _CHARS_PER_PAGE_RU)

        lines = [ln.strip() for ln in review_text.splitlines() if ln.strip()]
        n = 0
        for ln in lines[:1200]:
            if re.match(r"^\d+[\.\)]\s+\S", ln):
                n += 1
            elif re.match(r"^\[\d+\]\s*\S", ln):
                n += 1
        review_sources = n if n > 0 else None

    notes: list[str] = []
    if pages_total is None:
        notes.append("Страницы в docx не найдены (docProps/app.xml).")
    if review_text and review_sources is None:
        notes.append("Источники в обзоре не оценены (не найден шаблон нумерации).")

    return DissertationMetrics(
        approx_pages=approx_pages,
        pdf_pages=None,
        sources_count=sources,
        review_pages=review_pages,
        review_sources_count=review_sources,
        has_literature_review=has_review,
        has_results=has_results,
        has_discussion=has_discussion,
        headings_found=headings[:30],
        notes=notes,
    )


def _estimate_review_metrics(plain: str) -> tuple[int | None, int | None]:
    """
    Пытается выделить секцию «Обзор литературы» по plain text и оценить:
    - страницы секции (по _CHARS_PER_PAGE_RU)
    - число источников в секции (по нумерованным строкам)
    """
    low = plain.lower()
    start = low.find("обзор литературы")
    if start < 0:
        start = low.find("литературный обзор")
    if start < 0:
        return None, None
    # Конец: первое вхождение типичных следующих разделов после обзора
    end = len(plain)
    for marker in ("результат", "обсуждение", "заключение", "вывод", "глава 2", "глава ii"):
        pos = low.find(marker, start + 50)
        if 0 <= pos < end:
            end = pos
    seg = plain[start:end]
    pages = max(1, len(seg) // _CHARS_PER_PAGE_RU) if seg.strip() else None
    lines = [ln.strip() for ln in seg.splitlines() if ln.strip()]
    n = 0
    for ln in lines[:800]:
        if re.match(r"^\d+[\.\)]\s+\S", ln):
            n += 1
        elif re.match(r"^\[\d+\]\s*\S", ln):
            n += 1
    return pages, (n if n > 0 else None)
