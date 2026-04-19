"""Обратная совместимость: старый монолит ``bot_primary``.

Логика переехала в пакет :mod:`magister_checking.bot`. Этот модуль остался
тонкой обёрткой только для обратной совместимости и будет удалён в следующей
итерации. Используйте ``python -m magister_checking bot``.
"""

from __future__ import annotations

import warnings

from magister_checking.bot.app import build_application, configure_logging, run
from magister_checking.bot.config import BotConfig, ConfigError, load_config
from magister_checking.bot.handlers import (
    ASK_CONFIRM,
    ASK_FIELD,
    ask_confirm,
    cancel,
    receive_field,
    skip_field,
    start,
)
from magister_checking.bot.models import (
    FIELD_LABELS,
    FIELD_PROMPTS,
    REQUIRED_FIELDS,
    SHEET_HEADER,
    FillStatus,
    UserForm,
    compute_fill_status,
    get_missing_field_keys,
    get_missing_fields,
)
from magister_checking.bot.sheets_repo import (
    ensure_header,
    find_row_by_telegram_id,
    get_gspread_client,
    get_worksheet,
    load_user,
    upsert_user,
)
from magister_checking.bot.validation import (
    SKIP_TOKEN,
    check_report_url,
    is_probably_public_google_doc_response,
    is_valid_url,
    normalize_text,
)

warnings.warn(
    "magister_checking.bot_primary устарел; используйте magister_checking.bot "
    "и команду `python -m magister_checking bot`.",
    DeprecationWarning,
    stacklevel=2,
)


def main() -> None:
    """Точка входа для обратной совместимости — запускает бота через .env."""

    run(load_config())


__all__ = [
    "ASK_CONFIRM",
    "ASK_FIELD",
    "BotConfig",
    "ConfigError",
    "FIELD_LABELS",
    "FIELD_PROMPTS",
    "FillStatus",
    "REQUIRED_FIELDS",
    "SHEET_HEADER",
    "SKIP_TOKEN",
    "UserForm",
    "ask_confirm",
    "build_application",
    "cancel",
    "check_report_url",
    "compute_fill_status",
    "configure_logging",
    "ensure_header",
    "find_row_by_telegram_id",
    "get_gspread_client",
    "get_missing_field_keys",
    "get_missing_fields",
    "get_worksheet",
    "is_probably_public_google_doc_response",
    "is_valid_url",
    "load_config",
    "load_user",
    "main",
    "normalize_text",
    "receive_field",
    "run",
    "skip_field",
    "start",
    "upsert_user",
]


if __name__ == "__main__":
    main()
