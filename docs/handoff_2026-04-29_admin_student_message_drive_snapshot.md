# Handoff — сообщение магистранту админом, CLI `send-message`, вложение HTML по последнему снимку на Drive

**Файл (для вставки в новый чат):** `docs/handoff_2026-04-29_admin_student_message_drive_snapshot.md`

**Дата:** 2026-04-29  
**Статус:** реализовано в коде и в `docs/guide_admin_ru.md`; бота после выкладки — **перезапустить**.

**Содержание:** раздел 0 — сценарий и поток данных; раздел 1 — таблица ключевых файлов; раздел 2 — `pytest`; раздел 3 — деплой и `.env` для папок снимков; раздел 4 — шаблон нового чата и связанные handoff’ы.

---

## 0. Сценарий «Сообщение магистранту» (`/admin`, `/student_message`, `admstu:*`), CLI и вложение HTML

1. **Панель администратора:** команда **`/admin`** или ReplyKeyboard — кнопка **«Сообщение магистранту»** (вторая строка). Альтернативный вход без кнопки: **`/student_message`**. Отдельный `ConversationHandler` в **`app.py`**: строка/ФИО (`_resolve_project_card_target_row`) → выбор шаблона через inline (**`admstu:std`** / **`admstu:stdex`** / **`admstu:cust`**) → при необходимости ввод до трёх строк замечаний или произвольного текста → предпросмотр → **`admstu:send`** / **`admstu:cancel`**. Константы состояний: **`STUDENT_MSG_*`**, паттерны callback в **`handlers.py`** (`ADMSTU_CALLBACK_*`).
2. **Тексты напоминаний:** модуль **`magister_checking/bot/student_notify_text.py`** — **`build_standard_reminder`** (при необходимости ФИО из листа + блок «Замечания»).
3. **Одному адресату с сервера без бота:** CLI **`python -m magister_checking send-message`** — **`--message-file`**, цель **`--row`** или **`--telegram-id`**, отправка только с **`--send --i-know-this-is-irreversible`** (как у **`broadcast`**). Реализация: **`cmd_send_message`** в **`cli.py`**, отправка через **`send_broadcast`** на один id. **Вложение последнего JSON-снимка с Drive в виде HTML для браузера через CLI не выполняется** — только текстовое сообщение.
4. **Вложение после текстового напоминания из бота:** если в конфигурации заданы папки снимков (**`PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS`**; при пустом списке возможен фолбек **`PROJECT_CARD_OUTPUT_FOLDER_URL`** через **`_snapshot_folder_urls`** в **`snapshot_drive.py`**), после успешной отправки текста (**`admstu:send`** → **`send_broadcast`**) бот ищет на Drive файлы **`project_snapshot_r{N}_*.json`** (совпадение имени как в **`list_snapshot_json_candidates`** / загрузке **`try_upload_project_snapshot_json`**) среди всех этих папок, берёт объект с **максимальным** **`modifiedTime`**, качает JSON, парсит в **`ProjectSnapshot`**, рендерит **`render_commission_telegram_html`** из **`snapshot_render.py`**, оборачивает в страницу **`wrap_commission_html_for_browser`** в **`drive_latest_snapshot.py`**, отправляет **`send_document`** вторым сообщением (**`.html`**, имя вида `proverka_stroka_{N}.html`). Если снимка нет или ошибка — текст всё равно доставлен; администратору в чат добавляется пояснение (логика в **`student_reminder_confirm_callback`** в **`handlers.py`**, блок после **`pick_latest_snapshot_for_row`**).

---

## 1. Ключевые файлы

