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

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"

# MIME, которые дальше по пайплайну превращаются в Google Doc через
# drive_docx.google_doc_from_drive_file (нативный Doc — passthrough,
# .docx — копируется в buffer-папку с конверсией). Резолвер должен
# возвращать id любого из этих типов, иначе пайплайн ломается на стадии
# чтения через Docs API.
_REPORT_FILE_MIMES: tuple[str, ...] = (GOOGLE_DOC_MIME, DOCX_MIME)


def filename_starts_with_intermediate_report(name: str) -> bool:
    n = (name or "").strip()
    return any(n.startswith(p) for p in INTERMEDIATE_REPORT_NAME_PREFIXES)


def _list_folder_children(
    *, drive_service: Any, folder_id: str, mime_clause: str
) -> list[dict[str, Any]]:
    """Все дочерние сущности папки заданных MIME, через постранично."""
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    q = f"'{folder_id}' in parents and trashed = false and ({mime_clause})"
    while True:
        resp = (
            drive_service.files()
            .list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        items.extend(resp.get("files", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def _pick_best_report_file(files: list[dict[str, Any]]) -> str | None:
    """Из набора файлов отчёта выбрать лучший id.

    Предпочтение: нативный Google Doc → .docx. При нескольких в одной
    категории — лексикографически первый по имени (case-insensitive).
    Это даёт стабильный результат, если в папке оказались и Doc, и
    .docx (например, магистрант сконвертировал, но не удалил исходник).
    """
    if not files:
        return None
    docs = [f for f in files if f.get("mimeType") == GOOGLE_DOC_MIME]
    docxs = [f for f in files if f.get("mimeType") == DOCX_MIME]
    chosen = docs or docxs
    if not chosen:
        return None
    chosen.sort(key=lambda f: (f.get("name") or "").casefold())
    return chosen[0].get("id")


def pick_intermediate_report_doc_id(
    *, drive_service: Any, folder_id: str
) -> str | None:
    """Id файла промежуточного отчёта в папке Drive.

    Алгоритм (в порядке приоритета):

    1. В самой ``folder_id`` ищем файл с MIME Google Doc или ``.docx``,
       имя которого начинается с одного из ``INTERMEDIATE_REPORT_NAME_PREFIXES``.
       Это исторический случай: магистрант кладёт «Промежуточный отчет
       <ФИО>.docx» (или Doc) прямо в папку проекта.
    2. Если в текущей папке такого файла нет — ищем ВНУТРИ неё
       подпапку, имя которой начинается с того же префикса (например
       «Промежуточный отчет Танановой А.А.»). Спускаемся на один
       уровень и в этой подпапке выбираем любой Doc/.docx **без
       фильтра по имени** — именованная подпапка уже задаёт контекст,
       а внутри файл часто называется обобщённо
       («Промежуточный отчет магистранта.docx»).

    Возвращает id Google Doc или .docx; ``.docx`` дальше превращается в
    Doc через :func:`magister_checking.drive_docx.google_doc_from_drive_file`.
    """
    file_mime_clause = " or ".join(f"mimeType = '{m}'" for m in _REPORT_FILE_MIMES)

    # 1) файл нужного имени прямо в папке
    direct = [
        f
        for f in _list_folder_children(
            drive_service=drive_service,
            folder_id=folder_id,
            mime_clause=file_mime_clause,
        )
        if filename_starts_with_intermediate_report(f.get("name") or "")
    ]
    chosen = _pick_best_report_file(direct)
    if chosen:
        return chosen

    # 2) подпапка с именем «Промежуточный отчет ...» → файл внутри неё
    subfolders = [
        f
        for f in _list_folder_children(
            drive_service=drive_service,
            folder_id=folder_id,
            mime_clause=f"mimeType = '{GOOGLE_FOLDER_MIME}'",
        )
        if filename_starts_with_intermediate_report(f.get("name") or "")
    ]
    subfolders.sort(key=lambda f: (f.get("name") or "").casefold())
    for sf in subfolders:
        sub_id = sf.get("id")
        if not sub_id:
            continue
        nested = _list_folder_children(
            drive_service=drive_service,
            folder_id=sub_id,
            mime_clause=file_mime_clause,
        )
        chosen = _pick_best_report_file(nested)
        if chosen:
            return chosen

    return None
