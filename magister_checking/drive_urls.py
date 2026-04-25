"""Extract Google Drive / Docs / Sheets file IDs from URLs."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qs, urlparse

_DOC_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")
_SHEETS_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")
_FOLDER_RE = re.compile(
    r"/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)", re.IGNORECASE
)

DriveUrlKind = Literal[
    "google_doc",      # docs.google.com/document/...
    "google_sheet",    # docs.google.com/spreadsheets/...
    "drive_folder",    # drive.google.com/drive/folders/...
    "drive_file",      # drive.google.com/file/d/...  (mime определяется отдельно)
    "other",           # любая другая http(s) ссылка либо нераспознанный формат
]


def extract_google_file_id(url_or_id: str) -> str:
    """
    Принимает полный URL или сырой id. Возвращает id файла для API.
    """
    s = url_or_id.strip()
    if re.fullmatch(r"[a-zA-Z0-9_-]+", s):
        return s

    parsed = urlparse(s)
    path = parsed.path or ""

    for pattern in (_DOC_RE, _SHEETS_RE, _DRIVE_FILE_RE):
        m = pattern.search(path)
        if m:
            return m.group(1)

    if "google.com" in (parsed.netloc or ""):
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]

    raise ValueError(f"Не удалось извлечь id из строки: {url_or_id!r}")


def is_google_drive_folder_url(url_or_id: str) -> bool:
    """True, если строка похожа на ссылку на папку Google Drive (путь …/drive/…/folders/…)."""
    s = (url_or_id or "").strip()
    return bool(_FOLDER_RE.search(urlparse(s).path or ""))


def extract_google_folder_id(url_or_id: str) -> str:
    """Id папки из URL вида https://drive.google.com/drive/folders/ID."""
    s = url_or_id.strip()
    parsed = urlparse(s)
    path = parsed.path or ""
    m = _FOLDER_RE.search(path)
    if m:
        return m.group(1)
    raise ValueError(f"Не удалось извлечь id папки из строки: {url_or_id!r}")


def classify_drive_url(url: str) -> DriveUrlKind:
    """Тип Drive/Docs URL по форме ссылки, без сетевых вызовов.

    Используется в Stage 3 пайплайна (см. ``bot/row_pipeline.py``) для
    проверки соответствия типа ссылки ожиданию по полю (например,
    «Магистерский проект» должен быть folder, «Публикация» — file).
    Подкласс ``drive_file`` дополнительно уточняется по MIME через
    ``drive.files().get(fields='mimeType')`` — это делает caller, см.
    ``row_check_cli._prefetch_drive_file_mimes``.

    «other» возвращается для пустой строки, не http(s) URL и для
    drive.google.com URL формы, которую мы не распознаём (например,
    устаревший ``open?id=…``). Это безопасно: Stage 3 пометит такое
    как mismatch, а не молча примет.
    """

    if not url:
        return "other"
    s = url.strip()
    if not s:
        return "other"
    parsed = urlparse(s)
    if parsed.scheme.lower() not in {"http", "https"}:
        return "other"
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or ""
    # docs.google.com — сначала, чтобы /document не путался с /file/d.
    if netloc.endswith("docs.google.com"):
        if _DOC_RE.search(path):
            return "google_doc"
        if _SHEETS_RE.search(path):
            return "google_sheet"
        return "other"
    if netloc.endswith("drive.google.com"):
        if _FOLDER_RE.search(path):
            return "drive_folder"
        if _DRIVE_FILE_RE.search(path):
            return "drive_file"
        return "other"
    return "other"
