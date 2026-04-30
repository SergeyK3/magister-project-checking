"""Обогащение строки регистрации данными из промежуточного отчёта."""

from __future__ import annotations

import logging
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.dissertation_meta import (
    detect_dissertation_language_from_docx_bytes,
    detect_dissertation_language_from_gdoc,
    detect_dissertation_title_from_docx_bytes,
    detect_dissertation_title_from_gdoc,
    warn_if_unusual_language,
)
from magister_checking.dissertation_metrics import (
    analyze_dissertation,
    analyze_docx_bytes,
    count_pdf_pages_via_drive_export,
    download_drive_file_bytes,
)
from magister_checking.drive_urls import extract_google_file_id, is_google_drive_folder_url
from magister_checking.report_parser import ParsedReport, parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id

logger = logging.getLogger(__name__)

URL_MISSING = "url отсутствует"

# Совместимость: раньше при любом сбое подставлялось общее сообщение без пояснения.
URL_UNAVAILABLE = "url недоступен"


def _service_account_credentials(config: BotConfig) -> Credentials:
    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def _exc_one_line(exc: BaseException, *, limit: int = 220) -> str:
    s = f"{type(exc).__name__}: {exc}"
    s = s.replace("\n", " ").strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _exc_detail_blob_text(exc: BaseException) -> str:
    """Текст для классификации ошибок; у googleapiclient.HttpError поле ``content`` — bytes."""

    parts: list[str] = []
    for name in ("content", "error_details"):
        raw = getattr(exc, name, None)
        if raw is None or raw == "":
            continue
        if isinstance(raw, (bytes, bytearray)):
            parts.append(bytes(raw).decode("utf-8", errors="replace"))
        else:
            parts.append(str(raw))
    parts.append(str(exc))
    return "".join(parts)


def _human_google_docs_report_read_failure(exc: BaseException) -> str:
    """Краткое пояснение для ячеек без «простыни» HtmlError/Google API.

    Вызывающий уже пишет в лог полный объект исключения; пользователю и PDF достаточно класса ошибки."""

    low = _exc_detail_blob_text(exc).lower()

    # Типичный случай из логов: API не активирован для проекта GCP, к которому привязан ключ.
    if (
        "has not been used" in low
        or "service_disabled" in low
        or "not been enabled" in low
    ):
        return (
            "Промежуточный отчёт бот читает только через Google Docs API (по сервисному ключу), а не «как гость в браузере». "
            "В Google Cloud для проекта этого ключа не включён сервис «Google Docs API» (или после включения ещё не дошла задержка). "
            "Консоль GCP → APIs & Services → Library → «Google Docs API» → Enable. "
            "Режим «доступ по ссылке» в интерфейсе Docs сам по себе не включает API в облаке; это настройка проекта ключа, а не оценка ссылок магистранта в таблице отчёта."
        )

    if "403" in low or "forbidden" in low:
        return (
            "Google Docs API отклонил запрос к документу отчёта (часто 403). "
            "Попросите магистранта открыть доступ к файлу отчёта для чтения всем по ссылке "
            "(«Настроить доступ» → «Все, у кого есть ссылка» → «Читатель»)."
        )

    if "404" in low or "not found" in low:
        return "Документ отчёта не найден по id (404). Проверьте ссылку в строке регистрации."

    short = _exc_one_line(exc, limit=160)
    return f"Не удалось получить текст промежуточного отчёта по API: {short}"


_LINK_LABEL_PROJECT = "Папка проекта"
_LINK_LABEL_LKB = "ЛКБ"
_LINK_LABEL_DISS = "Диссертация (ссылка в промежуточном отчёте)"
_LINK_LABEL_PUBL = "Публикация или статья"

# Один сбой чтения тела отчёта по API — не дублируем длинный GCP-текст в ЛКБ/диссертации/публикации.
_DETAIL_ENRICHMENT_SKIP_CHILD_LINKS = (
    "Не заполнено из текста промежуточного отчёта: бот сначала получает документ отчёта по Google Docs API. "
    "Полное пояснение той же ошибки — в колонке «Папка проекта» этой строки (одна техническая причина на всю строку)."
)


def _link_column_error(link_label: str, detail_without_label: str) -> str:
    """Сообщение в ячейку: подпись поля + причина (не «URL мёртв в браузере»)."""
    return f"{link_label}: {detail_without_label}"


def _empty_metric_result() -> tuple[str, str, str, str, str]:
    return ("", "", "", "", "")


