"""Обогащение строки регистрации данными из промежуточного отчёта."""

from __future__ import annotations

from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.dissertation_metrics import (
    analyze_dissertation,
    analyze_docx_bytes,
    count_pdf_pages_via_drive_export,
    download_drive_file_bytes,
)
from magister_checking.drive_urls import extract_google_file_id, is_google_drive_folder_url
from magister_checking.report_parser import ParsedReport, parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id

URL_MISSING = "url отсутствует"
URL_UNAVAILABLE = "url недоступен"


def _service_account_credentials(config: BotConfig) -> Credentials:
    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def _compliance_value(value: bool | None) -> str:
    if value is True:
        return "Соответствует"
    if value is False:
        return "Не соответствует"
    return ""


def _analyze_dissertation_fields(
    *,
    docs_service: Any,
    drive_service: Any,
    dissertation_url: str | None,
    parsed: ParsedReport,
) -> tuple[str, str, str]:
    pages_total = ""
    sources_count = ""
    compliance = ""
    if not dissertation_url:
        return pages_total, sources_count, compliance

    file_id = extract_google_file_id(dissertation_url)
    try:
        diss_doc = docs_service.documents().get(documentId=file_id).execute()
    except Exception:  # noqa: BLE001
        data = download_drive_file_bytes(drive_service=drive_service, file_id=file_id)
        if not data:
            return pages_total, sources_count, compliance
        metrics = analyze_docx_bytes(data)
        if metrics.approx_pages:
            pages_total = str(metrics.approx_pages)
        if metrics.sources_count is not None:
            sources_count = str(metrics.sources_count)
        compliance = _compliance_value(metrics.formatting_compliance)
        return pages_total, sources_count, compliance

    metrics = analyze_dissertation(diss_doc)
    pdf_pages = count_pdf_pages_via_drive_export(drive_service=drive_service, file_id=file_id)
    chosen_pages = pdf_pages if pdf_pages is not None else metrics.approx_pages
    if chosen_pages:
        pages_total = str(chosen_pages)
    if metrics.sources_count is not None:
        sources_count = str(metrics.sources_count)
    compliance = _compliance_value(metrics.formatting_compliance)
    return pages_total, sources_count, compliance


def _link_value(url: str | None) -> str:
    return (url or "").strip() or URL_MISSING


def build_sheet_enrichment(config: BotConfig, user_form: UserForm) -> dict[str, str]:
    """Собирает доп. поля для листа регистрации по report_url.

    Возвращает только значения, которые можно положить в пользовательские
    колонки листа (например, ``Ссылка на ЛКБ``, ``Число страниц``).
    """

    report_url = (user_form.report_url or "").strip()
    if not report_url:
        return {
            "project_folder_url": URL_MISSING,
            "lkb_url": URL_MISSING,
            "dissertation_url": URL_MISSING,
            "publication_url": URL_MISSING,
            "pages_total": "",
            "sources_count": "",
            "compliance": "",
            "dissertation_title": "",
            "dissertation_language": "",
        }

    creds = _service_account_credentials(config)
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        report_doc_id = resolve_report_google_doc_id(report_url, drive_service=drive_service)
        report_doc = docs_service.documents().get(documentId=report_doc_id).execute()
        parsed = parse_intermediate_report(report_doc)
    except Exception:
        return {
            "project_folder_url": URL_UNAVAILABLE,
            "lkb_url": URL_UNAVAILABLE,
            "dissertation_url": URL_UNAVAILABLE,
            "publication_url": URL_UNAVAILABLE,
            "pages_total": "",
            "sources_count": "",
            "compliance": "",
            "dissertation_title": "",
            "dissertation_language": "",
        }

    project_folder_url = parsed.project_folder_url or (
        report_url if is_google_drive_folder_url(report_url) else ""
    )
    publication_url = parsed.results_article_url or parsed.review_article_url or ""
    pages_total, sources_count, compliance = _analyze_dissertation_fields(
        docs_service=docs_service,
        drive_service=drive_service,
        dissertation_url=parsed.dissertation_url,
        parsed=parsed,
    )

    return {
        "project_folder_url": _link_value(project_folder_url),
        "lkb_url": _link_value(parsed.lkb_url),
        "dissertation_url": _link_value(parsed.dissertation_url),
        "publication_url": _link_value(publication_url),
        "pages_total": pages_total,
        "sources_count": sources_count,
        "compliance": compliance,
        "dissertation_title": "",
        "dissertation_language": "",
    }
