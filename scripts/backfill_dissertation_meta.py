"""Backfill колонок S «Название диссертации» и T «Язык диссертации».

Сценарий ручного бэкфила: pipeline ``check-row --apply`` эти колонки НЕ
трогает (handoff §5 «never_recheck»), поэтому начальное заполнение
делается отдельным скриптом. По умолчанию работает в режиме dry-run и
лишь печатает, что было бы записано; запись включается флагом ``--apply``.

Использование (PowerShell):

    # 1. Посмотреть, что получится — без записи в Sheets:
    python scripts\backfill_dissertation_meta.py

    # 2. Только конкретные строки:
    python scripts\backfill_dissertation_meta.py --rows 2,5,7

    # 3. Применить (запись в Sheets):
    python scripts\backfill_dissertation_meta.py --apply

    # 4. Перезаписать НЕпустые S/T (по умолчанию они защищены):
    python scripts\backfill_dissertation_meta.py --apply --force

Скрипт идемпотентен: повторный запуск без ``--force`` пропускает строки,
у которых обе колонки уже заполнены.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# В проекте нет pyproject.toml/setup.py — пакет ``magister_checking`` импортируем
# через ручное добавление корня репозитория в sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from google.oauth2.service_account import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from magister_checking.bot import sheets_repo as sr  # noqa: E402
from magister_checking.bot.config import BotConfig, load_config  # noqa: E402
from magister_checking.bot.report_enrichment import URL_MISSING, URL_UNAVAILABLE  # noqa: E402
from magister_checking.bot.sheets_repo import GOOGLE_SCOPES  # noqa: E402
from magister_checking.dissertation_meta import (  # noqa: E402
    detect_dissertation_language_from_docx_bytes,
    detect_dissertation_language_from_gdoc,
    detect_dissertation_title_from_docx_bytes,
    detect_dissertation_title_from_gdoc,
    warn_if_unusual_language,
)
from magister_checking.dissertation_metrics import download_drive_file_bytes  # noqa: E402
from magister_checking.drive_urls import extract_google_file_id  # noqa: E402

logger = logging.getLogger("backfill_dissertation_meta")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_rows_arg(value: str) -> list[int]:
    """Принимает «2,3,5-9» → [2,3,5,6,7,8,9]."""

    rows: list[int] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            rows.extend(range(start, end + 1))
        else:
            rows.append(int(chunk))
    return sorted(set(rows))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="реально записать значения в Sheets (без флага — только печать)",
    )
    parser.add_argument(
        "--rows",
        type=_parse_rows_arg,
        default=None,
        help="ограничить набор строк, например: 2,5,7-10 (по умолчанию — все, начиная со 2)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="перезаписать S/T, даже если они уже заполнены (по умолчанию пропускаются)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="пауза между строками, сек (защита от Google API rate limits)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="обработать не более N строк (для отладки)",
    )
    return parser


# ---------------------------------------------------------------------------
# Извлечение метаданных одной строки
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowResult:
    row: int
    fio: str
    dissertation_url: str
    title: str
    language: str
    note: str


def _looks_like_real_url(value: str) -> bool:
    """``URL_MISSING``/``URL_UNAVAILABLE`` и пустота — не ссылки."""

    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped in (URL_MISSING, URL_UNAVAILABLE):
        return False
    return stripped.startswith("http://") or stripped.startswith("https://")


def _extract_meta(
    *,
    docs_service: Any,
    drive_service: Any,
    dissertation_url: str,
) -> tuple[str, str, str]:
    """Возвращает ``(title, language, note)``.

    ``note`` — короткое объяснение для лога: «gdoc», «docx», «empty docx»,
    «no access» и т.п. Само значение в Sheets не идёт.
    """

    try:
        file_id = extract_google_file_id(dissertation_url)
    except ValueError as exc:
        return "", "", f"bad_url: {exc}"

    try:
        diss_doc = docs_service.documents().get(documentId=file_id).execute()
    except Exception as exc:  # noqa: BLE001
        data = download_drive_file_bytes(drive_service=drive_service, file_id=file_id)
        if not data:
            return "", "", f"no_access: {exc.__class__.__name__}"
        title = detect_dissertation_title_from_docx_bytes(data)
        language = detect_dissertation_language_from_docx_bytes(data)
        warn_if_unusual_language(language, context=f"row file_id={file_id}")
        return title, language, "docx_fallback"

    title = detect_dissertation_title_from_gdoc(diss_doc)
    language = detect_dissertation_language_from_gdoc(diss_doc)
    warn_if_unusual_language(language, context=f"row file_id={file_id}")
    return title, language, "gdoc"


# ---------------------------------------------------------------------------
# Основной проход
# ---------------------------------------------------------------------------


def _row_value(row: list[str], idx: int) -> str:
    if 0 <= idx < len(row):
        return (row[idx] or "").strip()
    return ""


def _service_account_credentials(config: BotConfig) -> Credentials:
    return Credentials.from_service_account_file(
        str(config.google_service_account_json),
        scopes=GOOGLE_SCOPES,
    )


def backfill(*, apply: bool, only_rows: list[int] | None, force: bool, sleep_sec: float,
             limit: int | None) -> int:
    """Возвращает количество строк, у которых что-то поменялось бы / поменялось."""

    config = load_config()
    worksheet = sr.get_worksheet(config)
    header = worksheet.row_values(1)
    mapping = sr._field_to_column_map(worksheet)  # noqa: SLF001 — публичной обёртки нет

    required = ("dissertation_url", "dissertation_title", "dissertation_language", "fio")
    missing = [name for name in required if name not in mapping]
    if missing:
        sys.stderr.write(
            f"Не нашли в шапке листа колонки: {missing}. Проверьте Регистрация-лист.\n"
        )
        return 0

    diss_url_idx = mapping["dissertation_url"]
    diss_title_idx = mapping["dissertation_title"]
    diss_lang_idx = mapping["dissertation_language"]
    fio_idx = mapping["fio"]

    title_col_letter = sr._column_letter(diss_title_idx)  # noqa: SLF001
    lang_col_letter = sr._column_letter(diss_lang_idx)  # noqa: SLF001
    print(
        f"Шапка: title → {title_col_letter} (idx={diss_title_idx + 1}), "
        f"language → {lang_col_letter} (idx={diss_lang_idx + 1})"
    )

    all_rows = worksheet.get_all_values()
    last_row = len(all_rows)
    target_rows: Iterable[int] = (
        only_rows if only_rows is not None else range(2, last_row + 1)
    )
    if limit is not None:
        target_rows = list(target_rows)[:limit]

    creds = _service_account_credentials(config)
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    print(
        f"Режим: {'APPLY (запись в Sheets)' if apply else 'dry-run (только печать)'}, "
        f"force={force}, lines={list(target_rows) if isinstance(target_rows, list) else 'all'}"
    )
    print("-" * 100)

    changed = 0
    for row_no in target_rows:
        if row_no < 2 or row_no > last_row:
            print(f"row {row_no:>3}  — пропуск (вне диапазона 2..{last_row})")
            continue
        row = all_rows[row_no - 1]
        fio = _row_value(row, fio_idx) or "(нет ФИО)"
        diss_url = _row_value(row, diss_url_idx)
        existing_title = _row_value(row, diss_title_idx)
        existing_lang = _row_value(row, diss_lang_idx)

        if not _looks_like_real_url(diss_url):
            print(f"row {row_no:>3}  {fio:30}  пропуск: dissertation_url={diss_url!r}")
            continue

        if not force and existing_title and existing_lang:
            print(
                f"row {row_no:>3}  {fio:30}  пропуск: уже заполнено (title='{existing_title[:60]}', "
                f"lang='{existing_lang}'); используйте --force для перезаписи"
            )
            continue

        try:
            title, language, note = _extract_meta(
                docs_service=docs_service,
                drive_service=drive_service,
                dissertation_url=diss_url,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"row {row_no:>3}  {fio:30}  ОШИБКА извлечения: {exc!r}")
            continue

        action_chunks: list[str] = []
        new_title = title if (force or not existing_title) else existing_title
        new_lang = language if (force or not existing_lang) else existing_lang
        if new_title != existing_title:
            action_chunks.append(f"title:'{existing_title}'→'{new_title}'")
        if new_lang != existing_lang:
            action_chunks.append(f"lang:'{existing_lang}'→'{new_lang}'")
        will_change = bool(action_chunks)

        suffix = "WILL_WRITE" if (will_change and apply) else (
            "WOULD_WRITE" if will_change else "NO_CHANGE"
        )
        print(
            f"row {row_no:>3}  {fio:30}  [{note:14}] title='{title[:60]}'  lang='{language:10}'  "
            f"{suffix}  {' | '.join(action_chunks)}"
        )

        if will_change:
            changed += 1

        if apply and will_change:
            updates = []
            if new_title != existing_title:
                updates.append(
                    {"range": f"{title_col_letter}{row_no}", "values": [[new_title]]}
                )
            if new_lang != existing_lang:
                updates.append(
                    {"range": f"{lang_col_letter}{row_no}", "values": [[new_lang]]}
                )
            try:
                worksheet.batch_update(updates, value_input_option="USER_ENTERED")
            except TypeError:
                worksheet.batch_update(updates)
            except Exception as exc:  # noqa: BLE001
                print(f"row {row_no:>3}  {fio:30}  ОШИБКА записи: {exc!r}")
                continue

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    print("-" * 100)
    print(
        f"Готово. {'Применено' if apply else 'Будет применено'} изменений: {changed}."
    )
    return changed


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    backfill(
        apply=args.apply,
        only_rows=args.rows,
        force=args.force,
        sleep_sec=args.sleep,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
