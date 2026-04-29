"""Имена файлов промежуточного отчёта в папке Drive + резолвер id."""

import unittest
from typing import Any
from unittest.mock import MagicMock

from magister_checking.drive_folder import (
    DOCX_MIME,
    GOOGLE_DOC_MIME,
    GOOGLE_FOLDER_MIME,
    filename_starts_with_intermediate_report,
    pick_intermediate_report_doc_id,
)


class TestIntermediateReportFilename(unittest.TestCase):
    def test_prefix_as_requested(self) -> None:
        self.assertTrue(
            filename_starts_with_intermediate_report("Проммежуточный отчет — Иванов")
        )
        self.assertTrue(filename_starts_with_intermediate_report("Проммежуточный отчёт"))

    def test_standard_spelling(self) -> None:
        self.assertTrue(filename_starts_with_intermediate_report("Промежуточный отчет v2"))

    def test_non_matching(self) -> None:
        self.assertFalse(filename_starts_with_intermediate_report("Отчет промежуточный"))
        self.assertFalse(filename_starts_with_intermediate_report(""))


def _make_drive_service(folder_to_children: dict[str, list[dict[str, Any]]]) -> MagicMock:
    """Мок Drive v3, отвечает на files().list() в зависимости от parent folder.

    folder_to_children: {folder_id: [{id, name, mimeType}, ...]} —
    полный список содержимого, фильтрация по mimeType-условию из q-строки
    делается этим хелпером (мы парсим только список MIME из ``q`` —
    не полноценный SQL-парсер, а ровно то, что генерирует наш код:
    ``'<folder>' in parents and trashed = false and (mimeType = 'A' or mimeType = 'B')``).
    """

    def _list(
        *,
        q: str,
        fields: str,
        pageSize: int,  # noqa: N803
        pageToken: str | None,  # noqa: N803
        supportsAllDrives: bool,  # noqa: N803
        includeItemsFromAllDrives: bool,  # noqa: N803
    ):
        parent = q.split("'", 2)[1]
        children = folder_to_children.get(parent, [])

        wanted_mimes: list[str] = []
        for token in q.split("mimeType = '"):
            if token == q.split("mimeType = '")[0]:
                continue
            mime = token.split("'", 1)[0]
            if mime:
                wanted_mimes.append(mime)
        filtered = [c for c in children if c.get("mimeType") in wanted_mimes]

        executor = MagicMock()
        executor.execute.return_value = {"files": filtered}
        return executor

    files_api = MagicMock()
    files_api.list.side_effect = _list

    drive = MagicMock()
    drive.files.return_value = files_api
    return drive


class TestPickIntermediateReportDocId(unittest.TestCase):
    """Поведение pick_intermediate_report_doc_id для реальных раскладок папок."""

    def test_native_doc_in_folder(self) -> None:
        """Исторический случай: нативный Google Doc прямо в папке проекта."""
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "DOC1",
                        "name": "Промежуточный отчет — Иванов И.И.",
                        "mimeType": GOOGLE_DOC_MIME,
                    }
                ]
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "DOC1",
        )

    def test_docx_in_folder_returned_for_conversion(self) -> None:
        """Сулейменова: в папке только .docx с правильным именем — вернуть его id.

        Дальше .docx подхватит drive_docx.google_doc_from_drive_file и
        отконвертирует в Doc через buffer-папку.
        """
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "DOCX1",
                        "name": "Промежуточный отчет магистранта Сулейменовой И.С..docx",
                        "mimeType": DOCX_MIME,
                    },
                    {
                        "id": "SUBPROJ",
                        "name": "Магистерский проект",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    },
                ]
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "DOCX1",
        )

    def test_prefers_native_doc_over_docx(self) -> None:
        """Если в папке есть и Doc, и .docx — берём нативный Doc (не нужно конвертить)."""
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "DOCX1",
                        "name": "Промежуточный отчет вариант.docx",
                        "mimeType": DOCX_MIME,
                    },
                    {
                        "id": "DOC1",
                        "name": "Промежуточный отчет финал",
                        "mimeType": GOOGLE_DOC_MIME,
                    },
                ]
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "DOC1",
        )

    def test_descends_into_named_subfolder(self) -> None:
        """Тананова: отчёт лежит в подпапке «Промежуточный отчет …».

        Внутри подпапки имя файла часто обобщённое («Промежуточный
        отчет магистранта.docx») — фильтр по имени там НЕ применяется,
        контекст задаёт сама подпапка.
        """
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "SUBREP",
                        "name": "Промежуточный отчет Танановой А.А.",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    },
                    {
                        "id": "SUBPUB",
                        "name": "Публикации",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    },
                ],
                "SUBREP": [
                    {
                        "id": "DOCX_INSIDE",
                        "name": "Промежуточный отчет магистранта.docx",
                        "mimeType": DOCX_MIME,
                    }
                ],
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "DOCX_INSIDE",
        )

    def test_direct_file_wins_over_named_subfolder(self) -> None:
        """Если в самой папке уже есть подходящий файл — в подпапку НЕ спускаемся."""
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "ROOT_DOC",
                        "name": "Промежуточный отчет на уровне папки",
                        "mimeType": GOOGLE_DOC_MIME,
                    },
                    {
                        "id": "SUBREP",
                        "name": "Промежуточный отчет копия",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    },
                ],
                "SUBREP": [
                    {
                        "id": "NESTED_DOC",
                        "name": "Старая версия",
                        "mimeType": GOOGLE_DOC_MIME,
                    }
                ],
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "ROOT_DOC",
        )

    def test_returns_none_when_nothing_matches(self) -> None:
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "OTHER",
                        "name": "Какая-то справка.pdf",
                        "mimeType": "application/pdf",
                    },
                    {
                        "id": "SUB_OTHER",
                        "name": "Сопутствующие документы",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    },
                ]
            }
        )
        self.assertIsNone(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT")
        )

    def test_named_subfolder_without_report_falls_back_to_none(self) -> None:
        """Подпапка с правильным именем есть, но внутри нет Doc / .docx / PDF."""
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "SUBREP",
                        "name": "Промежуточный отчет Иванова",
                        "mimeType": GOOGLE_FOLDER_MIME,
                    }
                ],
                "SUBREP": [
                    {
                        "id": "PIC",
                        "name": "scan.pdf",
                        "mimeType": "image/jpeg",
                    }
                ],
            }
        )
        self.assertIsNone(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT")
        )

    def test_pdf_in_folder_returned_for_conversion(self) -> None:
        """В папке PDF с префиксом имени отчёта — тот же путь конверсии, что у .docx."""
        drive = _make_drive_service(
            {
                "ROOT": [
                    {
                        "id": "PDF1",
                        "name": "Промежуточный отчет Иванова.pdf",
                        "mimeType": "application/pdf",
                    },
                ]
            }
        )
        self.assertEqual(
            pick_intermediate_report_doc_id(drive_service=drive, folder_id="ROOT"),
            "PDF1",
        )


if __name__ == "__main__":
    unittest.main()
