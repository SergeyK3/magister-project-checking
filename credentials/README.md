# Учётные данные (локально)

Сюда **не коммитятся** файлы с секретами. В репозитории остаются только этот файл и `.gitkeep`.

## Что положить

1. Скачанный из Google Cloud Console JSON для OAuth **Desktop** — сохраните как `client_secret.json` (или укажите имя в коде).
2. После первого входа приложение создаёт `token.json` — только на вашем ПК.

Подробности: [docs/google_cloud_console.md](../docs/google_cloud_console.md).

## Смена прав (scope)

Если менялся код (`magister_checking/auth.py`) или появляется **403 / insufficient authentication scopes**, **удалите `token.json`** и снова выполните:

```powershell
python -m magister_checking login
```

Сейчас приложению нужен scope **`documents`** (запись в целевые Google Doc), а не только `documents.readonly`.
