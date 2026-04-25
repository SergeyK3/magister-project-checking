"""Диагностика sources_count напрямую по dissertation_url каждой строки.

Не идёт через CLI/pipeline (избегаем cp1251-крэшей и stage-плюмбинга),
а делает только то, что нужно для проверки регрессии:

1. Открывает лист «Регистрация», находит строку.
2. Парсит её Промежуточный отчёт (Docs API), берёт dissertation_url.
3. Скачивает .docx через Drive API (или .docx-конверсию Google Doc).
4. Запускает analyze_docx_bytes / analyze_dissertation и печатает
   sources_count, плюс трёхсигнальную раскладку (word_list, text max,
   has line numbering, url paragraph count).

Запуск:
    python -m scripts._diag_sources 2 3 6 8 9 14 18

При отсутствии аргументов — стандартный набор reference-строк.
"""

from __future__ import annotations

import io
import re
import sys
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from magister_checking.bot.config import load_config
from magister_checking.bot import sheets_repo as sr
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.dissertation_metrics import (
    _docx_bibliography_has_line_numbering,
    _docx_bibliography_url_paragraph_count,
    _docx_bibliography_windows,
    _docx_bibliography_word_list_count,
    _docx_paragraph_records,
    _docx_plain_text_all_paragraphs,
    _estimate_sources_count,
    _is_appendix_marker,
    _is_bibliography_marker,
    analyze_docx_bytes,
    analyze_dissertation,
)
from magister_checking.drive_urls import classify_drive_url, extract_google_file_id
from magister_checking.report_parser import parse_intermediate_report
from magister_checking.summary_pipeline import resolve_report_google_doc_id
from docx import Document  # type: ignore[import-untyped]


_REGISTRATION_WORKSHEET_NAME = "Регистрация"

# Эталоны источников из ручной сверки (handoff).
_REFERENCE = {
    2: ("Камзебаева А.Д.", None),
    3: ("Гизатова И.В.", 106),
    6: ("Сулейменова И.С.", 45),
    8: ("Досанов Б.А.", 40),
    9: ("Макишева Г.Д.", None),
    14: ("Тананова А.А.", 43),
    18: ("Мараджапова С.Б.", 42),
}


def _download_drive_bytes(*, drive_service: Any, file_id: str) -> bytes | None:
    try:
        req = drive_service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
    except TypeError:
        req = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    data = fh.getvalue()
    return data if data else None


def _diag_docx(data: bytes) -> dict[str, Any]:
    doc = Document(io.BytesIO(data))
    plain = _docx_plain_text_all_paragraphs(doc)
    windows = _docx_bibliography_windows(doc)
    records = _docx_paragraph_records(doc)
    bib_idx = [i for i, (t, _) in enumerate(records) if _is_bibliography_marker(t)]
    app_idx = [i for i, (t, _) in enumerate(records) if _is_appendix_marker(t)]
    metrics = analyze_docx_bytes(data)
    win_url_counts: list[int] = []
    for w in windows:
        win_url_counts.append(
            sum(1 for t, _ in w if re.search(r"https?://", t, re.IGNORECASE))
        )
    return {
        "kind": "docx",
        "bytes": len(data),
        "word_list": _docx_bibliography_word_list_count(doc),
        "text_max": _estimate_sources_count(plain),
        "has_line_numbering": _docx_bibliography_has_line_numbering(doc),
        "url_paragraph_count": _docx_bibliography_url_paragraph_count(doc),
        "bib_marker_indices": bib_idx,
        "appendix_marker_indices": app_idx[:6],
        "bib_window_url_counts": win_url_counts,
        "final_sources_count": metrics.sources_count,
        "final_pages": metrics.approx_pages,
    }


def _diag_google_doc(*, docs_service: Any, file_id: str) -> dict[str, Any]:
    doc = docs_service.documents().get(documentId=file_id).execute()
    metrics = analyze_dissertation(doc)
    return {
        "kind": "google_doc",
        "final_sources_count": metrics.sources_count,
        "final_pages": metrics.approx_pages,
    }


def _process_row(
    *,
    worksheet: Any,
    row_number: int,
    docs_service: Any,
    drive_service: Any,
    docx_conversion_folder_id: str,
) -> None:
    user = sr.load_user(worksheet, row_number)
    fio = (user.fio or "").strip() or "(нет ФИО)"
    print(f"\n=== row {row_number}: {fio} ===")

    report_url = (user.report_url or "").strip()
    if not report_url:
        print("  report_url пуст — пропуск.")
        return
    try:
        doc_id = resolve_report_google_doc_id(report_url, drive_service=drive_service)
    except Exception as exc:  # noqa: BLE001
        print(f"  resolve_report_google_doc_id failed: {exc!r}")
        return

    from magister_checking.drive_docx import google_doc_from_drive_file

    try:
        with google_doc_from_drive_file(
            drive_service, doc_id, conversion_folder_id=docx_conversion_folder_id
        ) as loadable_id:
            report_doc = docs_service.documents().get(documentId=loadable_id).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"  load report doc failed: {exc!r}")
        return

    try:
        parsed = parse_intermediate_report(report_doc)
    except Exception as exc:  # noqa: BLE001
        print(f"  parse_intermediate_report failed: {exc!r}")
        return

    diss_url = (parsed.dissertation_url or "").strip()
    if not diss_url:
        print("  dissertation_url пуст в отчёте.")
        return
    print(f"  dissertation_url: {diss_url}")
    kind = classify_drive_url(diss_url)
    try:
        file_id = extract_google_file_id(diss_url)
    except ValueError:
        print(f"  не удалось извлечь file_id из {diss_url!r}.")
        return
    print(f"  classify={kind}  file_id={file_id}")

    diag: dict[str, Any]
    if kind == "google_doc":
        try:
            diag = _diag_google_doc(docs_service=docs_service, file_id=file_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  google_doc diag failed: {exc!r} — fallback через docx bytes.")
            data = _download_drive_bytes(drive_service=drive_service, file_id=file_id)
            if not data:
                print("  байты не получены.")
                return
            diag = _diag_docx(data)
    elif kind == "drive_file":
        data = _download_drive_bytes(drive_service=drive_service, file_id=file_id)
        if not data:
            print("  байты не получены.")
            return
        diag = _diag_docx(data)
    else:
        print(f"  неподдерживаемый kind={kind}.")
        return

    ref = _REFERENCE.get(row_number, (fio, None))
    expected = ref[1]
    final = diag.get("final_sources_count")
    status = "OK" if (expected is None or final == expected) else "MISMATCH"
    print(f"  >>> sources_count = {final}  (эталон: {expected})  [{status}]")
    for k, v in diag.items():
        if k == "final_sources_count":
            continue
        print(f"    {k}: {v}")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [int(x) for x in argv] or [2, 3, 6, 8, 9, 14, 18]

    cfg = load_config()
    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json), scopes=GOOGLE_SCOPES
    )
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    spreadsheet = sr.get_spreadsheet(cfg)
    worksheet = spreadsheet.worksheet(_REGISTRATION_WORKSHEET_NAME)

    for r in rows:
        try:
            _process_row(
                worksheet=worksheet,
                row_number=r,
                docs_service=docs_service,
                drive_service=drive_service,
                docx_conversion_folder_id=cfg.docx_conversion_folder_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ROW {r} FAILED: {exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
