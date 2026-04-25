"""Glue-слой IO поверх чистого конвейера row_pipeline (п.7 ТЗ).

Отсюда CLI-команда ``check-row`` получает всё необходимое для прогона
одной строки листа «Регистрация» через этапы 1-3: открывает таблицу,
читает строку, подгружает промежуточный отчёт, проверяет HTTP-доступность
внешних ссылок и печатает «справку» магистранту в stdout.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    RowCheckReport,
    _LINK_FIELDS,
    _extract_link,
    compliance_to_text,
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
)
from magister_checking.bot.validation import (
    REPORT_URL_WRONG_TARGET_MESSAGE,
    check_report_url,
)
from magister_checking.dissertation_metrics import (
    DissertationMetrics,
    analyze_dissertation,
    analyze_docx_bytes,
)
from magister_checking.drive_docx import google_doc_from_drive_file
from magister_checking.drive_urls import (
    classify_drive_url,
    extract_google_file_id,
)
from magister_checking.report_parser import ParsedReport, parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id


_REGISTRATION_WORKSHEET_NAME = "Регистрация"

# MIME-тип .docx-диссертации; дублирует значение из bot.row_pipeline.DOCX_MIME,
# но импортировать его сюда не имеет смысла (избегаем лишней зависимости
# CLI ↔ pipeline). Стандартизованный OOXML mime, не меняется.
_DOCX_MIME_FOR_DISSERTATION = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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

    if not dissertation_url:
        return None

    kind = classify_drive_url(dissertation_url)
    try:
        file_id = extract_google_file_id(dissertation_url)
    except ValueError:
        return None

    if kind == "google_doc":
        try:
            doc = docs_service.documents().get(documentId=file_id).execute()
        except Exception:  # noqa: BLE001
            # Fallback: классификация по URL может ошибиться, если Drive
            # viewer URL .docx-файла прошёл в эту ветку без маркера rtpof
            # (например, магистрант руками выкинул query-параметры). Docs
            # API на .docx отвечает HTTP 400 «not supported for this
            # document» — пробуем тот же путь, что и для drive_file:
            # download bytes → analyze_docx_bytes. Если файл всё-таки
            # нативный Google Doc — get_media вернёт ошибку и мы вернём
            # None так же, как и без fallback.
            data = _download_drive_file_bytes_all_drives(
                drive_service=drive_service, file_id=file_id
            )
            if not data:
                return None
            try:
                return analyze_docx_bytes(data)
            except Exception:  # noqa: BLE001
                return None
        try:
            return analyze_dissertation(doc)
        except Exception:  # noqa: BLE001
            return None

    if kind == "drive_file":
        data = _download_drive_file_bytes_all_drives(
            drive_service=drive_service, file_id=file_id
        )
        if not data:
            return None
        try:
            return analyze_docx_bytes(data)
        except Exception:  # noqa: BLE001
            return None

    return None


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
    ``cli`` (CLI вручную), ``bot`` (магистрант через /recheck).
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

    parsed_report = _try_parse_report(report_document) if report_document else None

    # Re-check (Stage 4 (c)) fingerprint: вход коротко-замыкающего флага
    # ``--only-if-changed`` (handoff §8). Считаем СРАЗУ после парсинга
    # отчёта — раньше HTTP-пробы report_url, mime-prefetch и Stage 4
    # IO, чтобы при совпадении с прошлым прогоном вообще ничего тяжёлого
    # не делать (короткое замыкание = «без прогона пайплайна»).
    report_file_id = _resolve_report_file_id(
        report_url=report_url, drive_service=drive_service
    )
    report_modified_time = _get_drive_modified_time(
        drive_service=drive_service, file_id=report_file_id
    )
    fingerprint = _compute_recheck_fingerprint(
        report_url=report_url,
        report_modified_time=report_modified_time,
        parsed=parsed_report,
    )

    if only_if_changed:
        last = read_last_recheck_entry(spreadsheet, row_number)
        if last is not None and last.fingerprint == fingerprint:
            return RowCheckReport(
                fio=user.fio or "",
                row_number=row_number,
                unchanged=True,
            )

    url_probe: tuple[str, str] | None = None
    if not skip_http and report_url:
        url_probe = check_report_url(report_url)

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

    # Stage 4 IO: метрики диссертации, если Stage 3 имеет шанс пройти.
    # Запускаем загрузку только когда диссертация — google_doc или
    # drive_file с DOCX (другие kinds Stage 3 hard-fail). Дешёвая
    # проверка по URL без сети, чтобы не делать лишний Drive call.
    dissertation_metrics: DissertationMetrics | None = None
    if parsed_report is not None:
        diss_url = (parsed_report.dissertation_url or "").strip()
        diss_kind = classify_drive_url(diss_url) if diss_url else "other"
        diss_mime_ok = True
        if diss_kind == "drive_file":
            mime = (link_mime_types or {}).get(diss_url, "")
            diss_mime_ok = mime == _DOCX_MIME_FOR_DISSERTATION
        if diss_url and diss_kind in {"google_doc", "drive_file"} and diss_mime_ok:
            dissertation_metrics = _try_load_dissertation_metrics(
                dissertation_url=diss_url,
                docs_service=docs_service,
                drive_service=drive_service,
            )

    pipeline_report = run_row_pipeline(
        user,
        report_document=report_document,
        url_probe=url_probe,
        parsed_report=parsed_report,
        link_accessibility=link_accessibility,
        link_mime_types=link_mime_types,
        dissertation_metrics=dissertation_metrics,
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
            stage4_cells=pipeline_report.stage4_cells,
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

    return pipeline_report


def format_report(report: RowCheckReport, *, applied: bool = False) -> str:
    """Форматирует отчёт в многострочный текст «справки».

    ``applied=True`` дописывает пометку о том, что результаты этапов 2-4
    записаны в лист; иначе выводит пометку dry-run.

    Если ``report.unchanged`` — короткое замыкание ``--only-if-changed``:
    выводится одна строка о том, что с прошлого прогона ничего не
    поменялось и ни лист, ни история не тронуты.
    """

    if report.unchanged:
        fio = report.fio or "(без ФИО)"
        row = report.row_number or "?"
        return (
            f"Магистрант: {fio}\n"
            f"Строка: {row}\n\n"
            "С прошлой проверки входы не менялись (--only-if-changed).\n"
            "Лист и история проверок не тронуты."
        )

    lines = report.spravka_lines()
    if report.stage3_cells:
        lines.append("")
        lines.append("Извлечённые ссылки (L/M/N/O):")
        for cell in report.stage3_cells:
            mark = " [зачёркнута]" if cell.strikethrough else ""
            lines.append(f"  {cell.column_key}: {cell.value}{mark}")
    if report.stage4.executed:
        lines.append("")
        lines.append("Содержательный разбор диссертации (Stage 4):")
        pages = report.stage4.pages_total
        sources = report.stage4.sources_count
        lines.append(f"  страниц всего: {pages if pages is not None else '—'}")
        lines.append(f"  источников: {sources if sources is not None else '—'}")
        lines.append(
            f"  оформление: {compliance_to_text(report.stage4.compliance)}"
        )
    elif report.stage4.skipped_reason:
        lines.append("")
        lines.append(
            f"Stage 4 пропущен: {report.stage4.skipped_reason}"
        )
    lines.append("")
    if applied:
        lines.append("(запись в лист выполнена: J/K/L/M/N/O + Stage 4)")
    else:
        lines.append("(dry-run: лист не изменён — добавьте --apply для записи)")
    return "\n".join(lines)
