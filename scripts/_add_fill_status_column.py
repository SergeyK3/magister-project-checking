"""Одноразово добавляет колонку «Статус заполнения» в шапку листа «Регистрация».

Безопасные инварианты:
- Добавляем строго В КОНЕЦ существующей шапки (не сдвигаем индексы других колонок).
- Сначала проверяем, нет ли уже колонки, нормализованное имя которой совпадает
  с любым алиасом ``fill_status`` из ``_HEADER_ALIASES`` (в т.ч. «статус
  заполнения», «fill_status»). Если есть — ничего не пишем.
- ``_CHECK_RESULT_COLUMN_KEYS`` (sheets_repo.py) НЕ содержит ``fill_status``,
  поэтому добавление колонки не меняет поведение clean-write при ``--apply``.
- Запись делаем одной операцией ``worksheet.update`` в ячейку первой строки.
"""

from __future__ import annotations

import sys


HEADER_TITLE = "Статус заполнения"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    from magister_checking.bot.config import load_config
    from magister_checking.bot import sheets_repo as sr

    cfg = load_config()
    ws = sr.get_worksheet(cfg)

    header = ws.row_values(1)
    normalized = {sr._normalize_header(name): idx for idx, name in enumerate(header)}

    aliases = sr._HEADER_ALIASES["fill_status"]
    for alias in aliases:
        norm = sr._normalize_header(alias)
        if norm and norm in normalized:
            existing_idx = normalized[norm]
            print(
                f"Колонка уже есть: name={header[existing_idx]!r} "
                f"col={existing_idx + 1} (A1={sr._column_letter(existing_idx)}). "
                f"Ничего не меняю."
            )
            return 0

    new_col_idx = len(header)
    cell_a1 = f"{sr._column_letter(new_col_idx)}1"
    grid_cols = int(getattr(ws, "col_count", 0) or 0)
    print(
        f"Шапка длиной {len(header)} колонок; grid_cols={grid_cols}; "
        f"целевая ячейка {cell_a1}."
    )

    if grid_cols and new_col_idx >= grid_cols:
        delta = new_col_idx + 1 - grid_cols
        print(f"Расширяю grid: add_cols({delta}).")
        ws.add_cols(delta)

    ws.update(range_name=cell_a1, values=[[HEADER_TITLE]])

    new_header = ws.row_values(1)
    field_map = sr._field_to_column_map(ws)
    fill_idx = field_map.get("fill_status")
    print(f"Новая длина шапки: {len(new_header)}.")
    print(f"Заголовок в {cell_a1}: {new_header[new_col_idx]!r}.")
    if fill_idx is None:
        print("ВНИМАНИЕ: _field_to_column_map не нашёл 'fill_status' после записи.")
        return 1
    print(
        f"_field_to_column_map → fill_status = col {fill_idx + 1} "
        f"({sr._column_letter(fill_idx)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
