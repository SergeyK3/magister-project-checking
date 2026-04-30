"""Алерты в Telegram при необработанных исключениях в хендлерах (roadmap B3)."""

from __future__ import annotations

import html
import logging
import time
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

_MAG_ADMIN_ALERT_COOLDOWN_SEC = 60.0
"""Не чаще одного алерта в Telegram‑чаты админам на одного пользователя за это время."""

_last_mag_alert_unix: dict[int, float] = {}
"""Время успешной последней отправки алерта по ``user_id`` (wall clock)."""


def reset_mag_alert_cooldown_for_tests() -> None:
    """Только для юнит‑тестов: очистка глобального состояния."""

    _last_mag_alert_unix.clear()


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

    uid_maybe = getattr(getattr(update, "effective_user", None), "id", None)
    uid_int = uid_maybe if isinstance(uid_maybe, int) else None

    now = time.time()
    if uid_int is not None:
        last_alert = _last_mag_alert_unix.get(uid_int)
        if (
            last_alert is not None
            and (now - last_alert) < _MAG_ADMIN_ALERT_COOLDOWN_SEC
        ):
            logger.warning(
                "Исключение при обработке update (тот же user_id=%s за ≤%ss): "
                "алерт админам повторно не отправляем. %s: %s",
                uid_int,
                int(_MAG_ADMIN_ALERT_COOLDOWN_SEC),
                type(err).__name__,
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
        if uid_int is not None:
            _last_mag_alert_unix[uid_int] = time.time()
    except Exception:
        logger.exception("Failed to send Telegram alert for handler error")
