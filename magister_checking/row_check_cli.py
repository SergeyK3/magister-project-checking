"""Glue-слой IO поверх чистого конвейера row_pipeline (п.7 ТЗ).

Отсюда CLI-команда ``check-row`` получает всё необходимое для прогона
одной строки листа «Регистрация» через этапы 1-3: открывает таблицу,
читает строку, подгружает промежуточный отчёт, проверяет HTTP-доступность
внешних ссылок и печатает «справку» магистранту в stdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    RowCheckReport,
    _LINK_FIELDS,
    _extract_link,
    run_row_pipeline,
)
from magister_checking.bot.sheets_repo import (
    GOOGLE_SCOPES,
    apply_row_check_updates,
    find_rows_by_fio,
    get_spreadsheet,
    load_user,
)
from magister_checking.bot.validation import (
    REPORT_URL_WRONG_TARGET_MESSAGE,
    check_report_url,
)
from magister_checking.drive_docx import google_doc_from_drive_file
from magister_checking.drive_urls import (
    classify_drive_url,
    extract_google_file_id,
)
from magister_checking.report_parser import ParsedReport, parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id


_REGISTRATION_WORKSHEET_NAME = "Регистрация"


@dataclass
class RowLocator:
    """Как идентифицировать строку листа: по номеру или по ФИО."""

    row_number: int | None = None
    fio: str | None = None


def _service_account_credentials(config: BotConfig) -> Credentials:
    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def _resolve_row_number(
    worksheet: Any,
    locator: RowLocator,
) -> int:
    if locator.row_number is not None:
        return locator.row_number
    if locator.fio:
        matches = find_rows_by_fio(worksheet, locator.fio)
        if not matches:
            raise ValueError(f"ФИО не найдено в листе: {locator.fio!r}")
        if len(matches) > 1:
            raise ValueError(
                f"Найдено несколько строк по ФИО {locator.fio!r}: {matches}. "
                "Уточните --row."
            )
        return matches[0]
    raise ValueError("Не указан номер строки (--row) или ФИО (--fio).")


def _try_load_report_document(
    *,
    report_url: str,
    docs_service: Any,
    drive_service: Any,
    docx_conversion_folder_id: str = "",
) -> Any | None:
    """Пытается получить тело Google Doc по ссылке на отчёт.

    Если по ссылке лежит .docx, временно копирует его в
    ``docx_conversion_folder_id`` с конверсией в Google Doc, читает через
    Docs API и удаляет копию.

    Возвращает dict документа или None при любой ошибке (неподдерживаемый
    формат, нет доступа, папка без отчёта, нет папки для конверсии и т.п.).
    """

    if not report_url:
        return None
    try:
        doc_id = resolve_report_google_doc_id(report_url, drive_service=drive_service)
    except Exception:  # noqa: BLE001
        return None
    try:
        with google_doc_from_drive_file(
            drive_service,
            doc_id,
            conversion_folder_id=docx_conversion_folder_id,
        ) as loadable_id:
            return docs_service.documents().get(documentId=loadable_id).execute()
    except Exception:  # noqa: BLE001
        return None


def _try_parse_report(document: Any) -> ParsedReport | None:
    if not document:
        return None
    try:
        return parse_intermediate_report(document)
    except Exception:  # noqa: BLE001
        return None


def _collect_stage3_urls(parsed: ParsedReport) -> list[str]:
    urls: list[str] = []
    for key, _label in _LINK_FIELDS:
        url = _extract_link(parsed, key)
        if url and url not in urls:
            urls.append(url)
    return urls


def _build_accessibility_map(urls: list[str]) -> dict[str, bool]:
    accessibility: dict[str, bool] = {}
    for url in urls:
        valid, accessible = check_report_url(url)
        accessibility[url] = valid == "yes" and accessible == "yes"
    return accessibility


def _prefetch_drive_file_mimes(
    *, urls: list[str], drive_service: Any
) -> dict[str, str]:
    """Для всех drive_file URL получает mimeType через Drive API.

    Используется Stage 3, чтобы проверить, что ЛКБ/публикация — PDF, а
    .docx-вариант диссертации действительно DOCX. Folder-URL и Doc-URL
    сюда не попадают (тип однозначен по URL). Ошибки API (404/403,
    сеть) — не блокирующие: для такого URL mime в карту не пишется,
    и Stage 3 трактует это как «не удалось определить формат» —
    warning + зачёркивание.

    drive.files().get вызывается с supportsAllDrives=True, чтобы
    работало и для файлов в Shared Drive магистрантов.
    """

    mimes: dict[str, str] = {}
    for url in urls:
        if classify_drive_url(url) != "drive_file":
            continue
        try:
            file_id = extract_google_file_id(url)
        except ValueError:
            continue
        try:
            meta = (
                drive_service.files()
                .get(fileId=file_id, fields="mimeType", supportsAllDrives=True)
                .execute()
            )
        except Exception:  # noqa: BLE001
            continue
        mime = (meta or {}).get("mimeType") or ""
        if mime:
            mimes[url] = mime
    return mimes


def run_row_check(
    config: BotConfig,
    locator: RowLocator,
    *,
    skip_http: bool = False,
    apply: bool = False,
) -> RowCheckReport:
    """Основной glue: открывает таблицу, подгружает артефакты, запускает пайплайн.

    При ``apply=True`` пишет результаты Stage 2/Stage 3 в лист (колонки
    J/K/L/M/N/O и флаг ``strikethrough`` для неоткрывающихся ссылок Stage 3).
    По умолчанию лист не изменяется (dry-run).
    """

    spreadsheet = get_spreadsheet(config)
    worksheet = spreadsheet.worksheet(_REGISTRATION_WORKSHEET_NAME)
    row_number = _resolve_row_number(worksheet, locator)
    user: UserForm = load_user(worksheet, row_number)

    report_url = (user.report_url or "").strip()

    creds = _service_account_credentials(config)
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    report_document = _try_load_report_document(
        report_url=report_url,
        docs_service=docs_service,
        drive_service=drive_service,
        docx_conversion_folder_id=config.docx_conversion_folder_id,
    )

    url_probe: tuple[str, str] | None = None
    if not skip_http and report_url:
        url_probe = check_report_url(report_url)

    parsed_report = _try_parse_report(report_document) if report_document else None

    link_accessibility: dict[str, bool] | None = None
    link_mime_types: dict[str, str] | None = None
    if parsed_report is not None:
        stage3_urls = _collect_stage3_urls(parsed_report)
        if not skip_http:
            link_accessibility = _build_accessibility_map(stage3_urls)
        # MIME-prefetch — Drive API call, но не HTTP-проба «open url»; делаем
        # его независимо от skip_http, потому что без mime Stage 3 для
        # лкб/публикации/диссертации-как-.docx не может ответственно
        # сказать «PDF/DOCX». Если drive_service недоступен (ошибка
        # ранее), вызовы внутри будут падать в except и просто пропускаться.
        link_mime_types = _prefetch_drive_file_mimes(
            urls=stage3_urls, drive_service=drive_service
        )

    pipeline_report = run_row_pipeline(
        user,
        report_document=report_document,
        url_probe=url_probe,
        parsed_report=parsed_report,
        link_accessibility=link_accessibility,
        link_mime_types=link_mime_types,
        row_number=row_number,
    )

    # Если документ не удалось загрузить, но URL валиден и доступен — это значит,
    # что ссылка ведёт не на «Промежуточный отчёт» (другой тип файла/папка без
    # нужного файла). Дублируем сообщение в Stage 1.
    if (
        report_url
        and report_document is None
        and url_probe
        and url_probe == ("yes", "yes")
        and REPORT_URL_WRONG_TARGET_MESSAGE not in pipeline_report.stage1.issues
    ):
        pipeline_report.stage1.issues.append(REPORT_URL_WRONG_TARGET_MESSAGE)
        pipeline_report.stage1.passed = False
        if pipeline_report.stopped_at is None:
            pipeline_report.stopped_at = "stage1"

    if apply:
        report_url_valid = url_probe[0] if url_probe is not None else None
        report_url_accessible = url_probe[1] if url_probe is not None else None
        apply_row_check_updates(
            worksheet,
            row_number,
            report_url_valid=report_url_valid,
            report_url_accessible=report_url_accessible,
            stage3_cells=pipeline_report.stage3_cells,
        )

    return pipeline_report


def format_report(report: RowCheckReport, *, applied: bool = False) -> str:
    """Форматирует отчёт в многострочный текст «справки».

    ``applied=True`` дописывает пометку о том, что результаты Stage 2/Stage 3
    записаны в лист; иначе выводит пометку dry-run.
    """

    lines = report.spravka_lines()
    if report.stage3_cells:
        lines.append("")
        lines.append("Извлечённые ссылки (L/M/N/O):")
        for cell in report.stage3_cells:
            mark = " [зачёркнута]" if cell.strikethrough else ""
            lines.append(f"  {cell.column_key}: {cell.value}{mark}")
    lines.append("")
    if applied:
        lines.append("(запись в лист выполнена: J/K/L/M/N/O)")
    else:
        lines.append("(dry-run: лист не изменён — добавьте --apply для записи)")
    return "\n".join(lines)
