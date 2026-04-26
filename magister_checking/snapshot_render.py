"""Рендеры для ProjectSnapshot: Telegram, комиссия (см. contract_project_snapshot.md)."""

from __future__ import annotations

import html
import re

from magister_checking.project_snapshot import (
    PHASE_STAGE1,
    PHASE_STAGE2,
    PHASE_STAGE3,
    PHASE_STAGE4,
    ProjectSnapshot,
)


def render_spravka_telegram(snapshot: ProjectSnapshot, *, applied: bool) -> str:
    """Текст «справки» магистранту: коротко, согласован с прежним ``format_report``."""

    if snapshot.unchanged:
        fio = snapshot.identity.fio or "(без ФИО)"
        row = snapshot.row_number if snapshot.row_number is not None else "?"
        return (
            f"Магистрант: {fio}\n"
            f"Строка: {row}\n\n"
            "С прошлой проверки входы не менялись (--only-if-changed).\n"
            "Лист и история проверок не тронуты."
        )

    lines: list[str] = []
    fio = snapshot.identity.fio
    if fio:
        lines.append(f"Магистрант: {fio}")
    if snapshot.row_number is not None:
        lines.append(f"Строка в листе «Регистрация»: {snapshot.row_number}")
    by_id = {p.id: p for p in snapshot.phases}
    all_issues: list[str] = []
    for p in snapshot.phases:
        all_issues.extend(p.warnings)
    if not all_issues:
        lines.append("Нарушений не найдено.")
    else:
        lines.append("Найдены отклонения:")
        for issue in all_issues:
            lines.append(f"- {issue}")
    if snapshot.stopped_at:
        lines.append(f"Проверка остановлена на этапе: {snapshot.stopped_at}")
    if snapshot.stage3_extracted:
        lines.append("")
        lines.append("Извлечённые ссылки (L/M/N/O):")
        for cell in snapshot.stage3_extracted:
            mark = " [зачёркнута]" if cell.strikethrough else ""
            lines.append(f"  {cell.column_key}: {cell.value}{mark}")
    s4 = by_id[PHASE_STAGE4]
    if s4.status != "skipped" and snapshot.metrics is not None:
        m = snapshot.metrics
        lines.append("")
        lines.append("Содержательный разбор диссертации (Stage 4):")
        lines.append(
            f"  страниц всего: {m.pages_total if m.pages_total is not None else '—'}"
        )
        lines.append(
            f"  источников: {m.sources_count if m.sources_count is not None else '—'}"
        )
        lines.append(f"  оформление: {m.compliance_label}")
    elif snapshot.stage4_skipped_reason:
        lines.append("")
        lines.append(f"Stage 4 пропущен: {snapshot.stage4_skipped_reason}")
    lines.append("")
    if applied:
        lines.append("(запись в лист выполнена: J/K/L/M/N/O + Stage 4)")
    else:
        lines.append(
            "(dry-run: лист не изменён — добавьте --apply для записи)"
        )
    return "\n".join(lines)


