"""Одноразовая проверка: качаем .docx Танановой напрямую и считаем источники.

Запуск:
    python -m scripts._verify_tananova_sources

Печатает все три сигнала по библиографии:
- Word-нумерованный список (numPr count)
- Реальная нумерация «1. … N.» / «[1] …» в начале абзацев → max(n.) на text-плене
- Подсчёт URL-абзацев в окне библиографии (fallback)

Это не unit-test: упирается в реальный файл на Drive (нужен SA-доступ
к 14qtf_5d0Wh2uqNYhllLvN0M2uRBvSSjn — handoff §«Полезные file_id»).
"""

from __future__ import annotations

import io
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from magister_checking.bot.config import load_config
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES
from magister_checking.dissertation_metrics import (
    _BIB_MARKERS,
    _docx_bibliography_has_line_numbering,
    _docx_bibliography_url_paragraph_count,
    _docx_bibliography_windows,
    _docx_bibliography_word_list_count,
    _docx_paragraph_records,
    _estimate_sources_count,
    _docx_plain_text_all_paragraphs,
    _is_bibliography_marker,
    _is_appendix_marker,
    analyze_docx_bytes,
)
from docx import Document  # type: ignore[import-untyped]


_TANANOVA_DOCX_FILE_ID = "14qtf_5d0Wh2uqNYhllLvN0M2uRBvSSjn"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json), scopes=GOOGLE_SCOPES
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    req = drive.files().get_media(
        fileId=_TANANOVA_DOCX_FILE_ID, supportsAllDrives=True
    )
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    data = fh.getvalue()
    print(f"docx bytes: {len(data)}")

    doc = Document(io.BytesIO(data))
    plain = _docx_plain_text_all_paragraphs(doc)

    word_list = _docx_bibliography_word_list_count(doc)
    text_max = _estimate_sources_count(plain)
    has_lines = _docx_bibliography_has_line_numbering(doc)
    url_paras = _docx_bibliography_url_paragraph_count(doc)
    windows = _docx_bibliography_windows(doc)
    window = windows[0] if windows else []

    print(f"Word-list count:                {word_list}")
    print(f"Text max(n.) (clipped at app.): {text_max}")
    print(f"Bib has '1.'/'[1]' line starts: {has_lines}")
    print(f"URL paragraph count (max):      {url_paras}")
    print(f"Bib windows count:              {len(windows)}")
    for i, w in enumerate(windows):
        urls = sum(
            1
            for text, _ in w
            if __import__("re").search(r"https?://", text, __import__("re").IGNORECASE)
        )
        print(f"  window[{i}]: paragraphs={len(w)}, urls={urls}")

    metrics = analyze_docx_bytes(data)
    print(f"\nFINAL sources_count = {metrics.sources_count}")
    print(f"FINAL approx_pages  = {metrics.approx_pages}")

    print("\n--- bib window first 5 / last 5 paragraphs ---")
    for i, (text, numpr) in enumerate(window[:5]):
        snip = text[:100].replace("\n", " ")
        print(f"  [{i:3}] numpr={numpr}  {snip!r}")
    print("  ...")
    for i, (text, numpr) in enumerate(window[-5:], start=len(window) - 5):
        snip = text[:100].replace("\n", " ")
        print(f"  [{i:3}] numpr={numpr}  {snip!r}")

    print("\n--- numpr stats in window ---")
    counts: dict[object, int] = {}
    for _, numpr in window:
        counts[numpr] = counts.get(numpr, 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {k!r}: {v}")

    print("\n--- numpr runs in window (active=key, len=run length) ---")
    runs: list[tuple[object, int, int]] = []
    active = None
    active_count = 0
    start = 0
    gap = 0
    for i, (_, numpr) in enumerate(window):
        if numpr is None:
            gap += 1
            if gap > 1:
                if active is not None:
                    runs.append((active, start, active_count))
                active = None
                active_count = 0
            continue
        gap = 0
        if active is None or numpr != active:
            if active is not None:
                runs.append((active, start, active_count))
            active = numpr
            start = i
            active_count = 1
        else:
            active_count += 1
    if active is not None:
        runs.append((active, start, active_count))
    for npr, st, ln in runs:
        print(f"  numpr={npr} start={st} len={ln}")

    print("\n--- ALL bib & appendix markers in document ---")
    records = _docx_paragraph_records(doc)
    for i, (text, _) in enumerate(records):
        is_bib = _is_bibliography_marker(text)
        is_app = _is_appendix_marker(text)
        if is_bib or is_app:
            short = text[:80].replace("\n", " ")
            tag = "BIB" if is_bib else "APP"
            print(f"  [{i:4}] {tag}  {short!r}")

    print("\n--- url paragraphs (sample first 5 / last 5) ---")
    import re as _re
    url_indices = [
        i for i, (text, _) in enumerate(window) if _re.search(r"https?://", text, _re.IGNORECASE)
    ]
    print(f"  url-bearing indices count: {len(url_indices)}")
    for i in url_indices[:5] + url_indices[-5:]:
        text = window[i][0][:120].replace("\n", " ")
        print(f"  [{i:3}] {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
