# Handoff — лист «Магистранты», синхронизация статуса, команды научрука и магистранта

**Дата:** 2026-04-30  
**Статус:** реализовано в коде; в `.env` задано **`MAGISTRANTS_WORKSHEET_NAME=Магистранты`** — бот перезапущен.

---

## 0. Кратко для следующей сессии

1. **Master-лист** в той же книге, что `SPREADSHEET_ID`: имя задаётся **`MAGISTRANTS_WORKSHEET_NAME`** (у вас **`Магистранты`**). Пустое значение — синхронизация и команды научрука, завязанные на этот лист, не используются (бот отвечает, что лист не настроен).

2. **`sync_magistrants_registration_status`** (`magister_checking/bot/sheets_repo.py`): по данным листа **«Регистрация»** строится множество ключей **(normalize_fio, normalize_phone_ru_kz)** для строк, где одновременно непусты **`telegram_id`**, **`fio`**, **`phone`**. Для каждой строки листа магистрантов обновляются колонки **«Телефон»** (канонический **`+7` + 10 цифр**, если парсится) и **«Регистрация»**: текст **`зарегистрирован`** или **`нет`**.

3. **Точки вызова синка:** после сохранения анкеты (**`ask_confirm`**, после `sync_registration_dashboard`); отдельная админ-команда **`/sync_magistrants`**. При **429** Google Sheets к пользователю может добавиться та же пометка, что и для Dashboard (rate limit).

4. **Нормализация телефона:** `magister_checking/bot/phone_normalize.py`, тесты `tests/bot/test_phone_normalize.py`.

5. **Команды (личка, `filters=private`):**
   - **Админ:** **`/sync_magistrants`**; **`/unreg ФИО научрука`**, **`/reg_list ФИО научрука`** — превью тех же отчётов, что **`/unreg`** / **`/reg_list`** у научрука без аргументов (`supervisor_fio_override`). Админ **без** роли научрука без аргументов получает подсказку добавить ФИО; админ **и** научрук без аргументов — обычный список «своих» студентов. См. `docs/guide_admin_ru.md`.
   - **Научрук:** **`/unreg`**, **`/reg_list`** (без аргументов), **`/status ФИО`** — сначала проверяется роль научрука; без аргументов **`/status`** — подсказка формата.
   - **Магистрант:** **`/register`** (тот же сценарий, что «новая регистрация»; есть в **entry_points** и **fallbacks** `ConversationHandler`), **`/status`** (своя строка «Регистрация» по `telegram_id`).

6. **Сопоставление научрука с строками master-листа:** колонка научного руководителя (алиасы в коде: научный руков, научный руководитель, научрук) сравнивается с ФИО из листа **`научрук`** для текущего `telegram_id` — **`supervisor_name_matches`** (в т.ч. вхождение подстроки после `normalize_fio`).

7. **Колонки первой строки листа «Магистранты»** (ожидаются по алиасам): **ФИО** (`фио магистранта`, `фио`), **Телефон**, **Регистрация**; для команд научрука нужна ещё колонка **научрука** (см. алиасы выше). Без полного набора бот сообщает об ошибке шапки.

8. **Меню Telegram:** пункты **`unreg`** и **`reg_list`** с подсказкой для админа (превью при передаче **ФИО научрука** в том же сообщении в `context.args`), например: `/unreg Иванов Иван Иванович`. **Альтернатива без интерактивного бота:** `python -m magister_checking supervisor-list-preview` (`--telegram-id` или `--supervisor-fio`).

---

## 1. Ключевые файлы

| Файл | Назначение |
|------|------------|
| `magister_checking/bot/phone_normalize.py` | `normalize_phone_ru_kz` → `+7XXXXXXXXXX` или `""`. |
| `magister_checking/bot/sheets_repo.py` | `sync_magistrants_registration_status`, `registration_students_by_fio_phone`, `magistrants_sheet_column_indices`, `get_supervisor_fio_for_telegram_id`, `supervisor_name_matches`. |
| `magister_checking/bot/config.py` | `BotConfig.magistrants_worksheet_name`, env **`MAGISTRANTS_WORKSHEET_NAME`**. |
| `magister_checking/bot/handlers.py` | `admin_sync_magistrants`, `register_command`, `status_command`, `supervisor_unregistered_list_command`, `supervisor_registered_list_command` (ветка админа + **ФИО** = превью), HELP-тексты, **`default_bot_commands`**. |
| `magister_checking/bot/app.py` | `CommandHandler` для `/sync_magistrants`, `/status`, `/unreg`, `/reg_list`; **`/register`** только внутри `ConversationHandler`. |
| `magister_checking/bot/supervisor_lists.py` | Общая логика отчётов для научрука и превью админа. |
| `magister_checking/cli.py` | **`supervisor-list-preview`** (альтернатива бот-командам). |
| `.env.example` | Пример **`MAGISTRANTS_WORKSHEET_NAME=Магистранты`**. |
| `docs/guide_admin_ru.md` | §1–§2 — админ: **`/unreg`/`/reg_list`** с ФИО научрука (превью); подраздел превью **бот + CLI**; §7 — лист **Магистранты**. |
| `docs/guide_supervisor_ru.md` | §4 — превью списков для администратора. |
| `tests/bot/test_sheets_repo.py` | `SyncMagistrantsRegistrationStatusTests` (лист в фейке называется **`Магистранты`**). |
| `tests/bot/test_handlers.py` | **`HelpAndCommandsTests.test_default_bot_commands_lists_core_slugs`** — полный список slug’ов меню. |

---

## 2. Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest tests.bot.test_phone_normalize tests.bot.test_sheets_repo.SyncMagistrantsRegistrationStatusTests tests.bot.test_handlers.HelpAndCommandsTests.test_default_bot_commands_lists_core_slugs -v
```

Полный `tests.bot.test_sheets_repo` при регрессии по Sheets.

---

## 3. Деплой и конфигурация

- В **`.env`**: `MAGISTRANTS_WORKSHEET_NAME=Магистранты` (имя листа **точно** как в Google Sheets).
- **`scripts\bot_stop.ps1`** при дубликатах процесса, затем **`scripts\bot_start.ps1`**.

---

## 4. Старт нового чата (шаблон)

> Контекст: `docs/handoff_2026-04-30_magistrants_sync_supervisor_commands.md`. Нужно [доработка PIN / второй источник магистрантов / …].

Связанное ТЗ-черновик: `docs/doptz20260430.md`. По нему см. актуальный разбор пробелов реализации: **`docs/handoff_doptz20260430_remaining.md`** (PIN по MVP там **реализован**; расходящиеся пункты перечислены в этом новом файле).