def render_commission_plaintext(snapshot: ProjectSnapshot) -> str:
    """Развёрнутый текст для печати / PDF / комиссии: те же факты, больше структуры."""

    if snapshot.unchanged:
        return render_spravka_telegram(snapshot, applied=True)

    lines: list[str] = [
        "Сведения о магистерском проекте (снимок)",
        "",
        f"Сформировано: {snapshot.generated_at}",
        f"Строка таблицы: {snapshot.row_number if snapshot.row_number is not None else '—'}",
        f"Статус заполнения: {snapshot.fill_status or '—'}",
        "",
        "Магистрант",
        f"  ФИО: {snapshot.identity.fio or '—'}",
        f"  Группа: {snapshot.identity.group or '—'}",
        f"  Место работы: {snapshot.identity.workplace or '—'}",
        f"  Должность: {snapshot.identity.position or '—'}",
        f"  Телефон: {snapshot.identity.phone or '—'}",
        f"  Научный руководитель: {snapshot.identity.supervisor or '—'}",
        "",
        "Ссылки и извлечённые данные",
        f"  Промежуточный отчёт: {snapshot.links.report_url or '—'}",
        f"  Папка проекта: {snapshot.links.project_folder_url or '—'}",
        f"  ЛКБ: {snapshot.links.lkb_url or '—'}",
        f"  Диссертация: {snapshot.links.dissertation_url or '—'}",
        f"  Публикация: {snapshot.links.publication_url or '—'}",
        f"  Проверка URL отчёта: валидность={snapshot.links.report_url_valid or '—'}, "
        f"доступ={snapshot.links.report_url_accessible or '—'}",
    ]
    if snapshot.links.dissertation_title or snapshot.links.dissertation_language:
        lines.extend(
            [
                f"  Название диссертации: {snapshot.links.dissertation_title or '—'}",
                f"  Язык диссертации: {snapshot.links.dissertation_language or '—'}",
            ]
        )
    lines.append("")
    lines.append("Этапы проверки")
    for ph in snapshot.phases:
        lines.append(f"  [{ph.id}] {ph.status} — {ph.summary or '—'}")
        for w in ph.warnings:
            lines.append(f"    - {w}")
    if snapshot.stage3_extracted:
        lines.append("")
        lines.append("Извлечённые ссылки (L/M/N/O)")
        for cell in snapshot.stage3_extracted:
            mark = " [зачёркнута]" if cell.strikethrough else ""
            lines.append(f"  {cell.column_key}: {cell.value}{mark}")
    if snapshot.metrics is not None:
        m = snapshot.metrics
        lines.extend(
            [
                "",
                "Метрики диссертации",
                f"  Страниц: {m.pages_total if m.pages_total is not None else '—'}",
                f"  Источников: {m.sources_count if m.sources_count is not None else '—'}",
                f"  Оформление: {m.compliance_label}",
            ]
        )
    elif snapshot.sheet_enrichment_metrics is not None:
        p, s, c = snapshot.sheet_enrichment_metrics
        lines.extend(
            [
                "",
                "Показатели по диссертации (лист, обогащение)",
                f"  Страниц: {p or '—'}",
                f"  Источников: {s or '—'}",
                f"  Оформление: {c or '—'}",
            ]
        )
    if snapshot.provenance.source_fingerprint:
        lines.append("")
        lines.append(f"Отпечаток входов (re-check): {snapshot.provenance.source_fingerprint}")
    return "\n".join(lines) + "\n"


def escape_tg_html(s: str) -> str:
    """Экранирование для ``parse_mode=HTML`` (текст и значения, не href)."""

    return html.escape(s or "", quote=False)


def _href(url: str) -> str:
    u = (url or "").strip()
    if not u or u in ("—", "–", "-"):
        return "—"
    if re.match(r"^https?://", u, re.I):
        return f'<a href="{html.escape(u, quote=True)}">открыть</a>'
    return escape_tg_html(u)


def render_spravka_telegram_html(snapshot: ProjectSnapshot, *, applied: bool) -> str:
    """Та же «справка» магистранту, что :func:`render_spravka_telegram`, в HTML-разметке."""

    if snapshot.unchanged:
        fio = snapshot.identity.fio or "(без ФИО)"
        row = snapshot.row_number if snapshot.row_number is not None else "?"
        return (
            f"<b>Без изменений</b>\n"
            f"Магистрант: {escape_tg_html(fio)}\n"
            f"Строка: {row}\n\n"
            "С прошлой проверки входы не менялись "
            f"(<i>--only-if-changed</i>).\n"
            "Лист и история проверок не тронуты."
        )

    lines: list[str] = []
    if snapshot.identity.fio:
        lines.append(
            f"<b>Магистрант</b>\n{escape_tg_html(snapshot.identity.fio)}"
        )
    if snapshot.row_number is not None:
        lines.append(
            f"<b>Строка в «Регистрация»</b>\n{snapshot.row_number}"
        )
    by_id = {p.id: p for p in snapshot.phases}
    all_issues: list[str] = []
    for p in snapshot.phases:
        all_issues.extend(p.warnings)
    lines.append("<b>Результаты</b>")
    if not all_issues:
        lines.append("Нарушений не найдено.")
    else:
        lines.append("Найдены отклонения:")
        for issue in all_issues:
            lines.append(f"• {escape_tg_html(issue)}")
    if snapshot.stopped_at:
        lines.append(
            f"<b>Остановка</b>\nэтап: {escape_tg_html(snapshot.stopped_at)}"
        )
    if snapshot.stage3_extracted:
        lines.append("\n<b>Ссылки (L/M/N/O)</b>")
        for cell in snapshot.stage3_extracted:
            mark = " <i>(зачёркнута)</i>" if cell.strikethrough else ""
            lines.append(
                f"{escape_tg_html(cell.column_key)}: {escape_tg_html(cell.value)}{mark}"
            )
    s4 = by_id[PHASE_STAGE4]
    if s4.status != "skipped" and snapshot.metrics is not None:
        m = snapshot.metrics
        lines.append("\n<b>Диссертация (этап 4)</b>")
        lines.append(
            f"страниц: {m.pages_total if m.pages_total is not None else '—'}"
        )
        lines.append(
            f"источников: {m.sources_count if m.sources_count is not None else '—'}"
        )
        lines.append(f"оформление: {escape_tg_html(m.compliance_label)}")
    elif snapshot.stage4_skipped_reason:
        lines.append("")
        lines.append(
            f"<b>Этап 4</b> пропущен: {escape_tg_html(snapshot.stage4_skipped_reason)}"
        )
    lines.append("")
    if applied:
        lines.append(
            "<i>Запись в лист выполнена: J—R, этап 4 при наличии</i>"
        )
    else:
        lines.append(
            "<i>Dry-run: лист не изменён — в CLI укажите --apply</i>"
        )
    return "\n".join(lines)


