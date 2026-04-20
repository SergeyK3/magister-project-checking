"""Метрики по документу диссертации (страницы, источники, оформление)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from googleapiclient.http import MediaIoBaseDownload
from docx import Document  # type: ignore[import-untyped]
from docx.enum.text import WD_LINE_SPACING  # type: ignore[import-untyped]

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
    formatting_compliance: bool | None
    font_size_14_ratio: float | None
    times_new_roman_ratio: float | None
    single_spacing_ratio: float | None
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


def _iter_paragraphs_in_content(content: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for element in content:
        if "paragraph" in element:
            yield element["paragraph"]
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _iter_paragraphs_in_content(cell.get("content", []))


def iter_paragraphs(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Итерирует все paragraph-элементы документа, включая таблицы."""

    content = document.get("body", {}).get("content", [])
    yield from _iter_paragraphs_in_content(content)


def _count_styled_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _normalize_font_family(name: str | None) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _is_times_new_roman(name: str | None) -> bool:
    return _normalize_font_family(name) == "times new roman"


def _is_14_pt(magnitude: float | int | None, unit: str | None = None) -> bool:
    if magnitude is None:
        return False
    if unit and str(unit).upper() not in {"PT", ""}:
        return False
    return abs(float(magnitude) - 14.0) <= 0.1


def _is_single_spacing_percent(value: float | int | None) -> bool:
    if value is None:
        return True
    return abs(float(value) - 100.0) <= 0.5


def _safe_ratio(part: int, total: int) -> float | None:
    if total <= 0:
        return None
    return part / total


def _formatting_compliance(
    *,
    font_size_ratio: float | None,
    font_family_ratio: float | None,
    line_spacing_ratio: float | None,
) -> bool | None:
    ratios = (font_size_ratio, font_family_ratio, line_spacing_ratio)
    if any(r is None for r in ratios):
        return None
    return all((r or 0.0) > 0.95 for r in ratios)


