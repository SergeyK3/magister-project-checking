"""Command-line entry point."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from magister_checking import __version__
from magister_checking.auth import get_credentials
from magister_checking.docs_extract import extract_plain_text, iter_hyperlinks
from magister_checking.drive_urls import extract_google_file_id
from magister_checking.summary_pipeline import (
    SUMMARY_HEADER,
    build_detail_body_text,
    build_summary_rows,
    run_fill_one_student_detail_doc,
    run_fill_all_students_docs,
    run_test1_fill_summary_doc,
)


def cmd_login(_: argparse.Namespace) -> int:
    get_credentials(interactive=True)
    print("Учётные данные сохранены в credentials/token.json")
    return 0


def cmd_bot(_: argparse.Namespace) -> int:
    """Запускает Telegram-бота @magistrcheckbot (long polling).

    Конфигурация читается из переменных окружения (см. .env.example).
    """

    from magister_checking.bot.app import run as run_bot
    from magister_checking.bot.config import ConfigError, load_config

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации бота: {exc}", file=sys.stderr)
        print(
            "Заполните .env по шаблону .env.example "
            "(TELEGRAM_BOT_TOKEN, SPREADSHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON).",
            file=sys.stderr,
        )
        return 2

    run_bot(config)
    return 0


def cmd_broadcast(ns: argparse.Namespace) -> int:
    """Рассылает текстовое сообщение всем зарегистрированным пользователям бота.

    По умолчанию — dry-run: только печатает список адресатов и текст. Реальная
    отправка требует двух явных флагов: ``--send`` и ``--i-know-this-is-irreversible``.
    Это два барьера от случайной массовой отправки (handoff §3 — рассылка
    необратима).

    Источник адресатов:
    - ``registration``: только колонка ``telegram_id`` листа Регистрация;
    - ``persistence``:  только ``user_data``/``chat_data`` PicklePersistence;
    - ``both`` (default): объединение с дедупликацией.

    Текст сообщения берётся из файла (``--message-file``) — это надёжнее, чем
    передавать многострочный текст через ``-m`` PowerShell (handoff §5,
    PowerShell-mojibake).
    """

    import asyncio

    from telegram import Bot

    from magister_checking.bot.config import ConfigError, load_config
    from magister_checking.bot.sheets_repo import (
        get_worksheet,
        list_registered_telegram_ids,
    )
    from magister_checking.broadcast import (
        collect_chat_ids_from_persistence,
        format_dry_run_preview,
        format_send_summary,
        merge_dedup,
        send_broadcast,
    )

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации бота: {exc}", file=sys.stderr)
        return 2

    message_path = ns.message_file
    try:
        # utf-8-sig: спокойно проглотит BOM, который PowerShell ``Out-File``
        # на Windows добавляет в файлы с -Encoding utf8 — иначе сообщение
        # уйдёт в Telegram с невидимым ``\ufeff`` в начале первой строки.
        message = message_path.read_text(encoding="utf-8-sig").rstrip("\n")
    except OSError as exc:
        print(f"Не удалось прочитать --message-file {message_path}: {exc}", file=sys.stderr)
        return 2
    if not message.strip():
        print(f"Файл {message_path} пуст — нечего рассылать.", file=sys.stderr)
        return 2

    source = ns.source
    reg_ids: list[str] = []
    persist_ids: list[str] = []
    if source in ("registration", "both"):
        worksheet = get_worksheet(config)
        reg_ids = list_registered_telegram_ids(worksheet)
    if source in ("persistence", "both"):
        persist_ids = collect_chat_ids_from_persistence(config.persistence_file)

    if source == "registration":
        recipients = merge_dedup(reg_ids)
        source_label = f"Регистрация ({len(reg_ids)} ID)"
    elif source == "persistence":
        recipients = merge_dedup(persist_ids)
        source_label = (
            f"PicklePersistence: {config.persistence_file} ({len(persist_ids)} ID)"
        )
    else:
        recipients = merge_dedup(reg_ids, persist_ids)
        source_label = (
            f"Регистрация ({len(reg_ids)}) ∪ PicklePersistence "
            f"{config.persistence_file} ({len(persist_ids)}) → дедуп: {len(recipients)}"
        )

    if not ns.send:
        print(format_dry_run_preview(recipients, message, source_label=source_label))
        return 0

    if not ns.i_know_this_is_irreversible:
        print(
            "--send требует подтверждения: добавьте --i-know-this-is-irreversible.",
            file=sys.stderr,
        )
        return 2

    if not recipients:
        print("Получателей нет — отправка не выполнена.", file=sys.stderr)
        return 1

    print(f"Отправляю сообщение {len(recipients)} получателям...")
    sleep_between = max(1.0 / max(ns.rate, 1.0), 0.0)

    async def _run() -> int:
        async with Bot(config.telegram_bot_token) as bot:
            result = await send_broadcast(
                bot,
                recipients,
                message,
                sleep_between=sleep_between,
            )
        print(format_send_summary(result))
        return 0 if not result.failed else 1

    return asyncio.run(_run())


def cmd_check_row(ns: argparse.Namespace) -> int:
    """Построчная проверка магистранта (этапы 1-3 ТЗ).

    Печатает «справку» в stdout; лист не изменяется (sheet writes — в
    отдельной фазе).
    """

    from magister_checking.bot.config import ConfigError, load_config
    from magister_checking.row_check_cli import (
        RowLocator,
        format_report,
        run_row_check,
    )

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    locator = RowLocator(row_number=ns.row, fio=ns.fio)
    try:
        report = run_row_check(
            config,
            locator,
            skip_http=ns.skip_http,
            apply=ns.apply,
            only_if_changed=ns.only_if_changed,
            history_source="cli",
        )
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    # При only_if_changed + unchanged лист не трогали — applied=False,
    # чтобы пометка «(запись в лист выполнена)» не печаталась.
    applied_effective = ns.apply and not report.unchanged
    print(format_report(report, applied=applied_effective))
    return 0


def cmd_doc_info(ns: argparse.Namespace) -> int:
    creds = get_credentials(interactive=True)
    doc_id = extract_google_file_id(ns.url_or_id)
    service = build("docs", "v1", credentials=creds, cache_discovery=False)
    doc = service.documents().get(documentId=doc_id).execute()
    title = doc.get("title", "")
    body = doc.get("body", {})
    content_len = len(json.dumps(body, ensure_ascii=False))
    print(f"documentId: {doc_id}")
    print(f"title: {title!r}")
    print(f"body (approx JSON size): {content_len} chars")
    return 0


def cmd_doc_extract(ns: argparse.Namespace) -> int:
    creds = get_credentials(interactive=True)
    doc_id = extract_google_file_id(ns.url_or_id)
    service = build("docs", "v1", credentials=creds, cache_discovery=False)
    doc = service.documents().get(documentId=doc_id).execute()

    if ns.links_only:
        for h in iter_hyperlinks(doc):
            anchor = h.anchor_text.replace("\n", " ").strip()
            print(f"{h.context_path}\n  {h.url}\n  {anchor!r}\n")
        return 0

    plain = extract_plain_text(doc)
    print(f"documentId: {doc_id}")
    print(f"plain_text length: {len(plain)}")
    if not ns.no_plain_preview:
        preview = plain[: ns.plain_max]
        print(f"\n--- plain text (first {len(preview)} chars) ---\n")
        print(preview)
        if len(plain) > len(preview):
            print(f"\n... [{len(plain) - len(preview)} chars more]")

    links = list(iter_hyperlinks(doc))
    print(f"\n--- hyperlinks ({len(links)}) ---")
    for h in links:
        anchor = h.anchor_text.replace("\n", " ").strip()
        print(f"{h.context_path}: {h.url} ({anchor!r})")
    return 0


def cmd_build_summary(ns: argparse.Namespace) -> int:
    creds = get_credentials(interactive=True)
    summary_id = extract_google_file_id(ns.summary_doc)

    if ns.output_sheet and not ns.dry_run:
        print(
            "Запись в Google Sheets отключена по обновлённому ТЗ.\n"
            "Используйте: python -m magister_checking fill-docs-test1 "
            "<список_doc> <сводный_выход_doc>\n"
            "Или укажите только сводный список — результат будет выведен в stdout (TSV).",
            file=sys.stderr,
        )
        return 2

    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    summary = docs.documents().get(documentId=summary_id).execute()
    result = build_summary_rows(
        summary_document=summary, docs_service=docs, drive_service=drive
    )
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(SUMMARY_HEADER)
    w.writerows(result.rows)
    sys.stdout.write(buf.getvalue())
    for line in result.log_lines:
        print(line, file=sys.stderr)
    return 0


def cmd_fill_docs_test1(ns: argparse.Namespace) -> int:
    """Тест 1: один магистрант → одна строка; при --all-students — все строки + детальный Doc."""
    creds = get_credentials(interactive=True)
    list_id = extract_google_file_id(ns.list_doc)
    out_sum = extract_google_file_id(ns.output_summary_doc)
    out_detail = (
        extract_google_file_id(ns.output_detail_doc) if ns.output_detail_doc else None
    )

    if ns.dry_run:
        docs = build("docs", "v1", credentials=creds, cache_discovery=False)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        list_doc = docs.documents().get(documentId=list_id).execute()
        from magister_checking.summary_doc_parser import parse_summary_document
        from magister_checking.summary_pipeline import build_one_summary_row

        students = parse_summary_document(list_doc)
        if not students:
            print("Список пуст.", file=sys.stderr)
            return 1
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="\t", lineterminator="\n")
        w.writerow(SUMMARY_HEADER)
        if ns.all_students:
            built: list[tuple[Any, list[Any]]] = []  # (student, row) for stderr preview
            for i, st in enumerate(students, start=1):
                row, _ex = build_one_summary_row(
                    index=i, st=st, docs_service=docs, ts=ts, drive_service=drive
                )
                w.writerow(row)
                built.append((st, row))
            sys.stdout.write(buf.getvalue())
            if out_detail:
                print("\n--- Детальный Doc (превью тел под H1) ---", file=sys.stderr)
                for st, row in built:
                    print(f"\n### {st.name or st} ###", file=sys.stderr)
                    print(build_detail_body_text(st=st, summary_row=row), file=sys.stderr)
            print("(dry-run: целевые Doc не изменены)", file=sys.stderr)
            return 0

        row, _ex = build_one_summary_row(
            index=1, st=students[0], docs_service=docs, ts=ts, drive_service=drive
        )
        w.writerow(row)
        sys.stdout.write(buf.getvalue())
        print("(dry-run: целевой Doc не изменён)", file=sys.stderr)
        return 0

    try:
        if ns.all_students:
            pr, names = run_fill_all_students_docs(
                list_doc_id=list_id,
                output_summary_doc_id=out_sum,
                output_detail_doc_id=out_detail,
                creds=creds,
                bootstrap_templates=ns.bootstrap_templates,
            )
            if ns.only_student_index and out_detail:
                name = run_fill_one_student_detail_doc(
                    list_doc_id=list_id,
                    output_detail_doc_id=out_detail,
                    creds=creds,
                    student_index=ns.only_student_index,
                )
                print(f"Детальный Doc: заполнен только магистрант #{ns.only_student_index}: {name}")
        else:
            pr, name = run_test1_fill_summary_doc(
                list_doc_id=list_id,
                output_summary_doc_id=out_sum,
                creds=creds,
                data_row_index=ns.data_row,
            )
            names = [name] if name else []
    except Exception as e:  # noqa: BLE001
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    if not pr.rows:
        print("Нечего записывать (список пуст).", file=sys.stderr)
        return 1

    if ns.all_students:
        print(
            f"Сводная таблица: заполнено строк данных: {len(pr.rows)}. "
            f"Магистранты: {', '.join(n or '(без имени)' for n in names)}"
        )
        if out_detail:
            print("Детальная таблица: обновлены заголовки H1 и тела первых N секций.")
        elif ns.output_detail_doc is None:
            print(
                "Детальный Doc не указан (третий аргумент).",
                file=sys.stderr,
            )
    else:
        print(f"Заполнена строка {ns.data_row} сводного Doc для: {names[0] if names else '(без имени)'}")
        if ns.output_detail_doc:
            print(
                "Для заполнения детальной таблицы используйте --all-students и третий аргумент.",
                file=sys.stderr,
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="magister_checking",
        description="Проверка магистерских проектов (Google APIs).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="OAuth: сохранить token.json (браузер)")
    p_login.set_defaults(func=cmd_login)

    p_bot = sub.add_parser(
        "bot",
        help="Запустить Telegram-бота @magistrcheckbot (long polling). Конфиг — .env",
    )
    p_bot.set_defaults(func=cmd_bot)

    p_bcast = sub.add_parser(
        "broadcast",
        help="Рассылка текстового сообщения зарегистрированным пользователям бота "
        "(dry-run по умолчанию; --send + --i-know-this-is-irreversible для отправки)",
    )
    p_bcast.add_argument(
        "--message-file",
        type=Path,
        required=True,
        metavar="PATH",
        help="путь к UTF-8 текстовому файлу с сообщением (без шаблонизации)",
    )
    p_bcast.add_argument(
        "--source",
        choices=("registration", "persistence", "both"),
        default="both",
        help="источник адресатов: лист Регистрация / PicklePersistence / "
        "оба с дедупом (default)",
    )
    p_bcast.add_argument(
        "--send",
        action="store_true",
        help="реально отправлять (без флага — dry-run: только превью)",
    )
    p_bcast.add_argument(
        "--i-know-this-is-irreversible",
        dest="i_know_this_is_irreversible",
        action="store_true",
        help="обязательное второе подтверждение для --send",
    )
    p_bcast.add_argument(
        "--rate",
        type=float,
        default=25.0,
        metavar="MSGS_PER_SEC",
        help="темп отправки (default 25 msg/сек, лимит Telegram ~30/сек)",
    )
    p_bcast.set_defaults(func=cmd_broadcast)

    p_check = sub.add_parser(
        "check-row",
        help="Прогнать одну строку листа «Регистрация» через этапы 1-3 (справка в stdout)",
    )
    row_group = p_check.add_mutually_exclusive_group(required=True)
    row_group.add_argument(
        "--row",
        type=int,
        default=None,
        metavar="N",
        help="номер строки листа «Регистрация» (1 = заголовок, 2 = первый магистрант)",
    )
    row_group.add_argument(
        "--fio",
        default=None,
        metavar="ФИО",
        help="ФИО магистранта (должно однозначно совпадать с одной строкой)",
    )
    p_check.add_argument(
        "--skip-http",
        action="store_true",
        help="не делать сетевых проверок URL (быстрый сухой прогон без HTTP)",
    )
    p_check.add_argument(
        "--apply",
        action="store_true",
        help="записать результаты Stage 2/Stage 3 в лист (J/K/L/M/N/O + "
        "strikethrough для недоступных Stage 3 ссылок). По умолчанию dry-run.",
    )
    p_check.add_argument(
        "--only-if-changed",
        action="store_true",
        help="не запускать пайплайн, если входы (URL отчёта, modifiedTime, "
        "ссылки Stage 3) совпадают с последним прогоном из листа "
        "«История проверок» (handoff Stage 4 (c) — diff_detection).",
    )
    p_check.set_defaults(func=cmd_check_row)

    p_doc = sub.add_parser("doc-info", help="Прочитать метаданные Google Doc по ссылке или id")
    p_doc.add_argument("url_or_id", help="URL документа или его id")
    p_doc.set_defaults(func=cmd_doc_info)

    p_ex = sub.add_parser(
        "doc-extract",
        help="Текст документа (включая таблицы) и список внешних ссылок",
    )
    p_ex.add_argument("url_or_id", help="URL документа или его id")
    p_ex.add_argument(
        "--plain-max",
        type=int,
        default=4000,
        metavar="N",
        help="сколько символов текста показать (по умолчанию 4000)",
    )
    p_ex.add_argument(
        "--no-plain-preview",
        action="store_true",
        help="не печатать превью текста, только длину и ссылки",
    )
    p_ex.add_argument(
        "--links-only",
        action="store_true",
        help="только ссылки (путь, URL, якорный текст)",
    )
    p_ex.set_defaults(func=cmd_doc_extract)

    p_sum = sub.add_parser(
        "build-summary",
        help="Сводный Google Doc → отчёты → метрики; вывод TSV в stdout (без Sheets)",
    )
    p_sum.add_argument(
        "summary_doc",
        help="URL или id сводного документа (Doc с таблицей магистрантов)",
    )
    p_sum.add_argument(
        "output_sheet",
        nargs="?",
        default=None,
        help="(устарело) если указано без --dry-run — команда завершится с подсказкой использовать fill-docs-test1",
    )
    p_sum.add_argument(
        "--dry-run",
        action="store_true",
        help="то же, что без второго аргумента: только TSV в stdout",
    )
    p_sum.set_defaults(func=cmd_build_summary)

    p_fd = sub.add_parser(
        "fill-docs-test1",
        help="Тест 1 (ТЗ): список Doc → первый отчёт → строка сводного выходного Google Doc",
    )
    p_fd.add_argument(
        "list_doc",
        help="URL/id Doc со списком магистрантов и ссылками на отчёты",
    )
    p_fd.add_argument(
        "output_summary_doc",
        help="URL/id пустого сводного Google Doc (таблица в документе)",
    )
    p_fd.add_argument(
        "output_detail_doc",
        nargs="?",
        default=None,
        help="URL/id детального Doc (заполняется при --all-students: H1 + текст)",
    )
    p_fd.add_argument(
        "--data-row",
        type=int,
        default=1,
        metavar="N",
        help="индекс строки таблицы для заполнения (1 = первая строка под заголовком)",
    )
    p_fd.add_argument(
        "--all-students",
        action="store_true",
        help="все магистранты из списка: строки 1…N сводной таблицы и N секций H1 в детальном Doc",
    )
    p_fd.add_argument(
        "--bootstrap-templates",
        action="store_true",
        help="если в сводном Doc нет таблицы — вставить 7×(1+N); если в детальном мало H1 — добавить в конец",
    )
    p_fd.add_argument(
        "--only-student-index",
        type=int,
        default=0,
        metavar="N",
        help="только для детального Doc: заполнить одного магистранта (1-based индекс в списке). Работает с --all-students.",
    )
    p_fd.add_argument(
        "--dry-run",
        action="store_true",
        help="не писать в Doc; TSV (одна или все строки); с --all-students и третьим URL — превью детализации в stderr",
    )
    p_fd.set_defaults(func=cmd_fill_docs_test1)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
