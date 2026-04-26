"""Сборка Telegram Application и точки входа run/build."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)
from telegram.request import HTTPXRequest

from magister_checking.bot.config import BotConfig
from magister_checking.bot.error_alerts import on_handler_error
from magister_checking.bot.handlers import (
    ADMIN_PROJECT_CARD_BUTTON,
    ASK_CONFIRM,
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    CLAIM_ASK_FIO,
    CLAIM_CONFIRM,
    CONFIG_BOT_DATA_KEY,
    PROJECT_CARD_ASK_TARGET,
    RECHECK_CALLBACK_DATA,
    SPRAVKA_ASK_TARGET,
    SPRAVKA_MENU,
    ROLE_PICK,
    admin_menu,
    admin_stats,
    admin_sync_dashboard,
    ask_confirm,
    cancel,
    confirm_bind,
    confirm_claim,
    default_bot_commands,
    help_command,
    project_card_receive_target,
    project_card_start,
    receive_bind_fio,
    receive_claim_fio,
    receive_field,
    recheck,
    recheck_button,
    skip_bind,
    skip_field,
    spravka_choose,
    on_project_snapshot_json_file,
    spravka_receive_target,
    spravka_start,
    start,
    start_role_callback,
)

logger = logging.getLogger("magistrcheckbot")


_NOISY_LOGGERS_WITH_TOKEN = ("httpx", "httpcore", "telegram.ext.Updater", "telegram.bot")
"""Логгеры, которые на INFO печатают URL с секретным TELEGRAM_BOT_TOKEN.

Чтобы исключить утечку токена в stdout/файлы логов, их уровень поднимается
минимум до WARNING — независимо от выбранного LOG_LEVEL бота.
"""


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

LOG_FILE_BACKUP_COUNT = 30
"""Сколько ротированных дневных файлов лога хранить.

При ``when='midnight'`` ``TimedRotatingFileHandler`` оставляет последние
N архивов (``bot.log.YYYY-MM-DD``) и удаляет более старые. Месяц истории
выбран как разумный баланс «достаточно для post-mortem за прошлый
инцидент» vs «не пухнет на диске». Поменять — править здесь и в тесте."""


class _JsonLogFormatter(logging.Formatter):
    """JSON-форматтер для файла: одна строка = один JSON-объект.

    Поля: ``ts`` (ISO-8601, UTC), ``level``, ``logger``, ``module``,
    ``func``, ``lineno``, ``message``. При ``logger.exception(...)`` или
    ``exc_info=True`` добавляется поле ``exc_info`` с отформатированным
    traceback. Не использует ``logging.Formatter.format`` для ``message``,
    чтобы не дублировать level/name в строку (структурное поле уже их
    несёт). ``ensure_ascii=False`` — кириллица сохраняется как есть."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "func": record.funcName,
            "lineno": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int, log_file: Optional[Path] = None) -> None:
    """Настраивает корневое логирование, если оно ещё не настроено.

    StreamHandler остаётся текстовым (``_LOG_FORMAT``) — удобно читать в
    консоли при foreground-запуске. Если задан ``log_file`` — добавляется
    ``TimedRotatingFileHandler`` (``when='midnight'``,
    ``backupCount=LOG_FILE_BACKUP_COUNT``) с JSON-форматтером, чтобы
    headless-запуск через Task Scheduler писал структурированный лог в
    файл; ротация по локальной полночи, истории — месяц
    (``bot.log.YYYY-MM-DD``). Без ``log_file`` файл не пишется (см.
    комментарий к ``BotConfig.log_file``)."""

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=_LOG_FORMAT)
    else:
        root.setLevel(level)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        target = str(log_path.resolve())
        already_attached = any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", None) == target
            for h in root.handlers
        )
        if not already_attached:
            # utc=False: ротация по локальной полуночи, suffix
            # YYYY-MM-DD совпадает с местным календарным днём.
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_path,
                when="midnight",
                interval=1,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
                utc=False,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(_JsonLogFormatter())
            root.addHandler(file_handler)

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


async def _post_init(application: Application) -> None:
    """Меню команд в клиенте Telegram (C1 polish)."""

    await application.bot.set_my_commands(default_bot_commands())


def build_application(config: BotConfig) -> Application:
    """Собирает Application с ConversationHandler регистрации.

    BotConfig прокидывается в ``application.bot_data`` — хендлеры читают его
    через ``CONFIG_BOT_DATA_KEY``.
    """

    persistence = _build_persistence(config)
    builder = (
        Application.builder()
        .token(config.telegram_bot_token)
        .persistence(persistence)
        .post_init(_post_init)
    )
    if config.telegram_force_ipv4:
        # Отдельные транспорты: независимые пулы для обычных запросов и long polling.
        builder = (
            builder.request(
                HTTPXRequest(
                    httpx_kwargs={
                        "transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")
                    }
                )
            ).get_updates_request(
                HTTPXRequest(
                    httpx_kwargs={
                        "transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")
                    }
                )
            )
        )
    application = builder.build()
    application.bot_data[CONFIG_BOT_DATA_KEY] = config

    application.add_handler(CommandHandler("help", help_command), group=-1)

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

    spravka_callback = CallbackQueryHandler(
        spravka_choose, pattern=r"^spravka:(telegram|pdf|commission)$"
    )
    start_role_callback_handler = CallbackQueryHandler(
        start_role_callback, pattern=r"^start:"
    )
    claim_fio_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_claim_fio
    )
    claim_confirm_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND, confirm_claim
    )
    spravka_target_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        spravka_receive_target,
    )
    conv_handler = ConversationHandler(
        name="registration",
        persistent=True,
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("project_card", project_card_start),
            CommandHandler("spravka", spravka_start),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$"),
                project_card_start,
            ),
        ],
        states={
            ROLE_PICK: [start_role_callback_handler],
            BIND_ASK_FIO: [
                CommandHandler("skip", skip_bind),
                bind_fio_message_handler,
            ],
            BIND_CONFIRM: [bind_confirm_message_handler],
            CLAIM_ASK_FIO: [claim_fio_message_handler],
            CLAIM_CONFIRM: [claim_confirm_message_handler],
            ASK_FIELD: [
                CommandHandler("skip", skip_field),
                field_message_handler,
            ],
            ASK_CONFIRM: [confirm_message_handler],
            PROJECT_CARD_ASK_TARGET: [project_card_target_handler],
            SPRAVKA_MENU: [spravka_callback],
            SPRAVKA_ASK_TARGET: [spravka_target_handler],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("project_card", project_card_start),
            CommandHandler("spravka", spravka_start),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$"),
                project_card_start,
            ),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    application.add_handler(
        MessageHandler(
            filters.Document.FileExtension("json"),
            on_project_snapshot_json_file,
        ),
        group=1,
    )

    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("sync_dashboard", admin_sync_dashboard))
    application.add_handler(CommandHandler("recheck", recheck))
    application.add_handler(
        CallbackQueryHandler(recheck_button, pattern=f"^{RECHECK_CALLBACK_DATA}$")
    )
    application.add_error_handler(on_handler_error)
    return application


def run(config: BotConfig) -> None:
    """Запускает long-polling Telegram-бота. Блокирующий вызов."""

    configure_logging(config.log_level, log_file=config.log_file)
    application = build_application(config)
    logger.info(
        "magistrcheckbot started: spreadsheet=%s worksheet=%s",
        config.spreadsheet_id,
        config.worksheet_name,
    )
    application.run_polling()
