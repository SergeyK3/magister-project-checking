# Скриншоты Google Cloud и доступа к книге

Сюда кладите статичные изображения, на которые ссылается **`docs/guide_admin_ru.md`**, раздел *«Где снять скриншоты…»*.

**Не загружать:** полный текст JSON ключа; строки ключей OAuth; любые секреты.

Рекомендуемые имена (можно добавить суффикс даты `_2026-04`):

| Файл | Что должно быть видно |
|------|-------------------------|
| `01_project_selector.png` | Выбран проект MagisterChecker / `magisterchecker` |
| `02_apis_dashboard.png` | [API Dashboard](https://console.cloud.google.com/apis/dashboard?project=magisterchecker): Sheets, Drive, Docs в списке включённых |
| `03_service_account_email.png` | Страница service account: виден **email** (как `client_email` в ключе) |
| `04_sheets_share_editor.png` | Диалог «Поделиться» у книги `SPREADSHEET_ID`: сервисный аккаунт с ролью **Редактор** |

После добавления файлов в **`guide_admin_ru.md`** можно в таблице в §9 указать строки вида `![APIs](../images/gcp/02_apis_dashboard.png)` — по желанию.
