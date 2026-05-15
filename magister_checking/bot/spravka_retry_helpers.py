"""Pure spravka/recheck parsing, chunking, and keyboard helpers."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_TELEGRAM_SPRAVKA_MAX = 4000
"""Запас под лимит 4096 символов одного ``sendMessage`` (справка, отчёты)."""

SPRAVKA_CALLBACK_TELEGRAM = "spravka:telegram"
SPRAVKA_CALLBACK_PDF = "spravka:pdf"
SPRAVKA_CALLBACK_COMMISSION = "spravka:commission"

RECHECK_QUICK_TOKENS = {"quick", "only-if-changed", "only_if_changed", "fast", "diff"}
"""Ключевые слова, после которых legacy ``/recheck`` работает как ``--only-if-changed``.

По умолчанию legacy ``/recheck`` запускает полный прогон (handoff §8 —
diff_detection режим «full by default»), но магистрант может написать ``/recheck quick``,
чтобы получить ответ «без изменений» без повторной нагрузки на Drive.
"""

SPRAVKA_RETRY_ONLY_IF_CHANGED = False
"""Канонический публичный ``/справка`` всегда формирует актуальный отчёт по строке."""

RECHECK_BUTTON_LABEL = "🔄 Перепроверить"
RECHECK_CALLBACK_DATA = "recheck:full"
RECHECK_CALLBACK_PATTERN = r"^recheck:full(?::\d+)?$"
"""Шаблон callback: ``recheck:full`` или ``recheck:full:<номер строки>`` для админской
перепроверки без повторного ввода цели (кнопка под отчётом по той же строке).
"""


def _iter_telegram_spravka_chunks(
    text: str, max_len: int = _TELEGRAM_SPRAVKA_MAX
) -> list[str]:
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


def _parse_recheck_callback_row(callback_data: str | None) -> int | None:
    if not callback_data:
        return None
    parts = callback_data.strip().split(":")
    if len(parts) != 3 or parts[0] != "recheck" or parts[1] != "full":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _parse_recheck_command_parts(
    message_text: str, *, default_only_if_changed: bool = False
) -> tuple[bool, str | None]:
    """Разбор текста retry-команды.

    Возвращает ``(only_if_changed, target)``, где ``target`` — номер строки или ФИО
    (всё, что осталось после удаления токенов ``quick`` / ``only-if-changed`` и т.д.).
    Только администраторы могут передать непустой ``target`` (см. ``recheck`` /
    ``spravka_start``). Для legacy ``/recheck`` default — full; публичная
    ``/справка`` также запускает полный прогон, если пользователь явно не передал
    quick-токен.
    """

    parts = (message_text or "").strip().split()
    if len(parts) < 2:
        return default_only_if_changed, None
    body = parts[1:]
    only_if_changed = default_only_if_changed
    collected: list[str] = []
    for token in body:
        if token.strip().lower() in RECHECK_QUICK_TOKENS:
            only_if_changed = True
        else:
            collected.append(token)
    target = " ".join(collected).strip() or None
    return only_if_changed, target


def build_recheck_keyboard(row_number: int | None = None) -> InlineKeyboardMarkup:
    """Inline-кнопка под итоговым отчётом и финалом регистрации.

    Inline (а не Reply) — чтобы кнопка была привязана к конкретному сообщению
    и пропадала после нажатия (см. ``recheck_button``). Это снижает риск
    случайного двойного запуска тяжёлого пайплайна.

    Если передан ``row_number``, в callback кладётся ``recheck:full:<row>``, чтобы
    администратор (без своей строки в «Регистрация») мог перепроверить ту же цель
    кнопкой, не вводя номер снова. Магистрант всё равно ищется по ``telegram_id``.
    """

    payload = (
        RECHECK_CALLBACK_DATA
        if row_number is None
        else f"{RECHECK_CALLBACK_DATA}:{row_number}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(RECHECK_BUTTON_LABEL, callback_data=payload)]]
    )


def _recheck_reply_markup_after_check(
    row_number: int, *, attach_kb: bool
) -> InlineKeyboardMarkup | None:
    """Inline «Перепроверить» только если callback сможет подставить строку."""

    return build_recheck_keyboard(row_number) if attach_kb else None