| Файл | Назначение |
|------|------------|
| `magister_checking/bot/handlers.py` | **`student_reminder_*`**, клавиатуры **`_student_reminder_*`**, **`_clear_student_reminder`**, после **`send_broadcast`** — **`pick_latest_snapshot_for_row`**, **`download_drive_file_bytes`**, **`send_document`**. |
| `magister_checking/bot/app.py` | Состояния **`STUDENT_MSG_*`**, `CallbackQueryHandler` для **`ADMSTU_CALLBACK_*`**, точки входа **`/student_message`** и regex кнопки с панели **`/admin`**. |
| `magister_checking/bot/student_notify_text.py` | Шаблоны текста напоминания магистранту. |
| `magister_checking/bot/config.py` | Разбор **`PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS`** / **`PROJECT_CARD_OUTPUT_FOLDER_URL`**. |
| `magister_checking/cli.py` | Подкоманда **`send-message`**, функция **`cmd_send_message`**. |
| `magister_checking/drive_latest_snapshot.py` | Поиск последнего JSON по строке по **`modifiedTime`**, скачивание, **`wrap_commission_html_for_browser`**. |
| `magister_checking/snapshot_drive.py` | **`_snapshot_folder_urls`**, именование загружаемых JSON (**`project_snapshot_r…`**) — согласовано с поиском. |
| `magister_checking/snapshot_render.py` | **`render_commission_telegram_html`** — ссылки в блоке документов кликабельны во вложенном HTML в браузере. |
| `magister_checking/project_snapshot.py` | **`project_snapshot_from_json_str`**, модель снимка. |
| `docs/guide_admin_ru.md` | Раздел **6**: бот-текст vs **`send-message`** vs **`broadcast`**, описание вложения снимка. |
| `tests/bot/test_handlers.py` | Регрессия хендлеров (в т.ч. **`student_message`**). |
| `tests/test_drive_latest_snapshot.py` | Префиксы имён файла и обёртка HTML. |
| `tests/bot/test_student_reminder.py` | Колбэк отмены (mock). |
| `tests/test_student_notify_text.py` | Тексты шаблонов. |
| `tests/test_cli_send_message.py` | CLI **`send-message`**. |

---

## 2. Команды pytest

```powershell
python -m pytest tests/bot/test_handlers.py tests/bot/test_student_reminder.py tests/test_drive_latest_snapshot.py tests/test_student_notify_text.py tests/test_cli_send_message.py -q
```

Расширенно при регрессии по боту:

```powershell
python -m pytest tests/bot/ -q
```

---

## 3. Деплой и `.env` для папок снимков

- Остановить бота (`Ctrl+C` в сеансе со **`scripts\bot_start.ps1`**), выкладывать изменения, снова запустить скрипт.
- **Переменные папок JSON-снимков** (см. комментарии в **`.env.example`** и **`magister_checking/bot/config.py`**):
  - **`PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS`** — список URL папок через запятую; если задан, он **полностью** задаёт список ( **`PROJECT_CARD_OUTPUT_FOLDER_URL` не подмешивается** ).
  - **`PROJECT_CARD_OUTPUT_FOLDER_URL`** — одна папка; используется, если **`PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS`** пуст (обратная совместимость и поведение **`_snapshot_folder_urls`**).
- Сервисный аккаунт должен иметь доступ **Content manager** (или эквивалент на чтение/листинг файлов) к этим папкам на Shared Drive; путь к ключу задаётся **`GOOGLE_SERVICE_ACCOUNT_JSON`** (см. **`.env.example`** и **`BotConfig`**).
- Ограничение размера скачиваемого JSON при вложении задаётся **`_SNAPSHOT_JSON_MAX_BYTES`** в **`handlers.py`**.

---

## 4. Шаблон для нового чата и предыдущие handoff’ы

Скопируйте в начало запроса (имя файла — этот документ):

```text
Контекст: docs/handoff_2026-04-29_admin_student_message_drive_snapshot.md
Нужно: [доработать сценарий / офлайн-генерацию снимка при отсутствии файла на Drive / …]
```

Связанные handoff’ы:

- Перепроверка и справка админа: **`docs/handoff_2026-04-29_recheck_spravka_admin_guide.md`**
- PDF, ACL, библиография: **`docs/handoff_2026-04-29_pdf_acl_recheck_bibliography.md`**
