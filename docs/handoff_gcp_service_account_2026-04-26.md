# Handoff — Google Cloud: проект MagisterChecker, service account, доступы

**Дата:** 2026-04-26  
**Статус:** настроено в Console; JSON-ключ скачан; политика org, блокировавшая ключи, ослаблена.

---

## 0. Кратко для следующей сессии

- **GCP-проект:** `MagisterChecker` (id в URL: `magisterchecker`), **организация** `zdravconsult.com`.
- **Service account:** `magister-bot@magisterchecker.iam.gserviceaccount.com` — ключ **JSON** в репо: `key/magisterchecker-54031006193b.json` (в `.env`: `GOOGLE_SERVICE_ACCOUNT_JSON=key/magisterchecker-54031006193b.json`). **Старый** ключ `magister-checking-dev-…` **не** используется (строка в `.env` закомментирована; при желании **удалить** SA/Drive-шаринг **старого** `client_email` для гигиены).
- **Org policy (ключи):** на constraint **`iam-disableServiceAccountKeyCreation`** (legacy, не `iam-managed.…`) для org: **Override** + правило **Enforcement: Off** → **effective: Not enforced**. Без этого Console не давала **Add key → JSON** (`Service account key creation is disabled`). Роль **Organization Policy Administrator** на `ai@zdravconsult.com` нужна была, чтобы **сохранить** политику.
- **Таблица бота:** `SPREADSHEET_ID=1RuAXZt9wAu3CQNn3GAL65ifyjykN5AnD8XFwnZ_v39s` = книга **«АттестМагистр202605 (Ответы)»** — **Редактор** для `magister-bot@…` на **эту** книгу.
- **Shared drive MagisterChecking:** `magister-bot` — **Менеджер контента**; папки **buffer** (буфер `.docx`, `GOOGLE_DRIVE_BUFFER_FOLDER_ID`), **Magistr2026** (в т.ч. `magistr_card` для `PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS`).

**Не коммитить:** `key/*.json`, `.env` с токенами. В handoff **нет** токенов и содержимого ключей.

---

## 1. Переменные окружения (смыслово)

| Переменная | Назначение |
|------------|------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Путь к **новому** JSON (`key/magisterchecker-54031006193b.json`). |
| `SPREADSHEET_ID` | ID книги **«АттестМагистр202605 (Ответы)»** (см. выше). |
| `GOOGLE_DRIVE_BUFFER_FOLDER_ID` | Папка **buffer** в Shared drive (`.docx` → Google Doc). |
| `PROJECT_SNAPSHOT_OUTPUT_FOLDER_URLS` | Папка(и) **JSON** снимков (например `magistr_card`). |

См. также `.env.example` и `magister_checking/bot/config.py`.

---

## 2. Навигация Console (коллеге)

- **Service accounts (проект):**  
  `https://console.cloud.google.com/iam-admin/serviceaccounts?project=magisterchecker`
- **Organization policies (org):** селектор **zdravconsult.com** → **IAM & Admin** → **Organization policies**; фильтр по таблице: `key` / `service account key` — **не** глобальный поиск в шапке.
- **API Library:** глобальный поиск `API Library` → пункт *Library* **или**  
  `https://console.cloud.google.com/apis/library?project=magisterchecker`

---

## 3. Если снова «key creation is disabled»

1. **Organization policies** → **`iam-disableServiceAccountKeyCreation`** (legacy) → **Not enforced** / **Enforcement Off** в правилах.  
2. Проверить, что правите **не** только managed-строку `iam.managed.…` (у вас она была **Inactive**, блокировал **active** legacy).

---

## 4. Старт нового чата (шаблон)

> Контекст: `docs/handoff_gcp_service_account_2026-04-26.md`. Нужно [ротация ключа / новая папка Drive / сбой 403 на Drive / ещё один SPREADSHEET_ID].

---

## 5. Ссылки на нормы в репо

- Снимок проекта: `docs/contract_project_snapshot.md`  
- Буфер и Shared drive: комментарии в `.env.example` около `GOOGLE_DRIVE_BUFFER_FOLDER_*`

---

## 6. Telegram: человекочитаемый вывод JSON-снимков (после доработки бота)

- **`/spravka`:** кнопки «кратко (магистрант)», «полный текст для комиссии в чат» (админ), «PDF»; ответы проверки и справки — **HTML** (`parse_mode=HTML`), структура из `magister_checking/snapshot_render.py`.
- **Админ:** вложить в чат файл `project_snapshot_*.json` с Drive → бот пришлёт два сообщения (вид для магистранта и для комиссии) без повторного прогона пайплайна. Реализация: `on_project_snapshot_json_file` в `magister_checking/bot/handlers.py` (только `telegram_id` из листа «Admins»).
- Парсинг файла: `project_snapshot_from_json_str` в `magister_checking/project_snapshot.py` (`schema_version` = 1).
