"""Интеграционные тесты IO-слоя row_check_cli (с моками сервисов)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.report_parser import ParsedReport
from magister_checking.row_check_cli import RowLocator, format_report, run_row_check


def _doc_with_text(text: str) -> dict:
    return {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{"textRun": {"content": text}}],
                    }
                }
            ]
        }
    }


def _parsed(**overrides: object) -> ParsedReport:
    base: dict[str, object] = {
        "lkb_status": "да",
        "lkb_url": None,
        "dissertation_url": None,
        "review_article_url": None,
        "review_article_note": "",
        "results_article_url": None,
    }
    base.update(overrides)
    return ParsedReport(**base)  # type: ignore[arg-type]


def _make_native_doc_drive_service() -> MagicMock:
    """Drive-мок, у которого file.get(...).execute() возвращает метаданные
    нативного Google Doc. С таким MIME ``drive_docx.google_doc_from_drive_file``
    отдаёт file_id без копирования и удаления — эквивалент «старого» поведения.
    """

    drive = MagicMock()
    drive.files.return_value.get.return_value.execute.return_value = {
        "id": "doc-id",
        "name": "doc",
        "mimeType": "application/vnd.google-apps.document",
    }
    return drive


def _install_io_mocks(
    *,
    user: UserForm,
    document: object | None,
    parsed: ParsedReport | None,
    url_probe_map: dict[str, tuple[str, str]] | None = None,
    matched_rows: list[int] | None = None,
    drive_service: MagicMock | None = None,
):
    """Возвращает контекстный менеджер, маскирующий все IO вызовы run_row_check.

    url_probe_map: {url: (valid, accessible)} — результаты check_report_url
    для каждого URL, к которому обратится пайплайн. Значение по умолчанию
    («yes», «yes») используется, если URL не указан явно.

    drive_service: кастомный мок Drive API; по умолчанию — «нативный Google Doc»,
    чтобы ``google_doc_from_drive_file`` отдавал id без копирования.
    """

    probes = url_probe_map or {}

    def fake_check_report_url(url: str) -> tuple[str, str]:
        return probes.get(url, ("yes", "yes"))

    spreadsheet = MagicMock()
    worksheet = MagicMock()
    spreadsheet.worksheet.return_value = worksheet

    effective_drive = drive_service or _make_native_doc_drive_service()

    patches = [
        patch(
            "magister_checking.row_check_cli.get_spreadsheet",
            return_value=spreadsheet,
        ),
        patch(
            "magister_checking.row_check_cli.load_user",
            return_value=user,
        ),
        patch(
            "magister_checking.row_check_cli.find_rows_by_fio",
            return_value=matched_rows if matched_rows is not None else [2],
        ),
        patch(
            "magister_checking.row_check_cli._service_account_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "magister_checking.row_check_cli.build",
            side_effect=[MagicMock(), MagicMock()],
        ),
        patch(
            "magister_checking.row_check_cli.resolve_report_google_doc_id",
            return_value="doc-id",
        ),
        patch(
            "magister_checking.row_check_cli.parse_intermediate_report",
            return_value=parsed,
        ),
        patch(
            "magister_checking.row_check_cli.check_report_url",
            side_effect=fake_check_report_url,
        ),
    ]

    class _Manager:
        def __enter__(self) -> None:
            for p in patches:
                p.start()
            docs_service = MagicMock()
            if document is None:
                docs_service.documents.return_value.get.return_value.execute.side_effect = (
                    RuntimeError("no doc")
                )
            else:
                docs_service.documents.return_value.get.return_value.execute.return_value = document
            patches[4].stop()
            self._build_patch = patch(
                "magister_checking.row_check_cli.build",
                side_effect=[docs_service, effective_drive],
            )
            self._build_patch.start()

        def __exit__(self, *args: object) -> None:
            self._build_patch.stop()
            for p in patches[:4] + patches[5:]:
                p.stop()

    return _Manager()


class RunRowCheckTests(unittest.TestCase):
    def test_row_with_wrong_doc_marker_stops_at_stage1(self) -> None:
        user = UserForm(
            fio="Камзебаева Анель Дулатовна",
            phone="+77052107246",
            report_url="https://drive.google.com/drive/folders/magister-project",
        )
        wrong_doc = _doc_with_text("Магистерский проект: содержание")
        config = MagicMock()
        with _install_io_mocks(user=user, document=wrong_doc, parsed=_parsed()):
            report = run_row_check(config, RowLocator(row_number=2))
        self.assertEqual(report.row_number, 2)
        self.assertEqual(report.stopped_at, "stage1")
        self.assertFalse(report.stage2.executed)

    def test_row_stops_at_stage2_when_link_not_open(self) -> None:
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url="https://docs.google.com/document/d/report/edit",
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=_parsed(),
            url_probe_map={
                "https://docs.google.com/document/d/report/edit": ("yes", "no")
            },
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        self.assertEqual(report.stopped_at, "stage2")
        self.assertIn("Ссылка не открыта", report.stage2.issues)

    def test_full_stage3_with_mixed_accessibility(self) -> None:
        report_url = "https://docs.google.com/document/d/report/edit"
        diss_url = "https://docs.google.com/document/d/diss/edit"
        lkb_url = "https://drive.google.com/file/d/lkb"
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url=report_url,
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(dissertation_url=diss_url, lkb_url=lkb_url)
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
            url_probe_map={
                report_url: ("yes", "yes"),
                diss_url: ("yes", "yes"),
                lkb_url: ("yes", "no"),
            },
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        self.assertIsNone(report.stopped_at)
        self.assertTrue(report.stage3.executed)
        lkb_cell = next(
            c for c in report.stage3_cells if c.column_key == "lkb_url"
        )
        self.assertTrue(lkb_cell.strikethrough)
        self.assertIn("Ссылка на заключение ЛКБ не открывается", report.stage3.issues)
        self.assertIn("Ссылка на магистерский проект отсутствует", report.stage3.issues)

    def test_fio_locator_resolves_single_row(self) -> None:
        user = UserForm(
            fio="Иванов И.И.",
            phone="+77052100000",
            report_url="",
        )
        config = MagicMock()
        with _install_io_mocks(
            user=user, document=None, parsed=None, matched_rows=[5]
        ):
            report = run_row_check(config, RowLocator(fio="Иванов И.И."))
        self.assertEqual(report.row_number, 5)

    def test_format_report_includes_links_block(self) -> None:
        user = UserForm(
            fio="Гизатова Ирина Владимировна",
            phone="+77052107777",
            report_url="https://docs.google.com/document/d/report/edit",
        )
        good_doc = _doc_with_text("Промежуточный отчёт")
        parsed = _parsed(
            dissertation_url="https://docs.google.com/document/d/diss",
        )
        config = MagicMock()
        with _install_io_mocks(
            user=user,
            document=good_doc,
            parsed=parsed,
        ):
            report = run_row_check(config, RowLocator(row_number=3))
        text = format_report(report)
        self.assertIn("Магистрант: Гизатова Ирина Владимировна", text)
        self.assertIn("Извлечённые ссылки", text)
        self.assertIn("dissertation_url: https://docs.google.com/document/d/diss", text)


if __name__ == "__main__":
    unittest.main()
