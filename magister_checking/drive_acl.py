"""Доступ к файлам Drive для пользовательских сообщений и диагностики."""

from __future__ import annotations

from typing import Any


def drive_file_has_anyone_with_link_permission(drive_service: Any, file_id: str) -> bool:
    """True, если у файла есть правило «любой по ссылке» (``type=anyone``).

    В этом режиме чтение по URL без входа в аккаунт Google уже разрешено;
    отдельно добавлять в доступ сервисный аккаунт бота для того же файла
    обычно не требуется. Если при этом запросы Drive/Docs API всё же возвращают
    403, причина чаще в конвертации PDF/DOCX, буферной папке Shared Drive или
    включении API — см. лог бота.
    """

    if not file_id:
        return False
    try:
        resp = (
            drive_service.permissions()
            .list(
                fileId=file_id,
                fields="permissions(type,role)",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception:
        return False
    for perm in resp.get("permissions") or []:
        if perm.get("type") == "anyone":
            return True
    return False
