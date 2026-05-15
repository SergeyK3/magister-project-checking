"""Найти строку магистранта в листе «Регистрация» по подстроке ФИО."""

from __future__ import annotations

import sys


def main(needles: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from magister_checking.bot.config import load_config
    from magister_checking.bot import sheets_repo as sr

    cfg = load_config()
    spreadsheet = sr.get_spreadsheet(cfg)
    ws = spreadsheet.worksheet("Регистрация")
    fios = ws.col_values(2)
    for needle in needles:
        nl = needle.lower()
        print(f"--- search: {needle!r} ---")
        hits = []
        for i, fio in enumerate(fios, start=1):
            if nl in (fio or "").lower():
                hits.append((i, fio))
        for row, fio in hits:
            print(f"  row {row:>3}: {fio}")
        if not hits:
            print("  не найдено.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1:])
    else:
        main(["Танан", "Марадж", "Макиш", "Хайтба", "Камзеба"])
