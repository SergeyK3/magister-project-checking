"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from googleapiclient.discovery import build

from magister_checking import __version__
from magister_checking.auth import get_credentials
from magister_checking.docs_extract import extract_plain_text, iter_hyperlinks
from magister_checking.drive_urls import extract_google_file_id


def cmd_login(_: argparse.Namespace) -> int:
    get_credentials(interactive=True)
    print("Учётные данные сохранены в credentials/token.json")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="magister_checking",
        description="Проверка магистерских проектов (Google APIs).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="OAuth: сохранить token.json (браузер)")
    p_login.set_defaults(func=cmd_login)

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

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
