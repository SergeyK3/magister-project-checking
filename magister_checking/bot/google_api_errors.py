"""Типовые ошибки Google API: 429 / quota — для текстов пользователю и логики алертов."""

from __future__ import annotations

GOOGLE_SHEETS_RATE_LIMIT_USER_NOTE = (
    "Сводные листы таблицы сейчас не обновились: у Google кратковременный лимит запросов. "
    "Запись анкеты сохранена. Через 5–10 минут можно снова нажать /start или обратитесь к куратору."
)

GOOGLE_SHEETS_RATE_LIMIT_ADMIN_NOTE = (
    "Сервис Google временно ограничил число запросов к таблице. Повторите через 5–10 минут."
)

# Сообщение при сбое до завершения шага (исключение доходит до глобального обработчика).
GOOGLE_SHEETS_RATE_LIMIT_REGISTRATION_RETRY = (
    "Сервис Google временно ограничил число запросов к таблице (лимит на минуту). "
    "Попробуйте зарегистрироваться снова через 30–60 секунд: нажмите /start или иную кнопку в меню. "
    "Если не получится — напишите админу."
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
