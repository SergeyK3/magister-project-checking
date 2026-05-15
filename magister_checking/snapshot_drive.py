"""Сохранение ProjectSnapshot (JSON) в папку Google Drive (см. contract_project_snapshot)."""

from __future__ import annotations

import io
import logging
import re
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from magister_checking.bot.config import BotConfig
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.drive_urls import extract_google_folder_id
from magister_checking.observability import (
    google_error_fields,
    hash_value,
    id_tail,
    structured_extra,
)
from magister_checking.project_snapshot import ProjectSnapshot, project_snapshot_to_json

logger = logging.getLogger(__name__)


def _snapshot_folder_urls(config: BotConfig) -> tuple[str, ...]:
    """URL папок: ``project_snapshot_output_folder_urls``; для старых моков — ``project_card_output_folder_url``."""
    raw = getattr(config, "project_snapshot_output_folder_urls", None)
    if raw:
        return tuple(str(u).strip() for u in raw if str(u).strip())
    one = (getattr(config, "project_card_output_folder_url", None) or "").strip()
    return (one,) if one else ()


def _service_account_creds_from_config(config: BotConfig):
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def _filename_for_snapshot(snapshot: ProjectSnapshot) -> str:
    row = snapshot.row_number if snapshot.row_number is not None else 0
    raw = (snapshot.identity.fio or f"row{row}").strip() or f"row{row}"
    safe = re.sub(r'[\\/:*?"<>|]+', "_", raw)[:50]
    safe = re.sub(r"\s+", "_", safe).strip("._") or f"r{row}"
    ts = snapshot.generated_at
    for ch in (":", "+", " "):
        ts = ts.replace(ch, "-")
    return f"project_snapshot_r{row}_{safe}_{ts}.json"


def try_upload_project_snapshot_json(
    config: BotConfig,
    snapshot: ProjectSnapshot,
) -> list[str]:
    """Кладёт JSON-снимок в **каждую** папку из ``project_snapshot_output_folder_urls``
    (или в одну — из ``PROJECT_CARD_OUTPUT_FOLDER_URL``), см. load_config.

    Ошибки по отдельной папке логируются; возвращаются ссылки, для которых
    загрузка прошла успешно.
    """

    folder_urls = _snapshot_folder_urls(config)
    if not folder_urls:
        logger.info(
            "snapshot.upload.not_configured",
            extra=structured_extra(
                event="snapshot.upload.not_configured",
                category="snapshot.upload",
                operation="upload_project_snapshot_json",
                status="skipped",
                row_number=snapshot.row_number,
            ),
        )
        return []

    name = _filename_for_snapshot(snapshot)
    payload = project_snapshot_to_json(snapshot).encode("utf-8")
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
                operation="snapshot_upload_prepare",
                status="success",
                row_number=snapshot.row_number,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "snapshot.upload.drive_prepare_failed",
            extra=structured_extra(
                event="snapshot.upload.drive_prepare_failed",
                category="snapshot.upload",
                operation="upload_project_snapshot_json",
                status="failed",
                row_number=snapshot.row_number,
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
                operation="snapshot_upload_prepare",
                status="failed",
                row_number=snapshot.row_number,
                **google_error_fields(exc),
            ),
        )
        return []

    out_links: list[str] = []
    for folder_url in folder_urls:
        try:
            folder_id = extract_google_folder_id(folder_url)
        except ValueError as exc:
            logger.warning(
                "snapshot.upload.invalid_folder_url",
                extra=structured_extra(
                    event="snapshot.upload.invalid_folder_url",
                    category="snapshot.upload",
                    operation="upload_project_snapshot_json",
                    status="skipped",
                    row_number=snapshot.row_number,
                    error_class=type(exc).__name__,
                ),
            )
            continue
        media = MediaIoBaseUpload(
            io.BytesIO(payload),
            mimetype="application/json",
            resumable=False,
        )
        try:
            body = {
                "name": name,
                "parents": [folder_id],
            }
            created = (
                drive.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "snapshot.upload.failed",
                extra=structured_extra(
                    event="snapshot.upload.failed",
                    category="snapshot.upload",
                    operation="upload_project_snapshot_json",
                    status="failed",
                    row_number=snapshot.row_number,
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
                    method="files.create",
                    operation="snapshot_upload",
                    status="failed",
                    row_number=snapshot.row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    **google_error_fields(exc),
                ),
            )
            continue
        fid = created.get("id", "")
        link = created.get("webViewLink")
        if link:
            logger.info(
                "google.api.success",
                extra=structured_extra(
                    event="google.api.success",
                    category="google.api",
                    api="drive",
                    method="files.create",
                    operation="snapshot_upload",
                    status="success",
                    row_number=snapshot.row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    file_id_hash=hash_value(fid),
                    file_id_tail=id_tail(fid),
                ),
            )
            logger.info(
                "snapshot.upload.success",
                extra=structured_extra(
                    event="snapshot.upload.success",
                    category="snapshot.upload",
                    operation="upload_project_snapshot_json",
                    status="success",
                    row_number=snapshot.row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    file_id_hash=hash_value(fid),
                    file_id_tail=id_tail(fid),
                ),
            )
            out_links.append(str(link))
        elif fid:
            u = f"https://drive.google.com/file/d/{fid}/view"
            logger.info(
                "google.api.success",
                extra=structured_extra(
                    event="google.api.success",
                    category="google.api",
                    api="drive",
                    method="files.create",
                    operation="snapshot_upload",
                    status="success",
                    row_number=snapshot.row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    file_id_hash=hash_value(fid),
                    file_id_tail=id_tail(fid),
                ),
            )
            logger.info(
                "snapshot.upload.success",
                extra=structured_extra(
                    event="snapshot.upload.success",
                    category="snapshot.upload",
                    operation="upload_project_snapshot_json",
                    status="success",
                    row_number=snapshot.row_number,
                    folder_id_hash=hash_value(folder_id),
                    folder_id_tail=id_tail(folder_id),
                    file_id_hash=hash_value(fid),
                    file_id_tail=id_tail(fid),
                ),
            )
            out_links.append(u)
    logger.info(
        "snapshot.upload.completed",
        extra=structured_extra(
            event="snapshot.upload.completed",
            category="snapshot.upload",
            operation="upload_project_snapshot_json",
            status="success" if out_links else "no_uploads",
            row_number=snapshot.row_number,
            folder_count=len(folder_urls),
            snapshot_upload_count=len(out_links),
        ),
    )
    return out_links
