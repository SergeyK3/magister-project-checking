# Magister Project Checking

Проект для проверки магистерских работ. Автоматизация сбора проверяемых сведений (Google Диск, отчёты, сводные таблицы). Пилот — личный Google-аккаунт и OAuth.

**Репозиторий на GitHub:** [github.com/SergeyK3/magister-project-checking](https://github.com/SergeyK3/magister-project-checking)

## Документация

| Файл | Содержание |
|------|------------|
| [docs/tz_magister_checking_0d150b8f.plan.md](docs/tz_magister_checking_0d150b8f.plan.md) | Техническое задание |
| [docs/google_cloud_console.md](docs/google_cloud_console.md) | Настройка проекта в Google Cloud Console |

Личные черновики можно хранить в `docs/private/` — эта папка в `.gitignore`.

## Быстрый старт (окружение)

```powershell
cd "D:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

```powershell
python -m pip install -r requirements.txt
```

### Что уже можно запускать

1. Положите в `credentials/` файл OAuth **Desktop** из Google Cloud Console (см. [docs/google_cloud_console.md](docs/google_cloud_console.md)), имя: `client_secret.json` или `client_secret_….json`.
2. Первый вход в браузере (сохранится `credentials/token.json`):

   ```powershell
   python -m magister_checking login
   ```

3. Проверка доступа к любому Google Doc (ссылка из тестовой папки или id):

   ```powershell
   python -m magister_checking doc-info "https://docs.google.com/document/d/ВАШ_ID/edit"
   ```

4. Модульные тесты разбора ссылок:

   ```powershell
   python -m unittest discover -s tests -v
   ```

Дальше по ТЗ: парсинг «Промежуточного отчёта», метрики диссертации, запись в Sheets.

## Секреты

JSON клиента OAuth и `token.json` — только в каталоге [credentials/](credentials/README.md), не в Git.
