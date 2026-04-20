"""Тесты для load_config: путь к SA-ключу vs содержимое JSON."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from magister_checking.bot.config import ConfigError, load_config


_SAMPLE_JSON = {
    "type": "service_account",
    "project_id": "demo",
    "private_key_id": "x",
    "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    "client_email": "demo@demo.iam.gserviceaccount.com",
    "client_id": "1",
}


def _base_env(overrides: dict) -> dict:
    env = {
        "TELEGRAM_BOT_TOKEN": "stub",
        "SPREADSHEET_ID": "sheet123",
        "WORKSHEET_NAME": "Регистрация",
        "PROJECT_CARD_OUTPUT_FOLDER_URL": "",
        "LOG_LEVEL": "INFO",
    }
    env.update(overrides)
    return env


class LoadConfigTests(unittest.TestCase):
    def test_path_value(self) -> None:
        with NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(_SAMPLE_JSON))
            sa_path = fh.name
        try:
            with patch.dict(
                os.environ,
                _base_env({"GOOGLE_SERVICE_ACCOUNT_JSON": sa_path}),
                clear=True,
            ):
                cfg = load_config()
            self.assertEqual(str(cfg.google_service_account_json), sa_path)
            self.assertEqual(cfg.project_card_output_folder_url, "")
        finally:
            os.unlink(sa_path)

    def test_project_card_output_folder_url(self) -> None:
        with NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(_SAMPLE_JSON))
            sa_path = fh.name
        try:
            with patch.dict(
                os.environ,
                _base_env(
                    {
                        "GOOGLE_SERVICE_ACCOUNT_JSON": sa_path,
                        "PROJECT_CARD_OUTPUT_FOLDER_URL": "https://drive.google.com/drive/folders/abc123",
                    }
                ),
                clear=True,
            ):
                cfg = load_config()
            self.assertEqual(
                cfg.project_card_output_folder_url,
                "https://drive.google.com/drive/folders/abc123",
            )
        finally:
            os.unlink(sa_path)

    def test_content_in_dedicated_var(self) -> None:
        with patch.dict(
            os.environ,
            _base_env({"GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT": json.dumps(_SAMPLE_JSON)}),
            clear=True,
        ):
            cfg = load_config()
        try:
            self.assertTrue(cfg.google_service_account_json.is_file())
            data = json.loads(cfg.google_service_account_json.read_text(encoding="utf-8"))
            self.assertEqual(data["client_email"], _SAMPLE_JSON["client_email"])
        finally:
            cfg.google_service_account_json.unlink(missing_ok=True)

    def test_content_passed_via_path_var(self) -> None:
        """GOOGLE_SERVICE_ACCOUNT_JSON, начинающийся с '{', считается JSON."""

        with patch.dict(
            os.environ,
            _base_env({"GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(_SAMPLE_JSON)}),
            clear=True,
        ):
            cfg = load_config()
        try:
            self.assertTrue(cfg.google_service_account_json.is_file())
        finally:
            cfg.google_service_account_json.unlink(missing_ok=True)

    def test_missing_required_vars(self) -> None:
        with patch.dict(os.environ, {"WORKSHEET_NAME": "x"}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                load_config(dotenv_path=Path("__missing_test_env__.env"))
        msg = str(ctx.exception)
        self.assertIn("TELEGRAM_BOT_TOKEN", msg)
        self.assertIn("SPREADSHEET_ID", msg)
        self.assertIn("GOOGLE_SERVICE_ACCOUNT_JSON", msg)

    def test_path_does_not_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            ghost = Path(tmp) / "absent.json"
            with patch.dict(
                os.environ,
                _base_env({"GOOGLE_SERVICE_ACCOUNT_JSON": str(ghost)}),
                clear=True,
            ):
                with self.assertRaises(ConfigError) as ctx:
                    load_config()
            self.assertIn("не найден", str(ctx.exception))

    def test_invalid_json_content(self) -> None:
        with patch.dict(
            os.environ,
            _base_env({"GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT": "{oops}"}),
            clear=True,
        ):
            with self.assertRaises(ConfigError) as ctx:
                load_config()
        self.assertIn("некорректно", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
