# Handoff — роли при /start, лист «научрук», привязка по ФИО

**Дата:** 2026-04-26  
**Статус:** реализовано в коде; тесты `pytest` зелёные (в т.ч. полный прогон).

---

## 0. Кратко для следующей сессии

- **`/start`:** сначала строка в листе **«Регистрация»** (как `WORKSHEET_NAME` в `.env`) → продолжение анкеты; иначе проверка **«Администраторы»** → краткое приветствие, диалог обрывается; иначе **«научрук»** (константа `SUPERVISORS_WORKSHEET_NAME` в `sheets_repo.py`, имя листа **`научрук`**) → приветствие, `END`; иначе **инлайн-кнопки** ролей.
- **Магистрант (новый):** подменю **«Новая регистрация»** / **«Уже есть запись (по ФИО)»** — второе = прежняя привязка к **«Регистрация»** (`receive_bind_fio` / `confirm_bind`).
- **Админ или научрук (впервые):** ввод ФИО по **соответствующему** листу → подтверждение да/нет → `attach_telegram_to_row` на **этот** лист; без совпавшей строки в таблице — отказ.
- **`/help`:** приоритет **админ** → **научрук** → **магистрант** (`help_reply_for_user`); текст научрука — **MVP**, константа `HELP_REPLY_TEXT_SUPERVISOR` в `handlers.py`.
- **Ручно в Google Таблице:** должен существовать лист **`научрук`** (структура как у **«Администраторы»**: как минимум `fio`, `telegram_id`, `active` — по смыслу кода), строки с ФИО до самопривязки. См. комментарий в `.env.example`.

**Основные файлы:** `magister_checking/bot/handlers.py`, `magister_checking/bot/sheets_repo.py`, `magister_checking/bot/app.py` (состояния `ROLE_PICK`, `CLAIM_ASK_FIO`, `CLAIM_CONFIRM` и `CallbackQueryHandler` с паттерном `^start:`).

---

## 1. Состояния и callback-данные

| Состояние | Смысл |
|-----------|--------|
| `ROLE_PICK` | Выбор роли; callback `start:pick:mag\|admin\|sup`, затем `start:mag:new`, `start:mag:bind` |
| `CLAIM_ASK_FIO` / `CLAIM_CONFIRM` | Привязка к листу админов или научруков по ФИО |
| `BIND_ASK_FIO` / `BIND_CONFIRM` | Без изменений: привязка к **Регистрация** (после `start:mag:bind`) |

`user_data`: `claim_target` = `admin` | `supervisor`, `claim_candidate_row` — номер строки на подтверждение.

---

## 2. API репозитория (для тестов и правок)

- `is_supervisor_telegram_id`, `is_admin_telegram_id` — логика `active` как в коде супервизора/админа.
- `get_telegram_id_at_row` — чтение `telegram_id` в строке (гонки при подтверждении).
- `attach_telegram_to_row` — запись в **любой** лист с маппингом колонок `telegram_id` / username / имена.

---

## 3. Тесты

- `tests/bot/test_handlers.py` — сценарии старта, ролей, bind-flow (вход в bind через `start:mag:bind`).
- `tests/bot/test_sheets_repo.py` — `is_supervisor_telegram_id`, `get_telegram_id_at_row`.

---

## 4. Старт нового чата (шаблон)

> Контекст: `docs/handoff_start_roles_supervisor_2026-04-26.md`. Нужно [добавить команды научруку / сменить имя листа / сценарий «и админ, и магистрант» / …].

---

## 5. План (источник требований)

Логика согласована с планом в Cursor: роли, Mermaid, лист «научрук», MVP help для научрука (сам план-файл в Cursor **не** менялся в репозитории).
