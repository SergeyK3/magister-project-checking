"""Тесты выбора последнего снимка и HTML-обёртки."""

from __future__ import annotations

import re
import unittest

from magister_checking.drive_latest_snapshot import (
    wrap_commission_html_for_browser,
)


class DriveLatestSnapshotNamingTests(unittest.TestCase):
    def test_row_prefixed_name_matches(self) -> None:
        name_re = re.compile(rf"^project_snapshot_r{5}_\S+\.json$", re.I)
        self.assertTrue(
            bool(
                name_re.match(
                    "project_snapshot_r5_LastName_TS.json",
                ),
            ),
        )


class WrapCommissionHtmlTests(unittest.TestCase):
    def test_wrap_includes_charset(self) -> None:
        out = wrap_commission_html_for_browser('<b>Hi</b>\nline')
        self.assertIn("charset=", out.lower())
        self.assertIn("<b>Hi</b>", out)


if __name__ == "__main__":
    unittest.main()