def _four_link_failures(
    *,
    project_detail: str,
    lkb_detail: str,
    dissertation_detail: str,
    publication_detail: str,
) -> dict[str, str]:
    """Четыре независимых текста ошибок — по одному на колонку ссылок."""
    return {
        "project_folder_url": _link_column_error(_LINK_LABEL_PROJECT, project_detail),
        "lkb_url": _link_column_error(_LINK_LABEL_LKB, lkb_detail),
        "dissertation_url": _link_column_error(_LINK_LABEL_DISS, dissertation_detail),
        "publication_url": _link_column_error(_LINK_LABEL_PUBL, publication_detail),
        "pages_total": "",
        "sources_count": "",
        "compliance": "",
        "dissertation_title": "",
        "dissertation_language": "",
    }


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
) -> tuple[str, str, str, str, str]:
    """Возвращает ``(pages_total, sources_count, compliance, dissertation_title, dissertation_language)``."""

    pages_total = ""
    sources_count = ""
    compliance = ""
    dissertation_title = ""
    dissertation_language = ""
    if not dissertation_url:
        return pages_total, sources_count, compliance, dissertation_title, dissertation_language

    file_id = extract_google_file_id(dissertation_url)
    try:
        diss_doc = docs_service.documents().get(documentId=file_id).execute()
    except Exception:  # noqa: BLE001
        data = download_drive_file_bytes(drive_service=drive_service, file_id=file_id)
        if not data:
            return (
                pages_total,
                sources_count,
                compliance,
                dissertation_title,
                dissertation_language,
            )
        metrics = analyze_docx_bytes(data)
        if metrics.approx_pages:
            pages_total = str(metrics.approx_pages)
        if metrics.sources_count is not None:
            sources_count = str(metrics.sources_count)
        compliance = _compliance_value(metrics.formatting_compliance)
        dissertation_title = detect_dissertation_title_from_docx_bytes(data)
        dissertation_language = detect_dissertation_language_from_docx_bytes(data)
        warn_if_unusual_language(
            dissertation_language, context=f"dissertation_url={dissertation_url}"
        )
        return pages_total, sources_count, compliance, dissertation_title, dissertation_language

    metrics = analyze_dissertation(diss_doc)
    pdf_pages = count_pdf_pages_via_drive_export(drive_service=drive_service, file_id=file_id)
    chosen_pages = pdf_pages if pdf_pages is not None else metrics.approx_pages
    if chosen_pages:
        pages_total = str(chosen_pages)
    if metrics.sources_count is not None:
        sources_count = str(metrics.sources_count)
    compliance = _compliance_value(metrics.formatting_compliance)
    dissertation_title = detect_dissertation_title_from_gdoc(diss_doc)
    dissertation_language = detect_dissertation_language_from_gdoc(diss_doc)
    warn_if_unusual_language(
        dissertation_language, context=f"dissertation_url={dissertation_url}"
    )
    return pages_total, sources_count, compliance, dissertation_title, dissertation_language


def _link_value(url: str | None) -> str:
    return (url or "").strip() or URL_MISSING


def build_sheet_enrichment(config: BotConfig, user_form: UserForm) -> dict[str, str]:
    """Собирает доп. поля для листа регистрации по report_url.

    Возвращает только значения, которые можно положить в пользовательские
    колонки листа (например, ``Ссылка на ЛКБ``, ``Число страниц``).

    Сбой на этапе «разрешить id / скачать Doc / разобрать таблицу» обрабатывается
    отдельно с понятными сообщениями **по каждому из четырёх полей ссылок**.
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
        report_doc_id = resolve_report_google_doc_id(
            report_url, drive_service=drive_service
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "report_enrichment: resolve_report_google_doc_id failed: %s",
            _exc_one_line(exc),
        )
        line = _exc_one_line(exc)
        common = (
            f"не удалось определить файл отчёта по этой строке регистрации ({line}); "
            f"если указана папка — проверьте имя файла отчёта и доступ сервисного аккаунта "
            f"Google к папке"
        )
        return _four_link_failures(
            project_detail=common,
            lkb_detail=common,
            dissertation_detail=common,
            publication_detail=common,
        )

    try:
        report_doc = docs_service.documents().get(documentId=report_doc_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "report_enrichment: documents().get(report) failed: %s",
            _exc_one_line(exc, limit=400),
        )
        hint = _human_google_docs_report_read_failure(exc)
        return _four_link_failures(
            project_detail=hint,
            lkb_detail=_DETAIL_ENRICHMENT_SKIP_CHILD_LINKS,
            dissertation_detail=_DETAIL_ENRICHMENT_SKIP_CHILD_LINKS,
            publication_detail=_DETAIL_ENRICHMENT_SKIP_CHILD_LINKS,
        )

    try:
        parsed = parse_intermediate_report(report_doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "report_enrichment: parse_intermediate_report failed: %s",
            _exc_one_line(exc),
        )
        line = _exc_one_line(exc)
        common = (
            f"сбой разбора структуры отчёта ({line}). Бывает при пустой ячейке таблицы "
            f"(content null) или нестандартной вёрстке — см. лог бота"
        )
        return _four_link_failures(
            project_detail=common,
            lkb_detail=common,
            dissertation_detail=common,
            publication_detail=common,
        )

    project_folder_url = parsed.project_folder_url or (
        report_url if is_google_drive_folder_url(report_url) else ""
    )
    publication_url = parsed.results_article_url or parsed.review_article_url or ""

    try:
        (
            pages_total,
            sources_count,
            compliance,
            dissertation_title,
            dissertation_language,
        ) = _analyze_dissertation_fields(
            docs_service=docs_service,
            drive_service=drive_service,
            dissertation_url=parsed.dissertation_url,
            parsed=parsed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "report_enrichment: _analyze_dissertation_fields failed: %s",
            _exc_one_line(exc),
        )
        (
            pages_total,
            sources_count,
            compliance,
            dissertation_title,
            dissertation_language,
        ) = _empty_metric_result()
        pages_total = _link_column_error(
            "Метрики диссертации",
            f"ошибка анализа файла диссертации ({_exc_one_line(exc)})",
        )

    return {
        "project_folder_url": _link_value(project_folder_url),
        "lkb_url": _link_value(parsed.lkb_url),
        "dissertation_url": _link_value(parsed.dissertation_url),
        "publication_url": _link_value(publication_url),
        "pages_total": pages_total,
        "sources_count": sources_count,
        "compliance": compliance,
        "dissertation_title": dissertation_title,
        "dissertation_language": dissertation_language,
    }
