"""Extract Google Drive / Docs / Sheets file IDs from URLs."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_DOC_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")
_SHEETS_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


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
