"""Release updates: only newer verified releases are staged; installing keeps everything else."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import zipfile

from universal_downloader import updates

NAME, REPOSITORY, EXECUTABLE = "clearspace", "mighelsterzkdev/clearspace", "clearspace.exe"


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self._url = url

    def geturl(self):
        return self._url


def package(version: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name if name.startswith("/") or name.startswith("..") else f"{NAME}-{version}-windows-x64/{name}", data)
    return buffer.getvalue()


class Server:
    """Serves one release the way GitHub does; records every address it is asked for."""

    def __init__(self, version="0.6.0", files=None, assets=None, digest=None):
        archive_name = f"{NAME}-{version}-windows-x64.zip"
        self.archive = package(version, files or {EXECUTABLE: b"new", "_internal/base.dll": b"new"})
        self.requests = []
        self.routes = {
            f"https://api.github.com/repos/{REPOSITORY}/releases/latest": json.dumps({
                "tag_name": f"v{version}", "assets": [{"name": name} for name in (assets or (archive_name, "SHA256SUMS.txt"))]}).encode(),
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/SHA256SUMS.txt":
                f"{digest or hashlib.sha256(self.archive).hexdigest()}  {archive_name}\n".encode(),
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/{archive_name}": self.archive,
        }

    def __call__(self, url, agent):
        updates._check_address(url)
        self.requests.append(url)
        if url not in self.routes:
            raise OSError("404")
        return Response(self.routes[url], url)


def wait(updater, *statuses):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = updater.snapshot()
        if state["status"] in statuses:
            return state
        time.sleep(0.01)
    raise AssertionError(f"Expected {statuses}: {updater.snapshot()}")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="unidown-update-test-")
        self.root = Path(self.temporary.name)
        self.install = self.root / "app"
        self.install.mkdir()
        (self.install / EXECUTABLE).write_bytes(b"old")

    def tearDown(self):
        self.temporary.cleanup()

    def updater(self, server, version="0.5.0", clock=time.time, install=True):
        return updates.Updater(name=NAME, repository=REPOSITORY, version=version, executable=EXECUTABLE,
                               state_file=self.root / "updates.json", install_dir=self.install if install else None,
                               opener=server, clock=clock)

    def test_versions(self):
        self.assertEqual(updates.parse_version("v0.12.3"), (0, 12, 3))
        self.assertGreater(updates.parse_version("0.10.0"), updates.parse_version("0.9.9"))
        for text in ("latest", "v1.2", "1.2.3-beta", None, 7):
            self.assertIsNone(updates.parse_version(text))

    def test_only_github_release_addresses_are_used(self):
        for url in ("http://github.com/x", "https://example.com/a.zip", "https://github.com.evil.example/a.zip", "file:///C:/a.zip"):
            with self.assertRaises(updates.UpdateError):
                updates._check_address(url)

    def test_newer_release_is_offered_and_checked_at_most_daily(self):
        server = Server()
        updater = self.updater(server)
        updater.check()
        state = wait(updater, "available")
        self.assertEqual(state["version"], "0.6.0")
        self.assertEqual(updater.release_page(), f"https://github.com/{REPOSITORY}/releases/tag/v0.6.0")
        again = self.updater(server)
        again.check()
        wait(again, "available")
        self.assertEqual(len(server.requests), 1)

    def test_current_version_and_releases_without_archives_are_not_offered(self):
        updater = self.updater(Server(version="0.5.0"))
        updater.check()
        wait(updater, "idle")
        updater.check(manual=True)
        wait(updater, "current")
        (self.root / "updates.json").unlink()
        updater = self.updater(Server(assets=("SHA256SUMS.txt",)))
        updater.check()
        self.assertIsNone(wait(updater, "idle")["version"])

    def test_dismissed_version_returns_only_on_manual_check(self):
        server = Server()
        updater = self.updater(server)
        updater.check()
        wait(updater, "available")
        updater.dismiss()
        later = self.updater(server)
        later.check()
        wait(later, "idle")
        later.check(manual=True)
        self.assertEqual(wait(later, "available")["version"], "0.6.0")

    def test_failed_check_stays_quiet_unless_manual(self):
        def offline(url, agent):
            raise OSError("offline")
        updater = self.updater(offline)
        updater.check()
        self.assertIsNone(wait(updater, "idle")["error"])
        updater.check(manual=True)
        self.assertIn("connection", wait(updater, "failed")["error"])

    def test_running_from_source_never_updates(self):
        updater = self.updater(Server(), install=False)
        updater.check()
        self.assertEqual(updater.snapshot()["status"], "unsupported")
        with self.assertRaises(updates.UpdateError):
            updater.download()

    def test_verified_release_is_staged(self):
        updater = self.updater(Server())
        updater.check()
        wait(updater, "available")
        updater.download()
        wait(updater, "ready")
        staged = self.install / ".update" / f"{NAME}-0.6.0-windows-x64"
        self.assertEqual((staged / EXECUTABLE).read_bytes(), b"new")
        self.assertFalse((self.install / ".update" / f"{NAME}-0.6.0-windows-x64.zip").exists())
        self.assertEqual((self.install / EXECUTABLE).read_bytes(), b"old")
        with patch.object(updates, "_launch") as launch:
            updater.install()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[:2], (staged / EXECUTABLE, updates.FINISH_FLAG))

    def test_mismatched_or_unsafe_archives_are_discarded(self):
        for server in (Server(digest="0" * 64), Server(files={"../escape.txt": b"x", EXECUTABLE: b"new"}), Server(files={"_internal/a": b"x"})):
            (self.root / "updates.json").unlink(missing_ok=True)
            updater = self.updater(server)
            updater.check()
            wait(updater, "available")
            updater.download()
            self.assertTrue(wait(updater, "failed")["error"])
            self.assertFalse((self.install / ".update").exists())
            self.assertFalse((self.root / "escape.txt").exists())
            with self.assertRaises(updates.UpdateError):
                updater.install()


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="unidown-finish-test-")
        self.target = Path(self.temporary.name) / "app"
        (self.target / "_internal").mkdir(parents=True)
        (self.target / EXECUTABLE).write_bytes(b"old")
        (self.target / "_internal/old.dll").write_bytes(b"old")
        (self.target / ".tools").mkdir()
        (self.target / ".tools/ffmpeg.exe").write_bytes(b"tool")
        (self.target / "downloads").mkdir()
        (self.target / "downloads/video.mp4").write_bytes(b"mine")
        self.staged = self.target / ".update" / f"{NAME}-0.6.0-windows-x64"
        (self.staged / "_internal").mkdir(parents=True)
        (self.staged / EXECUTABLE).write_bytes(b"new")
        (self.staged / "_internal/new.dll").write_bytes(b"new")
        (self.staged / "READ-ME.txt").write_text("new")
        self.launched = []

    def tearDown(self):
        self.temporary.cleanup()

    def test_replaces_shipped_entries_and_keeps_everything_else(self):
        self.assertEqual(updates.finish(["0"], EXECUTABLE, staged=self.staged, launch=self.launched.append), 0)
        self.assertEqual((self.target / EXECUTABLE).read_bytes(), b"new")
        self.assertEqual(sorted(path.name for path in (self.target / "_internal").iterdir()), ["new.dll"])
        self.assertTrue((self.target / "READ-ME.txt").is_file())
        self.assertEqual((self.target / ".tools/ffmpeg.exe").read_bytes(), b"tool")
        self.assertEqual((self.target / "downloads/video.mp4").read_bytes(), b"mine")
        self.assertEqual(self.launched, [self.target / EXECUTABLE])

    def test_failure_restores_the_previous_version(self):
        copy = updates.shutil.copy2

        def broken(source, destination, **kwargs):
            # The executable is the last entry, so earlier ones were already replaced.
            if Path(source).name == EXECUTABLE:
                raise OSError("disk full")
            return copy(source, destination, **kwargs)
        with patch.object(updates.shutil, "copy2", broken), patch.object(updates, "_retry", lambda action, attempts=20: action()):
            self.assertEqual(updates.finish(["0"], EXECUTABLE, staged=self.staged, launch=self.launched.append), 1)
        self.assertEqual((self.target / EXECUTABLE).read_bytes(), b"old")
        self.assertEqual(sorted(path.name for path in (self.target / "_internal").iterdir()), ["old.dll"])
        self.assertFalse((self.target / "READ-ME.txt").exists())
        self.assertEqual(self.launched, [self.target / EXECUTABLE])

    def test_only_a_staged_copy_can_install(self):
        elsewhere = Path(self.temporary.name) / "other"
        elsewhere.mkdir()
        (elsewhere / EXECUTABLE).write_bytes(b"x")
        self.assertEqual(updates.finish(["0"], EXECUTABLE, staged=elsewhere, launch=self.launched.append), 2)
        self.assertEqual((self.target / EXECUTABLE).read_bytes(), b"old")
        self.assertEqual(self.launched, [])


if __name__ == "__main__":
    unittest.main()
