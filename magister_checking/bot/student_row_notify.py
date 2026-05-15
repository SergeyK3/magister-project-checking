"""Уведомление магистранта в Telegram после записи результатов проверки в лист.

Используется администратором после ручной доводки строки: ``check-row --apply
--notify-student`` — текст тот же, что после ``/recheck`` (HTML, этапы 1–4,
поля из таблицы и обогащения).
"""

from __future__ import annotations

import asyncio
import logging
from magister_checking.bot.config import BotConfig
from magister_checking.bot.row_pipeline import RowCheckReport
from magister_checking.bot.sheets_repo import get_telegram_id_at_row, get_worksheet
from magister_checking.row_check_cli import format_report, load_user_enrichment_for_row

logger = logging.getLogger(__name__)

_TELEGRAM_MAX_MESSAGE = 4000
"""Запас под лимит 4096 символов одного ``sendMessage``."""


def _iter_message_chunks(text: str, max_len: int = _TELEGRAM_MAX_MESSAGE) -> list[str]:
    if len(text) <= max_len:
        return [text]
    out: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            cut = max_len
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return out


_MANUAL_COMPLETION_INTRO_HTML = (
    "<b>Данные в регистрации обновлены администратором.</b> "
    "Ниже — актуальная справка: этапы проверки и заполненные поля.\n\n"
)


def build_manual_completion_spravka_html(
    config: BotConfig,
    report: RowCheckReport,
    row_number: int,
    *,
    applied: bool,
) -> str:
    """HTML справки для магистранта (как после /recheck) + короткое вступление."""

    user_row, extra_values = load_user_enrichment_for_row(config, row_number)
    body = format_report(
        report,
        applied=applied,
        user=user_row,
        extra_values=extra_values,
        fill_status=None,
        trigger="manual_sheet_completion",
        view="student",
        as_html=True,
    )
    return _MANUAL_COMPLETION_INTRO_HTML + body


async def _send_html_chunks(
    bot: Bot,
    *,
    chat_id: int,
    text: str,
) -> None:
    parts = _iter_message_chunks(text)
    for part in parts:
        await bot.send_message(
            chat_id=chat_id,
            text=part,
            parse_mode=ParseMode.HTML,
        )


def notify_student_after_manual_row_apply(
    config: BotConfig,
    report: RowCheckReport,
    *,
    applied: bool,
) -> tuple[bool, str]:
    """Шлёт магистранту полную справку по результатам последнего прогона.

    ``applied`` — была ли реальная запись в лист (как в CLI ``applied_effective``).

    Возвращает ``(успех, человекочитаемое сообщение для stdout/stderr)``."""
    row_number = report.row_number
    if row_number is None:
        return False, "уведомление не отправлено: в отчёте нет номера строки"

    worksheet = get_worksheet(config)
    tg_raw = get_telegram_id_at_row(worksheet, row_number)
    if not (tg_raw or "").strip():
        return False, (
            f"уведомление не отправлено: в строке {row_number} пустой telegram_id"
        )
    try:
        chat_id = int(str(tg_raw).strip())
    except ValueError:
        return False, f"уведомление не отправлено: некорректный telegram_id {tg_raw!r}"

    try:
        html = build_manual_completion_spravka_html(
            config, report, row_number, applied=applied
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сборка справки для уведомления (строка %s)", row_number)
        return False, f"уведомление не отправлено: ошибка сборки справки: {exc}"

    async def _run() -> None:
        async with Bot(config.telegram_bot_token) as bot:
            await _send_html_chunks(bot, chat_id=chat_id, text=html)

    try:
        asyncio.run(_run())
    except TelegramError as exc:
        logger.warning("Telegram отклонил отправку chat_id=%s: %s", chat_id, exc)
        return False, f"уведомление не отправлено: {type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой при отправке уведомления chat_id=%s", chat_id)
        return False, f"уведомление не отправлено: {exc}"

    return True, f"Справка отправлена магистранту (chat_id={chat_id}, строка {row_number})."
