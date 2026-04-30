"""Универсальная разведка оформления DOCX-диссертации по строкам Google Sheet.

Запуск:
    python -m scripts._diag_formatting --row 2 --row 11 --row 17

Для каждой строки:
1. Читает значение колонки «Ссылка на диссертацию» (`dissertation_url`).
2. Качает файл с Google Drive.
3. Если это .docx — парсит margins (sectPr/pgMar, twips÷567), привязку
   footer'ов и положение PAGE-абзаца.
4. Печатает итог в формате одной строки на магистранта:
       row=N name=... sectPr=K footerRefs=M PAGE_in=L positions=[...]
   и подробный дамп секций/footer'ов.

Назначение: откалибровать алгоритм `page_numbering_present` на эталонных
магистрантах (у кого нумерация visually-подтверждена) перед фиксацией
правила в коде. Без этого compliance может давать ложные диагнозы.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaIoBaseDownload  # noqa: E402

from magister_checking.bot.config import load_config  # noqa: E402
from magister_checking.bot.sheets_repo import (  # noqa: E402
    _field_to_column_map,
    get_gspread_client,
    get_worksheet,
)
from magister_checking.drive_urls import extract_google_file_id  # noqa: E402

from scripts._diag_kamzebayeva_formatting import (  # noqa: E402
    _dump_footers,
    _dump_pgmar,
)


def _build_drive(cfg) -> Any:
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _download(drive: Any, file_id: str) -> tuple[bytes, str | None]:
    meta = (
        drive.files()
        .get(fileId=file_id, fields="name,mimeType,size", supportsAllDrives=True)
        .execute()
    )
    mime = meta.get("mimeType")
    name = meta.get("name")
    if mime == "application/vnd.google-apps.document":
        # Экспортируем Google Doc в DOCX, чтобы прогнать одним парсером.
        req = drive.files().export_media(
            fileId=file_id,
            mimeType=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
    else:
        req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return fh.getvalue(), name


def _read_row_dissertation_url(ws, row: int) -> tuple[str, str]:
    """Возвращает (fio, dissertation_url) по строке листа."""

    field_map = _field_to_column_map(ws)
    diss_idx = field_map.get("dissertation_url")
    fio_idx = field_map.get("fio")
    if diss_idx is None:
        raise SystemExit("В шапке листа нет колонки «Ссылка на диссертацию»")
    values = ws.row_values(row)
    diss_url = values[diss_idx].strip() if len(values) > diss_idx else ""
    fio = (
        values[fio_idx].strip()
        if (fio_idx is not None and len(values) > fio_idx)
        else ""
    )
    return fio, diss_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--row",
        action="append",
        type=int,
        required=True,
        help="Номер строки в Google Sheet (можно повторять)",
    )
    args = parser.parse_args()

    cfg = load_config()
    client = get_gspread_client(cfg)
    ws = get_worksheet(cfg)
    drive = _build_drive(cfg)

    for row in args.row:
        print("\n" + "=" * 70)
        try:
            fio, diss_url = _read_row_dissertation_url(ws, row)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"row={row}: ошибка чтения листа: {exc}")
            continue
        if not diss_url:
            print(f"row={row} fio={fio!r}: ссылка на диссертацию пустая, пропуск")
            continue
        try:
            file_id = extract_google_file_id(diss_url)
        except ValueError as exc:
            print(f"row={row} fio={fio!r}: не удалось извлечь file_id ({exc})")
            continue
        print(f"row={row} fio={fio!r} url={diss_url}")
        try:
            blob, name = _download(drive, file_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  download error: {exc}")
            continue
        print(f"  file: name={name!r} size={len(blob)} байт")
        if not blob.startswith(b"PK"):
            print("  не похоже на .docx (нет PK-сигнатуры) — пропуск")
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                _dump_pgmar(zf)
                _dump_footers(zf)
        except zipfile.BadZipFile as exc:
            print(f"  bad zip: {exc}")
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
