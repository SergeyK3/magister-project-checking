"""Алерты в Telegram при необработанных исключениях в хендлерах (roadmap B3)."""

from __future__ import annotations

import asyncio
import html
import logging
import time
import traceback
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from magister_checking.bot.google_api_errors import (
    GOOGLE_SHEETS_RATE_LIMIT_REGISTRATION_RETRY,
    is_google_sheets_rate_limit,
)
from magister_checking.bot.handlers import CONFIG_BOT_DATA_KEY
from magister_checking.bot.admin_message_helpers import (
    ADMIN_PROJECT_CARD_BUTTON,
    ADMIN_STATS_BUTTON,
    ADMIN_STUDENT_MESSAGE_BULK_BUTTON,
    ADMIN_STUDENT_MESSAGE_BUTTON,
    ADMIN_SUPERVISOR_MESSAGE_BUTTON,
)
from magister_checking.bot.sheets_repo import is_admin_telegram_id

if TYPE_CHECKING:
    from magister_checking.bot.config import BotConfig

logger = logging.getLogger("magistrcheckbot")

_TELEGRAM_MAX_MESSAGE_LEN = 4096
_ALERT_HEAD = "magistrcheckbot: ошибка в обработчике"

_MAG_ADMIN_ALERT_COOLDOWN_SEC = 60.0
"""Не чаще одного алерта в Telegram‑чаты админам на одного пользователя за это время."""

_last_mag_alert_unix: dict[int, float] = {}
"""Время успешной последней отправки алерта по ``user_id`` (wall clock)."""

_ADMIN_RATE_LIMIT_RETRY_DELAY_SECONDS = 60.0
_ADMIN_RATE_LIMIT_RETRIED_KEYS: set[tuple[int | None, int | None, int | None, str]] = set()
"""Update/message keys already scheduled once after a Google Sheets 429."""

_ADMIN_RETRY_COMMANDS = {
    "/admin",
    "/stats",
    "/ops_row",
    "/sync_dashboard",
    "/sync_magistrants",
    "/project_card",
    "/student_message",
    "/student_message_bulk",
    "/supervisor_message",
}
_ADMIN_RETRY_BUTTONS = {
    ADMIN_PROJECT_CARD_BUTTON,
    ADMIN_STATS_BUTTON,
    ADMIN_STUDENT_MESSAGE_BUTTON,
    ADMIN_STUDENT_MESSAGE_BULK_BUTTON,
    ADMIN_SUPERVISOR_MESSAGE_BUTTON,
}


def reset_mag_alert_cooldown_for_tests() -> None:
    """Только для юнит‑тестов: очистка глобального состояния."""

    _last_mag_alert_unix.clear()
    _ADMIN_RATE_LIMIT_RETRIED_KEYS.clear()


def _clip(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 24] + "\n…(обрезано)"


def _update_retry_key(
    update: Update,
) -> tuple[int | None, int | None, int | None, str]:
    user_id = update.effective_user.id if update.effective_user is not None else None
    msg = update.effective_message
    message_id = msg.message_id if msg is not None else None
    text = str(getattr(msg, "text", "") or "")
    return update.update_id, user_id, message_id, text


def _looks_like_admin_retry_input(update: Update) -> bool:
    msg = update.effective_message
    text = str(getattr(msg, "text", "") or "").strip()
    if not text:
        return False
    if text in _ADMIN_RETRY_BUTTONS:
        return True
    if not text.startswith("/"):
        return False
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    return command in _ADMIN_RETRY_COMMANDS


def _is_retryable_admin_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or chat.type != ChatType.PRIVATE or user is None:
        return False
    cfg: BotConfig | None = context.application.bot_data.get(CONFIG_BOT_DATA_KEY)
    if cfg is None:
        return False
    try:
        return is_admin_telegram_id(cfg, str(user.id))
    except Exception as exc:  # noqa: BLE001
        if is_google_sheets_rate_limit(exc) and _looks_like_admin_retry_input(update):
            logger.warning(
                "Не удалось проверить админство из-за 429; планируем повтор админской команды user_id=%s",
                user.id,
            )
            return True
        logger.warning("Не удалось проверить админство для авто-повтора: %s", exc)
        return False


async def _retry_update_after_google_sheets_limit(update: Update, application) -> None:
    await asyncio.sleep(_ADMIN_RATE_LIMIT_RETRY_DELAY_SECONDS)
    try:
        logger.info(
            "Повторяем админскую команду после Google Sheets 429: update_id=%s user_id=%s",
            update.update_id,
            getattr(getattr(update, "effective_user", None), "id", None),
        )
        await application.process_update(update)
    except Exception:
        logger.exception("Авто-повтор админской команды после Google Sheets 429 завершился ошибкой")


async def _maybe_schedule_admin_rate_limit_retry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not _is_retryable_admin_update(update, context):
        return False
    key = _update_retry_key(update)
    if key in _ADMIN_RATE_LIMIT_RETRIED_KEYS:
        return False
    _ADMIN_RATE_LIMIT_RETRIED_KEYS.add(key)
    context.application.create_task(
        _retry_update_after_google_sheets_limit(update, context.application),
        update=update,
    )
    return True


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
        if isinstance(update, Update):
            chat = update.effective_chat
            msg = update.effective_message
            if chat is not None and chat.type == ChatType.PRIVATE:
                note = GOOGLE_SHEETS_RATE_LIMIT_REGISTRATION_RETRY
                scheduled_retry = await _maybe_schedule_admin_rate_limit_retry(
                    update, context
                )
                if scheduled_retry:
                    note += (
                        "\n\nДля администратора: бот автоматически повторит последнюю "
                        "команду через 60 секунд."
                    )
                try:
                    if msg is not None:
                        await msg.reply_text(note)
                    else:
                        await context.bot.send_message(chat_id=chat.id, text=note)
                except Exception:
                    logger.exception(
                        "Не удалось отправить пользователю подсказку про лимит Google Sheets (429)"
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
