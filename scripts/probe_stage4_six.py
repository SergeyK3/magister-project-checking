"""Verify after --apply for six rows: значения и strikethrough в J:O (Stage 3)
плюс значения колонок Stage 4 (pages_total / sources_count / compliance).

Запускать ПОСЛЕ ``check-row --row N --apply`` для строк 3, 6, 8, 9, 14, 18.

Полезен для ручного контроля живого прогона: показывает, что записал бот
и не оставил ли мусор в Shared Drive буфере (для Stage 4 буфер не используется,
но проверяем и его — на случай регресса в drive_docx).
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from google.oauth2.service_account import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from magister_checking.bot.config import load_config  # noqa: E402
from magister_checking.bot.sheets_repo import _HEADER_ALIASES  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

BUFFER_FOLDER = "1FAmQ5NBahuNrhXfqvdrOw3F3agyKaBgI"
ROWS = (3, 6, 8, 9, 14, 18)
STAGE4_KEYS = ("pages_total", "sources_count", "compliance")


def _column_letter(idx_zero_based: int) -> str:
    """0 → A, 25 → Z, 26 → AA. Достаточно для типичного листа регистрации."""
    n = idx_zero_based
    out = ""
    while True:
        out = chr(ord("A") + n % 26) + out
        n = n // 26 - 1
        if n < 0:
            return out


def _resolve_stage4_columns(sheets, sheet_id: str, ws_name: str) -> dict[str, str]:
    """По заголовку находит буквенные индексы колонок Stage 4.

    Использует те же алиасы, что и runtime (``_HEADER_ALIASES``), чтобы
    дамп точно совпал с тем, куда писал ``apply_row_check_updates``.
    """
    header_resp = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{ws_name}'!1:1",
    ).execute()
    header = (header_resp.get("values") or [[]])[0]

    norm = [str(h or "").strip().lower() for h in header]
    found: dict[str, str] = {}
    for key in STAGE4_KEYS:
        aliases = {a.lower() for a in _HEADER_ALIASES.get(key, ())}
        for idx, name in enumerate(norm):
            if name in aliases:
                found[key] = _column_letter(idx)
                break
    return found


def _dump_stage3_block(sheets, sheet_id: str, ws_name: str, row: int) -> None:
    print(f"\n== Row {row} J:O (Stage 3) ==")
    fmt = sheets.spreadsheets().get(
        spreadsheetId=sheet_id,
        ranges=[f"'{ws_name}'!J{row}:O{row}"],
        fields=(
            "sheets(data(rowData(values(formattedValue,"
            "userEnteredFormat(textFormat(strikethrough))))))"
        ),
    ).execute()
    cells = (
        fmt.get("sheets", [{}])[0]
        .get("data", [{}])[0]
        .get("rowData", [{}])[0]
        .get("values", [])
    )
    for col_letter, cell in zip("JKLMNO", cells):
        tf = cell.get("userEnteredFormat", {}).get("textFormat", {})
        strike = tf.get("strikethrough")
        marker = " [STRIKE]" if strike else ""
        v = cell.get("formattedValue") or ""
        if len(v) > 70:
            v = v[:67] + "..."
        print(f"  {col_letter}: {v!r}{marker}")


def _dump_stage4_block(
    sheets,
    sheet_id: str,
    ws_name: str,
    row: int,
    cols: dict[str, str],
) -> None:
    print(f"== Row {row} Stage 4 ==")
    if not cols:
        print("  (заголовки pages_total / sources_count / compliance не найдены)")
        return
    for key in STAGE4_KEYS:
        letter = cols.get(key)
        if not letter:
            print(f"  {key}: (колонка не найдена в заголовке)")
            continue
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{ws_name}'!{letter}{row}",
        ).execute()
        values = resp.get("values") or [[""]]
        v = (values[0] or [""])[0]
        print(f"  {key} [{letter}{row}]: {v!r}")


def main() -> int:
    cfg = load_config()
    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json), scopes=SCOPES
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    print("== Buffer folder content (excluding trashed) ==")
    resp = drive.files().list(
        q=f"'{BUFFER_FOLDER}' in parents and trashed=false",
        fields="files(id,name,createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if not files:
        print("  (clean — buffer empty)")
    for f in files:
        print(f"  id={f['id']} name={f['name']!r} created={f.get('createdTime')}")

    cols = _resolve_stage4_columns(
        sheets, cfg.spreadsheet_id, cfg.worksheet_name
    )
    print("\n== Stage 4 column mapping ==")
    for key in STAGE4_KEYS:
        print(f"  {key} -> {cols.get(key, '(missing)')}")

    for r in ROWS:
        _dump_stage3_block(sheets, cfg.spreadsheet_id, cfg.worksheet_name, r)
        _dump_stage4_block(
            sheets, cfg.spreadsheet_id, cfg.worksheet_name, r, cols
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