def render_commission_telegram_html(snapshot: ProjectSnapshot) -> str:
    """Развёрнутый снимок для чата: те же факты, что :func:`render_commission_plaintext`, в HTML."""

    if snapshot.unchanged:
        return render_spravka_telegram_html(snapshot, applied=True)

    lines: list[str] = [
        "<b>Сведения о магистерском проекте</b>",
        "",
        f"Сформировано: <code>{escape_tg_html(snapshot.generated_at)}</code>",
        f"Строка: {snapshot.row_number if snapshot.row_number is not None else '—'}",
        f"Статус заполнения: {escape_tg_html(snapshot.fill_status) or '—'}",
        "",
        "<b>Магистрант</b>",
        f"ФИО: {escape_tg_html(snapshot.identity.fio) or '—'}",
        f"Группа: {escape_tg_html(snapshot.identity.group) or '—'}",
        f"Место работы: {escape_tg_html(snapshot.identity.workplace) or '—'}",
        f"Должность: {escape_tg_html(snapshot.identity.position) or '—'}",
        f"Телефон: {escape_tg_html(snapshot.identity.phone) or '—'}",
        f"Руководитель: {escape_tg_html(snapshot.identity.supervisor) or '—'}",
        "",
        "<b>Документы и ссылки</b>",
        f"Промежуточный отчёт: {_href(snapshot.links.report_url)}",
        f"Папка проекта: {_href(snapshot.links.project_folder_url)}",
        f"ЛКБ: {_href(snapshot.links.lkb_url)}",
        f"Диссертация: {_href(snapshot.links.dissertation_url)}",
        f"Публикация: {_href(snapshot.links.publication_url)}",
        f"URL отчёта: валидность {escape_tg_html(snapshot.links.report_url_valid) or '—'}, "
        f"доступ {escape_tg_html(snapshot.links.report_url_accessible) or '—'}",
    ]
    if snapshot.links.dissertation_title or snapshot.links.dissertation_language:
        lines.extend(
            [
                f"Название диссертации: {escape_tg_html(snapshot.links.dissertation_title) or '—'}",
                f"Язык: {escape_tg_html(snapshot.links.dissertation_language) or '—'}",
            ]
        )
    lines.append("<b>Этапы</b>")
    for ph in snapshot.phases:
        lines.append(
            f"[{escape_tg_html(ph.id)}] {escape_tg_html(ph.status)} — "
            f"{escape_tg_html(ph.summary) or '—'}"
        )
        for w in ph.warnings:
            lines.append(f"  • {escape_tg_html(w)}")
    if snapshot.stage3_extracted:
        lines.append("\n<b>Колонки L/M/N/O</b>")
        for cell in snapshot.stage3_extracted:
            mark = " <i>(зачёркнута)</i>" if cell.strikethrough else ""
            lines.append(
                f"{escape_tg_html(cell.column_key)}: {escape_tg_html(cell.value)}{mark}"
            )
    if snapshot.metrics is not None:
        m = snapshot.metrics
        lines.extend(
            [
                "\n<b>Метрики диссертации</b>",
                f"Страниц: {m.pages_total if m.pages_total is not None else '—'}",
                f"Источников: {m.sources_count if m.sources_count is not None else '—'}",
                f"Оформление: {escape_tg_html(m.compliance_label)}",
            ]
        )
    elif snapshot.sheet_enrichment_metrics is not None:
        p, s, c = snapshot.sheet_enrichment_metrics
        lines.extend(
            [
                "\n<b>Показатели (лист)</b>",
                f"Страниц: {escape_tg_html(p) or '—'}",
                f"Источников: {escape_tg_html(s) or '—'}",
                f"Оформление: {escape_tg_html(c) or '—'}",
            ]
        )
    if snapshot.provenance.source_fingerprint:
        lines.append(
            f"\n<i>Отпечаток re-check: {escape_tg_html(snapshot.provenance.source_fingerprint)}</i>"
        )
    return "\n".join(lines)
