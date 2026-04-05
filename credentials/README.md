# Учётные данные (локально)

Сюда **не коммитятся** файлы с секретами. В репозитории остаются только этот файл и `.gitkeep`.

## Что положить

1. Скачанный из Google Cloud Console JSON для OAuth **Desktop** — сохраните как `client_secret.json` (или укажите имя в коде).
2. После первого входа приложение может создать `token.json` — он тоже должен оставаться только на вашем ПК.

Подробности: [docs/google_cloud_console.md](../docs/google_cloud_console.md).

## Запись в Google Таблицы

Если при `build-summary` появляется ошибка **403 / insufficient authentication scopes**, удалите `token.json` и снова выполните `python -m magister_checking login`, чтобы выдать приложению право **редактировать** Sheets (не только чтение). Раньше вы могли получить токен с `spreadsheets.readonly` из другого скрипта.
