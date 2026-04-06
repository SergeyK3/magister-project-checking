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

Учётная запись OAuth должна иметь **право редактировать** оба файла. Документ со **списком** магистрантов и ссылками на отчёты передаётся первым аргументом (см. ниже).

Пример **Тест 1** (первая строка данных сводной таблицы):

```powershell
python -m magister_checking fill-docs-test1 "URL_Doc_со_списком_магистрантов" "https://docs.google.com/document/d/1uMKdyUkeMOHVuCgl-Gn-FkpJNI9dW0bmdEgam8ixUPk/edit" "https://docs.google.com/document/d/16Zv1lysdXzBCzIJJrTBn9fSef4PsNK2TGIb2HFSOYZY/edit"
```

Третий аргумент (детальная таблица) пока только для единообразия вызова; заполнение детального Doc в коде — следующий этап.

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

3. **Тест 1 (ТЗ):** первый магистрант из списка → чтение отчёта → заполнение **первой строки данных** (индекс строки `1`) в **целевом** сводном Google Doc (пустой шаблон с таблицей):

   ```powershell
   python -m magister_checking fill-docs-test1 "URL_списка_магистрантов" "URL_пустого_сводного_Doc"
   ```

   Опционально третий аргумент — детальный Doc (пока не заполняется).  
   `--dry-run` — только одна строка TSV в консоль, без записи.  
   `--data-row N` — другая строка таблицы (по умолчанию `1`).

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
