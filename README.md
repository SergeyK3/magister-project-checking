# Magister Project Checking

Проект для проверки магистерских работ. Состоит из двух частей:

- **Telegram-бот `@magistrcheckbot`** — основной канал регистрации магистрантов и
  первичной проверки ссылки на промежуточный отчёт (этапы 1–2 ТЗ v2). Пишет
  данные в Google Sheets через Service Account.
- **CLI-утилиты анализа Google Docs** — `doc-info`, `doc-extract`,
  `build-summary`, `fill-docs-test1`. Используют OAuth (личный Google-аккаунт).

**Репозиторий на GitHub:** [github.com/SergeyK3/magister-project-checking](https://github.com/SergeyK3/magister-project-checking)

## Документация

| Файл | Содержание |
|------|------------|
| [docs/tz_magister_checking_0d150b8f.plan.md](docs/tz_magister_checking_0d150b8f.plan.md) | Техническое задание (исходное) |
| [docs/tz_amendment_2026-04-06_docs_output.md](docs/tz_amendment_2026-04-06_docs_output.md) | Изменение ТЗ: вывод в Google Doc вместо Sheets |
| [docs/tz_tezis_v2.md](docs/tz_tezis_v2.md) | Тезисы ТЗ v2: Telegram-бот + Google Sheets |
| [docs/google_cloud_console.md](docs/google_cloud_console.md) | Настройка Google Cloud Console |

Личные черновики: `docs/private/` (в `.gitignore`).

## Боевые документы (выход)

Пустые шаблоны на Google Диске для записи результатов бота:

| Назначение | Документ |
|------------|----------|
| **Сводная таблица по магистерским проектам** | [открыть](https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit?usp=sharing) · `documentId` `1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk` |
| **Детальная таблица по магистерским проектам** | [открыть](https://docs.google.com/document/d/16Zv1lysdXzBCzIJJrTBn9fSef4PsNK2TGIb2HFSOYZY/edit?usp=sharing) · `documentId` `16Zv1lysdXzBCzIJJrTBn9fSef4PsNK2TGIb2HFSOYZY` |

Учётная запись OAuth должна иметь **право редактировать** оба файла. Документ со **списком** магистрантов и ссылками на промежуточные отчёты передаётся **первым** аргументом `fill-docs-test1` (в примере ниже это отдельный Doc, не обязательно тот же id, что в колонке «сводная»). Типовой id `1uMKdy…` в таблице — **выходной** сводный Doc; если у вас список лежит в другом файле (в т.ч. с другим id), указывайте его первым аргументом.

### После опроса / проверки: заполнить сводную таблицу

Итог проверки пишется во **второй** аргумент — [сводный Doc](https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit?usp=sharing) (`1uMKdy…`). Рабочий список с четырьмя магистрантами и ссылками на отчёты: [список (тест)](https://docs.google.com/document/d/1yZH2fjlqVWBRSaHDWmjkcsqlYo0rSmSp7UtjJoWCnyY/edit?usp=sharing).

Полный прогон (все строки списка → сводная + детальная таблица):

```powershell
python -m magister_checking fill-docs-test1 `
  "https://docs.google.com/document/d/1yZH2fjlqVWBRSaHDWmjkcsqlYo0rSmSp7UtjJoWCnyY/edit" `
  "https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit" `
  "https://docs.google.com/document/d/16Zv1lysdXzBCzIJJrTBn9fSef4PsNK2TGIb2HFSOYZY/edit" `
  --all-students --bootstrap-templates
```

`--bootstrap-templates` нужен, если в сводном или детальном Doc ещё нет таблицы / недостаточно заголовков H1; если шаблон уже подготовлен вручную, флаг можно опустить.

Если в сводном файле **сначала идёт узкая таблица (3 колонки: №, ФИО, группа)**, а полная строка не заполняется — бот теперь пишет в таблицу с **наибольшим числом колонок**. При одной только 3-колоночной таблице запустите с `--bootstrap-templates`: в конец документа добавится таблица **7×(1+N)**; старую узкую таблицу можно удалить вручную. Пустые страницы в Doc — это обычно разрывы раздела: удалите лишние через меню «Вставка» / визуально в редакторе.

**Только первая строка** (старый «Тест 1»):

```powershell
python -m magister_checking fill-docs-test1 "URL_списка" "https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit"
```

## Быстрый старт

```powershell
cd "D:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

1. Положите `credentials/client_secret.json` (OAuth Desktop), см. [docs/google_cloud_console.md](docs/google_cloud_console.md).
2. После смены scope в коде удалите старый `credentials/token.json`, затем:

   ```powershell
   python -m magister_checking login
   ```

3. **Запись в сводный Doc:** первый магистрант → первая строка данных (`--data-row 1` по умолчанию); боевой сводный файл — см. раздел «Боевые документы» выше (`1uMKdy…`).

   ```powershell
   python -m magister_checking fill-docs-test1 "URL_списка" "https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit"
   ```

   Третий аргумент — детальный Doc; вместе с `--all-students` заполняются оба.  
   `--dry-run` — только TSV в консоль.  
   `--data-row N` — другая строка (только без `--all-students`).

4. Полный свод по всем строкам списка в виде TSV (без записи в файл):

   ```powershell
   python -m magister_checking build-summary "URL_сводного_списка_Doc"
   ```

   Если указать второй аргумент (старый формат Sheets) без `--dry-run`, команда завершится с подсказкой использовать `fill-docs-test1`.

5. Прочее: `doc-info`, `doc-extract`, `login` — как раньше.

6. Тесты:

   ```powershell
   python -m unittest discover -s tests -v
   ```

## Секреты

См. [credentials/README.md](credentials/README.md).

## Telegram-бот @magistrcheckbot (ТЗ v2, этапы 1–2)

Бот собирает регистрационные данные магистранта через диалог `/start`, делает
первичную проверку ссылки на промежуточный отчёт и записывает строку в Google
Sheets. Логика разнесена по пакету `magister_checking.bot`
(`config`, `models`, `validation`, `sheets_repo`, `handlers`, `app`).

### Подготовка

1. Создайте бота у [@BotFather](https://t.me/BotFather), получите токен.
2. Создайте Service Account в Google Cloud Console и скачайте JSON-ключ.
   Положите его в `credentials/` (папка под `.gitignore`).
3. Откройте целевую Google Sheets и **поделитесь ею с email сервисного аккаунта**
   (поле `client_email` из JSON) с правом «Редактор».
4. Скопируйте `.env.example` в `.env` и заполните:

   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...
   SPREADSHEET_ID=16gpZSZgKBcbf8Z9LZvcYKT1lUPG-URuo9C6K9BRPDHU
   WORKSHEET_NAME=Регистрация
   GOOGLE_SERVICE_ACCOUNT_JSON=credentials/service_account.json
   LOG_LEVEL=INFO
   ```

### Запуск

```powershell
python -m magister_checking bot
```

Бот стартует в режиме long polling. На листе автоматически создаётся (или
перезаписывается) шапка из 16 столбцов п.8.1 ТЗ:

```
telegram_id, telegram_username, telegram_first_name, telegram_last_name,
fio, group_name, workplace, position, phone, supervisor,
report_url, report_url_valid, report_url_accessible, report_url_public_guess,
fill_status, last_action
```

### Сценарий пользователя

- `/start` — начать или продолжить регистрацию. Если запись уже есть, бот
  спросит только незаполненные поля (см. п.5.4 ТЗ).
- Любое поле можно пропустить, отправив `-` или команду `/skip`.
- `/cancel` — выйти из диалога без сохранения.
- В конце бот показывает сводку и просит подтвердить «да / нет». При «да»
  выполняется upsert строки по `telegram_id`.

### Статусы (`fill_status`, п.12 ТЗ)

- `NEW` — ни одно обязательное поле не заполнено;
- `PARTIAL` — часть полей заполнена;
- `REGISTERED` — все 7 обязательных полей заполнены.

`last_action` фиксирует имя последнего шага (`ask_<field>`, `answered_<field>`,
`skipped_<field>`, `confirmed_save`, `cancelled_save`, `cancelled`) — это
помогает оператору видеть, где магистрант остановился.

### Тесты

```powershell
python -m pytest tests/bot -q
```

Тесты используют фейковый worksheet и моки Telegram/HTTP — сеть и реальный
Service Account не нужны.
