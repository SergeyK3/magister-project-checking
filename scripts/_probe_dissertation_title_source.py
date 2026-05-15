"""Диагностический одноразовик: проверяем, ОТКУДА для конкретной строки
извлекается тема (через «На тему:», «Тақырыбы:» или Heading-fallback).

Запуск: python scripts\\_probe_dissertation_title_source.py 2 16
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from google.oauth2.service_account import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from magister_checking.bot import sheets_repo as sr  # noqa: E402
from magister_checking.bot.config import load_config  # noqa: E402
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES  # noqa: E402
from magister_checking.dissertation_meta import (  # noqa: E402
    _ON_TOPIC_HEADER_RE,
    _ON_TOPIC_INLINE_RE,
    _TOPIC_HEADER_RE,
    _first_paragraphs_from_plain,
    _is_stop_phrase,
)
from magister_checking.dissertation_metrics import download_drive_file_bytes  # noqa: E402
from magister_checking.drive_urls import extract_google_file_id  # noqa: E402

try:
    from docx import Document  # type: ignore[import-untyped]
except ImportError:
    Document = None  # type: ignore[assignment]


def _probe(row_no: int) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    ws = sr.get_worksheet(cfg)
    mapping = sr._field_to_column_map(ws)
    diss_url_idx = mapping["dissertation_url"]
    fio_idx = mapping["fio"]

    rows = ws.get_all_values()
    row = rows[row_no - 1]
    fio = row[fio_idx].strip() if fio_idx < len(row) else "?"
    diss_url = row[diss_url_idx].strip() if diss_url_idx < len(row) else ""
    print(f"=== row {row_no}: {fio} ===")
    print(f"dissertation_url = {diss_url!r}")

    if not diss_url.startswith("http"):
        print("  пропуск: url отсутствует/невалиден")
        return

    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json), scopes=GOOGLE_SCOPES
    )
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    file_id = extract_google_file_id(diss_url)
    paragraphs: list[str] = []
    headings: list[str] = []

    try:
        doc = docs_service.documents().get(documentId=file_id).execute()
        from magister_checking.dissertation_metrics import iter_heading_texts
        from magister_checking.docs_extract import extract_plain_text

        plain = extract_plain_text(doc)
        paragraphs = _first_paragraphs_from_plain(plain, limit=120)
        headings = [h.strip() for h in iter_heading_texts(doc) if h.strip()]
        print("  source: GOOGLE DOC")
    except Exception as exc:  # noqa: BLE001
        print(f"  Google Doc fetch failed ({exc.__class__.__name__}); пробуем как .docx")
        data = download_drive_file_bytes(drive_service=drive_service, file_id=file_id)
        if not data or Document is None:
            print(f"  download failed (data={bool(data)}); abort")
            return
        d = Document(io.BytesIO(data))
        for p in d.paragraphs:
            text = (p.text or "").strip()
            if not text:
                continue
            paragraphs.append(text)
            try:
                style_name = str(p.style.name or "")
            except Exception:  # noqa: BLE001
                style_name = ""
            if "heading" in style_name.lower() or "заголов" in style_name.lower():
                headings.append(text)
            if len(paragraphs) >= 120:
                break
        print(f"  source: DOCX, paragraphs={len(paragraphs)}, headings={len(headings)}")

    print(f"  --- first 30 paragraphs ---")
    for i, p in enumerate(paragraphs[:30]):
        print(f"  [{i:>2}] {p[:140]}")

    print(f"  --- headings (first 10) ---")
    for i, h in enumerate(headings[:10]):
        is_stop = _is_stop_phrase(h)
        print(f"  H[{i}] stop={is_stop} | {h[:140]}")

    print("  --- pattern hits ---")
    for i, p in enumerate(paragraphs[:60]):
        m_inline = _ON_TOPIC_INLINE_RE.search(p)
        m_header = _ON_TOPIC_HEADER_RE.match(p)
        m_topic = _TOPIC_HEADER_RE.search(p)
        if m_inline or m_header or m_topic:
            tags = []
            if m_inline:
                tags.append(f"INLINE→{m_inline.group(1)[:80]!r}")
            if m_header:
                tags.append("HEADER (пустой хвост, тема в следующем параграфе)")
            if m_topic:
                tags.append(f"TOPIC→{m_topic.group(1)[:80]!r}")
            print(f"  P[{i}] {' | '.join(tags)}  raw={p[:120]!r}")


if __name__ == "__main__":
    targets = [int(x) for x in sys.argv[1:]] or [2, 16]
    for r in targets:
        _probe(r)
        print()
