"""Конвертация загруженных в Drive .docx / .pdf в Google Doc на лету.

Docs API умеет читать только нативные Google Docs
(``application/vnd.google-apps.document``). Магистранты часто загружают
в Drive обычный ``.docx``, который в Docs API отвечает ``HTTP 400
"This operation is not supported for this document"``.

Этот модуль даёт контекстный менеджер
``google_doc_from_drive_file``: на время блока он копирует .docx в
указанную папку с конверсией ``mimeType=application/vnd.google-apps.document``
и переводит копию в корзину Shared Drive на выходе (в том числе при
исключении). Возвращает id, который уже можно отдавать в
``docs.documents().get(documentId=...)``.

Папка-буфер должна лежать в Shared Drive — у Service Account без
Workspace ``files().copy()`` в обычном My Drive падает с HTTP 403
``storageQuotaExceeded`` (квота SA = 0). Cleanup идёт через
``files().update(trashed=True)``, а не ``files().delete()``: в Shared
Drive файлы принадлежат самому Shared Drive, а не SA, и роль
``fileOrganizer`` (Content Manager) в общем случае не имеет права
permanent delete — Drive возвращает HTTP 404 на ``delete``, тогда как
``trashed=True`` отрабатывает штатно. Корзина Shared Drive
автоматически очищается через 30 дней.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
CONVERTIBLE_DRIVE_MIMES = frozenset({DOCX_MIME, PDF_MIME})
"""MIME-типы, которые Drive умеет сконвертировать в Google Doc при ``files.copy``.

Поддерживаются загруженные в Drive ``.docx`` и ``.pdf`` (конвертация на стороне
Google — как при «Открыть с помощью Google Документы» в веб-интерфейсе).
"""

_LOG = logging.getLogger(__name__)


class UnsupportedDocumentMimeTypeError(RuntimeError):
    """Файл не Google Doc и не .docx — автоматическая конверсия невозможна."""

    def __init__(self, mime_type: str, file_id: str) -> None:
        super().__init__(
            f"Файл {file_id!r} имеет MIME-тип {mime_type!r}; "
            "поддерживаются только Google Doc и загруженные в Drive .docx / .pdf "
            "(через конверсию в Google Doc)."
        )
        self.mime_type = mime_type
        self.file_id = file_id


class MissingConversionFolderError(RuntimeError):
    """В конфиге не задана папка для временных Google-Doc копий .docx."""


@contextlib.contextmanager
def google_doc_from_drive_file(
    drive_service: Any,
    file_id: str,
    *,
    conversion_folder_id: str,
) -> Iterator[str]:
    """Возвращает id файла, который гарантированно можно читать Docs API.

    - Если ``file_id`` — уже Google Doc, отдаём его как есть и ничего не удаляем.
    - Если ``.docx`` или ``.pdf`` (``CONVERTIBLE_DRIVE_MIMES``), делаем копию в папке
      ``conversion_folder_id`` с ``mimeType=application/vnd.google-apps.document``
      и удаляем копию при выходе из контекста (в т.ч. при исключении).
    - Для других MIME-типов бросаем ``UnsupportedDocumentMimeTypeError``.

    ``drive_service`` — экземпляр Drive v3 API (``googleapiclient.discovery``).
    """

    meta = (
        drive_service.files()
        .get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True)
        .execute()
    )
    mime_type = meta.get("mimeType", "")

    if mime_type == GOOGLE_DOC_MIME:
        yield file_id
        return

    if mime_type not in CONVERTIBLE_DRIVE_MIMES:
        raise UnsupportedDocumentMimeTypeError(mime_type, file_id)

    if not conversion_folder_id:
        raise MissingConversionFolderError(
            "Для чтения .docx из Drive нужна папка-буфер в Shared Drive. "
            "Задайте GOOGLE_DRIVE_BUFFER_FOLDER_URL / GOOGLE_DRIVE_BUFFER_FOLDER_ID "
            "(или legacy DOCX_CONVERSION_FOLDER_URL/_ID) в .env и добавьте "
            "Service Account в Shared Drive как Content Manager."
        )

    name = meta.get("name") or file_id
    copy = (
        drive_service.files()
        .copy(
            fileId=file_id,
            body={
                "name": f"[magistrcheckbot conv] {name}",
                "mimeType": GOOGLE_DOC_MIME,
                "parents": [conversion_folder_id],
            },
            supportsAllDrives=True,
        )
        .execute()
    )
    copy_id = copy["id"]

    try:
        yield copy_id
    finally:
        # Не files().delete() — fileOrganizer в Shared Drive не имеет
        # права permanent delete и получает HTTP 404. trashed=True
        # отправляет файл в корзину Shared Drive (auto-purge через 30 дней).
        try:
            drive_service.files().update(
                fileId=copy_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "Не удалось перевести временную копию %s в корзину Shared Drive",
                copy_id,
                exc_info=True,
            )
