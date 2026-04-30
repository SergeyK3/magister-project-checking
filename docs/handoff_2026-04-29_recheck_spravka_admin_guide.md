# Handoff — /recheck для админа, вёрстка справки, подсказки Stage 3, 429 Sheets

**Дата:** 2026-04-29  
**Статус:** реализовано в коде и в `docs/guide_admin_ru.md`; бота на сервере после выкладки — **перезапустить**.

---

## 0. Кратко для следующей сессии

1. **Кнопка «Перепроверить» у администратора без строки в «Регистрация».** Раньше после ответа бота callback был только `recheck:full` → снова запрашивалась цель. Теперь в callback подставляется **`recheck:full:<N>`** (номер проверенной строки); **`recheck_button`** разбирает payload и для **админа** вызывает **`_do_recheck(..., row_number_override=N)`**. У не-админа номер из callback **игнорируется** (защита от подделки). Паттерн хендлера: **`RECHECK_CALLBACK_PATTERN`** = `^recheck:full(?::\d+)?$` в `app.py`.
2. **Справка магистранту (HTML и plain)** в **`snapshot_render.py`**: предупреждения Stage 3 с префиксами вида ««Заключение ЛКБ»»… уходят из общего блока «Результаты» и дублируются **под строкой колонки** в блоке ссылок L/M/N/O; тексты Stage 4 с подробным оформлением («Найдено / Нужно») — в **конце** сообщения, заголовок **«Оформление (подробно)»**. Функции **`_stage3_issue_column_key`**, **`_partition_stage3_warnings`**.
3. **Stage 3 (`row_pipeline.py`)** — дополнительные пояснения при неверном типе ссылки: **`report_url`** + folder / слово folder в URL Google; **`project_folder_url`** — файл/doc вместо папки или ссылка без типичного `…/folders/…`; **`lkb_url`** + папка — фраза про pdf (уже было, обобщено через **`_extra_kind_mismatch_hints`**).
4. **Документация:** **`docs/guide_admin_ru.md`** — обновлена строка таблицы про **`/recheck`**, добавлены §**8.4** (429 Read requests/min), §**10** (формат справки), §11 «Связанные документы».
5. **Лог `429` Sheets:** это **квота чтений в минуту**, не «нет связи». В **`error_alerts.py`** для таких ошибок — **WARNING**, алерты на **`BOT_ALERT_CHAT_IDS`** не шлются. Подождать ~минуту и повторить **`/recheck 21`** и т.п.

---

## 1. Ключевые файлы

| Файл | Назначение |
|------|------------|
| `magister_checking/bot/handlers.py` | `build_recheck_keyboard(row_number)`, `_parse_recheck_callback_row`, `recheck_button` → override строки для админа; `_do_recheck` передаёт `row_number` в клавиатуру ответов. |
| `magister_checking/bot/app.py` | `CallbackQueryHandler(..., pattern=RECHECK_CALLBACK_PATTERN)`. |
| `magister_checking/snapshot_render.py` | Разбиение предупреждений по фазам для справки магистранту. |
| `magister_checking/bot/row_pipeline.py` | `_extra_kind_mismatch_hints`, `_folder_word_in_google_url`, импорт `is_google_drive_folder_url`. |
| `docs/guide_admin_ru.md` | Админское руководство (recheck, 429, формат справки). |
| `tests/bot/test_handlers.py` | Клавиатура с `:2`, тест админа с `recheck:full:7`. |
| `tests/test_project_snapshot.py` | Порядок секций в HTML-справке. |
| `tests/bot/test_row_pipeline.py` | Новые кейсы Stage 3 для отчёта/папки проекта. |

---

## 2. Проверки

```powershell
python -m pytest tests/bot/test_handlers.py tests/test_project_snapshot.py tests/bot/test_row_pipeline.py -q
```

Расширенный набор при необходимости:

```powershell
python -m pytest tests/bot/test_handlers.py tests/test_project_snapshot.py tests/bot/test_row_pipeline.py tests/test_error_alerts.py -q
```

---

## 3. Деплой

- Остановить бота (`Ctrl+C` в терминале со `scripts\bot_start.ps1`), запустить снова после выкладки.

---

## 4. Старт нового чата (шаблон)

> Контекст: `docs/handoff_2026-04-29_recheck_spravka_admin_guide.md`. Нужно [доработка / backoff при 429 / …].

Предыдущий связанный handoff (PDF, ACL, библиография): `docs/handoff_2026-04-29_pdf_acl_recheck_bibliography.md`.
