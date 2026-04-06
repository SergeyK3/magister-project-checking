"""
Заполнение строки таблицы внутри Google Doc через documents.batchUpdate.

Индексы startIndex/endIndex берутся из ответа documents.get (тело документа).
Запросы применяются справа налево по документу, чтобы не сбивать смещения.
"""

from __future__ import annotations

from typing import Any


def find_first_table(document: dict[str, Any]) -> dict[str, Any] | None:
    for el in document.get("body", {}).get("content", []):
        if "table" in el:
            return el["table"]
    return None


def _cell_text_run_span(cell: dict[str, Any]) -> tuple[int, int] | None:
    """Диапазон [start, end) по всем textRun в ячейке (безопаснее для batchUpdate)."""
    mins: int | None = None
    maxe: int | None = None
    for el in cell.get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
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


def _cell_structural_span(cell: dict[str, Any]) -> tuple[int, int] | None:
    """Запасной вариант: весь блок structural elements ячейки."""
    content = cell.get("content", [])
    if not content:
        return None
    starts: list[int] = []
    ends: list[int] = []
    for el in content:
        si = el.get("startIndex")
        ei = el.get("endIndex")
        if si is not None and ei is not None:
            starts.append(si)
            ends.append(ei)
    if not starts:
        return None
    return min(starts), max(ends)


def _cell_editable_span(cell: dict[str, Any]) -> tuple[int, int] | None:
    return _cell_text_run_span(cell) or _cell_structural_span(cell)


def table_row_cell_spans(table: dict[str, Any], row_index: int) -> list[tuple[int, int] | None]:
    """Список диапазонов по колонкам для строки row_index (0 — первая строка таблицы)."""
    rows = table.get("tableRows", [])
    if row_index < 0 or row_index >= len(rows):
        return []
    out: list[tuple[int, int] | None] = []
    for cell in rows[row_index].get("tableCells", []):
        out.append(_cell_editable_span(cell))
    return out


def fill_table_row(
    *,
    document_id: str,
    docs_service: Any,
    table: dict[str, Any],
    row_index: int,
    values: list[str],
) -> int:
    """
    Очищает текст в ячейках строки row_index и вставляет values[i].
    Число ячеек = min(колонок, len(values)).
    Возвращает число выполненных batchUpdate-запросов (пар delete+insert).
    """
    spans = table_row_cell_spans(table, row_index)
    if not spans:
        return 0
    n = min(len(spans), len(values))
    operations: list[tuple[int, int, str, bool]] = []
    for i in range(n):
        sp = spans[i]
        if sp is None:
            continue
        start, end = sp
        if end <= start:
            continue
        text = values[i] if values[i] is not None else ""
        if not text.strip():
            text = " "
        # Нельзя удалять весь диапазон ячейки — API требует оставить символ (часто \n в конце абзаца).
        delete_end = end - 1
        do_delete = delete_end > start
        operations.append((start, end, text, do_delete))

    # Справа налево по startIndex
    operations.sort(key=lambda x: x[0], reverse=True)

    requests: list[dict[str, Any]] = []
    for start, end, text, do_delete in operations:
        if do_delete:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {"startIndex": start, "endIndex": end - 1},
                    }
                }
            )
        requests.append(
            {
                "insertText": {
                    "location": {"index": start},
                    "text": text,
                }
            }
        )

    if not requests:
        return 0

    docs_service.documents().batchUpdate(
        documentId=document_id, body={"requests": requests}
    ).execute()
    return len(operations)


def fill_first_table_data_row_from_document(
    *,
    document_id: str,
    docs_service: Any,
    data_row_index: int,
    values: list[str],
) -> dict[str, Any]:
    """
    Читает документ, находит первую таблицу, заполняет строку data_row_index.
    data_row_index=1 — обычно первая строка под заголовком (вторая физическая строка).
    Возвращает обновлённый document (последний get после batch) для отладки.
    """
    doc = docs_service.documents().get(documentId=document_id).execute()
    tbl = find_first_table(doc)
    if not tbl:
        raise ValueError("В документе не найдена таблица.")
    fill_table_row(
        document_id=document_id,
        docs_service=docs_service,
        table=tbl,
        row_index=data_row_index,
        values=values,
    )
    return docs_service.documents().get(documentId=document_id).execute()
