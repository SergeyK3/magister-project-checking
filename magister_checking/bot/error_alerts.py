"""Алерты в Telegram при необработанных исключениях в хендлерах (roadmap B3)."""

from __future__ import annotations

import html
import logging
import traceback
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from magister_checking.bot.google_api_errors import is_google_sheets_rate_limit
from magister_checking.bot.handlers import CONFIG_BOT_DATA_KEY

if TYPE_CHECKING:
    from magister_checking.bot.config import BotConfig

logger = logging.getLogger("magistrcheckbot")

_TELEGRAM_MAX_MESSAGE_LEN = 4096
_ALERT_HEAD = "magistrcheckbot: ошибка в обработчике"


def _clip(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 24] + "\n…(обрезано)"


def format_handler_error_html(update: object, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Текст для send_message(..., parse_mode=HTML): краткий контекст + traceback."""

    err = context.error
    lines: list[str] = [_ALERT_HEAD]

    if isinstance(update, Update):
        if update.effective_user:
            lines.append(f"user_id={update.effective_user.id}")
        if update.effective_chat:
            lines.append(f"chat_id={update.effective_chat.id}")

    lines.append("")
    if err is None:
        lines.append("(context.error is None)")
        tb = ""
    else:
        lines.append(f"{type(err).__name__}: {err}")
        tb = "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        )

    lines.append("")
    lines.append(tb)
    plain = "\n".join(lines)
    plain = _clip(plain, _TELEGRAM_MAX_MESSAGE_LEN - 32)
    return f"<pre>{html.escape(plain)}</pre>"


async def on_handler_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует исключение и рассылает его в ``BotConfig.alert_chat_ids`` (если заданы)."""

    err = context.error
    if err is None:
        return

    # run_polling передаёт сюда ошибки long polling с update=None (см. PTB Application.run_polling).
    # Для них не пишем ERROR «while handling an update» и не шлём алерты — polling сам повторяет.
    if update is None and isinstance(err, (NetworkError, TimedOut)):
        logger.warning(
            "Сбой сети при опросе Telegram (%s: %s). Повтор запроса выполнит библиотека.",
            type(err).__name__,
            err,
        )
        return

    if is_google_sheets_rate_limit(err):
        logger.warning(
            "Временный лимит Google Sheets/API (429); алерт админам не отправляем: %s",
            err,
        )
        return

    logger.error("Exception while handling an update", exc_info=err)

    cfg: BotConfig | None = context.application.bot_data.get(CONFIG_BOT_DATA_KEY)
    if cfg is None or not cfg.alert_chat_ids:
        return

    text = format_handler_error_html(update, context)
    try:
        for chat_id in cfg.alert_chat_ids:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        logger.exception("Failed to send Telegram alert for handler error")
