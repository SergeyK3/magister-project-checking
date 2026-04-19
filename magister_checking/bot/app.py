"""Сборка Telegram Application и точки входа run/build."""

from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from magister_checking.bot.config import BotConfig
from magister_checking.bot.handlers import (
    ASK_CONFIRM,
    ASK_FIELD,
    CONFIG_BOT_DATA_KEY,
    ask_confirm,
    cancel,
    receive_field,
    skip_field,
    start,
)

logger = logging.getLogger("magistrcheckbot")


def configure_logging(level: int) -> None:
    """Настраивает корневое логирование, если оно ещё не настроено."""

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
    else:
        logging.getLogger().setLevel(level)


def build_application(config: BotConfig) -> Application:
    """Собирает Application с ConversationHandler регистрации.

    BotConfig прокидывается в ``application.bot_data`` — хендлеры читают его
    через ``CONFIG_BOT_DATA_KEY``.
    """

    application = Application.builder().token(config.telegram_bot_token).build()
    application.bot_data[CONFIG_BOT_DATA_KEY] = config

    field_message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, receive_field)
    confirm_message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, ask_confirm)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_FIELD: [
                CommandHandler("skip", skip_field),
                field_message_handler,
            ],
            ASK_CONFIRM: [confirm_message_handler],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)
    return application


def run(config: BotConfig) -> None:
    """Запускает long-polling Telegram-бота. Блокирующий вызов."""

    configure_logging(config.log_level)
    application = build_application(config)
    logger.info(
        "magistrcheckbot started: spreadsheet=%s worksheet=%s",
        config.spreadsheet_id,
        config.worksheet_name,
    )
    application.run_polling()
