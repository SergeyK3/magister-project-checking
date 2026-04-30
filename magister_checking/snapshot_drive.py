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
        return []

    name = _filename_for_snapshot(snapshot)
    payload = project_snapshot_to_json(snapshot).encode("utf-8")
    try:
        creds = _service_account_creds_from_config(config)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:  # noqa: BLE001
        logger.exception("Snapshot JSON: не удалось подготовить Drive API")
        return []

    out_links: list[str] = []
    for folder_url in folder_urls:
        try:
            folder_id = extract_google_folder_id(folder_url)
        except ValueError as exc:
            logger.warning("Папка snapshot (пропуск): %s — %s", folder_url, exc)
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
        except Exception:  # noqa: BLE001
            logger.exception(
                "Snapshot JSON upload failed (folder_id=%s)", folder_id
            )
            continue
        fid = created.get("id", "")
        link = created.get("webViewLink")
        if link:
            logger.info("Project snapshot JSON saved: %s", link)
            out_links.append(str(link))
        elif fid:
            u = f"https://drive.google.com/file/d/{fid}/view"
            logger.info("Project snapshot JSON saved: %s", u)
            out_links.append(u)
    return out_links
