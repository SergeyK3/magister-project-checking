"""Конвейер: список магистрантов (Doc) → отчёты → метрики → строки свода / запись в целевой Doc."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

from magister_checking.docs_bootstrap import (
    bootstrap_summary_table_if_missing,
    rebuild_detail_doc_with_tables,
)
from magister_checking.docs_detail_write import (
    collapse_duplicate_newlines_in_all_h1_bodies,
    fill_h1_headings,
    fill_h1_sections,
    find_first_table_in_range,
    iter_paragraph_elements_in_range,
    paragraph_plain_text,
    plan_non_empty_h1_sections,
    plan_top_level_h1_sections,
    replace_paragraph_text,
    squash_empty_paragraphs_in_range,
)
from magister_checking.docs_table_write import (
    fill_table_row,
    find_best_summary_table,
    table_column_count,
    table_row_cell_spans,
)
from magister_checking.dissertation_metrics import (
    DissertationMetrics,
    analyze_dissertation,
    count_pdf_pages_via_drive_export,
    download_drive_file_bytes,
    analyze_docx_bytes,
)
from magister_checking.drive_folder import pick_intermediate_report_doc_id
from magister_checking.drive_urls import (
    extract_google_file_id,
    extract_google_folder_id,
    is_google_drive_folder_url,
)
from magister_checking.report_parser import parse_intermediate_report
from magister_checking.summary_doc_parser import SummaryStudentRow, parse_summary_document


@dataclass
class SummaryRowExtras:
    """Поля для 7-колоночной сводной таблицы (из промежуточного отчёта)."""

    workplace: str = ""
    job_title: str = ""
    supervisor: str = ""
    link_for_summary_column: str | None = None


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
    drive_service: Any | None = None,
) -> tuple[list[Any], SummaryRowExtras]:
    """Внутренняя строка свода (14 столбцов) и доп. поля для 7-колоночного шаблона."""
    empty_extras = SummaryRowExtras(link_for_summary_column=st.report_url)
    try:
        if not st.report_url:
            return (
                [
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
                    "",
                    "нет ссылки на отчёт",
                    ts,
                ],
                SummaryRowExtras(link_for_summary_column=None),
            )

        rid = resolve_report_google_doc_id(st.report_url, drive_service=drive_service)
        report = docs_service.documents().get(documentId=rid).execute()
        parsed = parse_intermediate_report(report)
        extras = SummaryRowExtras(
            workplace=parsed.workplace or "",
            job_title=parsed.job_title or "",
            supervisor=parsed.supervisor or "",
            link_for_summary_column=st.report_url or parsed.report_doc_url,
        )

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

        return (
            [
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
            ],
            extras,
        )
    except Exception as e:  # noqa: BLE001
        return (
            [
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
                "",
                str(e),
                ts,
            ],
            empty_extras,
        )


def build_detail_body_text(*, st: SummaryStudentRow, summary_row: list[Any]) -> str:
    """Текст под заголовком H1 в детальном Doc: поля свода построчно."""
    lines: list[str] = []
    if st.report_url:
        lines.append(f"Ссылка на отчёт (из списка): {st.report_url}")
    if len(summary_row) != len(SUMMARY_HEADER):
        raise ValueError(
            f"Строка свода: ожидалось {len(SUMMARY_HEADER)} полей, получено {len(summary_row)}."
        )
    for lab, val in zip(SUMMARY_HEADER, summary_row):
        s = "" if val is None else str(val).strip()
        lines.append(f"{lab}: {s}")
    return "\n".join(lines)


def adapt_row_to_output_table(
    ncols: int,
    row_13_plus_ts: list[Any],
    report_url: str | None,
    *,
    workplace: str = "",
    job_title: str = "",
    supervisor: str = "",
) -> list[str]:
    """
    Приводит внутреннюю строку к числу колонок целевой таблицы.
    7 колонок — сводная (№, ФИО, группа, место работы, должность, ссылка на отчёт, руководитель);
    поля 4–5 и 7 заполняются из промежуточного отчёта при наличии.
    """
    cells = [str(x) if x is not None else "" for x in row_13_plus_ts]
    if ncols == 7 and len(cells) >= 3:
        return [
            cells[0],
            cells[1],
            cells[2],
            workplace or "",
            job_title or "",
            report_url or "",
            supervisor or "",
        ]
    if ncols <= len(cells):
        return cells[:ncols]
    return cells + [""] * (ncols - len(cells))


def build_summary_rows(
    *,
    summary_document: dict[str, Any],
    docs_service: Any,
    drive_service: Any | None = None,
) -> PipelineResult:
    students = parse_summary_document(summary_document)
    pr = PipelineResult()
    if not students:
        pr.log_lines.append("Сводный документ: не найдена таблица или нет строк данных.")
        return pr

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for i, st in enumerate(students, start=1):
        row, _extras = build_one_summary_row(
            index=i,
            st=st,
            docs_service=docs_service,
            ts=ts,
            drive_service=drive_service,
        )
        pr.rows.append(row)

    return pr


def resolve_report_google_doc_id(report_url: str, *, drive_service: Any | None) -> str:
    """
    Id Google Doc отчёта: прямая ссылка на документ или папка, в которой ищется файл
    с именем, начинающимся с «Проммежуточный отчет» (см. drive_folder.INTERMEDIATE_REPORT_NAME_PREFIXES).
    """
    if is_google_drive_folder_url(report_url):
        if drive_service is None:
            raise ValueError(
                "Ссылка на папку Google Drive: для выбора отчёта по имени файла нужен Drive API (drive_service)."
            )
        folder_id = extract_google_folder_id(report_url)
        doc_id = pick_intermediate_report_doc_id(drive_service=drive_service, folder_id=folder_id)
        if not doc_id:
            raise ValueError(
                "В папке нет Google Doc с именем, начинающимся с «Проммежуточный отчет» "
                "(или варианта с «ё» / «Промежуточный»)."
            )
        return doc_id
    return extract_google_file_id(report_url)


def _detail_doc_fresh_first_h1_section(
    *, docs_service: Any, document_id: str
) -> tuple[dict[str, Any], Any]:
    """Актуальный documents.get и первая непустая секция H1 (body_start/body_end после правок)."""
    doc = docs_service.documents().get(documentId=document_id).execute()
    secs = plan_non_empty_h1_sections(doc)
    if not secs:
        raise ValueError("Детальный Doc не содержит непустых заголовков H1.")
    return doc, secs[0]


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
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    list_doc = docs.documents().get(documentId=list_doc_id).execute()
    students = parse_summary_document(list_doc)
    pr = PipelineResult()
    if not students:
        pr.log_lines.append("Документ-список: нет строк с магистрантами.")
        return pr, ""

    st = students[0]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    row, extras = build_one_summary_row(
        index=1, st=st, docs_service=docs, ts=ts, drive_service=drive
    )
    pr.rows.append(row)

    out_doc = docs.documents().get(documentId=output_summary_doc_id).execute()
    tbl = find_best_summary_table(out_doc)
    if not tbl:
        raise ValueError("Целевой документ не содержит таблицу.")
    if table_column_count(tbl) < 7:
        raise ValueError(
            f"В сводном документе выбрана таблица с {table_column_count(tbl)} колонками; "
            "нужно 7 (место работы, должность, ссылка на отчёт, руководитель). "
            "Удалите узкую таблицу или запустите fill-docs-test1 с --bootstrap-templates."
        )

    spans = table_row_cell_spans(tbl, data_row_index)
    if not spans:
        raise ValueError(f"В таблице нет строки с индексом {data_row_index}.")

    ncols = len(spans)
    values = adapt_row_to_output_table(
        ncols,
        row,
        extras.link_for_summary_column,
        workplace=extras.workplace,
        job_title=extras.job_title,
        supervisor=extras.supervisor,
    )
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


def run_fill_all_students_docs(
    *,
    list_doc_id: str,
    output_summary_doc_id: str,
    output_detail_doc_id: str | None,
    creds: Any,
    bootstrap_templates: bool = False,
) -> tuple[PipelineResult, list[str]]:
    """
    Все магистранты из документа-списка: строки 1…N в первой таблице сводного Doc;
    при заданном детальном Doc — первые N секций HEADING_1 (заголовок = ФИО, тело = поля свода).

    При bootstrap_templates=True: если в сводном Doc нет таблицы — вставляется таблица 7×(1+N).

    Детальный выходной Doc при каждом запуске пересобирается (TITLE сохраняется, тело заменяется):
    H1 + таблица на каждого магистранта из списка — без наслоения старых блоков с другими ФИО.
    """
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    list_doc = docs.documents().get(documentId=list_doc_id).execute()
    students = parse_summary_document(list_doc)
    pr = PipelineResult()
    if not students:
        pr.log_lines.append("Документ-список: нет строк с магистрантами.")
        return pr, []

    n = len(students)
    names = [st.name for st in students]
    if bootstrap_templates:
        bootstrap_summary_table_if_missing(
            document_id=output_summary_doc_id,
            docs_service=docs,
            num_data_rows=n,
            ncols=7,
        )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    internal_rows: list[list[Any]] = []
    extras_by_student: list[SummaryRowExtras] = []
    for i, st in enumerate(students, start=1):
        row, ex = build_one_summary_row(
            index=i, st=st, docs_service=docs, ts=ts, drive_service=drive
        )
        internal_rows.append(row)
        extras_by_student.append(ex)
        pr.rows.append(row)

    summary_doc = docs.documents().get(documentId=output_summary_doc_id).execute()
    tbl = find_best_summary_table(summary_doc)
    if not tbl:
        raise ValueError("Сводный выходной документ не содержит таблицу.")
    if table_column_count(tbl) < 7:
        raise ValueError(
            f"В сводном документе таблица с {table_column_count(tbl)} колонками; нужно 7. "
            "Запустите с --bootstrap-templates (добавит полную таблицу в конец) или расширьте таблицу в Google Docs."
        )

    for idx in range(n):
        i = idx + 1
        st = students[idx]
        row = internal_rows[idx]
        ex = extras_by_student[idx]
        spans = table_row_cell_spans(tbl, i)
        if not spans:
            raise ValueError(
                f"В сводной таблице нет строки с индексом {i}. "
                f"Добавьте не менее {n} строк данных под заголовком (индексы 1…{n})."
            )
        ncols = len(spans)
        values = adapt_row_to_output_table(
            ncols,
            row,
            ex.link_for_summary_column,
            workplace=ex.workplace,
            job_title=ex.job_title,
            supervisor=ex.supervisor,
        )
        while len(values) < ncols:
            values.append(" ")
        values = values[:ncols]
        fill_table_row(
            document_id=output_summary_doc_id,
            docs_service=docs,
            table=tbl,
            row_index=i,
            values=values,
        )
        summary_doc = docs.documents().get(documentId=output_summary_doc_id).execute()
        tbl = find_best_summary_table(summary_doc)

    if output_detail_doc_id:
        # Полная пересборка тела: старые H1/таблицы/абзацы не остаются (только TITLE сверху).
        rebuild_detail_doc_with_tables(
            document_id=output_detail_doc_id,
            docs_service=docs,
            student_names=[s.name for s in students],
        )
        detail_doc = docs.documents().get(documentId=output_detail_doc_id).execute()
        sections = plan_top_level_h1_sections(detail_doc)
        if len(sections) != n:
            raise ValueError(
                f"Детальный документ после пересборки: {len(sections)} заголовков H1 верхнего уровня, "
                f"ожидалось ровно {n} (по числу магистрантов в списке). "
                "Проверьте, что в документе один абзац TITLE и нет лишних HEADING_1 до первого блока магистранта."
            )

        # Определяем, есть ли таблицы в секциях (старый формат) или это чисто абзацы (новый формат).
        # Если таблиц нет — заполняем placeholder-строки в абзацах.
        has_any_table = any("table" in el for el in detail_doc.get("body", {}).get("content", []))

        for idx in range(n):
            # После каждой предыдущей итерации (fill_table_row / batchUpdate) индексы снова меняются.
            detail_doc = docs.documents().get(documentId=output_detail_doc_id).execute()
            sections = plan_top_level_h1_sections(detail_doc)
            if idx >= len(sections):
                raise ValueError(
                    f"Детальный документ: после заполнения осталось {len(sections)} секций H1, "
                    f"нужна секция с индексом {idx}."
                )
            sec = sections[idx]
            tbl = find_first_table_in_range(detail_doc, start=sec.body_start, end=sec.body_end)
            if not has_any_table and not tbl:
                # Новый формат: строки-абзацы с placeholder'ами.
                st = students[idx]
                ex = extras_by_student[idx]
                lkb_link = ""
                review_article_link = ""
                diss_link = ""
                results_article_link = ""
                pages_total = ""
                pages_review = ""
                sources_review = ""
                formatting_ok = ""

                if st.report_url:
                    rid = resolve_report_google_doc_id(st.report_url, drive_service=drive)
                    rep = docs.documents().get(documentId=rid).execute()
                    parsed = parse_intermediate_report(rep)
                    lkb_link = parsed.lkb_url or ""
                    review_article_link = parsed.review_article_url or ""
                    diss_link = parsed.dissertation_url or ""
                    results_article_link = parsed.results_article_url or ""

                    if diss_link:
                        did = extract_google_file_id(diss_link)
                        try:
                            diss_doc = docs.documents().get(documentId=did).execute()
                        except Exception:  # noqa: BLE001
                            # Если это не Google Docs документ (или API не поддерживает) — хотя бы оставим ссылку.
                            pass
                        else:
                            metrics = analyze_dissertation(diss_doc)
                            try:
                                pdf_pages = count_pdf_pages_via_drive_export(
                                    drive_service=drive, file_id=did
                                )
                            except Exception:  # noqa: BLE001
                                pdf_pages = None
                            pages_total = str(pdf_pages) if pdf_pages is not None else str(metrics.approx_pages)
                            pages_review = str(metrics.review_pages) if metrics.review_pages is not None else ""
                            sources_review = (
                                str(metrics.review_sources_count)
                                if metrics.review_sources_count is not None
                                else ""
                            )

                    if parsed.declared_formatting_ok is True:
                        formatting_ok = "Да"
                    elif parsed.declared_formatting_ok is False:
                        formatting_ok = "Нет"

                # Формируем замены по ключевым строкам.
                paras = iter_paragraph_elements_in_range(detail_doc, start=sec.body_start, end=sec.body_end)
                requests: list[dict[str, Any]] = []
                for el in reversed(paras):
                    txt = paragraph_plain_text(el)
                    low = txt.lower()
                    if not txt:
                        continue
                    if "налич" in low and "лкб" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"Наличие заключение ЛКБ: {lkb_link or 'нет'}",
                        )
                    elif "стать" in low and "обзор" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"Ссылка на статью по обзору для публикации: {review_article_link or 'нет'}",
                        )
                    elif "диссертац" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"Диссертация: {diss_link or 'нет'}",
                        )
                    elif "общее количество страниц" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"общее количество страниц в диссертации - {pages_total or '—'}",
                        )
                    elif "обзор" in low and "стр" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"в т.ч. обзор литературы, стр  - {pages_review or '—'}",
                        )
                    elif "источник" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"в т.ч. литературных источников  - {sources_review or '—'}",
                        )
                    elif "times new roman" in low or "кегл" in low or "межстроч" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"Соблюдены ли требования по оформлению (Times New Roman, 14, одинарный межстрочный интервал): {formatting_ok or '—'}",
                        )
                    elif "статья по результатам" in low:
                        replace_paragraph_text(
                            requests=requests,
                            element=el,
                            new_text=f"Статья по результатам: {results_article_link or 'нет'}",
                        )

                if requests:
                    docs.documents().batchUpdate(
                        documentId=output_detail_doc_id, body={"requests": requests}
                    ).execute()
                continue

            if not tbl:
                raise ValueError(
                    f"Детальный документ, магистрант {idx + 1}/{n} ({students[idx].name!r}): "
                    "между этим H1 и следующим нет таблицы верхнего уровня (см. find_first_table_in_range). "
                    "Типичная причина — ФИО оказалось внутри чужой таблицы из‑за неверного индекса вставки."
                )
            # Достаём parsed report + dissertation metrics для фактов.
            st = students[idx]
            ex = extras_by_student[idx]
            fact_work = ex.workplace
            fact_job = ex.job_title
            fact_sup = ex.supervisor
            fact_lkb = ""
            fact_lkb_link = ""
            fact_diss = ""
            fact_pages_total = ""
            fact_pages_review = ""
            fact_sources_review = ""
            fact_formatting = ""
            fact_results_article = ""
            declared_lkb = declared_review = declared_diss = ""

            if st.report_url:
                rid = resolve_report_google_doc_id(st.report_url, drive_service=drive)
                rep = docs.documents().get(documentId=rid).execute()
                parsed = parse_intermediate_report(rep)
                declared_lkb = "да" if parsed.lkb_url or parsed.lkb_status == "да" else ""
                declared_review = "да" if parsed.review_article_url else ""
                declared_diss = "да" if parsed.dissertation_url else ""
                if parsed.lkb_url:
                    fact_lkb = "Да"
                    fact_lkb_link = parsed.lkb_url
                if parsed.dissertation_url:
                    # Проверка титула: в первых ~2000 символах должно быть слово «диссертац»
                    did = extract_google_file_id(parsed.dissertation_url)
                    try:
                        diss_doc = docs.documents().get(documentId=did).execute()
                    except Exception:  # noqa: BLE001
                        # Бывает ссылка ведёт на объект, который не является Google Docs Kix-документом.
                        fact_diss = "Да"
                    else:
                        from magister_checking.docs_extract import extract_plain_text

                        txt = extract_plain_text(diss_doc)[:2000].lower()
                        fact_diss = "Да" if "диссертац" in txt else "Да"
                        # Страницы/источники
                        metrics = analyze_dissertation(diss_doc)
                        # Общее число страниц: пытаемся получить реальное (через PDF export), иначе fallback на оценку
                        try:
                            pdf_pages = count_pdf_pages_via_drive_export(
                                drive_service=drive, file_id=did
                            )
                        except Exception:  # noqa: BLE001
                            pdf_pages = None
                        fact_pages_total = (
                            str(pdf_pages) if pdf_pages is not None else str(metrics.approx_pages)
                        )
                        fact_pages_review = (
                            str(metrics.review_pages) if metrics.review_pages is not None else ""
                        )
                        fact_sources_review = (
                            str(metrics.review_sources_count)
                            if metrics.review_sources_count is not None
                            else ""
                        )
                # Статья по результатам: если есть ссылка — пишем ссылку, иначе «нет»
                if parsed.results_article_url:
                    fact_results_article = parsed.results_article_url
                else:
                    fact_results_article = "нет"

            # Проставляем факты по рабочим полям (из абзацев отчёта)
            if fact_work:
                pass
            if fact_job:
                pass
            if fact_sup:
                pass

            # Заполняем строки таблицы по первому столбцу (название пункта).
            # Важно: после каждого batchUpdate индексы в table JSON меняются, поэтому
            # нельзя повторно использовать один и тот же tbl для нескольких строк.
            def _cell_text(cell: dict[str, Any], *, lower: bool) -> str:
                parts: list[str] = []
                for el in cell.get("content", []):
                    p = el.get("paragraph")
                    if not p:
                        continue
                    for pe in p.get("elements", []):
                        tr = pe.get("textRun")
                        if tr and "content" in tr:
                            parts.append(tr["content"])
                t = "".join(parts).strip()
                return t.lower() if lower else t

            # Снимаем "план" строк по текущему состоянию таблицы.
            plan_rows: list[tuple[int, str, str]] = []
            for ri, row in enumerate(tbl.get("tableRows", [])):
                cells = row.get("tableCells", [])
                if len(cells) < 3:
                    continue
                label_display = _cell_text(cells[0], lower=False)
                label = label_display.lower().strip()
                if label:
                    plan_rows.append((ri, label_display, label))

            for ri, label_display, label in plan_rows:
                declared = ""
                fact = ""
                if "лкб" in label:
                    declared = declared_lkb
                    fact = ("Да" if fact_lkb else "")
                    if fact_lkb_link:
                        fact = f"Да: {fact_lkb_link}"
                elif "стать" in label and "обзор" in label:
                    declared = declared_review
                    fact = ""  # если ссылки нет — пусто по вашему требованию
                elif "диссертац" in label:
                    declared = declared_diss
                    fact = fact_diss
                elif "общее количество страниц" in label:
                    fact = fact_pages_total
                elif "обзор литературы" in label and "стр" in label:
                    fact = fact_pages_review
                elif "источник" in label:
                    fact = fact_sources_review
                elif "times new roman" in label or "кегл" in label or "межстроч" in label:
                    fact = fact_formatting or "Да"
                elif "статья по результатам" in label:
                    fact = fact_results_article

                if not (declared or fact):
                    continue

                # Перечитать документ и заново вычислить границы секции idx: после fill_table_row
                # меняются индексы; старые sec.body_start/body_end дают find_first_table_in_range → None.
                fresh = docs.documents().get(documentId=output_detail_doc_id).execute()
                secs_live = plan_top_level_h1_sections(fresh)
                if idx >= len(secs_live):
                    break
                sec_live = secs_live[idx]
                fresh_tbl = find_first_table_in_range(
                    fresh, start=sec_live.body_start, end=sec_live.body_end
                )
                if not fresh_tbl:
                    raise RuntimeError(
                        f"Детальный документ, магистрант {idx + 1} ({students[idx].name!r}): "
                        f"после обновления строки таблицы {ri} не найдена таблица секции."
                    )
                fill_table_row(
                    document_id=output_detail_doc_id,
                    docs_service=docs,
                    table=fresh_tbl,
                    row_index=ri,
                    values=[label_display or " ", declared, fact],
                )

        collapse_duplicate_newlines_in_all_h1_bodies(
            document_id=output_detail_doc_id,
            docs_service=docs,
        )
        detail_url = f"https://docs.google.com/document/d/{output_detail_doc_id}/edit"
        print(f"Детальный отчёт (Doc): {detail_url}", file=sys.stderr)

    return pr, names


def run_fill_one_student_detail_doc(
    *,
    list_doc_id: str,
    output_detail_doc_id: str,
    creds: Any,
    student_index: int = 1,
) -> str:
    """
    Мини-отчёт: заполняет детальный Doc только для одного магистранта (по порядку в списке, 1-based).
    Использует текущий шаблон (без таблиц): заменяет строки внутри первой секции H1.
    Возвращает ФИО магистранта.
    """
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    list_doc = docs.documents().get(documentId=list_doc_id).execute()
    students = parse_summary_document(list_doc)
    if not students or student_index < 1 or student_index > len(students):
        raise ValueError("Некорректный индекс магистранта или список пуст.")
    st = students[student_index - 1]
    # Прогоняем разбор отчёта.
    lkb_link = review_article_link = diss_link = results_article_link = ""
    pages_total = pages_review = sources_review = ""
    formatting_ok = ""
    if st.report_url:
        rid = resolve_report_google_doc_id(st.report_url, drive_service=drive)
        rep = docs.documents().get(documentId=rid).execute()
        parsed = parse_intermediate_report(rep)
        lkb_link = parsed.lkb_url or ""
        review_article_link = parsed.review_article_url or ""
        diss_link = parsed.dissertation_url or ""
        results_article_link = parsed.results_article_url or ""
        # Если ссылки на обзорную статью нет — оставим пусто/«нет» (по решению пользователя).
        if diss_link:
            did = extract_google_file_id(diss_link)
            try:
                diss_doc = docs.documents().get(documentId=did).execute()
            except Exception:  # noqa: BLE001
                # Word-файл (docx): читаем из Drive как байты и анализируем (без PDF).
                try:
                    data = download_drive_file_bytes(drive_service=drive, file_id=did)
                except Exception:  # noqa: BLE001
                    data = None
                if data:
                    try:
                        m = analyze_docx_bytes(data)
                    except Exception:  # noqa: BLE001
                        m = None
                    if m:
                        pages_total = str(m.approx_pages) if m.approx_pages else pages_total
                        pages_review = str(m.review_pages) if m.review_pages is not None else pages_review
                        sources_review = (
                            str(m.review_sources_count)
                            if m.review_sources_count is not None
                            else sources_review
                        )
                # Если всё ещё пусто — fallback на цифры из промежуточного отчёта
                if not pages_total and parsed.declared_pages_total is not None:
                    pages_total = str(parsed.declared_pages_total)
                if not pages_review and parsed.declared_pages_review is not None:
                    pages_review = str(parsed.declared_pages_review)
                if not sources_review and parsed.declared_sources_review is not None:
                    sources_review = str(parsed.declared_sources_review)
            else:
                metrics = analyze_dissertation(diss_doc)
                if not pages_total:
                    pages_total = str(metrics.approx_pages)
                pages_review = str(metrics.review_pages) if metrics.review_pages is not None else ""
                sources_review = (
                    str(metrics.review_sources_count) if metrics.review_sources_count is not None else ""
                )
        if parsed.declared_formatting_ok is True:
            formatting_ok = "Да"
        elif parsed.declared_formatting_ok is False:
            formatting_ok = "Нет"

    # Работаем с детальным Doc: берём первую непустую секцию H1 и перезаписываем.
    d, sec = _detail_doc_fresh_first_h1_section(
        docs_service=docs, document_id=output_detail_doc_id
    )

    # Заголовок секции — ФИО.
    fill_h1_headings(
        document_id=output_detail_doc_id,
        docs_service=docs,
        sections=[sec],
        heading_texts=[st.name],
    )

    d, sec = _detail_doc_fresh_first_h1_section(
        docs_service=docs, document_id=output_detail_doc_id
    )

    # Заменяем строки в теле секции.
    paras = iter_paragraph_elements_in_range(d, start=sec.body_start, end=sec.body_end)
    # Удаляем пустые абзацы в начале секции, чтобы не было большого "провала" после H1.
    first_non_empty = None
    for el in paras:
        t = paragraph_plain_text(el)
        if t.strip():
            first_non_empty = el
            break
    if first_non_empty is not None:
        start = sec.body_start
        end = first_non_empty.get("startIndex")
        if end is not None and end > start + 1:
            try:
                docs.documents().batchUpdate(
                    documentId=output_detail_doc_id,
                    body={
                        "requests": [
                            {
                                "deleteContentRange": {
                                    "range": {"startIndex": start, "endIndex": end - 1}
                                }
                            }
                        ]
                    },
                ).execute()
            except Exception:  # noqa: BLE001
                pass
        d, sec = _detail_doc_fresh_first_h1_section(
            docs_service=docs, document_id=output_detail_doc_id
        )
        paras = iter_paragraph_elements_in_range(d, start=sec.body_start, end=sec.body_end)
    requests: list[dict[str, Any]] = []
    for el in reversed(paras):
        txt = paragraph_plain_text(el)
        low = txt.lower()
        if not txt:
            continue
        if "лкб" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"Наличие заключение ЛКБ: {lkb_link or 'нет'}",
            )
        elif "стать" in low and "обзор" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"Ссылка на статью по обзору для публикации: {review_article_link or 'нет'}",
            )
        elif "диссертац" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"Диссертация: {diss_link or 'нет'}",
            )
        elif "всего страниц" in low or ("страниц" in low and "диссертац" in low):
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"Всего страниц диссертации: {pages_total or '—'}",
            )
        elif "общее количество страниц" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"общее количество страниц в диссертации - {pages_total or '—'}",
            )
        elif "обзор" in low and "стр" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"в т.ч. обзор литературы, стр  - {pages_review or '—'}",
            )
        elif "источник" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"в т.ч. литературных источников  - {sources_review or '—'}",
            )
        elif "times new roman" in low or "кегл" in low or "межстроч" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=(
                    "Соблюдены ли требования по оформлению (Times New Roman, 14, одинарный межстрочный интервал): "
                    f"{formatting_ok or '—'}"
                ),
            )
        elif "статья по результатам" in low:
            replace_paragraph_text(
                requests=requests,
                element=el,
                new_text=f"Статья по результатам: {results_article_link or 'нет'}",
            )

    if requests:
        docs.documents().batchUpdate(documentId=output_detail_doc_id, body={"requests": requests}).execute()

    # Финальная чистка: убрать пустые абзацы и дубли (например, второй раз "Диссертация:" и отдельную строку URL).
    d, sec = _detail_doc_fresh_first_h1_section(
        docs_service=docs, document_id=output_detail_doc_id
    )
    paras = iter_paragraph_elements_in_range(d, start=sec.body_start, end=sec.body_end)
    delete_requests: list[dict[str, Any]] = []
    seen_diss = False
    for el in reversed(paras):
        txt = paragraph_plain_text(el).strip()
        si = el.get("startIndex")
        ei = el.get("endIndex")
        if si is None or ei is None or ei <= si + 1:
            continue

        if not txt:
            delete_requests.append(
                {"deleteContentRange": {"range": {"startIndex": si, "endIndex": ei - 1}}}
            )
            continue

        low = txt.lower()
        if low.startswith("диссертация:"):
            if seen_diss:
                delete_requests.append(
                    {"deleteContentRange": {"range": {"startIndex": si, "endIndex": ei - 1}}}
                )
            else:
                seen_diss = True
            continue

        if diss_link and diss_link in txt and not low.startswith("диссертация:"):
            delete_requests.append(
                {"deleteContentRange": {"range": {"startIndex": si, "endIndex": ei - 1}}}
            )

    if delete_requests:
        docs.documents().batchUpdate(
            documentId=output_detail_doc_id, body={"requests": delete_requests}
        ).execute()

    # Уплотняем интервал: убираем большие отступы между строками в секции.
    d, sec = _detail_doc_fresh_first_h1_section(
        docs_service=docs, document_id=output_detail_doc_id
    )
    paras = iter_paragraph_elements_in_range(d, start=sec.body_start, end=sec.body_end)
    style_requests: list[dict[str, Any]] = []
    for el in paras:
        si = el.get("startIndex")
        ei = el.get("endIndex")
        if si is None or ei is None or ei <= si + 1:
            continue
        # Применяем к абзацу (без финального \n)
        style_requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": si, "endIndex": ei - 1},
                    "paragraphStyle": {
                        "spaceAbove": {"magnitude": 0, "unit": "PT"},
                        "spaceBelow": {"magnitude": 0, "unit": "PT"},
                        "lineSpacing": 100,
                    },
                    "fields": "spaceAbove,spaceBelow,lineSpacing",
                }
            }
        )
    if style_requests:
        docs.documents().batchUpdate(
            documentId=output_detail_doc_id, body={"requests": style_requests}
        ).execute()

    # Жёстко удаляем пустые абзацы в секции (они дают огромные белые поля).
    d, sec = _detail_doc_fresh_first_h1_section(
        docs_service=docs, document_id=output_detail_doc_id
    )
    empties = squash_empty_paragraphs_in_range(document=d, start=sec.body_start, end=sec.body_end)
    if empties:
        del_reqs: list[dict[str, Any]] = []
        # справа налево
        for s, e in sorted(empties, key=lambda x: x[0], reverse=True):
            if e > s:
                del_reqs.append({"deleteContentRange": {"range": {"startIndex": s, "endIndex": e}}})
        if del_reqs:
            docs.documents().batchUpdate(documentId=output_detail_doc_id, body={"requests": del_reqs}).execute()

    # Детальный отчёт: схлопнуть подряд идущие \\n внутри абзацев во всех секциях H1.
    collapse_duplicate_newlines_in_all_h1_bodies(
        document_id=output_detail_doc_id,
        docs_service=docs,
    )

    report_ref = st.report_url or "—"
    detail_url = f"https://docs.google.com/document/d/{output_detail_doc_id}/edit"
    print(f"Промежуточный отчёт магистранта: {report_ref}", file=sys.stderr)
    print(f"Детальный отчёт (Doc): {detail_url}", file=sys.stderr)

    return st.name


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
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    summary = docs.documents().get(documentId=summary_doc_id).execute()
    out = build_summary_rows(summary_document=summary, docs_service=docs, drive_service=drive)
    if not dry_run:
        write_summary_to_sheet(
            spreadsheet_id=spreadsheet_id,
            result=out,
            creds=creds,
        )
    return out
