"""Verify tested dependency contracts and safe portable tool setup without network."""

import hashlib
from importlib import metadata
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts import build_windows, install_tools, portable_entry
from universal_downloader.compatibility import TESTED_VERSIONS, compatibility_issues
from universal_downloader import dependencies


class ReleaseCompatibilityTests(unittest.TestCase):
    def test_current_environment_satisfies_the_tested_adapter_contract(self):
        self.assertEqual(compatibility_issues(), [])

    def test_untested_and_missing_packages_are_reported(self):
        def version(name):
            if name == "yt-dlp-ejs":
                raise metadata.PackageNotFoundError(name)
            return "2099.1" if name == "yt-dlp" else TESTED_VERSIONS[name]
        with patch("universal_downloader.compatibility.metadata.version", side_effect=version):
            issues = compatibility_issues()
        self.assertTrue(any("yt-dlp 2099.1 is untested" in issue for issue in issues))
        self.assertTrue(any("yt-dlp-ejs is missing" in issue for issue in issues))

    def test_calendar_version_metadata_normalization_is_accepted(self):
        with patch("universal_downloader.compatibility.metadata.version", side_effect=lambda name: "2026.08.19" if name == "yt-dlp" else TESTED_VERSIONS[name]):
            self.assertEqual(compatibility_issues(), [])

    def test_an_upstream_internal_api_change_is_reported(self):
        with patch("yt_dlp.extractor.twitter.TwitterIE._extract_status", None):
            self.assertTrue(any("TwitterIE._extract_status" in issue for issue in compatibility_issues()))


class PortableToolTests(unittest.TestCase):
    def test_standalone_settings_and_cleanup_do_not_download_runtime_tools(self):
        for arguments in (("--show-preferences",), ("--reset-preferences",), ("--output", "folder", "--save-preferences"), ("--clear-resume",), ("--doctor",)):
            with self.subTest(arguments=arguments), patch.object(portable_entry.sys, "argv", ["unidown.exe", *arguments]), patch.object(portable_entry, "compatibility_issues", return_value=[]), patch.object(portable_entry, "find_ffmpeg", return_value=None), patch.object(portable_entry, "find_js_runtime", return_value=None), patch.object(portable_entry, "install_tools") as install, patch.object(portable_entry, "main", return_value=0) as cli:
                self.assertEqual(portable_entry.portable_main(), 0)
                install.assert_not_called()
                cli.assert_called_once()

    def test_portable_media_command_still_prepares_missing_runtime(self):
        with patch.object(portable_entry.sys, "argv", ["unidown.exe", "https://example.com/video", "--save-preferences"]), patch.object(portable_entry, "compatibility_issues", return_value=[]), patch.object(portable_entry, "find_ffmpeg", return_value=None), patch.object(portable_entry, "install_tools", return_value=0) as install, patch.object(portable_entry, "main", return_value=0):
            self.assertEqual(portable_entry.portable_main(), 0)
            install.assert_called_once()

    def test_repacked_archive_excludes_downloaded_tools_and_user_media(self):
        with tempfile.TemporaryDirectory(prefix="unidown-archive-test-") as temporary:
            root = Path(temporary)
            bundle = root / "portable"
            for name in ("unidown.exe", "_internal/library.bin", "licenses/notice.txt", ".tools/ffmpeg.exe", "downloads/private.mp4"):
                path = bundle / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            archive = root / "portable.zip"
            build_windows.write_archive(bundle, archive)
            with ZipFile(archive) as package:
                self.assertEqual(set(package.namelist()), {"portable/unidown.exe", "portable/_internal/library.bin", "portable/licenses/notice.txt"})

    def test_frozen_executable_searches_its_own_directory_instead_of_another_checkout(self):
        with patch.object(dependencies.sys, "frozen", True, create=True), patch.object(dependencies, "PROJECT_ROOT", Path("portable")):
            self.assertEqual(dependencies._tool_roots(), [Path("portable")])

    def package(self, files):
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as package:
            for name, contents in files.items():
                package.writestr(name, contents)
        return buffer.getvalue()

    def test_only_expected_binaries_and_licenses_are_installed(self):
        data = self.package({"build/bin/ffmpeg.exe": b"fixture-executable", "build/LICENSE": b"fixture-license", "../../escape.txt": b"unwanted"})
        with tempfile.TemporaryDirectory(prefix="unidown-installer-test-") as temporary:
            destination = Path(temporary) / "tools"
            with patch("scripts.install_tools._fetch", return_value=io.BytesIO(data)):
                install_tools.install_asset("https://example.com/package.zip", hashlib.sha256(data).hexdigest(), destination, {"ffmpeg.exe"})
            self.assertEqual((destination / "ffmpeg.exe").read_bytes(), b"fixture-executable")
            self.assertEqual((destination / "LICENSE").read_bytes(), b"fixture-license")
            self.assertFalse((Path(temporary) / "escape.txt").exists())
            self.assertFalse(list(destination.glob(".unidown-install-*")))

    def test_checksum_mismatch_never_overwrites_existing_tools(self):
        data = self.package({"ffmpeg.exe": b"corrupt-or-replaced"})
        with tempfile.TemporaryDirectory(prefix="unidown-installer-test-") as temporary:
            destination = Path(temporary)
            (destination / "ffmpeg.exe").write_bytes(b"working")
            with patch("scripts.install_tools._fetch", return_value=io.BytesIO(data)), self.assertRaisesRegex(RuntimeError, "SHA-256 does not match"):
                install_tools.install_asset("https://example.com/package.zip", "0" * 64, destination, {"ffmpeg.exe"})
            self.assertEqual((destination / "ffmpeg.exe").read_bytes(), b"working")

    def test_canceled_copy_keeps_the_previous_executable(self):
        data = self.package({"ffmpeg.exe": b"replacement"})

        def canceled(source, output):
            output.write(b"partial")
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory(prefix="unidown-installer-test-") as temporary:
            destination = Path(temporary)
            (destination / "ffmpeg.exe").write_bytes(b"working")
            with patch("scripts.install_tools._fetch", return_value=io.BytesIO(data)), patch("scripts.install_tools.shutil.copyfileobj", side_effect=canceled), self.assertRaises(KeyboardInterrupt):
                install_tools.install_asset("https://example.com/package.zip", hashlib.sha256(data).hexdigest(), destination, {"ffmpeg.exe"})
            self.assertEqual((destination / "ffmpeg.exe").read_bytes(), b"working")
            self.assertFalse(list(destination.glob(".unidown-install-*")))

    def test_ambiguous_binaries_are_rejected_before_installation(self):
        data = self.package({"one/ffmpeg.exe": b"one", "two/ffmpeg.exe": b"two"})
        with tempfile.TemporaryDirectory(prefix="unidown-installer-test-") as temporary:
            destination = Path(temporary) / "not-created"
            with patch("scripts.install_tools._fetch", return_value=io.BytesIO(data)), self.assertRaisesRegex(RuntimeError, "exactly one"):
                install_tools.install_asset("https://example.com/package.zip", hashlib.sha256(data).hexdigest(), destination, {"ffmpeg.exe"})
            self.assertFalse(destination.exists())

    def test_each_tool_uses_an_exact_publisher_release_and_checksum(self):
        for tool in install_tools.TOOLS.values():
            self.assertIn("/releases/download/", tool["url"])
            self.assertNotIn("latest", tool["url"])
            self.assertRegex(tool["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