def _google_named_styles(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    styles = (document.get("namedStyles") or {}).get("styles", [])
    out: dict[str, dict[str, Any]] = {}
    for style in styles:
        name = str(style.get("namedStyleType") or "").strip()
        if name:
            out[name] = style
    return out


def _google_effective_text_style(
    *,
    named_styles: dict[str, dict[str, Any]],
    paragraph: dict[str, Any],
    run_style: dict[str, Any] | None,
) -> dict[str, Any]:
    paragraph_style = paragraph.get("paragraphStyle") or {}
    named_style = str(paragraph_style.get("namedStyleType") or "NORMAL_TEXT")
    base = dict((named_styles.get(named_style) or {}).get("textStyle") or {})
    if run_style:
        base.update(run_style)
    return base


def _google_effective_paragraph_style(
    *,
    named_styles: dict[str, dict[str, Any]],
    paragraph: dict[str, Any],
) -> dict[str, Any]:
    paragraph_style = paragraph.get("paragraphStyle") or {}
    named_style = str(paragraph_style.get("namedStyleType") or "NORMAL_TEXT")
    base = dict((named_styles.get(named_style) or {}).get("paragraphStyle") or {})
    base.update(paragraph_style)
    return base


def _analyze_google_doc_formatting(document: dict[str, Any]) -> tuple[bool | None, float | None, float | None, float | None]:
    named_styles = _google_named_styles(document)
    total_chars = 0
    font_size_chars = 0
    font_family_chars = 0
    total_paragraphs = 0
    single_spacing_paragraphs = 0

    for paragraph in iter_paragraphs(document):
        text = _paragraph_text(paragraph)
        if text.strip():
            total_paragraphs += 1
            paragraph_style = _google_effective_paragraph_style(
                named_styles=named_styles,
                paragraph=paragraph,
            )
            if _is_single_spacing_percent(paragraph_style.get("lineSpacing")):
                single_spacing_paragraphs += 1

        for element in paragraph.get("elements", []):
            text_run = element.get("textRun") or {}
            content = str(text_run.get("content") or "")
            chars = _count_styled_chars(content)
            if chars <= 0:
                continue
            style = _google_effective_text_style(
                named_styles=named_styles,
                paragraph=paragraph,
                run_style=text_run.get("textStyle") or {},
            )
            total_chars += chars
            font_size = style.get("fontSize") or {}
            if _is_14_pt(font_size.get("magnitude"), font_size.get("unit")):
                font_size_chars += chars
            family = (style.get("weightedFontFamily") or {}).get("fontFamily") or style.get("fontFamily")
            if _is_times_new_roman(family):
                font_family_chars += chars

    font_size_ratio = _safe_ratio(font_size_chars, total_chars)
    font_family_ratio = _safe_ratio(font_family_chars, total_chars)
    line_spacing_ratio = _safe_ratio(single_spacing_paragraphs, total_paragraphs)
    return (
        _formatting_compliance(
            font_size_ratio=font_size_ratio,
            font_family_ratio=font_family_ratio,
            line_spacing_ratio=line_spacing_ratio,
        ),
        font_size_ratio,
        font_family_ratio,
        line_spacing_ratio,
    )


def _iter_docx_paragraphs(container: Any) -> Iterator[Any]:
    for paragraph in getattr(container, "paragraphs", []):
        yield paragraph
    for table in getattr(container, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_docx_paragraphs(cell)


def _docx_font_size_pt(run: Any, paragraph: Any, document: Any) -> float | None:
    normal_style = document.styles["Normal"] if "Normal" in document.styles else None
    candidates = [
        getattr(getattr(run, "font", None), "size", None),
        getattr(getattr(getattr(run, "style", None), "font", None), "size", None),
        getattr(getattr(getattr(paragraph, "style", None), "font", None), "size", None),
        getattr(getattr(normal_style, "font", None), "size", None),
    ]
    for value in candidates:
        if value is not None:
            try:
                return float(value.pt)
            except Exception:  # noqa: BLE001
                continue
    return None


def _docx_font_name(run: Any, paragraph: Any, document: Any) -> str | None:
    normal_style = document.styles["Normal"] if "Normal" in document.styles else None
    candidates = [
        getattr(getattr(run, "font", None), "name", None),
        getattr(getattr(getattr(run, "style", None), "font", None), "name", None),
        getattr(getattr(getattr(paragraph, "style", None), "font", None), "name", None),
        getattr(getattr(normal_style, "font", None), "name", None),
    ]
    for value in candidates:
        if value:
            return str(value)
    return None


def _docx_is_single_spacing(paragraph: Any, document: Any) -> bool:
    normal_style = document.styles["Normal"] if "Normal" in document.styles else None
    fmt_candidates = [
        getattr(paragraph, "paragraph_format", None),
        getattr(getattr(paragraph, "style", None), "paragraph_format", None),
        getattr(normal_style, "paragraph_format", None),
    ]
    for fmt in fmt_candidates:
        if fmt is None:
            continue
        rule = getattr(fmt, "line_spacing_rule", None)
        if rule == WD_LINE_SPACING.SINGLE:
            return True
        value = getattr(fmt, "line_spacing", None)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return abs(float(value) - 1.0) <= 0.05
        return False
    return True


def _analyze_docx_formatting(document: Any) -> tuple[bool | None, float | None, float | None, float | None]:
    total_chars = 0
    font_size_chars = 0
    font_family_chars = 0
    total_paragraphs = 0
    single_spacing_paragraphs = 0

    for paragraph in _iter_docx_paragraphs(document):
        if (paragraph.text or "").strip():
            total_paragraphs += 1
            if _docx_is_single_spacing(paragraph, document):
                single_spacing_paragraphs += 1

        for run in getattr(paragraph, "runs", []):
            text = str(getattr(run, "text", "") or "")
            chars = _count_styled_chars(text)
            if chars <= 0:
                continue
            total_chars += chars
            if _is_14_pt(_docx_font_size_pt(run, paragraph, document)):
                font_size_chars += chars
            if _is_times_new_roman(_docx_font_name(run, paragraph, document)):
                font_family_chars += chars

    font_size_ratio = _safe_ratio(font_size_chars, total_chars)
    font_family_ratio = _safe_ratio(font_family_chars, total_chars)
    line_spacing_ratio = _safe_ratio(single_spacing_paragraphs, total_paragraphs)
    return (
        _formatting_compliance(
            font_size_ratio=font_size_ratio,
            font_family_ratio=font_family_ratio,
            line_spacing_ratio=line_spacing_ratio,
        ),
        font_size_ratio,
        font_family_ratio,
        line_spacing_ratio,
    )


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
    formatting_compliance, font_size_ratio, font_family_ratio, line_spacing_ratio = (
        _analyze_google_doc_formatting(document)
    )

    notes: list[str] = []
    if sources is None:
        notes.append("Число источников не оценено (маркер списка не найден).")
    if formatting_compliance is None:
        notes.append("Соответствие оформлению не оценено (не хватило данных по стилям).")

    return DissertationMetrics(
        approx_pages=pages,
        pdf_pages=None,
        sources_count=sources,
        review_pages=review_pages,
        review_sources_count=review_sources,
        has_literature_review=has_review,
        has_results=has_results,
        has_discussion=has_discussion,
        formatting_compliance=formatting_compliance,
        font_size_14_ratio=font_size_ratio,
        times_new_roman_ratio=font_family_ratio,
        single_spacing_ratio=line_spacing_ratio,
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
    formatting_compliance, font_size_ratio, font_family_ratio, line_spacing_ratio = (
        _analyze_docx_formatting(doc)
    )

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
    if formatting_compliance is None:
        notes.append("Соответствие оформлению не оценено (не хватило данных по стилям).")

    return DissertationMetrics(
        approx_pages=approx_pages,
        pdf_pages=None,
        sources_count=sources,
        review_pages=review_pages,
        review_sources_count=review_sources,
        has_literature_review=has_review,
        has_results=has_results,
        has_discussion=has_discussion,
        formatting_compliance=formatting_compliance,
        font_size_14_ratio=font_size_ratio,
        times_new_roman_ratio=font_family_ratio,
        single_spacing_ratio=line_spacing_ratio,
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
