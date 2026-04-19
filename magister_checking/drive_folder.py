"""Файлы в папке Google Drive по шаблону имени (промежуточный отчёт)."""

from __future__ import annotations

from typing import Any

# Как в запросе пользователя + «ё», без опечатки (одна «м»), для реальных имён на Диске.
INTERMEDIATE_REPORT_NAME_PREFIXES: tuple[str, ...] = (
    "Проммежуточный отчет",
    "Проммежуточный отчёт",
    "Промежуточный отчет",
    "Промежуточный отчёт",
)


def filename_starts_with_intermediate_report(name: str) -> bool:
    n = (name or "").strip()
    return any(n.startswith(p) for p in INTERMEDIATE_REPORT_NAME_PREFIXES)


def pick_intermediate_report_doc_id(*, drive_service: Any, folder_id: str) -> str | None:
    """
    Среди Google Docs в папке выбирает документ, имя которого начинается с одного из
    INTERMEDIATE_REPORT_NAME_PREFIXES. При нескольких совпадениях — лексикографически первый по имени.
    """
    items: list[tuple[str, str]] = []
    page_token: str | None = None
    q = (
        f"'{folder_id}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.document'"
    )
    while True:
        req = drive_service.files().list(
            q=q,
            fields="nextPageToken, files(id, name)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        resp = req.execute()
        for f in resp.get("files", []):
            fid = f.get("id")
            fname = f.get("name") or ""
            if fid and filename_starts_with_intermediate_report(fname):
                items.append((fname, fid))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    if not items:
        return None
    items.sort(key=lambda x: x[0].casefold())
    return items[0][1]
