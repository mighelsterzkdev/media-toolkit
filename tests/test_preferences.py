import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from universal_downloader.errors import DownloadError
from universal_downloader.preferences import load_preferences, preferences_path, reset_preferences, save_preferences


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_missing_preferences_keep_defaults_without_creating_files(self):
        self.assertEqual(load_preferences(), {})
        self.assertFalse((self.root / "state").exists())

    def test_only_output_and_limits_round_trip_outside_downloads(self):
        values = {"output": str(self.root / "downloads"), "max_height": 720, "max_size": 1024}
        path = save_preferences(values)
        self.assertEqual(path, self.root / "state" / "preferences.json")
        self.assertEqual(load_preferences(), values)
        self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), {"version", "output", "max_height", "max_size"})
        self.assertFalse((self.root / "downloads").exists())

    def test_secrets_urls_and_unknown_settings_are_never_written(self):
        for key in ("url", "cookies", "token", "convert", "media_type"):
            with self.subTest(key=key), self.assertRaises(DownloadError):
                save_preferences({key: "secret"})
        self.assertFalse(preferences_path().exists())

    def test_atomic_failure_keeps_previous_preferences_and_cleans_own_temp(self):
        original = {"output": str(self.root / "original"), "max_height": 360}
        path = save_preferences(original)
        with patch("universal_downloader.preferences.os.replace", side_effect=PermissionError("locked")), self.assertRaises(DownloadError):
            save_preferences({"output": str(self.root / "new")})
        self.assertEqual(load_preferences(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_malformed_oversized_or_unversioned_files_fail_with_recovery_hint(self):
        path = preferences_path()
        path.parent.mkdir()
        for value in ("not json", "{}", '{"version": true}', '{"version": 1.0}', '{"version": 2}', '{"version": 1,"max_size": -1}', '{"version":1,"max_height":true}', '{"version":1,"output":"relative/path"}', '{"version":1,"cookies":"secret"}'):
            with self.subTest(value=value):
                path.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(DownloadError, "--reset-preferences"):
                    load_preferences()
        path.write_bytes(b"x" * 16_385)
        with self.assertRaises(DownloadError):
            load_preferences()

    def test_reset_removes_only_the_preferences_file(self):
        path = save_preferences({"max_size": 1024})
        other = path.parent / "downloads.sqlite3"
        other.write_bytes(b"other state")
        self.assertEqual(reset_preferences(), path)
        self.assertEqual(load_preferences(), {})
        self.assertEqual(other.read_bytes(), b"other state")


if __name__ == "__main__":
    unittest.main()
