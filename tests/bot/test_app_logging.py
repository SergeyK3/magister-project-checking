"""Тесты конфигурации логирования бота (защита от утечки TELEGRAM_BOT_TOKEN)."""

from __future__ import annotations

import logging
import unittest

from magister_checking.bot.app import configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def test_httpx_logger_does_not_emit_info_with_token(self) -> None:
        configure_logging(logging.DEBUG)

        for name in ("httpx", "httpcore", "telegram.ext.Updater", "telegram.bot"):
            logger = logging.getLogger(name)
            self.assertGreaterEqual(
                logger.getEffectiveLevel(),
                logging.WARNING,
                msg=(
                    f"Logger {name!r} печатает INFO-сообщения с URL Telegram API "
                    "и может засветить токен в логах"
                ),
            )


if __name__ == "__main__":
    unittest.main()
