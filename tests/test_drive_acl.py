"""Тесты для drive_acl."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from magister_checking.drive_acl import drive_file_has_anyone_with_link_permission


class DriveAclTests(unittest.TestCase):
    def test_true_when_anyone_permission_present(self) -> None:
        drive = MagicMock()
        drive.permissions.return_value.list.return_value.execute.return_value = {
            "permissions": [
                {"id": "123", "type": "user", "role": "owner"},
                {"type": "anyone", "role": "reader"},
            ]
        }
        self.assertTrue(drive_file_has_anyone_with_link_permission(drive, "file-id"))

    def test_false_when_no_anyone(self) -> None:
        drive = MagicMock()
        drive.permissions.return_value.list.return_value.execute.return_value = {
            "permissions": [{"type": "user", "role": "reader"}]
        }
        self.assertFalse(drive_file_has_anyone_with_link_permission(drive, "file-id"))

    def test_false_on_empty_file_id(self) -> None:
        drive = MagicMock()
        self.assertFalse(drive_file_has_anyone_with_link_permission(drive, ""))

    def test_false_when_permissions_list_raises(self) -> None:
        drive = MagicMock()
        drive.permissions.return_value.list.return_value.execute.side_effect = RuntimeError(
            "403"
        )
        self.assertFalse(drive_file_has_anyone_with_link_permission(drive, "x"))


if __name__ == "__main__":
    unittest.main()
