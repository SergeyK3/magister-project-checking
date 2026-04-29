"""Тесты канонического снимка проекта (docs/contract_project_snapshot.md)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import RowCheckReport, Stage4Result, StageResult
from magister_checking.project_snapshot import (
    PROJECT_SNAPSHOT_SCHEMA_VERSION,
    build_project_snapshot,
    project_snapshot_from_json_str,
    project_snapshot_to_json,
)
from magister_checking.project_card_pipeline import generate_project_card_pdf
from magister_checking.snapshot_render import (
    escape_tg_html,
    render_commission_plaintext,
    render_commission_telegram_html,
    render_spravka_telegram,
    render_spravka_telegram_html,
)


class BuildSnapshotTests(unittest.TestCase):
    def test_schema_version_and_provenance_fingerprint(self) -> None:
        r = RowCheckReport(
            fio="Петров П.П.",
            row_number=2,
            source_fingerprint="abc123",
            stage1=StageResult("stage1", issues=[], passed=True, executed=True),
        )
        u = UserForm(fio="Петров П.П.", group_name="МВ-21")
        s = build_project_snapshot(user=u, report=r, extra_values={})
        self.assertEqual(s.schema_version, PROJECT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(s.provenance.source_fingerprint, "abc123")
        self.assertIsNone(s.stopped_at)

    def test_json_roundtrip_shape(self) -> None:
        r = RowCheckReport(
            fio="Иванов",
            row_number=1,
            stage4=Stage4Result(executed=True, passed=True, pages_total=40, sources_count=25),
        )
        s = build_project_snapshot(
            user=UserForm(fio="Иванов"),
            report=r,
            extra_values={"project_folder_url": "https://x"},
            fill_status="OK",
        )
        raw = project_snapshot_to_json(s)
        data = json.loads(raw)
        self.assertEqual(data["schema_version"], PROJECT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(data["fill_status"], "OK")
        self.assertEqual(data["identity"]["fio"], "Иванов")
        self.assertIn("phases", data)

    def test_json_roundtrip_from_file_format(self) -> None:
        r = RowCheckReport(
            fio="Иванов",
            row_number=3,
            stage4=Stage4Result(executed=True, passed=True, pages_total=40, sources_count=25),
        )
        s = build_project_snapshot(
            user=UserForm(fio="Иванов"),
            report=r,
            extra_values={},
            fill_status="OK",
        )
        raw = project_snapshot_to_json(s)
        s2 = project_snapshot_from_json_str(raw)
        self.assertEqual(s2.identity.fio, s.identity.fio)
        self.assertEqual(s2.row_number, s.row_number)
        self.assertEqual(len(s2.phases), len(s.phases))


class RenderSpravkaTests(unittest.TestCase):
    def test_unchanged_message(self) -> None:
        r = RowCheckReport(
            fio="И.И.",
            row_number=5,
            unchanged=True,
            source_fingerprint="x",
        )
        s = build_project_snapshot(
            user=UserForm(fio="И.И."),
            report=r,
        )
        t = render_spravka_telegram(s, applied=False)
        self.assertIn("не менялись", t)
        self.assertNotIn("dry-run", t)
        h = render_spravka_telegram_html(s, applied=False)
        self.assertIn("<b>", h)

    def test_escape_tg_html(self) -> None:
        self.assertIn("&amp;", escape_tg_html("A & B"))
        self.assertIn("&lt;", escape_tg_html("a < b"))

    def test_spravka_html_stage3_note_under_link_and_stage4_at_bottom(self) -> None:
        from magister_checking.bot.row_pipeline import (
            Stage3CellUpdate,
            Stage4Result,
            StageResult,
        )

        link_issue = "«Заключение ЛКБ»: неверный тип ссылки — тест."
        fmt_issue = "не соответствует. Найдено: A. Нужно: B."
        r = RowCheckReport(
            fio="Иванов",
            row_number=3,
            stage1=StageResult("stage1", executed=True, passed=True),
            stage2=StageResult("stage2", executed=True, passed=True),
            stage3=StageResult(
                "stage3",
                executed=True,
                passed=True,
                issues=[link_issue],
            ),
            stage4=Stage4Result(
                executed=True,
                passed=True,
                pages_total=10,
                sources_count=5,
                compliance=False,
                issues=[fmt_issue],
            ),
            stage3_cells=[
                Stage3CellUpdate(
                    column_key="lkb_url",
                    value="https://drive.google.com/drive/folders/x",
                    strikethrough=True,
                ),
            ],
        )
        s = build_project_snapshot(user=UserForm(fio="Иванов"), report=r, extra_values={})
        h = render_spravka_telegram_html(s, applied=True)
        pos_links = h.index("Ссылки")
        pos_lkb_line = h.index("lkb_url")
        pos_note = h.index(link_issue)
        pos_fmt = h.index(fmt_issue)
        pos_recorded = h.index("Запись в лист")
        self.assertLess(pos_links, pos_lkb_line)
        self.assertLess(pos_lkb_line, pos_note)
        self.assertLess(pos_note, pos_recorded)
        self.assertLess(pos_recorded, pos_fmt)

    def test_commission_plaintext_four_cells_same_reason_still_four_lines(self) -> None:
        """Один текст во всех четырёх колонках — всё равно четыре строки без «слили в один блок»."""

        msg = "Одна общая причина для всех полей."
        extras = {
            "project_folder_url": f"Папка проекта: {msg}",
            "lkb_url": f"ЛКБ: {msg}",
            "dissertation_url": f"Диссертация (ссылка в промежуточном отчёте): {msg}",
            "publication_url": f"Публикация или статья: {msg}",
        }
        r = RowCheckReport(fio="Иванов", row_number=1)
        u = UserForm(
            fio="Иванов",
            report_url="https://docs.google.com/document/d/abc/edit",
        )
        s = build_project_snapshot(user=u, report=r, extra_values=extras)
        txt = render_commission_plaintext(s)
        self.assertEqual(txt.count(msg), 4)
        self.assertNotIn("одинаковое значение в листе", txt)
        self.assertNotIn("Папка проекта: Папка проекта:", txt)

    def test_commission_plaintext_collapses_url_line_breaks(self) -> None:
        r = RowCheckReport(fio="Иванов", row_number=1)
        u = UserForm(
            fio="Иванов",
            report_url=(
                "https://docs.google.com/document/d/x/edit?usp\n=sharing"
            ),
        )
        s = build_project_snapshot(user=u, report=r, extra_values={})
        txt = render_commission_plaintext(s)
        self.assertIn("usp=sharing", txt)
        self.assertNotIn("\n=sharing", txt)

    def test_commission_html_has_sections(self) -> None:
        r = RowCheckReport(
            fio="Тест",
            row_number=2,
        )
        s = build_project_snapshot(
            user=UserForm(fio="Тест", group_name="G"),
            report=r,
        )
        h = render_commission_telegram_html(s)
        self.assertIn("<b>Сведения", h)
        self.assertIn("Магистрант", h)


@patch("magister_checking.project_card_pipeline.sync_registration_dashboard")
@patch("magister_checking.project_card_pipeline.save_user_to_row_with_extras")
@patch("magister_checking.project_card_pipeline.build_sheet_enrichment")
@patch("magister_checking.project_card_pipeline.load_user")
@patch("magister_checking.project_card_pipeline.get_spreadsheet")
class ProjectCardSnapshotTests(unittest.TestCase):
    def test_pdf_uses_commission_render(
        self,
        m_gs: MagicMock,
        m_lu: MagicMock,
        m_bse: MagicMock,
        m_save: MagicMock,
        m_dash: MagicMock,
    ) -> None:
        cfg = MagicMock()
        cfg.worksheet_name = "Регистрация"
        m_ws = MagicMock()
        m_gs.return_value.worksheet.return_value = m_ws
        m_ws.row_values.return_value = ["x", "fio", "g"]
        m_lu.return_value = UserForm(
            fio="Тестов Т.Т.",
            group_name="G1",
            report_url="https://docs.google.com/document/d/x/edit",
            fill_status="OK",
        )
        m_bse.return_value = {
            "project_folder_url": "https://drive/folder",
            "pages_total": "10",
            "sources_count": "5",
            "compliance": "соответствует",
        }
        with patch(
            "magister_checking.project_card_pipeline._project_card_font_name",
            return_value="Helvetica",
        ), patch(
            "magister_checking.project_card_pipeline.try_upload_project_snapshot_json",
        ):
            out = generate_project_card_pdf(config=cfg, row_number=2)
        self.assertGreater(len(out.pdf_bytes), 200)
        self.assertTrue(out.pdf_name.endswith(".pdf"))
        self.assertIn("Карточка", out.pdf_name)
