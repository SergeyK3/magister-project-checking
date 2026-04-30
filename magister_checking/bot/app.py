"""Сборка Telegram Application и точки входа run/build."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
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
    ADMIN_STUDENT_MESSAGE_BUTTON,
    ADMIN_STUDENT_MESSAGE_BULK_BUTTON,
    ADMSTUB_CALLBACK_CONFIRM_PATTERN,
    ADMSTU_CALLBACK_CONFIRM_PATTERN,
    ADMSTU_CALLBACK_TEMPLATE_PATTERN,
    ASK_CONFIRM,
    ASK_FIELD,
    BIND_ASK_FIO,
    BIND_CONFIRM,
    CLAIM_ASK_FIO,
    CLAIM_CONFIRM,
    CONFIG_BOT_DATA_KEY,
    PIN_VERIFY_INPUT,
    PROJECT_CARD_ASK_TARGET,
    RECHECK_CALLBACK_PATTERN,
    ROLE_PICK,
    SPRAVKA_ASK_TARGET,
    SPRAVKA_MENU,
    STUDENT_MSG_ASK_CUSTOM,
    STUDENT_MSG_ASK_EXTRA,
    STUDENT_MSG_ASK_TARGET,
    STUDENT_MSG_BULK_ASK_ROWS,
    STUDENT_MSG_BULK_CONFIRM,
    STUDENT_MSG_CONFIRM,
    STUDENT_MSG_PICK_KIND,
    admin_menu,
    admin_recheck_pending_receive,
    admin_stats,
    admin_sync_dashboard,
    admin_sync_magistrants,
    ask_confirm,
    cancel,
    confirm_bind,
    confirm_claim,
    default_bot_commands,
    group_start_use_private_chat,
    help_command,
    project_card_receive_target,
    project_card_start,
    receive_bind_fio,
    receive_claim_fio,
    receive_field,
    receive_pin_input,
    recheck,
    recheck_button,
    register_command,
    skip_bind,
    skip_field,
    spravka_choose,
    on_project_snapshot_json_file,
    spravka_receive_target,
    spravka_start,
    start,
    start_role_callback,
    status_command,
    student_message_bulk_start,
    student_reminder_bulk_confirm_callback,
    student_reminder_bulk_receive_rows,
    student_reminder_confirm_callback,
    student_reminder_receive_custom,
    student_reminder_receive_extra,
    student_reminder_receive_target,
    student_reminder_pick_template,
    student_reminder_start,
    supervisor_registered_list_command,
    supervisor_unregistered_list_command,
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

    # Каждый 403 к Google пишет WARNING в консоль — при частых вызовах «рвёт»
    # строки в PowerShell из‑за нескольких процессов/потоков. Оставляем только
    # ERROR+ здесь; успехи/коды ответа по-прежнему видны в вашем коде (magistrcheckbot,
    # report_enrichment) и в файле JSON при LOG_FILE.
    logging.getLogger("googleapiclient.http").setLevel(logging.ERROR)


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

    private = filters.ChatType.PRIVATE

    application.add_handler(CommandHandler("help", help_command, filters=private), group=-1)

    application.add_handler(
        CommandHandler(
            "start",
            group_start_use_private_chat,
            filters=filters.ChatType.GROUPS,
        ),
    )

    field_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, receive_field
    )
    confirm_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, ask_confirm
    )
    bind_fio_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, receive_bind_fio
    )
    bind_confirm_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, confirm_bind
    )
    project_card_target_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        project_card_receive_target,
    )
    student_reminder_target_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        student_reminder_receive_target,
    )
    student_reminder_template_cb_handler = CallbackQueryHandler(
        student_reminder_pick_template,
        pattern=ADMSTU_CALLBACK_TEMPLATE_PATTERN,
    )
    student_reminder_extra_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        student_reminder_receive_extra,
    )
    student_reminder_custom_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        student_reminder_receive_custom,
    )
    student_reminder_confirm_cb_handler = CallbackQueryHandler(
        student_reminder_confirm_callback,
        pattern=ADMSTU_CALLBACK_CONFIRM_PATTERN,
    )
    student_bulk_rows_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        student_reminder_bulk_receive_rows,
    )
    student_bulk_confirm_cb_handler = CallbackQueryHandler(
        student_reminder_bulk_confirm_callback,
        pattern=ADMSTUB_CALLBACK_CONFIRM_PATTERN,
    )

    spravka_callback = CallbackQueryHandler(
        spravka_choose,
        pattern=r"^spravka:(telegram|pdf|commission)$",
    )
    start_role_callback_handler = CallbackQueryHandler(
        start_role_callback, pattern=r"^start:"
    )
    claim_fio_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, receive_claim_fio
    )
    claim_confirm_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, confirm_claim
    )
    pin_input_message_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private, receive_pin_input
    )
    spravka_target_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & private,
        spravka_receive_target,
    )
    conv_handler = ConversationHandler(
        name="registration",
        persistent=True,
        entry_points=[
            CommandHandler("start", start, filters=private),
            CommandHandler("register", register_command, filters=private),
            CommandHandler("project_card", project_card_start, filters=private),
            CommandHandler("student_message", student_reminder_start, filters=private),
            CommandHandler("student_message_bulk", student_message_bulk_start, filters=private),
            CommandHandler("spravka", spravka_start, filters=private),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$") & private,
                project_card_start,
            ),
            MessageHandler(
                filters.Regex(f"^{ADMIN_STUDENT_MESSAGE_BUTTON}$") & private,
                student_reminder_start,
            ),
            MessageHandler(
                filters.Regex("^" + re.escape(ADMIN_STUDENT_MESSAGE_BULK_BUTTON) + "$")
                & private,
                student_message_bulk_start,
            ),
        ],
        states={
            ROLE_PICK: [start_role_callback_handler],
            BIND_ASK_FIO: [
                CommandHandler("skip", skip_bind, filters=private),
                bind_fio_message_handler,
            ],
            BIND_CONFIRM: [bind_confirm_message_handler],
            CLAIM_ASK_FIO: [claim_fio_message_handler],
            CLAIM_CONFIRM: [claim_confirm_message_handler],
            ASK_FIELD: [
                CommandHandler("skip", skip_field, filters=private),
                field_message_handler,
            ],
            ASK_CONFIRM: [confirm_message_handler],
            PIN_VERIFY_INPUT: [pin_input_message_handler],
            PROJECT_CARD_ASK_TARGET: [project_card_target_handler],
            STUDENT_MSG_ASK_TARGET: [student_reminder_target_handler],
            STUDENT_MSG_PICK_KIND: [student_reminder_template_cb_handler],
            STUDENT_MSG_ASK_EXTRA: [student_reminder_extra_handler],
            STUDENT_MSG_ASK_CUSTOM: [student_reminder_custom_handler],
            STUDENT_MSG_CONFIRM: [student_reminder_confirm_cb_handler],
            STUDENT_MSG_BULK_ASK_ROWS: [student_bulk_rows_handler],
            STUDENT_MSG_BULK_CONFIRM: [student_bulk_confirm_cb_handler],
            SPRAVKA_MENU: [spravka_callback],
            SPRAVKA_ASK_TARGET: [spravka_target_handler],
        },
        fallbacks=[
            CommandHandler("cancel", cancel, filters=private),
            CommandHandler("start", start, filters=private),
            CommandHandler("register", register_command, filters=private),
            CommandHandler("project_card", project_card_start, filters=private),
            CommandHandler("student_message", student_reminder_start, filters=private),
            CommandHandler("student_message_bulk", student_message_bulk_start, filters=private),
            CommandHandler("spravka", spravka_start, filters=private),
            MessageHandler(
                filters.Regex(f"^{ADMIN_PROJECT_CARD_BUTTON}$") & private,
                project_card_start,
            ),
            MessageHandler(
                filters.Regex(f"^{ADMIN_STUDENT_MESSAGE_BUTTON}$") & private,
                student_reminder_start,
            ),
            MessageHandler(
                filters.Regex("^" + re.escape(ADMIN_STUDENT_MESSAGE_BULK_BUTTON) + "$")
                & private,
                student_message_bulk_start,
            ),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    application.add_handler(
        MessageHandler(
            filters.Document.FileExtension("json") & private,
            on_project_snapshot_json_file,
        ),
        group=1,
    )

    application.add_handler(CommandHandler("admin", admin_menu, filters=private))
    application.add_handler(CommandHandler("stats", admin_stats, filters=private))
    application.add_handler(
        CommandHandler("sync_dashboard", admin_sync_dashboard, filters=private)
    )
    application.add_handler(
        CommandHandler("sync_magistrants", admin_sync_magistrants, filters=private)
    )
    application.add_handler(CommandHandler("status", status_command, filters=private))
    application.add_handler(
        CommandHandler("unreg", supervisor_unregistered_list_command, filters=private)
    )
    application.add_handler(
        CommandHandler("reg_list", supervisor_registered_list_command, filters=private)
    )
    application.add_handler(CommandHandler("recheck", recheck, filters=private))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            admin_recheck_pending_receive,
        )
    )
    application.add_handler(
        CallbackQueryHandler(recheck_button, pattern=RECHECK_CALLBACK_PATTERN)
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
