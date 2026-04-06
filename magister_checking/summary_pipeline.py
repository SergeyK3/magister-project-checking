"""Конвейер: список магистрантов (Doc) → отчёты → метрики → строки свода / запись в целевой Doc."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

from magister_checking.docs_table_write import (
    fill_table_row,
    find_first_table,
    table_row_cell_spans,
)
from magister_checking.dissertation_metrics import DissertationMetrics, analyze_dissertation
from magister_checking.drive_urls import extract_google_file_id
from magister_checking.report_parser import parse_intermediate_report
from magister_checking.summary_doc_parser import SummaryStudentRow, parse_summary_document


@dataclass
class PipelineResult:
    """Итоговые строки (без шапки) и сообщения об ошибках по магистрантам."""

    rows: list[list[Any]] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)


def _fmt_bool(v: bool) -> str:
    return "да" if v else "нет"


def _article_cell(parsed: Any) -> str:
    parts: list[str] = []
    if parsed.review_article_url:
        parts.append("обзор: ссылка")
    if parsed.results_article_url:
        parts.append("результаты: ссылка")
    if parsed.review_article_note:
        parts.append(parsed.review_article_note[:120])
    return "; ".join(parts) if parts else ""


def build_one_summary_row(
    *,
    index: int,
    st: SummaryStudentRow,
    docs_service: Any,
    ts: str,
) -> list[Any]:
    """Одна строка свода (13 полей данных + время), без шапки SUMMARY_HEADER."""
    try:
        if not st.report_url:
            return [
                index,
                st.name,
                st.group,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "нет ссылки на отчёт",
                ts,
            ]

        rid = extract_google_file_id(st.report_url)
        report = docs_service.documents().get(documentId=rid).execute()
        parsed = parse_intermediate_report(report)

        metrics: DissertationMetrics | None = None
        diss_pages = ""
        sources = ""
        has_rev = has_res = has_disc = ""
        total_pages = ""
        err_parts: list[str] = []

        if parsed.dissertation_url:
            try:
                did = extract_google_file_id(parsed.dissertation_url)
                diss_doc = docs_service.documents().get(documentId=did).execute()
                metrics = analyze_dissertation(diss_doc)
                diss_pages = str(metrics.approx_pages)
                sources = (
                    str(metrics.sources_count)
                    if metrics.sources_count is not None
                    else ""
                )
                has_rev = _fmt_bool(metrics.has_literature_review)
                has_res = _fmt_bool(metrics.has_results)
                has_disc = _fmt_bool(metrics.has_discussion)
                total_pages = str(metrics.approx_pages)
            except Exception as e:  # noqa: BLE001
                err_parts.append(f"диссертация: {e}")
        else:
            err_parts.append("нет ссылки на диссертацию в отчёте")

        lkb = str(parsed.lkb_status)
        if parsed.lkb_url:
            u = parsed.lkb_url
            lkb += f" ({u[:48]}…)" if len(u) > 48 else f" ({u})"

        article = _article_cell(parsed)
        notes_tail = metrics.notes if metrics else []
        err = "; ".join([*err_parts, *notes_tail])

        return [
            index,
            st.name,
            st.group,
            lkb,
            article,
            "да" if parsed.dissertation_url else "нет",
            diss_pages,
            sources,
            has_rev,
            has_res,
            has_disc,
            total_pages,
            err,
            ts,
        ]
    except Exception as e:  # noqa: BLE001
        return [
            index,
            st.name,
            st.group,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            str(e),
            ts,
        ]


def adapt_row_to_output_table(ncols: int, row_13_plus_ts: list[Any], report_url: str | None) -> list[str]:
    """
    Приводит внутреннюю строку к числу колонок целевой таблицы.
    7 колонок — типичный шаблон сводной (№, ФИО, группа, место работы, должность, ссылка на отчёт, руководитель).
    """
    cells = [str(x) if x is not None else "" for x in row_13_plus_ts]
    if ncols == 7 and len(cells) >= 3:
        return [
            cells[0],
            cells[1],
            cells[2],
            "",
            "",
            report_url or "",
            "",
        ]
    if ncols <= len(cells):
        return cells[:ncols]
    return cells + [""] * (ncols - len(cells))


def build_summary_rows(
    *,
    summary_document: dict[str, Any],
    docs_service: Any,
) -> PipelineResult:
    students = parse_summary_document(summary_document)
    pr = PipelineResult()
    if not students:
        pr.log_lines.append("Сводный документ: не найдена таблица или нет строк данных.")
        return pr

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for i, st in enumerate(students, start=1):
        row = build_one_summary_row(index=i, st=st, docs_service=docs_service, ts=ts)
        pr.rows.append(row)

    return pr


SUMMARY_HEADER = [
    "№",
    "ФИО",
    "Группа",
    "ЛКБ",
    "Статьи (обзор/результаты, заметки)",
    "Диссертация (есть ссылка)",
    "Стр. дисс. (оценка)",
    "Источников (оценка)",
    "Раздел обзор",
    "Раздел результаты",
    "Раздел обсуждение",
    "Всего стр. (оценка)",
    "Замечания / ошибки",
    "Проверено (UTC)",
]


def run_test1_fill_summary_doc(
    *,
    list_doc_id: str,
    output_summary_doc_id: str,
    creds: Any,
    data_row_index: int = 1,
) -> tuple[PipelineResult, str]:
    """
    Тест 1: первый магистрант из списка → чтение отчёта → заполнение строки data_row_index
    в первой таблице выходного Google Doc (0 — заголовок, 1 — первая строка данных).
    """
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    list_doc = docs.documents().get(documentId=list_doc_id).execute()
    students = parse_summary_document(list_doc)
    pr = PipelineResult()
    if not students:
        pr.log_lines.append("Документ-список: нет строк с магистрантами.")
        return pr, ""

    st = students[0]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    row = build_one_summary_row(index=1, st=st, docs_service=docs, ts=ts)
    pr.rows.append(row)

    out_doc = docs.documents().get(documentId=output_summary_doc_id).execute()
    tbl = find_first_table(out_doc)
    if not tbl:
        raise ValueError("Целевой документ не содержит таблицу.")

    spans = table_row_cell_spans(tbl, data_row_index)
    if not spans:
        raise ValueError(f"В таблице нет строки с индексом {data_row_index}.")

    ncols = len(spans)
    values = adapt_row_to_output_table(ncols, row, st.report_url)
    while len(values) < ncols:
        values.append(" ")
    values = values[:ncols]

    fill_table_row(
        document_id=output_summary_doc_id,
        docs_service=docs,
        table=tbl,
        row_index=data_row_index,
        values=values,
    )

    return pr, st.name


def _sheet_a1_tab(meta: dict[str, Any]) -> str:
    title = meta["sheets"][0]["properties"]["title"]
    safe = "'" + str(title).replace("'", "''") + "'"
    return safe


def write_summary_to_sheet(
    *,
    spreadsheet_id: str,
    result: PipelineResult,
    creds: Any,
) -> None:
    """Устарело: запись в Google Sheets (нужен scope spreadsheets в токене)."""
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tab = _sheet_a1_tab(meta)
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A1:Z2000",
        body={},
    ).execute()
    values = [SUMMARY_HEADER] + result.rows
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def run_summary_pipeline(
    *,
    summary_doc_id: str,
    spreadsheet_id: str,
    creds: Any,
    dry_run: bool = False,
) -> PipelineResult:
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    summary = docs.documents().get(documentId=summary_doc_id).execute()
    out = build_summary_rows(summary_document=summary, docs_service=docs)
    if not dry_run:
        write_summary_to_sheet(
            spreadsheet_id=spreadsheet_id,
            result=out,
            creds=creds,
        )
    return out
