"""Broadcast-сообщения зарегистрированным пользователям бота.

Логика отделена от CLI-обвязки (см. ``cli.cmd_broadcast``), чтобы:
1. Тесты могли мокать ``Bot`` и проверять обработку Forbidden/RetryAfter
   без сети и без токена.
2. Сборка списка получателей (Регистрация + PicklePersistence + дедуп)
   не зависела от парсинга argv.

Источники получателей:
- ``registration``: колонка ``telegram_id`` листа «Регистрация» — точный
  список магистрантов, прошедших регистрацию.
- ``persistence``: ключи ``chat_data``/``user_data`` из PicklePersistence —
  все, кто хоть раз писал боту (включая случайных, не завершивших анкету).

При источнике ``both`` объединяем оба источника с дедупликацией по
строковому id; порядок: сначала Регистрация (как более «формальная»
аудитория), потом — оставшиеся ID из persistence.
"""

from __future__ import annotations

import asyncio
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, List, Tuple

from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

logger = logging.getLogger("magister_checking.broadcast")


def collect_chat_ids_from_persistence(path: Path) -> List[str]:
    """Достаёт все известные chat_id и user_id из PicklePersistence-файла.

    Бот хранит ``user_data``/``chat_data`` (см. ``app._build_persistence``);
    в private-chat сценарии Telegram chat_id == user_id, поэтому объединение
    обеих секций с дедупликацией даёт максимально широкий список тех, кому
    мы вообще можем что-то отправить.

    Если файла нет, он битый или содержит не-словарь — возвращаем пустой
    список (не падаем): broadcast в этом случае просто использует только
    второй источник, либо завершится с «получателей нет».
    """

    if not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            data = pickle.load(fh)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось прочитать persistence-файл %s", path, exc_info=True)
        return []
    if not isinstance(data, dict):
        return []

    out: List[str] = []
    seen: set[str] = set()
    for section_key in ("chat_data", "user_data"):
        section = data.get(section_key)
        if not isinstance(section, dict):
            continue
        for key in section.keys():
            cleaned = str(key).strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            out.append(cleaned)
    return out


def merge_dedup(*sources: Iterable[str]) -> List[str]:
    """Объединяет несколько источников ID с сохранением первого появления."""

    out: List[str] = []
    seen: set[str] = set()
    for source in sources:
        for raw in source:
            cleaned = str(raw or "").strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            out.append(cleaned)
    return out


@dataclass
class BroadcastResult:
    """Итог рассылки: успешные адресаты и список ошибок (id, причина)."""

    sent: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    skipped_invalid: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.sent) + len(self.failed) + len(self.skipped_invalid)


SleepFn = Callable[[float], Awaitable[None]]


async def send_broadcast(
    bot: Bot,
    recipients: Iterable[str],
    message: str,
    *,
    sleep_between: float = 0.04,
    sleep: SleepFn = asyncio.sleep,
) -> BroadcastResult:
    """Отправляет ``message`` всем ``recipients`` с rate-limit и одной попыткой retry на RetryAfter.

    Поведение по типам ошибок Telegram:
    - ``Forbidden`` — пользователь заблокировал бота / аккаунт удалён → не
      пытаемся повторно (тратить квоту бессмысленно), пишем в ``failed``.
    - ``BadRequest`` — chat не найден, неверный chat_id и т.п. → так же в
      ``failed`` без retry.
    - ``RetryAfter`` — Telegram просит подождать N секунд (flood-лимит) →
      ждём ``exc.retry_after`` (минимум 1 с), повторяем один раз. Если и
      повтор упал — фиксируем в ``failed``. Двух попыток достаточно: при
      ``sleep_between=0.04`` (~25 msg/сек) мы ниже глобального лимита 30/с,
      RetryAfter в норме случаться не должен.
    - Прочие ``TelegramError`` (NetworkError и т.п.) — в ``failed`` без retry,
      чтобы один сбой сети не заморозил всю рассылку.

    ``ValueError`` от ``int(tid)`` → в ``skipped_invalid`` (битая строка в
    источнике, например посторонний текст в колонке telegram_id).

    Параметр ``sleep`` инжектируется тестами, чтобы не ждать реальное время.
    """

    result = BroadcastResult()
    for raw in recipients:
        cleaned = str(raw or "").strip()
        if not cleaned:
            continue
        try:
            chat_id = int(cleaned)
        except ValueError:
            result.skipped_invalid.append(cleaned)
            logger.debug("Пропускаю некорректный id: %r", cleaned)
            continue

        try:
            await bot.send_message(chat_id=chat_id, text=message)
            result.sent.append(cleaned)
        except RetryAfter as exc:
            wait = max(float(getattr(exc, "retry_after", 1) or 1), 1.0)
            logger.info("RetryAfter %.1fs для %s — жду и повторяю", wait, cleaned)
            await sleep(wait)
            try:
                await bot.send_message(chat_id=chat_id, text=message)
                result.sent.append(cleaned)
            except TelegramError as exc2:
                result.failed.append((cleaned, f"{type(exc2).__name__}: {exc2}"))
        except (Forbidden, BadRequest) as exc:
            result.failed.append((cleaned, f"{type(exc).__name__}: {exc}"))
        except TelegramError as exc:
            result.failed.append((cleaned, f"{type(exc).__name__}: {exc}"))

        if sleep_between > 0:
            await sleep(sleep_between)

    return result


def format_dry_run_preview(
    recipients: Iterable[str],
    message: str,
    *,
    source_label: str,
) -> str:
    """Текстовый отчёт для ``--dry-run``: список адресатов + текст сообщения.

    Печатается в stdout и помогает удостовериться, что мы шлём ровно то и
    ровно тем — последний барьер перед необратимым ``--send``.
    """

    rec_list = list(recipients)
    lines = [
        "=== Broadcast DRY-RUN ===",
        f"Источник: {source_label}",
        f"Получателей: {len(rec_list)}",
        "",
        "ID получателей:",
    ]
    if not rec_list:
        lines.append("  (пусто)")
    else:
        for tid in rec_list:
            lines.append(f"  {tid}")
    lines.extend(
        [
            "",
            "Текст сообщения:",
            "------------------------------------------",
            message,
            "------------------------------------------",
            "",
            "Это dry-run, отправки не было. Для реальной рассылки добавьте",
            "--send --i-know-this-is-irreversible.",
        ]
    )
    return "\n".join(lines)


def format_send_summary(result: BroadcastResult) -> str:
    """Итоговая сводка после ``--send`` для stdout/log."""

    lines = [
        "=== Broadcast SUMMARY ===",
        f"Отправлено успешно: {len(result.sent)}",
        f"Ошибки доставки:    {len(result.failed)}",
        f"Пропущено (битые):  {len(result.skipped_invalid)}",
    ]
    if result.failed:
        lines.append("")
        lines.append("Ошибки:")
        for tid, reason in result.failed:
            lines.append(f"  {tid}: {reason}")
    if result.skipped_invalid:
        lines.append("")
        lines.append("Битые ID (не int):")
        for tid in result.skipped_invalid:
            lines.append(f"  {tid!r}")
    return "\n".join(lines)


__all__ = [
    "BroadcastResult",
    "collect_chat_ids_from_persistence",
    "format_dry_run_preview",
    "format_send_summary",
    "merge_dedup",
    "send_broadcast",
]
