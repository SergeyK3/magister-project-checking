"""Verify after --apply for six rows: значения и strikethrough в J:O,
а также пустота buffer-папки Shared Drive (мусор от .docx-конверсий)."""

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

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

BUFFER_FOLDER = "1FAmQ5NBahuNrhXfqvdrOw3F3agyKaBgI"
ROWS = (3, 6, 8, 9, 14, 18)


def dump_row(sheets, sheet_id: str, ws_name: str, row: int) -> None:
    print(f"\n== Row {row} J:O ==")
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

    for r in ROWS:
        dump_row(sheets, cfg.spreadsheet_id, cfg.worksheet_name, r)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
