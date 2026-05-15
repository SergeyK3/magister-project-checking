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
from magister_checking.observability import (
    google_error_fields,
    hash_value,
    id_tail,
    structured_extra,
)
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
        logger.info(
            "google.api.success",
            extra=structured_extra(
                event="google.api.success",
                category="google.api",
                api="drive",
                method="files.list",
                operation="snapshot_pick_list",
                status="success",
                row_number=row_number,
                folder_id_hash=hash_value(folder_id),
                folder_id_tail=id_tail(folder_id),
            ),
        )
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
        logger.info(
            "snapshot.pick.not_configured",
            extra=structured_extra(
                event="snapshot.pick.not_configured",
                category="snapshot.pick",
                operation="pick_latest_snapshot_for_row",
                status="skipped",
                row_number=row_number,
            ),
        )
        return None

    best: LatestSnapshotPick | None = None
    best_mtime = ""

    try:
        creds = _service_account_creds_from_config(config)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        logger.info(
            "google.api.success",
            extra=structured_extra(
                event="google.api.success",
                category="google.api",
                api="drive",
                method="build",
                operation="snapshot_pick_prepare",
                status="success",
                row_number=row_number,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "snapshot.pick.drive_prepare_failed",
            extra=structured_extra(
                event="snapshot.pick.drive_prepare_failed",
                category="snapshot.pick",
                operation="pick_latest_snapshot_for_row",
                status="failed",
                row_number=row_number,
                **google_error_fields(exc),
            ),
        )
        logger.warning(
            "google.api.error",
            extra=structured_extra(
                event="google.api.error",
                category="google.api",
                api="drive",
                method="build",
                operation="snapshot_pick_prepare",
                status="failed",
                row_number=row_number,
                **google_error_fields(exc),
            ),
        )
        return None

    candidate_count = 0
    for folder_url in urls:
        try:
            folder_id = extract_google_folder_id(folder_url)
        except ValueError as exc:
            logger.warning(
                "snapshot.pick.invalid_folder_url",
                extra=structured_extra(
                    event="snapshot.pick.invalid_folder_url",
                    category="snapshot.pick",
                    operation="pick_latest_snapshot_for_row",
                    status="skipped",
                    row_number=row_number,
                    error_class=type(exc).__name__,
                ),
            )
            continue

        try:
            candidates = list_snapshot_json_candidates(
                folder_id=folder_id, drive=drive, row_number=row_number
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "snapshot.pick.list_failed",
                extra=structured_extra(
                    event="snapshot.pick.list_failed",
                    category="snapshot.pick",
                    operation="pick_latest_snapshot_for_row",
                    status="failed",
                    row_number=row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    **google_error_fields(exc),
                ),
            )
            logger.warning(
                "google.api.error",
                extra=structured_extra(
                    event="google.api.error",
                    category="google.api",
                    api="drive",
                    method="files.list",
                    operation="snapshot_pick_list",
                    status="failed",
                    row_number=row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    **google_error_fields(exc),
                ),
            )
            raise
        candidate_count += len(candidates)
        logger.info(
            "snapshot.pick.folder_listed",
            extra=structured_extra(
                event="snapshot.pick.folder_listed",
                category="snapshot.pick",
                operation="pick_latest_snapshot_for_row",
                status="success",
                row_number=row_number,
                folder_id_hash=hash_value(folder_id),
                folder_id_tail=id_tail(folder_id),
                snapshot_candidate_count=len(candidates),
            ),
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

    if best is None:
        logger.info(
            "snapshot.pick.not_found",
            extra=structured_extra(
                event="snapshot.pick.not_found",
                category="snapshot.pick",
                operation="pick_latest_snapshot_for_row",
                status="not_found",
                row_number=row_number,
                folder_count=len(urls),
                snapshot_candidate_count=candidate_count,
                snapshot_pick_result="not_found",
            ),
        )
        return None

    logger.info(
        "snapshot.pick.found",
        extra=structured_extra(
            event="snapshot.pick.found",
            category="snapshot.pick",
            operation="pick_latest_snapshot_for_row",
            status="found",
            row_number=row_number,
            folder_count=len(urls),
            snapshot_candidate_count=candidate_count,
            snapshot_pick_result="found",
            file_id_hash=hash_value(best.file_id),
            file_id_tail=id_tail(best.file_id),
        ),
    )
    return best


def download_drive_file_bytes(config: BotConfig, file_id: str) -> bytes:
    """Читает содержимое файла на Drive (в т.ч. Shared Drive)."""

    try:
        creds = _service_account_creds_from_config(config)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        fh = io.BytesIO()
        req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = fh.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "snapshot.pick.download_failed",
            extra=structured_extra(
                event="snapshot.pick.download_failed",
                category="snapshot.pick",
                operation="download_drive_file_bytes",
                status="failed",
                file_id_hash=hash_value(file_id),
                file_id_tail=id_tail(file_id),
                **google_error_fields(exc),
            ),
        )
        logger.warning(
            "google.api.error",
            extra=structured_extra(
                event="google.api.error",
                category="google.api",
                api="drive",
                method="files.get_media",
                operation="snapshot_pick_download",
                status="failed",
                file_id_hash=hash_value(file_id),
                file_id_tail=id_tail(file_id),
                **google_error_fields(exc),
            ),
        )
        raise
    logger.info(
        "google.api.success",
        extra=structured_extra(
            event="google.api.success",
            category="google.api",
            api="drive",
            method="files.get_media",
            operation="snapshot_pick_download",
            status="success",
            file_id_hash=hash_value(file_id),
            file_id_tail=id_tail(file_id),
        ),
    )
    logger.info(
        "snapshot.pick.download_success",
        extra=structured_extra(
            event="snapshot.pick.download_success",
            category="snapshot.pick",
            operation="download_drive_file_bytes",
            status="success",
            file_id_hash=hash_value(file_id),
            file_id_tail=id_tail(file_id),
        ),
    )
    return data


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
