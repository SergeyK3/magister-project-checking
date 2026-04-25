"""Одноразовый дамп заполненных колонок указанных строк листа «Регистрация»."""

from __future__ import annotations

import sys


def main(target_rows: list[int]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from magister_checking.bot.config import load_config
    from magister_checking.bot import sheets_repo as sr

    cfg = load_config()
    ws = sr.get_worksheet(cfg)
    hdr = ws.row_values(1)
    for row in target_rows:
        rv = ws.row_values(row)
        print(f"--- row {row} ---")
        for i, v in enumerate(rv, start=1):
            if not v.strip():
                continue
            head = hdr[i - 1] if i <= len(hdr) else ""
            print(f"  col={i:>2}  {head:30}  v={v[:140]}")


if __name__ == "__main__":
    rows = [int(x) for x in sys.argv[1:]] or [8, 13]
    main(rows)
