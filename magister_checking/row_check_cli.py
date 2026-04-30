"""Glue-слой IO поверх чистого конвейера row_pipeline (п.7 ТЗ).

Отсюда CLI-команда ``check-row`` получает всё необходимое для прогона
одной строки листа «Регистрация» через этапы 1-3: открывает таблицу,
читает строку, подгружает промежуточный отчёт, проверяет HTTP-доступность
внешних ссылок и печатает «справку» магистранту в stdout.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    RowCheckReport,
    _LINK_FIELDS,
    _extract_link,
    compliance_to_text,
    resolve_fill_status_after_row_check,
    run_row_pipeline,
)
from magister_checking.bot.sheets_repo import (
    GOOGLE_SCOPES,
    RecheckHistoryEntry,
    append_recheck_history,
    apply_row_check_updates,
    find_rows_by_fio,
    get_spreadsheet,
    load_user,
    read_last_recheck_entry,
    read_sheet_link_overrides_for_row,
    write_dissertation_meta_columns,
)
from magister_checking.bot.report_enrichment import build_sheet_enrichment
from magister_checking.project_snapshot import build_project_snapshot
from magister_checking.snapshot_render import (
    render_commission_plaintext,
    render_commission_telegram_html,
    render_spravka_telegram,
    render_spravka_telegram_html,
)
from magister_checking.bot.validation import (
    REPORT_URL_WRONG_TARGET_MESSAGE,
    check_report_url,
    check_report_url_target_kind,
)
from magister_checking.dissertation_meta import (
    detect_dissertation_language_from_docx_bytes,
    detect_dissertation_language_from_gdoc,
    detect_dissertation_title_from_docx_bytes,
    detect_dissertation_title_from_gdoc,
    warn_if_unusual_language,
)
from magister_checking.dissertation_metrics import (
    DissertationMetrics,
    analyze_dissertation,
    analyze_docx_bytes,
    count_pdf_pages_via_drive_export,
)
from magister_checking.formatting_rules import load_formatting_rules
from magister_checking.drive_acl import drive_file_has_anyone_with_link_permission
from magister_checking.drive_docx import google_doc_from_drive_file
from magister_checking.drive_urls import (
    classify_drive_url,
    extract_google_file_id,
)
from magister_checking.snapshot_drive import try_upload_project_snapshot_json
from magister_checking.report_parser import ParsedReport, parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id


_REGISTRATION_WORKSHEET_NAME = "Регистрация"
logger = logging.getLogger(__name__)

# MIME-тип .docx-диссертации; дублирует значение из bot.row_pipeline.DOCX_MIME,
# но импортировать его сюда не имеет смысла (избегаем лишней зависимости
# CLI ↔ pipeline). Стандартизованный OOXML mime, не меняется.
_DOCX_MIME_FOR_DISSERTATION = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Stage 1: HttpError 401/403 при загрузке отчёта — тексты зависят от ACL в Drive.
REPORT_DRIVE_ACL_DENIED_MESSAGE = (
    "Промежуточный отчёт недоступен для проверки (бот не смог открыть файл в Google). "
    "Откройте доступ на чтение для всех по ссылке: в Google Drive — «Настроить доступ» → "
    "«Все, у кого есть ссылка» → роль «Читатель»."
)
REPORT_DRIVE_ANYONE_LINK_BUT_BOT_FAILED_MESSAGE = (
    "Промежуточный отчёт не удалось обработать через API бота (конвертация PDF/DOCX "
    "или чтение текста документа), хотя доступ «Все, у кого есть ссылка» уже включён. "
    "Администратору: буферная папка Shared Drive для конвертации "
    "(переменные GOOGLE_DRIVE_BUFFER_FOLDER_* / DOCX_CONVERSION_*), роль сервисного "
    "аккаунта на ней, включение Google Docs API — см. лог бота и руководство администратора."
)


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


def load_user_enrichment_for_row(
    config: BotConfig, row_number: int
) -> tuple[UserForm, dict[str, str]]:
    """Актуальная строка листа + обогащение — для снимка/справки после ``run_row_check``."""

    spreadsheet = get_spreadsheet(config)
    worksheet = spreadsheet.worksheet(_REGISTRATION_WORKSHEET_NAME)
    user = load_user(worksheet, row_number)
    return user, build_sheet_enrichment(config, user)


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
) -> tuple[Any | None, bool]:
    """Пытается получить тело Google Doc по ссылке на отчёт.

    Если по ссылке лежит .docx, временно копирует его в
    ``docx_conversion_folder_id`` с конверсией в Google Doc, читает через
    Docs API и удаляет копию.

    Возвращает ``(document | None, permission_denied)``.
    ``permission_denied`` — True при HTTP 401/403 от Drive/Docs API при загрузке
    отчёта (причина уточняется в ``run_row_check``: доступ по ACL или сбой
    конвертации/буфера при уже открытой ссылке).
    """

    if not report_url:
        return None, False
    try:
        doc_id = resolve_report_google_doc_id(report_url, drive_service=drive_service)
    except Exception:  # noqa: BLE001
        return None, False
    try:
        with google_doc_from_drive_file(
            drive_service,
            doc_id,
            conversion_folder_id=docx_conversion_folder_id,
        ) as loadable_id:
            doc = docs_service.documents().get(documentId=loadable_id).execute()
            return doc, False
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        try:
            code = int(status) if status is not None else 0
        except (TypeError, ValueError):
            code = 0
        denied = code in (403, 401)
        return None, denied
    except Exception:  # noqa: BLE001
        return None, False


def _try_parse_report(document: Any) -> ParsedReport | None:
    if not document:
        return None
    try:
        return parse_intermediate_report(document)
    except Exception:  # noqa: BLE001
        return None


def _normalize_manual_sheet_link(value: str) -> str:
    """Приводит текст ячейки к одному URL для ручного ввода в лист.

    Поддержка частых ошибок: пробелы/переносы из копипасты, ссылки без схемы
    ``https://`` для доменов Google.
    """

    s = (value or "").strip().replace("\ufeff", "").strip()
    if not s:
        return ""
    s = " ".join(s.split())
    if len(s) > 4000:
        return ""
    lower = s.lower()
    if not (s.startswith("http://") or s.startswith("https://")):
        if lower.startswith("docs.google.com/") or lower.startswith("drive.google.com/"):
            s = "https://" + s
    if not (s.startswith("http://") or s.startswith("https://")):
        return ""
    return s


def _plausible_manual_sheet_link(value: str) -> str:
    """Нормализованный URL или пустая строка, если значение — не доверяемая ссылка."""

    s = _normalize_manual_sheet_link(value)
    if not s:
        return ""
    low = s.lower()
    needles = (
        "google docs api",
        "не удалось получить текст",
        "url отсутствует",
        "url недоступен",
        "не заполнено из текста",
        "service_disabled",
        "has not been used",
    )
    if any(n in low for n in needles):
        return ""
    return s


def _is_plausible_manual_link_cell(value: str) -> bool:
    """True, если ячейка похожа на URL, введённый человеком (не текст ошибки бота)."""

    return bool(_plausible_manual_sheet_link(value))


def _merge_parsed_report_with_sheet_links(
    parsed: ParsedReport | None,
    overrides: dict[str, str],
) -> ParsedReport | None:
    """Подставляет ссылки из листа поверх (или вместо) результата парсера отчёта.

    Значения из таблицы учитываются только если они проходят
    :func:`_is_plausible_manual_link_cell` — чтобы не трактовать старые
    сообщения об ошибках как URL."""

    plausible: dict[str, str] = {}
    for key in (
        "project_folder_url",
        "lkb_url",
        "dissertation_url",
        "publication_url",
    ):
        raw = (overrides.get(key) or "").strip()
        link = _plausible_manual_sheet_link(raw)
        if link:
            plausible[key] = link

    if parsed is None:
        if not plausible:
            return None
        return ParsedReport(
            lkb_status="да" if plausible.get("lkb_url") else "?",
            lkb_url=plausible.get("lkb_url"),
            dissertation_url=plausible.get("dissertation_url"),
            review_article_url=None,
            review_article_note="",
            results_article_url=None,
            project_folder_url=plausible.get("project_folder_url"),
            publication_url=plausible.get("publication_url"),
        )

    cur = parsed
    if plausible.get("project_folder_url"):
        cur = replace(cur, project_folder_url=plausible["project_folder_url"])
    if plausible.get("lkb_url"):
        cur = replace(cur, lkb_url=plausible["lkb_url"], lkb_status="да")
    if plausible.get("dissertation_url"):
        cur = replace(cur, dissertation_url=plausible["dissertation_url"])
    if plausible.get("publication_url"):
        cur = replace(cur, publication_url=plausible["publication_url"])
    return cur


def _collect_stage3_urls(
    parsed: ParsedReport,
    *,
    registration_report_url: str,
) -> list[str]:
    urls: list[str] = []
    rreg = (registration_report_url or "").strip()
    if rreg:
        urls.append(rreg)
    for key, _label in _LINK_FIELDS:
        url = _extract_link(
            parsed, key, registration_report_url=registration_report_url
        )
        url = url.strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _build_accessibility_map(urls: list[str]) -> dict[str, bool]:
    accessibility: dict[str, bool] = {}
    for url in urls:
        valid, accessible = check_report_url(url)
        accessibility[url] = valid == "yes" and accessible == "yes"
    return accessibility


def _download_drive_file_bytes_all_drives(
    *, drive_service: Any, file_id: str
) -> bytes | None:
    """Скачивает файл с Drive (alt=media) с поддержкой Shared Drive.

    Отличие от ``magister_checking.dissertation_metrics.download_drive_file_bytes``
    — явный ``supportsAllDrives=True`` на ``files().get_media``. Это
    нужно для .docx-диссертаций, лежащих в Shared Drive магистрантов
    (см. handoff §7). Legacy-хелпер в ``dissertation_metrics`` оставлен
    как есть, чтобы не сломать ``summary_pipeline``.

    Возвращает ``None`` при любой ошибке (отсутствие доступа, сеть,
    пустой ответ).
    """

    try:
        req = drive_service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
    except TypeError:
        # Очень старый клиент без supportsAllDrives — фолбэк без него.
        # Для файлов в обычном My Drive это всё равно работает.
        req = drive_service.files().get_media(fileId=file_id)
    try:
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        data = fh.getvalue()
        return data if data else None
    except Exception:  # noqa: BLE001
        return None


def _try_load_dissertation_metrics_and_meta(
    *,
    dissertation_url: str,
    docs_service: Any,
    drive_service: Any,
) -> tuple[DissertationMetrics | None, str, str]:
    """Загружает диссертацию: метрики Stage 4 + название и язык для листа.

    Один проход по Docs API / скачиванию байт — без повторного чтения файла."""

    if not dissertation_url:
        return None, "", ""

    kind = classify_drive_url(dissertation_url)
    try:
        file_id = extract_google_file_id(dissertation_url)
    except ValueError:
        return None, "", ""

    if kind == "google_doc":
        try:
            doc = docs_service.documents().get(documentId=file_id).execute()
        except Exception:  # noqa: BLE001
            data = _download_drive_file_bytes_all_drives(
                drive_service=drive_service, file_id=file_id
            )
            if not data:
                return None, "", ""
            try:
                metrics = analyze_docx_bytes(data)
            except Exception:  # noqa: BLE001
                return None, "", ""
            title = detect_dissertation_title_from_docx_bytes(data)
            language = detect_dissertation_language_from_docx_bytes(data)
            warn_if_unusual_language(
                language, context=f"dissertation_url={dissertation_url}"
            )
            return metrics, title, language
        try:
            metrics = analyze_dissertation(doc)
        except Exception:  # noqa: BLE001
            return None, "", ""
        pdf_pages = count_pdf_pages_via_drive_export(
            drive_service=drive_service, file_id=file_id
        )
        if pdf_pages is not None and pdf_pages > 0:
            metrics = replace(metrics, pdf_pages=pdf_pages)
        title = detect_dissertation_title_from_gdoc(doc)
        language = detect_dissertation_language_from_gdoc(doc)
        warn_if_unusual_language(
            language, context=f"dissertation_url={dissertation_url}"
        )
        return metrics, title, language

    if kind == "drive_file":
        data = _download_drive_file_bytes_all_drives(
            drive_service=drive_service, file_id=file_id
        )
        if not data:
            return None, "", ""
        try:
            metrics = analyze_docx_bytes(data)
        except Exception:  # noqa: BLE001
            return None, "", ""
        title = detect_dissertation_title_from_docx_bytes(data)
        language = detect_dissertation_language_from_docx_bytes(data)
        warn_if_unusual_language(
            language, context=f"dissertation_url={dissertation_url}"
        )
        return metrics, title, language

    return None, "", ""


def _try_load_dissertation_metrics(
    *,
    dissertation_url: str,
    docs_service: Any,
    drive_service: Any,
) -> DissertationMetrics | None:
    """IO для Stage 4: загружает диссертацию и считает метрики.

    - ``classify_drive_url == "google_doc"`` → Docs API
      ``documents().get`` → ``analyze_dissertation`` (формат — дерево
      Docs v1).
    - ``classify_drive_url == "drive_file"`` → Drive API
      ``files().get_media`` (с ``supportsAllDrives=True``) →
      ``analyze_docx_bytes`` (читает .docx через python-docx). Этот
      путь не требует Shared Drive буфера и не делает копий: для
      .docx-диссертации нам нужны только байты файла.
    - Любая ошибка (нет доступа, неподдерживаемый формат, парсинг
      docx упал и т.п.) → ``None``. Stage 4 в пайплайне отметит это
      как ``skipped_reason='не удалось получить метрики диссертации'``.

    Caller гарантирует, что ``dissertation_url`` уже прошёл Stage 3
    (тип = google_doc или drive_file + DOCX), поэтому здесь нет
    дополнительной валидации формата.
    """

    metrics, _title, _lang = _try_load_dissertation_metrics_and_meta(
        dissertation_url=dissertation_url,
        docs_service=docs_service,
        drive_service=drive_service,
    )
    return metrics


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


def _get_drive_modified_time(*, drive_service: Any, file_id: str) -> str:
    """``modifiedTime`` файла в Drive (RFC3339) или ``""`` при ошибке.

    Используется для re-check fingerprint (handoff §8 — diff_detection):
    если магистрант переписал свой отчёт, ``modifiedTime`` поменяется,
    fingerprint станет другим и ``--only-if-changed`` не сработает.
    """

    if not file_id:
        return ""
    try:
        meta = (
            drive_service.files()
            .get(fileId=file_id, fields="modifiedTime", supportsAllDrives=True)
            .execute()
        )
    except Exception:  # noqa: BLE001
        return ""
    return str((meta or {}).get("modifiedTime") or "")


def _compute_recheck_fingerprint(
    *,
    report_url: str,
    report_modified_time: str,
    parsed: ParsedReport | None,
) -> str:
    """sha256 от существенных входов прогона (handoff §8 fingerprint).

    Состав:
    - URL отчёта;
    - ``modifiedTime`` отчёта в Drive (если получен);
    - четыре ссылки Stage 3 (project_folder_url, lkb_url, dissertation_url,
      publication_url) — их изменение должно перезапускать проверку.

    Если ``parsed`` отсутствует (отчёт не распарсился), вместо четырёх
    ссылок пишутся пустые строки — это даёт детерминированный
    fingerprint, который можно сравнить с прошлым.
    """

    parts: list[tuple[str, str]] = [
        ("report_url", report_url or ""),
        ("report_modified", report_modified_time or ""),
        (
            "project_folder_url",
            (parsed.project_folder_url if parsed else "") or "",
        ),
        ("lkb_url", (parsed.lkb_url if parsed else "") or ""),
        (
            "dissertation_url",
            (parsed.dissertation_url if parsed else "") or "",
        ),
        (
            "publication_url",
            (parsed.publication_url if parsed else "") or "",
        ),
    ]
    canonical = "\n".join(f"{k}={v}" for k, v in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_history_entry(
    *,
    report: RowCheckReport,
    source: str,
    fingerprint: str,
) -> RecheckHistoryEntry:
    """Готовит ``RecheckHistoryEntry`` для append в лист «История проверок»."""

    pages = report.stage4.pages_total
    sources = report.stage4.sources_count
    return RecheckHistoryEntry(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        row_number=report.row_number or 0,
        fio=report.fio or "",
        source=source,
        stopped_at=report.stopped_at or "",
        passed="no" if report.all_issues() else "yes",
        issues=" | ".join(report.all_issues()),
        pages_total="" if pages is None else str(pages),
        sources_count="" if sources is None else str(sources),
        compliance=compliance_to_text(report.stage4.compliance),
        fingerprint=fingerprint,
    )


def _resolve_report_file_id(
    *, report_url: str, drive_service: Any
) -> str:
    """ID отчёта в Drive (для запроса ``modifiedTime``).

    Без сети не определишь, поэтому передаём ``drive_service``. Возвращает
    ``""`` при любой ошибке: тогда modifiedTime в fingerprint попадёт пустой
    и --only-if-changed просто никогда не сработает по этому полю
    (но всё ещё сработает по списку URL).
    """

    if not report_url:
        return ""
    try:
        return resolve_report_google_doc_id(report_url, drive_service=drive_service)
    except Exception:  # noqa: BLE001
        return ""


def run_row_check(
    config: BotConfig,
    locator: RowLocator,
    *,
    skip_http: bool = False,
    apply: bool = False,
    only_if_changed: bool = False,
    history_source: str = "cli",
) -> RowCheckReport:
    """Основной glue: открывает таблицу, подгружает артефакты, запускает пайплайн.

    При ``apply=True`` пишет результаты Stage 2/Stage 3/Stage 4 в лист
    (колонки J:R с clean-write — старые значения и strikethrough
    затираются, чтобы re-check корректно отражал свежий результат), а
    также добавляет одну строку в лист «История проверок» с fingerprint
    входов (handoff §8 — Stage 4 (c) re-check).

    При ``only_if_changed=True`` сразу после загрузки и парсинга отчёта
    (но **до** HTTP-пробы Stage 2, mime-prefetch Stage 3 и тяжёлой
    Stage 4 загрузки диссертации) вычисляется fingerprint текущих
    входов и сравнивается с последней записью «Истории проверок» по
    этой строке. Совпадение → возвращается отчёт с ``unchanged=True``,
    пайплайн не выполняется, в лист и историю ничего не пишется
    (handoff §8 — diff_detection «без прогона пайплайна»).

    ``history_source`` — что записать в колонку ``source`` истории:
    ``cli`` (CLI вручную), ``bot`` (магистрант через /recheck),
    ``supervisor_status`` (научник запустил через /status ФИО подопечного в боте).
    """

    spreadsheet = get_spreadsheet(config)
    worksheet = spreadsheet.worksheet(_REGISTRATION_WORKSHEET_NAME)
    row_number = _resolve_row_number(worksheet, locator)
    user: UserForm = load_user(worksheet, row_number)
    sheet_link_overrides = read_sheet_link_overrides_for_row(worksheet, row_number)

    report_url = (user.report_url or "").strip()

    creds = _service_account_credentials(config)
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    report_document, report_doc_permission_denied = _try_load_report_document(
        report_url=report_url,
        docs_service=docs_service,
        drive_service=drive_service,
        docx_conversion_folder_id=config.docx_conversion_folder_id,
    )

    parsed_report = _try_parse_report(report_document) if report_document else None
    effective_parsed = _merge_parsed_report_with_sheet_links(
        parsed_report, sheet_link_overrides
    )

    # Re-check (Stage 4 (c)) fingerprint: вход коротко-замыкающего флага
    # ``--only-if-changed`` (handoff §8). Считаем СРАЗУ после парсинга
    # отчёта — раньше HTTP-пробы report_url, mime-prefetch и Stage 4
    # IO, чтобы при совпадении с прошлым прогоном вообще ничего тяжёлого
    # не делать (короткое замыкание = «без прогона пайплайна»).
    # Четыре ссылки Stage 3 берутся из ``effective_parsed``: парсер отчёта
    # и/или вручную введённые в лист URL (временный обход недоступного
    # Docs API / разбора).
    report_file_id = _resolve_report_file_id(
        report_url=report_url, drive_service=drive_service
    )
    report_modified_time = _get_drive_modified_time(
        drive_service=drive_service, file_id=report_file_id
    )
    fingerprint = _compute_recheck_fingerprint(
        report_url=report_url,
        report_modified_time=report_modified_time,
        parsed=effective_parsed,
    )

    if only_if_changed:
        last = read_last_recheck_entry(spreadsheet, row_number)
        if last is not None and last.fingerprint == fingerprint:
            return RowCheckReport(
                fio=user.fio or "",
                row_number=row_number,
                unchanged=True,
                source_fingerprint=fingerprint,
            )

    # Формальная проверка «папка vs документ» (без сети). Если report_url —
    # ссылка на папку Drive, дальше HTTP-проба не нужна: в листе и в
    # справке магистранта пишем явное сообщение «исправьте на ссылку на
    # документ». Реальный кейс — Камзебаева row 2 (handoff §«не удалось
    # извлечь file_id из …/folders/…»).
    report_url_target_message = check_report_url_target_kind(report_url)

    url_probe: tuple[str, str] | None = None
    if not skip_http and report_url and report_url_target_message is None:
        url_probe = check_report_url(report_url)

    link_accessibility: dict[str, bool] | None = None
    link_mime_types: dict[str, str] | None = None
    if effective_parsed is not None:
        stage3_urls = _collect_stage3_urls(
            effective_parsed, registration_report_url=report_url
        )
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

    # Stage 4 IO: метрики диссертации, если Stage 3 имеет шанс пройти.
    # Запускаем загрузку только когда диссертация — google_doc или
    # drive_file с DOCX (другие kinds Stage 3 hard-fail). Дешёвая
    # проверка по URL без сети, чтобы не делать лишний Drive call.
    dissertation_metrics: DissertationMetrics | None = None
    dissertation_title_for_sheet = ""
    dissertation_language_for_sheet = ""
    if effective_parsed is not None:
        diss_url = (effective_parsed.dissertation_url or "").strip()
        diss_kind = classify_drive_url(diss_url) if diss_url else "other"
        diss_mime_ok = True
        if diss_kind == "drive_file":
            mime = (link_mime_types or {}).get(diss_url, "")
            diss_mime_ok = mime == _DOCX_MIME_FOR_DISSERTATION
        if diss_url and diss_kind in {"google_doc", "drive_file"} and diss_mime_ok:
            (
                dissertation_metrics,
                dissertation_title_for_sheet,
                dissertation_language_for_sheet,
            ) = _try_load_dissertation_metrics_and_meta(
                dissertation_url=diss_url,
                docs_service=docs_service,
                drive_service=drive_service,
            )

    pipeline_report = run_row_pipeline(
        user,
        report_document=report_document,
        url_probe=url_probe,
        parsed_report=effective_parsed,
        link_accessibility=link_accessibility,
        link_mime_types=link_mime_types,
        dissertation_metrics=dissertation_metrics,
        formatting_rules=load_formatting_rules(),
        row_number=row_number,
    )

    # Если документ не удалось загрузить, но URL валиден и доступен — это значит,
    # что ссылка ведёт не на «Промежуточный отчёт» (другой тип файла/папка без
    # нужного файла). Дублируем сообщение в Stage 1.
    if (
        report_url
        and report_document is None
        and not report_doc_permission_denied
        and url_probe
        and url_probe == ("yes", "yes")
        and REPORT_URL_WRONG_TARGET_MESSAGE not in pipeline_report.stage1.issues
    ):
        pipeline_report.stage1.issues.append(REPORT_URL_WRONG_TARGET_MESSAGE)
        pipeline_report.stage1.passed = False
        if pipeline_report.stopped_at is None:
            pipeline_report.stopped_at = "stage1"

    if report_doc_permission_denied and report_url:
        anyone_link = False
        if report_file_id:
            anyone_link = drive_file_has_anyone_with_link_permission(
                drive_service, report_file_id
            )
        if anyone_link:
            clarify = REPORT_DRIVE_ANYONE_LINK_BUT_BOT_FAILED_MESSAGE
        else:
            clarify = REPORT_DRIVE_ACL_DENIED_MESSAGE
        issues = [
            i
            for i in pipeline_report.stage1.issues
            if i != REPORT_URL_WRONG_TARGET_MESSAGE
        ]
        if clarify not in issues:
            issues.insert(0, clarify)
        pipeline_report.stage1.issues = issues
        pipeline_report.stage1.passed = False
        pipeline_report.stopped_at = "stage1"

    # Folder-вместо-документа в report_url — отдельное более конкретное
    # сообщение (см. ``check_report_url_target_kind``). Перезаписывает
    # generic «неверна» при необходимости и попадает в справку магистранта.
    if (
        report_url_target_message
        and report_url_target_message not in pipeline_report.stage1.issues
    ):
        pipeline_report.stage1.issues.append(report_url_target_message)
        pipeline_report.stage1.passed = False
        if pipeline_report.stopped_at is None:
            pipeline_report.stopped_at = "stage1"

    if apply:
        if report_url_target_message:
            # Дублируем сообщение в колонку «Проверка ссылки» для админа,
            # «Доступ открыт» оставляем пустым (HTTP-пробы не делали).
            report_url_valid = report_url_target_message
            report_url_accessible = ""
        else:
            report_url_valid = url_probe[0] if url_probe is not None else None
            report_url_accessible = (
                url_probe[1] if url_probe is not None else None
            )
        fill_status_update = resolve_fill_status_after_row_check(user, pipeline_report)
        apply_row_check_updates(
            worksheet,
            row_number,
            report_url_valid=report_url_valid,
            report_url_accessible=report_url_accessible,
            stage3_executed=pipeline_report.stage3.executed,
            stage3_cells=pipeline_report.stage3_cells,
            stage4_cells=pipeline_report.stage4_cells,
            fill_status=fill_status_update,
        )
        if pipeline_report.stage4.executed and (
            dissertation_title_for_sheet or dissertation_language_for_sheet
        ):
            try:
                write_dissertation_meta_columns(
                    worksheet,
                    row_number,
                    title=dissertation_title_for_sheet,
                    language=dissertation_language_for_sheet,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось записать dissertation_title/dissertation_language "
                    "после row check (строка %s)",
                    row_number,
                )
        # Запись в историю — только при реальной записи в лист, чтобы
        # dry-run не «нагружал» историю шумовыми записями. Источник
        # (cli/bot) попадает в одноимённую колонку.
        try:
            append_recheck_history(
                spreadsheet,
                _build_history_entry(
                    report=pipeline_report,
                    source=history_source,
                    fingerprint=fingerprint,
                ),
            )
        except Exception:  # noqa: BLE001
            # История — вспомогательный артефакт. Если что-то пошло не
            # так (нет прав на add_worksheet, лимиты Sheets), не валим
            # основной flow проверки: магистрант увидел результат.
            pass

        try:
            user_after = load_user(worksheet, row_number)
            extras = build_sheet_enrichment(config, user_after)
            drive_snap = build_project_snapshot(
                user=user_after,
                report=pipeline_report,
                extra_values=extras,
                fill_status=None,
                trigger="row_check_apply",
                source_fingerprint=fingerprint,
            )
            try_upload_project_snapshot_json(config, drive_snap)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Сохранение JSON-снимка в Google Drive (PROJECT_CARD_OUTPUT_FOLDER_URL)"
            )

    pipeline_report.source_fingerprint = fingerprint
    return pipeline_report


def format_report(
    report: RowCheckReport,
    *,
    applied: bool = False,
    user: UserForm | None = None,
    extra_values: dict[str, str] | None = None,
    fill_status: str | None = None,
    trigger: str = "row_check",
    view: str = "student",
    as_html: bool = False,
) -> str:
    """Форматирует отчёт в многострочный текст «справки».

    Строит :class:`ProjectSnapshot` и рендерит через
    :func:`render_spravka_telegram` / HTML-варианты
    (см. ``docs/contract_project_snapshot.md``).

    ``view="student"`` — кратко (магистрант); ``view="commission"`` — полный
    текст для комиссии (как PDF). ``as_html=True`` — разметка для Telegram
    ``parse_mode=HTML``; в CLI и печати оставляйте ``False``.

    ``applied=True`` дописывает пометку о том, что результаты этапов 2-4
    записаны в лист; иначе выводит пометку dry-run.

    Если ``report.unchanged`` — короткое замыкание ``--only-if-changed``:
    выводится одна строка о том, что с прошлого прогона ничего не
    поменялось и ни лист, ни история не тронуты.
    """

    u = user if user is not None else UserForm(fio=report.fio or "")
    snap = build_project_snapshot(
        user=u,
        report=report,
        extra_values=extra_values or {},
        fill_status=fill_status,
        trigger=trigger,
    )
    if view not in ("student", "commission"):
        raise ValueError("view: ожидается 'student' или 'commission'")
    if view == "commission":
        if as_html:
            return render_commission_telegram_html(snap)
        return render_commission_plaintext(snap)
    if as_html:
        return render_spravka_telegram_html(snap, applied=applied)
    return render_spravka_telegram(snap, applied=applied)
