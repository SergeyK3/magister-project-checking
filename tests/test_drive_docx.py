"""Юнит-тесты модуля drive_docx (конверсия .docx → Google Doc через Drive API)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from magister_checking.drive_docx import (
    DOCX_MIME,
    GOOGLE_DOC_MIME,
    MissingConversionFolderError,
    PDF_MIME,
    UnsupportedDocumentMimeTypeError,
    google_doc_from_drive_file,
)


def _make_drive_service(
    *,
    mime_type: str,
    name: str = "file.ext",
    copy_id: str = "copy-id",
    raise_on_trash: bool = False,
) -> tuple[MagicMock, dict[str, Any]]:
    """Возвращает мок drive v3 и журнал вызовов (dict для удобства проверок).

    Cleanup идёт через ``files().update(body={'trashed': True})`` — см.
    drive_docx.py: ``files().delete()`` в Shared Drive у SA-fileOrganizer
    отдаёт HTTP 404 на permanent delete, а trashed=True работает.
    """

    log: dict[str, Any] = {"get": [], "copy": [], "update": []}

    def _get(fileId: str, fields: str, supportsAllDrives: bool):  # noqa: N803
        log["get"].append({"fileId": fileId, "fields": fields, "supportsAllDrives": supportsAllDrives})
        executor = MagicMock()
        executor.execute.return_value = {"id": fileId, "name": name, "mimeType": mime_type}
        return executor

    def _copy(fileId: str, body: dict, supportsAllDrives: bool):  # noqa: N803
        log["copy"].append({"fileId": fileId, "body": body, "supportsAllDrives": supportsAllDrives})
        executor = MagicMock()
        executor.execute.return_value = {"id": copy_id, "name": body.get("name"), "mimeType": GOOGLE_DOC_MIME}
        return executor

    def _update(fileId: str, body: dict, supportsAllDrives: bool):  # noqa: N803
        log["update"].append({"fileId": fileId, "body": body, "supportsAllDrives": supportsAllDrives})
        executor = MagicMock()
        if raise_on_trash:
            executor.execute.side_effect = RuntimeError("boom")
        else:
            executor.execute.return_value = {"id": fileId, "trashed": body.get("trashed", False)}
        return executor

    def _delete(fileId: str, supportsAllDrives: bool):  # noqa: N803
        # Любой вызов delete — баг: код должен ходить только через trash.
        raise AssertionError(
            "files().delete() called — drive_docx must use update(trashed=True) "
            "in Shared Drives (fileOrganizer cannot permanent-delete)."
        )

    files = MagicMock()
    files.get.side_effect = _get
    files.copy.side_effect = _copy
    files.update.side_effect = _update
    files.delete.side_effect = _delete

    drive = MagicMock()
    drive.files.return_value = files
    return drive, log


class NativeGoogleDocTests(unittest.TestCase):
    def test_passes_through_without_copy_or_trash(self) -> None:
        drive, log = _make_drive_service(mime_type=GOOGLE_DOC_MIME)

        with google_doc_from_drive_file(drive, "orig-id", conversion_folder_id="folder") as got:
            self.assertEqual(got, "orig-id")

        self.assertEqual(len(log["get"]), 1)
        self.assertEqual(log["copy"], [])
        self.assertEqual(log["update"], [])


class DocxConversionTests(unittest.TestCase):
    def test_copies_then_trashes_and_yields_copy_id(self) -> None:
        drive, log = _make_drive_service(mime_type=DOCX_MIME, name="Отчёт.docx")

        with google_doc_from_drive_file(
            drive, "orig-id", conversion_folder_id="buffer-folder"
        ) as got:
            self.assertEqual(got, "copy-id")
            self.assertEqual(len(log["copy"]), 1)
            self.assertEqual(log["update"], [])  # cleanup только на выходе

        self.assertEqual(len(log["update"]), 1)
        cleanup = log["update"][0]
        self.assertEqual(cleanup["fileId"], "copy-id")
        self.assertEqual(cleanup["body"], {"trashed": True})
        self.assertTrue(cleanup["supportsAllDrives"])

        copy_call = log["copy"][0]
        self.assertEqual(copy_call["fileId"], "orig-id")
        self.assertEqual(copy_call["body"]["mimeType"], GOOGLE_DOC_MIME)
        self.assertEqual(copy_call["body"]["parents"], ["buffer-folder"])
        self.assertIn("Отчёт.docx", copy_call["body"]["name"])

    def test_trashes_copy_on_exception_inside_block(self) -> None:
        drive, log = _make_drive_service(mime_type=DOCX_MIME)

        with self.assertRaises(ValueError):
            with google_doc_from_drive_file(
                drive, "orig-id", conversion_folder_id="buffer-folder"
            ):
                raise ValueError("inside")

        self.assertEqual(len(log["copy"]), 1)
        self.assertEqual(len(log["update"]), 1)
        self.assertEqual(log["update"][0]["body"], {"trashed": True})

    def test_swallows_trash_errors(self) -> None:
        # Если Drive внезапно не может trashed=True, контекст не должен падать
        # — файл уже прочитан, мусор подчистят отдельно или вручную.
        drive, log = _make_drive_service(mime_type=DOCX_MIME, raise_on_trash=True)

        with google_doc_from_drive_file(
            drive, "orig-id", conversion_folder_id="buffer-folder"
        ) as got:
            self.assertEqual(got, "copy-id")

        self.assertEqual(len(log["update"]), 1)


class PdfConversionTests(unittest.TestCase):
    def test_pdf_copies_then_trashes_like_docx(self) -> None:
        drive, log = _make_drive_service(mime_type=PDF_MIME, name="Отчёт.pdf")

        with google_doc_from_drive_file(
            drive, "orig-id", conversion_folder_id="buffer-folder"
        ) as got:
            self.assertEqual(got, "copy-id")

        self.assertEqual(len(log["copy"]), 1)
        self.assertEqual(log["copy"][0]["body"]["mimeType"], GOOGLE_DOC_MIME)
        self.assertEqual(len(log["update"]), 1)


class ErrorPathsTests(unittest.TestCase):
    def test_unsupported_mime_raises_without_copying(self) -> None:
        drive, log = _make_drive_service(mime_type="image/jpeg")

        with self.assertRaises(UnsupportedDocumentMimeTypeError) as ctx:
            with google_doc_from_drive_file(
                drive, "orig-id", conversion_folder_id="buffer-folder"
            ):
                pass  # pragma: no cover

        self.assertEqual(ctx.exception.mime_type, "image/jpeg")
        self.assertEqual(log["copy"], [])
        self.assertEqual(log["update"], [])

    def test_docx_without_conversion_folder_raises(self) -> None:
        drive, log = _make_drive_service(mime_type=DOCX_MIME)

        with self.assertRaises(MissingConversionFolderError):
            with google_doc_from_drive_file(drive, "orig-id", conversion_folder_id=""):
                pass  # pragma: no cover

        self.assertEqual(log["copy"], [])
        self.assertEqual(log["update"], [])


if __name__ == "__main__":
    unittest.main()
