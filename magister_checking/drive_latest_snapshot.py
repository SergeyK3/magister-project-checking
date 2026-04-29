"""Чтение с диска последнего JSON ProjectSnapshot для строки (по времени изменения файла).

Файлы выкладывает ``try_upload_project_snapshot_json`` (имена ``project_snapshot_r{N}_…``).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from magister_checking.bot.config import BotConfig
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.drive_urls import extract_google_folder_id
from magister_checking.snapshot_drive import _snapshot_folder_urls

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestSnapshotPick:
    """Выбранный файл на Drive: последний по ``modifiedTime`` среди всех папок снимков."""

    file_id: str
    name: str
    modified_time: str


def _service_account_creds_from_config(config: BotConfig):
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def _row_filename_prefix(row_number: int) -> str:
    return f"project_snapshot_r{row_number}_"


def list_snapshot_json_candidates(
    *,
    folder_id: str,
    drive,
    row_number: int,
) -> list[dict]:
    """Файлы в папке, по имени относящиеся к строке ``row_number``."""

    prefix = _row_filename_prefix(row_number)
    escaped = prefix.replace("\\", "\\\\").replace("'", "\\'")
    q = f"'{folder_id}' in parents and trashed = false and name contains '{escaped}'"
    collected: list[dict] = []
    page_token = None
    name_re = re.compile(rf"^project_snapshot_r{row_number}_\S+\.json$", re.I)
    while True:
        kwargs: dict = {
            "q": q,
            "corpora": "allDrives",
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "spaces": "drive",
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime)",
            "pageSize": 100,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = drive.files().list(**kwargs).execute()
        for f in resp.get("files", []):
            name = str(f.get("name") or "")
            if name_re.match(name):
                collected.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return collected


def pick_latest_snapshot_for_row(config: BotConfig, row_number: int) -> LatestSnapshotPick | None:
    """Среди файлов вида ``project_snapshot_r{row}_*.json`` во всех папках конфига — самый новый."""

    urls = _snapshot_folder_urls(config)
    if not urls:
        return None

    best: LatestSnapshotPick | None = None
    best_mtime = ""

    try:
        creds = _service_account_creds_from_config(config)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:  # noqa: BLE001
        logger.exception("Drive API: подготовка списка снимков")
        return None

    for folder_url in urls:
        try:
            folder_id = extract_google_folder_id(folder_url)
        except ValueError as exc:
            logger.warning("pick_latest_snapshot: папка %s — %s", folder_url, exc)
            continue

        candidates = list_snapshot_json_candidates(
            folder_id=folder_id, drive=drive, row_number=row_number
        )
        for cand in candidates:
            fid = str(cand.get("id") or "")
            name = str(cand.get("name") or "")
            mt = str(cand.get("modifiedTime") or "")
            if not fid or not mt:
                continue
            if best is None or mt > best_mtime:
                best_mtime = mt
                best = LatestSnapshotPick(file_id=fid, name=name, modified_time=mt)

    return best


def download_drive_file_bytes(config: BotConfig, file_id: str) -> bytes:
    """Читает содержимое файла на Drive (в т.ч. Shared Drive)."""

    creds = _service_account_creds_from_config(config)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    fh = io.BytesIO()
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def wrap_commission_html_for_browser(fragment: str) -> str:
    """Оборачивает фрагмент Telegram HTML в страницу: ссылки работают в браузере."""

    frag = fragment.strip("\n") if fragment else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\"><head>"
        '<meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        "<title>Справка по проверке (снимок)</title>"
        "<style>"
        "body{font-family:system-ui,Segoe UI,sans-serif;padding:16px;line-height:1.5;color:#1a1a1a;}"
        "a{color:#0366d6;}"
        "</style>"
        "</head>"
        '<body style="white-space:pre-line">'
        f"{frag}\n"
        "</body></html>"
    )
