"""Одноразовая/повторяемая заливка листа «научрук» из другой книги Google Sheets.

Запуск из корня репозитория (подхватывается .env):
  python scripts/copy_supervisors_sheet.py

Переменные окружения: GOOGLE_SERVICE_ACCOUNT_JSON или GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT
(как у бота). Доступ редактора к обеим книгам обязателен.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# корень репозитория: parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from magister_checking.bot.config import ConfigError, load_config
from magister_checking.bot.sheets_repo import (
    SUPERVISORS_WORKSHEET_NAME,
    get_gspread_client,
    get_or_create_worksheet,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Копирует данные в лист «научрук».")
    parser.add_argument(
        "--source-id",
        default="1rWIr5cowd1CkjBnMb_sxKtjXWMECcni-tveDjsy9-s4",
        help="ID исходной книги",
    )
    parser.add_argument(
        "--dest-id",
        default="1RuAXZt9wAu3CQNn3GAL65ifyjykN5AnD8XFwnZ_v39s",
        help="ID целевой книги (бота)",
    )
    parser.add_argument(
        "--source-sheet",
        default="",
        help="Имя листа-источника; пусто — первый лист книги",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    try:
        cfg = load_config(dotenv_path=ROOT / ".env")
    except ConfigError as e:
        print("Ошибка конфига:", e, file=sys.stderr)
        sys.exit(1)

    client = get_gspread_client(cfg)

    src_book = client.open_by_key(args.source_id)
    if args.source_sheet.strip():
        src_ws = src_book.worksheet(args.source_sheet.strip())
    else:
        src_ws = src_book.sheet1

    values = src_ws.get_all_values()
    if not values:
        print("Источник пуст — нечего копировать.", file=sys.stderr)
        sys.exit(2)

    nrows = max(len(values) + 50, 100)
    ncols = max((len(r) for r in values), default=1) + 5

    dest_book = client.open_by_key(args.dest_id)
    dest_ws = get_or_create_worksheet(
        dest_book,
        SUPERVISORS_WORKSHEET_NAME,
        rows=nrows,
        cols=min(ncols, 18278),
    )

    try:
        dest_ws.clear()
    except Exception:
        pass

    # RAW — как в sheets_repo (формулы не исполняются)
    dest_ws.update("A1", values, value_input_option="RAW")
    print(
        f"OK: {len(values)} строк(и) → «{SUPERVISORS_WORKSHEET_NAME}» "
        f"в книге {args.dest_id} (лист-источник: {src_ws.title!r})"
    )


if __name__ == "__main__":
    main()
