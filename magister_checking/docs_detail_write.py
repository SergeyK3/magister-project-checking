"""
Заполнение детального Google Doc: текст заголовка HEADING_1 (ФИО) и тело секции до следующего H1.

Операции batchUpdate выполняются от конца документа к началу, чтобы не сбивать индексы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_MULT_NEWLINE = re.compile(r"\n{2,}")


def _paragraph_text_run_span(element: dict[str, Any]) -> tuple[int, int] | None:
    """Диапазон [start, end) по textRun в структурном элементе с ключом paragraph."""
    para = element.get("paragraph")
    if not para:
        return None
    mins: int | None = None
    maxe: int | None = None
    for pe in para.get("elements", []):
        if "textRun" not in pe:
            continue
        si = pe.get("startIndex")
        ei = pe.get("endIndex")
        if si is not None and ei is not None:
            mins = si if mins is None else min(mins, si)
            maxe = ei if maxe is None else max(maxe, ei)
    if mins is None or maxe is None or maxe <= mins:
        return None
    return mins, maxe


def _is_heading_1(element: dict[str, Any]) -> bool:
    p = element.get("paragraph")
    if not p:
        return False
    style = (p.get("paragraphStyle") or {}).get("namedStyleType")
    return style == "HEADING_1"


@dataclass(frozen=True)
class H1SectionPlan:
    """Один заголовок верхнего уровня и интервал тела до следующего H1."""

    h1_element: dict[str, Any]
    body_start: int
    body_end: int


def plan_top_level_h1_sections(document: dict[str, Any]) -> list[H1SectionPlan]:
    """
    Только элементы body.content верхнего уровня (не таблицы внутри ячеек).
    Тело секции: [body_start, body_end) — от конца блока H1 до начала следующего H1
    или до конца документа.
    """
    content = document.get("body", {}).get("content", [])
    if not content:
        return []

    h1_elements: list[dict[str, Any]] = []
    for el in content:
        if _is_heading_1(el):
            h1_elements.append(el)

    if not h1_elements:
        return []

    doc_end = content[-1].get("endIndex")
    if doc_end is None:
        return []

    out: list[H1SectionPlan] = []
    for i, el in enumerate(h1_elements):
        body_start = el.get("endIndex")
        if body_start is None:
            continue
        if i + 1 < len(h1_elements):
            nxt = h1_elements[i + 1]
            body_end = nxt.get("startIndex")
        else:
            body_end = doc_end
        if body_end is None:
            continue
        if body_end < body_start:
            body_end = body_start
        out.append(H1SectionPlan(h1_element=el, body_start=body_start, body_end=body_end))
    return out


def plan_non_empty_h1_sections(document: dict[str, Any]) -> list[H1SectionPlan]:
    """
    Как plan_top_level_h1_sections, но:
    - берёт только H1 с непустым текстом
    - body_end ищет по следующему НЕпустому H1 (пропускает пустые H1, которые встречаются в некоторых шаблонах)
    """
    content = document.get("body", {}).get("content", [])
    if not content:
        return []

    # Собираем H1 элементы и их текст.
    h1_all: list[tuple[dict[str, Any], str]] = []
    for el in content:
        if not _is_heading_1(el):
            continue
        t = paragraph_plain_text(el)
        h1_all.append((el, t))

    h1 = [(el, t) for el, t in h1_all if t.strip()]
    if not h1:
        return []

    doc_end = content[-1].get("endIndex")
    if doc_end is None:
        return []

    out: list[H1SectionPlan] = []
    for i, (el, _t) in enumerate(h1):
        body_start = el.get("endIndex")
        if body_start is None:
            continue
        if i + 1 < len(h1):
            nxt = h1[i + 1][0]
            body_end = nxt.get("startIndex")
        else:
            body_end = doc_end
        if body_end is None:
            continue
        if body_end < body_start:
            body_end = body_start
        out.append(H1SectionPlan(h1_element=el, body_start=body_start, body_end=body_end))
    return out


def find_first_table_in_range(
    document: dict[str, Any], *, start: int, end: int
) -> dict[str, Any] | None:
    """Первая таблица верхнего уровня body.content, попадающая в [start, end)."""
    content = document.get("body", {}).get("content", [])
    for el in content:
        if "table" not in el:
            continue
        si = el.get("startIndex")
        if si is None:
            continue
        if start <= si < end:
            return el["table"]
    return None


def delete_body_suffix_from_index(
    *,
    document_id: str,
    docs_service: Any,
    start_index: int,
) -> bool:
    """
    Удаляет фрагмент тела документа [start_index, end) до конца документа.
    Несколько кандидатов end — как в rebuild_detail_doc (API иногда отклоняет границу).
    Возвращает True, если удаление выполнено.
    """
    doc = docs_service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    if not content:
        return False
    doc_end = content[-1].get("endIndex")
    if doc_end is None or doc_end <= start_index + 1:
        return False
    candidates = [doc_end - 1, doc_end - 2, doc_end - 10, doc_end - 50, doc_end - 100]
    last_err: Exception | None = None
    for end in candidates:
        if end <= start_index + 1:
            continue
        try:
            docs_service.documents().batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "deleteContentRange": {
                                "range": {"startIndex": start_index, "endIndex": end},
                            }
                        }
                    ]
                },
            ).execute()
            return True
        except Exception as e:  # noqa: BLE001
            last_err = e
    if last_err is not None:
        raise last_err
    return False


def _normalize_cell_like_text(text: str) -> str:
    t = text if text is not None else ""
    if not t.strip():
        return " "
    return t


def _append_replace_range(
    requests: list[dict[str, Any]],
    start: int,
    end: int,
    new_text: str,
) -> None:
    """Заменяет содержимое [start, end) на new_text (как в ячейках таблицы — оставляем 1 символ)."""
    text = _normalize_cell_like_text(new_text)
    if end <= start:
        requests.append({"insertText": {"location": {"index": start}, "text": text}})
        return
    delete_end = end - 1
    if delete_end > start:
        requests.append(
            {
                "deleteContentRange": {
                    "range": {"startIndex": start, "endIndex": delete_end},
                }
            }
        )
    requests.append({"insertText": {"location": {"index": start}, "text": text}})


def fill_h1_sections(
    *,
    document_id: str,
    docs_service: Any,
    sections: list[H1SectionPlan],
    heading_texts: list[str],
    body_texts: list[str],
) -> None:
    """
    Для каждой i-й секции: заголовок H1 → heading_texts[i], тело → body_texts[i].
    Длины списков должны совпадать и не превышать число секций в шаблоне.
    """
    n = len(heading_texts)
    if len(body_texts) != n:
        raise ValueError("heading_texts и body_texts должны быть одной длины.")
    if n > len(sections):
        raise ValueError(
            f"В документе {len(sections)} секций H1, передано {n} блоков данных."
        )

    # От последней секции к первой; внутри секции сначала тело (ниже по документу), потом заголовок
    requests: list[dict[str, Any]] = []
    for i in range(n - 1, -1, -1):
        sec = sections[i]
        body = body_texts[i]
        title = heading_texts[i]

        _append_replace_range(requests, sec.body_start, sec.body_end, body + "\n")

        hspan = _paragraph_text_run_span(sec.h1_element)
        if hspan:
            hs, he = hspan
            _append_replace_range(requests, hs, he, title + "\n")

    if requests:
        docs_service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()


def fill_h1_headings(
    *,
    document_id: str,
    docs_service: Any,
    sections: list[H1SectionPlan],
    heading_texts: list[str],
) -> None:
    """Обновляет только текст H1 (не трогает тело секций с таблицами)."""
    n = len(heading_texts)
    if n > len(sections):
        raise ValueError(
            f"В документе {len(sections)} секций H1, передано {n} заголовков."
        )
    requests: list[dict[str, Any]] = []
    for i in range(n - 1, -1, -1):
        sec = sections[i]
        title = heading_texts[i]
        hspan = _paragraph_text_run_span(sec.h1_element)
        if hspan:
            hs, he = hspan
            _append_replace_range(requests, hs, he, title + "\n")
    if requests:
        docs_service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()


def iter_paragraph_elements_in_range(
    document: dict[str, Any], *, start: int, end: int
) -> list[dict[str, Any]]:
    """Все элементы body.content с paragraph в [start, end)."""
    out: list[dict[str, Any]] = []
    for el in document.get("body", {}).get("content", []):
        si = el.get("startIndex")
        if si is None:
            continue
        if si < start or si >= end:
            continue
        if "paragraph" in el:
            out.append(el)
    return out


def paragraph_plain_text(element: dict[str, Any]) -> str:
    p = element.get("paragraph") or {}
    parts: list[str] = []
    for pe in p.get("elements", []) or []:
        tr = pe.get("textRun")
        if tr and "content" in tr:
            parts.append(tr["content"])
    return "".join(parts).strip()


def paragraph_raw_text(element: dict[str, Any]) -> str:
    """Текст абзаца из textRun без .strip() (сохраняет внутренние и краевые \\n)."""
    p = element.get("paragraph") or {}
    parts: list[str] = []
    for pe in p.get("elements", []) or []:
        tr = pe.get("textRun")
        if tr and "content" in tr:
            parts.append(tr["content"])
    return "".join(parts)


def collapse_duplicate_newlines_in_plain_text(text: str) -> str:
    """Все последовательности из 2+ \\n заменяет на один \\n."""
    return _MULT_NEWLINE.sub("\n", text or "")


def collapse_duplicate_newlines_in_all_h1_bodies(
    *,
    document_id: str,
    docs_service: Any,
) -> None:
    """
    Детальный отчёт (весь Doc): для каждой непустой секции H1 снизу вверх схлопывает
    подряд идущие \\n в тексте абзацев тела (индексы пересчитываются после каждой секции).
    """
    d0 = docs_service.documents().get(documentId=document_id).execute()
    sections = plan_non_empty_h1_sections(d0)
    if not sections:
        return
    for i in range(len(sections) - 1, -1, -1):
        d = docs_service.documents().get(documentId=document_id).execute()
        secs = plan_non_empty_h1_sections(d)
        if i >= len(secs):
            continue
        sec = secs[i]
        collapse_duplicate_newlines_in_section_body(
            document_id=document_id,
            docs_service=docs_service,
            document=d,
            body_start=sec.body_start,
            body_end=sec.body_end,
        )


def collapse_duplicate_newlines_in_section_body(
    *,
    document_id: str,
    docs_service: Any,
    document: dict[str, Any],
    body_start: int,
    body_end: int,
) -> None:
    """
    В пределах [body_start, body_end) проходит абзацы сверху вниз: если в textRun несколько \\n подряд,
    схлопывает до одного (через замену текста абзаца).
    """
    paras = iter_paragraph_elements_in_range(document, start=body_start, end=body_end)
    requests: list[dict[str, Any]] = []
    for el in reversed(list(paras)):
        raw = paragraph_raw_text(el)
        if not _MULT_NEWLINE.search(raw):
            continue
        fixed = collapse_duplicate_newlines_in_plain_text(raw).rstrip("\n")
        if not fixed.strip():
            continue
        replace_paragraph_text(requests=requests, element=el, new_text=fixed)
    if requests:
        docs_service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()


def replace_paragraph_text(
    *,
    requests: list[dict[str, Any]],
    element: dict[str, Any],
    new_text: str,
) -> None:
    """Заменяет текст абзаца (по span textRun) на new_text + \\n."""
    span = _paragraph_text_run_span(element)
    if not span:
        return
    s, e = span
    _append_replace_range(requests, s, e, new_text + "\n")


def squash_empty_paragraphs_in_range(
    *,
    document: dict[str, Any],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """
    Возвращает список диапазонов [startIndex, endIndex) для удаления пустых абзацев в диапазоне.
    Удаляем до endIndex-1, чтобы не схлопнуть структуру документа некорректно.
    """
    spans: list[tuple[int, int]] = []
    for el in iter_paragraph_elements_in_range(document, start=start, end=end):
        txt = paragraph_plain_text(el)
        if txt.strip():
            continue
        si = el.get("startIndex")
        ei = el.get("endIndex")
        if si is None or ei is None:
            continue
        if ei <= si + 1:
            continue
        spans.append((si, ei - 1))
    return spans
