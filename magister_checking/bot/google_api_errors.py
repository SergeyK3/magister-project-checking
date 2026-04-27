"""Типовые ошибки Google API: 429 / quota — для текстов пользователю и логики алертов."""

from __future__ import annotations

GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE = (
    "Сводный лист в таблице мог не обновиться: сейчас высокая нагрузка на сервис Google "
    "(достигнут лимит запросов). Ваши данные уже записаны. Попробуйте через 10–15 минут "
    "или обратитесь к куратору."
)

GOOGLE_SHEETS_RATE_LIMIT_ADMIN_NOTE = (
    "Сервис Google временно ограничил число запросов к таблице. Повторите через 10–15 минут."
)


def is_google_sheets_rate_limit(exc: BaseException) -> bool:
    """True для HTTP 429 / quota exceeded (gspread, googleapiclient)."""

    msg = str(exc).lower()
    if "429" in msg or "quota exceeded" in msg:
        return True
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None) if resp is not None else None
    if status is not None:
        try:
            return int(status) == 429
        except (TypeError, ValueError):
            pass
    return False
