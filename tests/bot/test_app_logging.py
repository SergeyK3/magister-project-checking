"""Тесты конфигурации логирования бота (защита от утечки TELEGRAM_BOT_TOKEN
и поведение опционального FileHandler для headless-запуска)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from magister_checking.bot.app import (
    LOG_FILE_BACKUP_COUNT,
    _JsonLogFormatter,
    configure_logging,
)


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
        хрупкий PowerShell-пайп ``2>&1 | Out-File`` (см. BotConfig.log_file).
        Запись в файле — JSON-объект (см. ``_JsonLogFormatter``)."""

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

                lines = [
                    line
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertGreaterEqual(len(lines), 1)
                payload = json.loads(lines[-1])
                self.assertEqual(payload["message"], test_message)
                self.assertEqual(payload["level"], "INFO")
                self.assertEqual(payload["logger"], "magistrcheckbot.test")
            finally:
                _close_file_handlers()

    def test_configure_logging_uses_timed_rotating_file_handler(self) -> None:
        """Файловый хендлер — TimedRotatingFileHandler (ротация в полночь).

        Параметры: when='MIDNIGHT' (нормализуется uppercase в stdlib),
        ``backupCount`` = ``LOG_FILE_BACKUP_COUNT``. Это закрывает требование
        B2 — лог не пухнет, история ограничена месяцем."""

        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "bot.log"
            try:
                configure_logging(logging.INFO, log_file=log_path)

                file_handlers = [
                    h
                    for h in logging.getLogger().handlers
                    if isinstance(h, logging.FileHandler)
                ]
                self.assertEqual(len(file_handlers), 1)
                handler = file_handlers[0]
                self.assertIsInstance(
                    handler, logging.handlers.TimedRotatingFileHandler
                )
                self.assertEqual(handler.when, "MIDNIGHT")
                self.assertEqual(handler.backupCount, LOG_FILE_BACKUP_COUNT)
                self.assertIsInstance(handler.formatter, _JsonLogFormatter)
            finally:
                _close_file_handlers()

    def test_json_formatter_emits_required_fields(self) -> None:
        """Прямой unit-тест ``_JsonLogFormatter``: все базовые поля на месте."""

        record = logging.LogRecord(
            name="magistrcheckbot.unit",
            level=logging.WARNING,
            pathname=__file__,
            lineno=42,
            msg="hello %s",
            args=("world",),
            exc_info=None,
            func="some_func",
        )
        out = _JsonLogFormatter().format(record)
        payload = json.loads(out)
        self.assertEqual(payload["level"], "WARNING")
        self.assertEqual(payload["logger"], "magistrcheckbot.unit")
        self.assertEqual(payload["message"], "hello world")
        self.assertEqual(payload["lineno"], 42)
        self.assertEqual(payload["func"], "some_func")
        self.assertIn("ts", payload)
        self.assertIn("module", payload)
        # Без exc_info поле не появляется (минимизация шума).
        self.assertNotIn("exc_info", payload)

    def test_logger_exception_includes_traceback_in_file(self) -> None:
        """``logger.exception(...)`` пишет ``exc_info`` с traceback в JSON-файл.

        Это критично для B3 (alert-канал в Telegram) и для post-mortem'ов:
        без exc_info структурный лог не покажет, ГДЕ упало."""

        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "bot.log"
            try:
                configure_logging(logging.INFO, log_file=log_path)

                logger = logging.getLogger("magistrcheckbot.exc_test")
                try:
                    raise RuntimeError("boom-for-test")
                except RuntimeError:
                    logger.exception("operation failed")

                for h in logging.getLogger().handlers:
                    if isinstance(h, logging.FileHandler):
                        h.flush()

                lines = [
                    line
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                payload = json.loads(lines[-1])
                self.assertEqual(payload["level"], "ERROR")
                self.assertEqual(payload["message"], "operation failed")
                self.assertIn("exc_info", payload)
                self.assertIn("RuntimeError", payload["exc_info"])
                self.assertIn("boom-for-test", payload["exc_info"])
            finally:
                _close_file_handlers()


def _close_file_handlers() -> None:
    """Закрывает FileHandler'ы root logger'а до выхода из TemporaryDirectory.

    Без этого Windows возвращает WinError 32 при попытке удалить файл,
    пока хендлер держит на него открытый дескриптор."""

    root = logging.getLogger()
    for h in [h for h in root.handlers if isinstance(h, logging.FileHandler)]:
        root.removeHandler(h)
        h.close()


if __name__ == "__main__":
    unittest.main()
