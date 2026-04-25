"""Сборка Telegram Application и точки входа run/build."""

from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)

from magister_checking.bot.config import BotConfig
from magister_checking.bot.handlers import (
    ADMIN_PROJECT_CARD_BUTTON,
    ASK_CONFIRM,
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    CONFIG_BOT_DATA_KEY,
    PROJECT_CARD_ASK_TARGET,
    admin_menu,
    ask_confirm,
    cancel,
    confirm_bind,
    project_card_receive_target,
    project_card_start,
    receive_bind_fio,
    receive_field,
    recheck,
    skip_bind,
    skip_field,
    start,
)

logger = logging.getLogger("magistrcheckbot")


_NOISY_LOGGERS_WITH_TOKEN = ("httpx", "httpcore", "telegram.ext.Updater", "telegram.bot")
"""Логгеры, которые на INFO печатают URL с секретным TELEGRAM_BOT_TOKEN.

Чтобы исключить утечку токена в stdout/файлы логов, их уровень поднимается
минимум до WARNING — независимо от выбранного LOG_LEVEL бота.
"""


def configure_logging(level: int) -> None:
    """Настраивает корневое логирование, если оно ещё не настроено."""

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
    else:
        logging.getLogger().setLevel(level)

    noisy_level = max(level, logging.WARNING)
    for name in _NOISY_LOGGERS_WITH_TOKEN:
        logging.getLogger(name).setLevel(noisy_level)


def _build_persistence(config: BotConfig) -> PicklePersistence:
    """Готовит PicklePersistence: создаёт каталог и возвращает объект.

    Сохраняем только ``user_data``/``chat_data``/``conversations`` — этого
    достаточно, чтобы после перезапуска бот помнил, на каком шаге регистрации
    каждый магистрант. ``bot_data`` не персистим, чтобы не тащить между
    запусками устаревший ``BotConfig`` (мы всегда перезаписываем его в
    ``build_application``).
    """

    persistence_path = config.persistence_file
    persistence_path.parent.mkdir(parents=True, exist_ok=True)
    return PicklePersistence(
        filepath=persistence_path,
        store_data=PersistenceInput(
            bot_data=False, chat_data=True, user_data=True, callback_data=False
        ),
    )


def build_application(config: BotConfig) -> Application:
    """Собирает Application с ConversationHandler регистрации.

    BotConfig прокидывается в ``application.bot_data`` — хендлеры читают его
    через ``CONFIG_BOT_DATA_KEY``.
    """

    persistence = _build_persistence(config)
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .persistence(persistence)
        .build()
    )
    application.bot_data[CONFIG_BOT_DATA_KEY] = config

    field_message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, receive_field)
    confirm_message_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, ask_confirm)
    bind_fio_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_bind_fio
    )
    bind_confirm_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND, confirm_bind
    )
    project_card_target_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        project_card_receive_target,
    )

    conv_handler = ConversationHandler(
        name="registration",
        persistent=True,
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("project_card", project_card_start),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$"),
                project_card_start,
            ),
        ],
        states={
            BIND_ASK_FIO: [
                CommandHandler("skip", skip_bind),
                bind_fio_message_handler,
            ],
            BIND_CONFIRM: [bind_confirm_message_handler],
            ASK_FIELD: [
                CommandHandler("skip", skip_field),
                field_message_handler,
            ],
            ASK_CONFIRM: [confirm_message_handler],
            PROJECT_CARD_ASK_TARGET: [project_card_target_handler],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("project_card", project_card_start),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$"),
                project_card_start,
            ),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("recheck", recheck))
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
