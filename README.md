# Magister Project Checking

Проект для проверки магистерских работ. Автоматизация сбора сведений из Google Docs (отчёты, сводные таблицы внутри документов). Пилот — личный Google-аккаунт и OAuth.

**Репозиторий на GitHub:** [github.com/SergeyK3/magister-project-checking](https://github.com/SergeyK3/magister-project-checking)

## Документация

| Файл | Содержание |
|------|------------|
| [docs/tz_magister_checking_0d150b8f.plan.md](docs/tz_magister_checking_0d150b8f.plan.md) | Техническое задание (исходное) |
| [docs/tz_amendment_2026-04-06_docs_output.md](docs/tz_amendment_2026-04-06_docs_output.md) | Изменение ТЗ: вывод в Google Doc вместо Sheets |
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
