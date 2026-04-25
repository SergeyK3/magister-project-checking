"""Тесты конфигурации логирования бота (защита от утечки TELEGRAM_BOT_TOKEN
и поведение опционального FileHandler для headless-запуска)."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from magister_checking.bot.app import configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        # configure_logging мутирует root logger глобально; сохраняем и
        # восстанавливаем состояние, чтобы тесты не влияли друг на друга
        # и на остальной test suite.
        root = logging.getLogger()
        self._saved_handlers = root.handlers[:]
        self._saved_level = root.level

    def tearDown(self) -> None:
        root = logging.getLogger()
        for h in root.handlers[:]:
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        for h in self._saved_handlers:
            root.addHandler(h)
        root.setLevel(self._saved_level)

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

    def test_configure_logging_without_file_does_not_add_file_handler(self) -> None:
        """Без log_file FileHandler не появляется (поведение foreground-запуска)."""

        configure_logging(logging.INFO)

        file_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
        ]
        self.assertEqual(file_handlers, [], msg="Не должно быть FileHandler без BOT_LOG_FILE")

    def test_configure_logging_with_file_writes_records_to_file(self) -> None:
        """С log_file записи Python logging реально доходят до файла.

        Воспроизводит сценарий headless-запуска: FileHandler заменяет
        хрупкий PowerShell-пайп ``2>&1 | Out-File`` (см. BotConfig.log_file)."""

        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "nested" / "bot.log"
            try:
                configure_logging(logging.INFO, log_file=log_path)

                self.assertTrue(
                    log_path.parent.is_dir(),
                    msg="configure_logging должен создавать каталог под log_file",
                )

                file_handlers = [
                    h
                    for h in logging.getLogger().handlers
                    if isinstance(h, logging.FileHandler)
                ]
                self.assertEqual(
                    len(file_handlers), 1, msg="Ожидаем ровно один FileHandler"
                )

                logger = logging.getLogger("magistrcheckbot.test")
                test_message = "headless-launcher sanity-check record"
                logger.info(test_message)
                for h in file_handlers:
                    h.flush()

                content = log_path.read_text(encoding="utf-8")
                self.assertIn(test_message, content)
                self.assertIn("INFO", content)
            finally:
                # На Windows TemporaryDirectory не сможет удалить файл, пока
                # FileHandler держит его открытым (WinError 32). Закрываем
                # обработчик до выхода из контекстного менеджера.
                root = logging.getLogger()
                for h in [
                    h for h in root.handlers if isinstance(h, logging.FileHandler)
                ]:
                    root.removeHandler(h)
                    h.close()


if __name__ == "__main__":
    unittest.main()
