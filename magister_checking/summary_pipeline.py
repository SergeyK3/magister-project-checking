"""Конвейер: сводный Doc → отчёты → метрики диссертации → строки для Sheets (Прил. 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

from magister_checking.dissertation_metrics import DissertationMetrics, analyze_dissertation
from magister_checking.drive_urls import extract_google_file_id
from magister_checking.report_parser import parse_intermediate_report
from magister_checking.summary_doc_parser import parse_summary_document


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
        err_parts: list[str] = []
        try:
            if not st.report_url:
                pr.rows.append(
                    [
                        i,
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
                )
                continue

            rid = extract_google_file_id(st.report_url)
            report = docs_service.documents().get(documentId=rid).execute()
            parsed = parse_intermediate_report(report)

            metrics: DissertationMetrics | None = None
            diss_pages = ""
            sources = ""
            has_rev = has_res = has_disc = ""
            total_pages = ""

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

            pr.rows.append(
                [
                    i,
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
            )
        except Exception as e:  # noqa: BLE001
            pr.log_lines.append(f"{st.name}: {e}")
            pr.rows.append(
                [
                    i,
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
            )

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
    """Полностью перезаписывает первый лист: шапка + строки."""
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
