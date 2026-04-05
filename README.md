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

Дальше: `pip install -r requirements.txt` (появится после добавления зависимостей).

## Секреты

JSON клиента OAuth и `token.json` — только в каталоге [credentials/](credentials/README.md), не в Git.
