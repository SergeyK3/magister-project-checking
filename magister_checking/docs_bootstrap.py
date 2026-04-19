"""
Подготовка пустых выходных Google Doc: таблица в сводном документе и заголовки H1 в детальном.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from magister_checking.docs_detail_write import (
    H1SectionPlan,
    find_first_table_in_range,
    paragraph_plain_text,
    plan_top_level_h1_sections,
)
from magister_checking.docs_table_write import (
    fill_table_row,
    find_best_summary_table,
    table_column_count,
)


_SUMMARY_HEADER_7 = [
    "№",
    "ФИО",
    "Группа",
    "Место работы",
    "Должность",
    "Промежуточный отчёт (ссылка)",
    "Научный руководитель",
]


def _body_insert_index(document: dict[str, Any]) -> int:
    content = document.get("body", {}).get("content", [])
    if not content:
        return 1
    last = content[-1]
    end = last.get("endIndex")
    if end is None or end < 2:
        return 1
    return end - 1


def _norm_heading_text(s: str) -> str:
    """Нормализация для сравнения ФИО (NFC, NBSP→пробел)."""
    t = (s or "").replace("\u00a0", " ").strip()
    return unicodedata.normalize("NFC", t)


def _find_last_top_level_paragraph_matching_title(
    content: list[dict[str, Any]], title: str
) -> dict[str, Any] | None:
    """
    С конца ищем верхнеуровневый абзац с текстом, совпадающим с ФИО (после нормализации).

    Нельзя брать просто «последний абзац»: после стиля H1 Docs часто добавляет пустой
    хвостовой абзац — тогда insertTable попадёт не под заголовок с ФИО.
    При полных дубликатах ФИО в списке совпадение с последним в документе — нужный блок.
    """
    want = _norm_heading_text(title)
    if not want:
        return None
    for el in reversed(content):
        p = el.get("paragraph")
        if not p:
            continue
        got = _norm_heading_text(_paragraph_text(p))
        if got == want:
            return el
    return None


def _h1_section_for_title(document: dict[str, Any], title: str) -> H1SectionPlan | None:
    """Секция верхнего уровня H1 с данным ФИО (с конца — актуально при дубликатах имён)."""
    want = _norm_heading_text(title)
    if not want:
        return None
    for sec in reversed(plan_top_level_h1_sections(document)):
        got = _norm_heading_text(paragraph_plain_text(sec.h1_element))
        if got == want:
            return sec
    return None


def _insert_detail_table_requests(*, rows: int, columns: int, index: int) -> dict[str, Any]:
    return {
        "insertTable": {
            "rows": rows,
            "columns": columns,
            "location": {"index": index},
        }
    }


def bootstrap_summary_table_if_missing(
    *,
    document_id: str,
    docs_service: Any,
    num_data_rows: int,
    ncols: int = 7,
) -> None:
    """Вставляет таблицу (1 строка заголовка + num_data_rows), если в документе ещё нет таблицы."""
    doc = docs_service.documents().get(documentId=document_id).execute()
    existing = find_best_summary_table(doc)
    if existing is not None and table_column_count(existing) >= ncols:
        return
    rows_total = 1 + max(1, num_data_rows)
    # Нельзя брать last.endIndex-1, если последний элемент — таблица: индекс попадёт внутрь ячейки.
    docs_service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "insertTable": {
                        "rows": rows_total,
                        "columns": ncols,
                        "endOfSegmentLocation": {"segmentId": ""},
                    }
                }
            ]
        },
    ).execute()
    doc2 = docs_service.documents().get(documentId=document_id).execute()
    tbl = find_best_summary_table(doc2)
    if not tbl:
        raise RuntimeError("Не удалось вставить таблицу в сводный документ.")
    header = (_SUMMARY_HEADER_7 + [""] * ncols)[:ncols]
    fill_table_row(
        document_id=document_id,
        docs_service=docs_service,
        table=tbl,
        row_index=0,
        values=header,
    )


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    parts: list[str] = []
    for pe in paragraph.get("elements", []):
        tr = pe.get("textRun")
        if tr and "content" in tr:
            parts.append(tr["content"])
    return "".join(parts)


def bootstrap_detail_h1_if_missing(
    *,
    document_id: str,
    docs_service: Any,
    n_sections: int,
) -> None:
    """Добавляет в конец документа n_sections абзацев и задаёт им стиль HEADING_1, если H1 не хватает."""
    doc = docs_service.documents().get(documentId=document_id).execute()
    existing = plan_top_level_h1_sections(doc)
    if len(existing) >= n_sections:
        return
    need = n_sections - len(existing)
    idx = _body_insert_index(doc)
    parts: list[str] = []
    for k in range(need):
        parts.append(f"Магистрант {len(existing) + k + 1}\n\n")
    text = "".join(parts)
    docs_service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [{"insertText": {"location": {"index": idx}, "text": text}}]},
    ).execute()
    doc2 = docs_service.documents().get(documentId=document_id).execute()
    content = doc2.get("body", {}).get("content", [])
    requests: list[dict[str, Any]] = []
    for el in content:
        p = el.get("paragraph")
        if not p:
            continue
        t = _paragraph_text(p).strip()
        if not re.match(r"^Магистрант \d+$", t):
            continue
        si = el.get("startIndex")
        ei = el.get("endIndex")
        if si is None or ei is None or ei <= si:
            continue
        # endIndex в structural element обычно включает завершающий \n; для стиля достаточно до ei-1
        end_style = ei - 1 if ei > si + 1 else ei
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": si, "endIndex": end_style},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                }
            }
        )
    if requests:
        docs_service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()


_DETAIL_TABLE_LABELS: list[str] = [
    "Наличие заключение ЛКБ",
    "Ссылка на статью по обзору для публикации",
    "Диссертация (наличие документа)",
    "общее количество страниц в диссертации",
    "в т.ч. обзор литературы, стр",
    "в т.ч. литературных источников",
    "соблюдены ли следующие требования по оформлению диссертации (Шрифт Times New Roman, кегль 14, одинарный межстрочный интервал)",
    "Статья по результатам",
]


def rebuild_detail_doc_with_tables(
    *,
    document_id: str,
    docs_service: Any,
    student_names: list[str],
) -> None:
    """
    Полностью пересобирает детальный Doc в предсказуемый вид:
    TITLE (оставляем как есть) + для каждого магистранта: H1(ФИО) + таблица 8×3 по образцу.
    Это убирает «список имён» и случайные стили HEADING_1 внутри тела.
    """
    doc = docs_service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    # Найдём конец первого абзаца TITLE (или первого paragraph вообще).
    keep_end: int | None = None
    for el in content:
        p = el.get("paragraph")
        if not p:
            continue
        st = (p.get("paragraphStyle") or {}).get("namedStyleType")
        if st == "TITLE":
            keep_end = el.get("endIndex")
            break
        if keep_end is None:
            keep_end = el.get("endIndex")
    if keep_end is None:
        keep_end = 1
    doc_end = content[-1].get("endIndex") if content else 1
    if doc_end and doc_end > keep_end + 1:
        # Удаляем всё после TITLE. Иногда API отвечает "Invalid deletion range" даже на вид корректных границ —
        # тогда пробуем чуть более консервативные endIndex.
        start = keep_end
        candidates = [doc_end - 1, doc_end - 2, doc_end - 10, doc_end - 50, doc_end - 100]
        last_err: Exception | None = None
        for end in candidates:
            if end <= start + 1:
                continue
            try:
                docs_service.documents().batchUpdate(
                    documentId=document_id,
                    body={
                        "requests": [
                            {
                                "deleteContentRange": {
                                    "range": {"startIndex": start, "endIndex": end}
                                }
                            }
                        ]
                    },
                ).execute()
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        if last_err is not None:
            raise last_err

    # Добавляем блоки по одному (с re-fetch), чтобы не гадать по индексам вставок таблиц.
    for name in student_names:
        title = (name or "").strip() or "Магистрант"
        # 1) Вставить ФИО в конец тела документа. Нельзя использовать location.index = endIndex-1
        #    последнего элемента, если это таблица: по правилам API текст вставляется в абзац,
        #    и индекс оказывается внутри последней ячейки — следующее ФИО попадает в чужую таблицу.
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "text": f"{title}\n",
                            "endOfSegmentLocation": {"segmentId": ""},
                        }
                    }
                ]
            },
        ).execute()
        # 2) HEADING_1 на абзац, который только что добавили (= последний верхнеуровневый paragraph).
        d2 = docs_service.documents().get(documentId=document_id).execute()
        content2 = d2.get("body", {}).get("content", [])
        h_el = _find_last_top_level_paragraph_matching_title(content2, title)
        if not h_el or h_el.get("startIndex") is None or h_el.get("endIndex") is None:
            raise RuntimeError(
                f"Детальный Doc: после вставки ФИО не найден абзац с текстом {title!r} "
                f"(ищем с конца документа, не «просто последний абзац» — см. пустой хвост после H1)."
            )
        si = h_el["startIndex"]
        ei = h_el["endIndex"]
        end_style = ei - 1 if ei > si + 1 else ei
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {
                        "updateParagraphStyle": {
                            "range": {"startIndex": si, "endIndex": end_style},
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                            "fields": "namedStyleType",
                        }
                    }
                ]
            },
        ).execute()

        # 3) Таблица строго в теле секции этого H1 (по plan_top_level_h1_sections), а не «последняя в документе».
        d3 = docs_service.documents().get(documentId=document_id).execute()
        sec3 = _h1_section_for_title(d3, title)
        if sec3 is None:
            raise RuntimeError(
                f"Детальный Doc: после стиля H1 не найдена секция с заголовком {title!r}."
            )
        tbl_existing = find_first_table_in_range(
            d3, start=sec3.body_start, end=sec3.body_end
        )
        if tbl_existing is None:
            h1_end = sec3.h1_element.get("endIndex")
            candidates: list[int] = []
            if sec3.body_start is not None and sec3.body_start >= 1:
                candidates.append(sec3.body_start)
            if h1_end is not None and h1_end > 1:
                candidates.append(h1_end - 1)
            uniq_idx: list[int] = []
            seen_idx: set[int] = set()
            for c in candidates:
                if c in seen_idx:
                    continue
                seen_idx.add(c)
                uniq_idx.append(c)
            last_err: Exception | None = None
            inserted = False
            for table_at in uniq_idx:
                try:
                    docs_service.documents().batchUpdate(
                        documentId=document_id,
                        body={
                            "requests": [
                                _insert_detail_table_requests(
                                    rows=len(_DETAIL_TABLE_LABELS),
                                    columns=3,
                                    index=table_at,
                                )
                            ]
                        },
                    ).execute()
                    inserted = True
                    last_err = None
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
            if not inserted:
                raise RuntimeError(
                    f"Детальный Doc: не удалось вставить таблицу под {title!r}: {last_err!r}"
                ) from last_err
            d3 = docs_service.documents().get(documentId=document_id).execute()
            sec3 = _h1_section_for_title(d3, title)
            if sec3 is None:
                raise RuntimeError(
                    f"Детальный Doc: после insertTable потеряна секция H1 {title!r}."
                )
            tbl_existing = find_first_table_in_range(
                d3, start=sec3.body_start, end=sec3.body_end
            )
            if tbl_existing is None:
                raise RuntimeError(
                    f"Детальный Doc: таблица под {title!r} не попала в тело секции H1 "
                    f"(диапазон [{sec3.body_start}, {sec3.body_end}))."
                )

        # 4) Подписи — только таблица внутри этой секции (раньше бралась последняя таблица всего Doc → чужие строки).
        for ri, lab in enumerate(_DETAIL_TABLE_LABELS):
            d4 = docs_service.documents().get(documentId=document_id).execute()
            sec4 = _h1_section_for_title(d4, title)
            if sec4 is None:
                raise RuntimeError(
                    f"Детальный Doc: при заполнении подписей не найдена секция {title!r}."
                )
            tbl = find_first_table_in_range(
                d4, start=sec4.body_start, end=sec4.body_end
            )
            if not tbl:
                raise RuntimeError(
                    f"Детальный Doc: в секции {title!r} нет таблицы (строка подписи {ri})."
                )
            fill_table_row(
                document_id=document_id,
                docs_service=docs_service,
                table=tbl,
                row_index=ri,
                values=[lab, "", ""],
            )
