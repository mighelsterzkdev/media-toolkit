"""Updates from this app's own GitHub releases.

Only the fixed repository is used, and an archive is installed only when it
matches the SHA256SUMS.txt published with the same release. Installing replaces
only the entries the new release ships: tools, downloads and anything else kept
beside the program stay where they are. This module is shared by Clearspace and
Media Toolkit and only uses the standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

CHECK_INTERVAL = 24 * 60 * 60
MAX_ARCHIVE = 700 * 1024 * 1024
MAX_METADATA = 1024 * 1024
STAGING = ".update"
FINISH_FLAG = "--finish-update"
_HOSTS = frozenset(("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"))
_VERSION = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")


class UpdateError(Exception):
    """Messages are written for display."""


def parse_version(text) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(text.strip()) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def _check_address(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _HOSTS:
        raise UpdateError("The update came from an unexpected address.")


def _open(url: str, user_agent: str):
    _check_address(url)
    response = urlopen(Request(url, headers={"User-Agent": user_agent, "Accept": "application/vnd.github+json, */*"}), timeout=20)
    try:
        _check_address(response.geturl())
    except UpdateError:
        response.close()
        raise
    return response


def _read(response, limit: int) -> bytes:
    with response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("The update information is too large.")
    return data


def _without_pyinstaller_environment() -> dict:
    # A frozen child must start as its own application, not as part of this one.
    return dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")


def _launch(executable: Path, *arguments: str) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(executable), *arguments], cwd=str(executable.parent), env=_without_pyinstaller_environment(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)


class Updater:
    """Checks, downloads and stages one release; the staged program installs it after this one exits."""

    def __init__(self, *, name: str, repository: str, version: str, executable: str, state_file: Path,
                 install_dir: Path | None, opener=_open, clock=time.time):
        self.name, self.repository, self.version, self.executable = name, repository, version, executable
        self._state_file, self._install_dir = Path(state_file), install_dir
        self._opener, self._clock = opener, clock
        self._lock = threading.Lock()
        self._release: dict | None = None
        self._state = {"status": "unsupported" if install_dir is None else "idle", "version": None, "progress": None, "error": None}

    # State -----------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _load(self) -> dict:
        try:
            stored = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return stored if isinstance(stored, dict) and stored.get("version") == 1 else {}

    def _save(self, **values) -> None:
        stored = {**self._load(), **values, "version": 1}
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(stored), encoding="utf-8")
        except OSError:
            pass

    def _release_for(self, version: str) -> dict:
        tag = f"v{version}"
        archive = f"{self.name}-{version}-windows-x64.zip"
        base = f"https://github.com/{self.repository}/releases"
        return {"version": version, "archive": archive, "archive_url": f"{base}/download/{tag}/{archive}",
                "sums_url": f"{base}/download/{tag}/SHA256SUMS.txt", "page": f"{base}/tag/{tag}"}

    # Checking --------------------------------------------------------------

    def check(self, *, manual: bool = False) -> None:
        """Startup checks reach GitHub at most once a day; a manual check always does."""
        with self._lock:
            if self._state["status"] in ("unsupported", "checking", "downloading", "ready"):
                return
            self._state.update(status="checking", error=None)
        threading.Thread(target=self._check, args=(manual,), name=f"{self.name}-update-check", daemon=True).start()

    def _check(self, manual: bool) -> None:
        try:
            stored = self._load()
            latest = stored.get("latest")
            fresh = isinstance(stored.get("checked_at"), (int, float)) and 0 <= self._clock() - stored["checked_at"] < CHECK_INTERVAL
            if manual or not fresh:
                latest = self._fetch_latest()
                self._save(checked_at=self._clock(), latest=latest)
            current = parse_version(self.version)
            if parse_version(latest) is None or current is None or parse_version(latest) <= current:
                self._set(status="current" if manual else "idle", version=None)
                return
            if not manual and stored.get("skipped") == latest:
                self._set(status="idle", version=None)
                return
            self._release = self._release_for(latest)
            self._set(status="available", version=latest)
        except Exception:
            self._set(status="failed" if manual else "idle", error="Could not check for updates. Check your connection and try again." if manual else None)

    def _fetch_latest(self) -> str | None:
        data = json.loads(_read(self._opener(f"https://api.github.com/repos/{self.repository}/releases/latest", self._agent()), MAX_METADATA))
        version = parse_version(data.get("tag_name")) if isinstance(data, dict) else None
        if version is None:
            return None
        text = ".".join(map(str, version))
        names = {asset.get("name") for asset in data.get("assets") or () if isinstance(asset, dict)}
        # A release without a Windows archive or checksums is never offered.
        return text if {f"{self.name}-{text}-windows-x64.zip", "SHA256SUMS.txt"} <= names else None

    def _agent(self) -> str:
        return f"{self.name}/{self.version} (+https://github.com/{self.repository})"

    def dismiss(self) -> None:
        with self._lock:
            if self._state["status"] not in ("available", "failed", "current"):
                return
            version = self._state["version"]
            self._state.update(status="idle", version=None, error=None, progress=None)
        if version:
            self._save(skipped=version)

    def release_page(self) -> str:
        with self._lock:
            release = self._release
        return release["page"] if release else f"https://github.com/{self.repository}/releases"

    # Downloading -----------------------------------------------------------

    def download(self) -> None:
        with self._lock:
            if self._state["status"] not in ("available", "failed") or self._release is None:
                raise UpdateError("There is no update to download.")
            self._state.update(status="downloading", progress=0.0, error=None)
            release = dict(self._release)
        threading.Thread(target=self._download, args=(release,), name=f"{self.name}-update-download", daemon=True).start()

    def _download(self, release: dict) -> None:
        staging = self._install_dir / STAGING
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            sums = _read(self._opener(release["sums_url"], self._agent()), MAX_METADATA).decode("utf-8", "replace")
            expected = next((line.split()[0].lower() for line in sums.splitlines()
                             if len(line.split()) == 2 and line.split()[1].lstrip("*") == release["archive"]), None)
            if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise UpdateError("The update has no checksum.")
            archive = staging / release["archive"]
            digest = hashlib.sha256()
            with self._opener(release["archive_url"], self._agent()) as response, archive.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                if total > MAX_ARCHIVE:
                    raise UpdateError("The update is too large.")
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > MAX_ARCHIVE:
                        raise UpdateError("The update is too large.")
                    digest.update(chunk)
                    output.write(chunk)
                    if total:
                        self._set(progress=min(received / total, 1.0) * 0.9)
            if digest.hexdigest() != expected:
                raise UpdateError("The downloaded update did not match its checksum.")
            bundle = staging / archive.name.removesuffix(".zip")
            _extract(archive, staging, bundle.name)
            archive.unlink()
            if not (bundle / self.executable).is_file():
                raise UpdateError("The update is incomplete.")
            self._set(status="ready", progress=1.0)
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            message = str(error) if isinstance(error, UpdateError) else (
                "This folder cannot be changed. Download the new version from the release page." if isinstance(error, PermissionError)
                else "The update could not be downloaded. Try again.")
            self._set(status="failed", progress=None, error=message)

    def install(self) -> None:
        """Start the staged program; it installs once this process has exited."""
        with self._lock:
            if self._state["status"] != "ready" or self._release is None:
                raise UpdateError("The update is not ready yet.")
            bundle = self._install_dir / STAGING / self._release["archive"].removesuffix(".zip")
        _launch(bundle / self.executable, FINISH_FLAG, str(os.getpid()))

    def remove_leftovers(self, delay: float = 5.0) -> None:
        """Remove the staging folder of an earlier update once its helper has exited."""
        if self._install_dir is None:
            return

        def remove():
            time.sleep(delay)
            with self._lock:
                busy = self._state["status"] in ("downloading", "ready")
            if not busy:
                shutil.rmtree(self._install_dir / STAGING, ignore_errors=True)

        threading.Thread(target=remove, name=f"{self.name}-update-cleanup", daemon=True).start()


def _extract(archive: Path, destination: Path, bundle: str) -> None:
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        total = sum(member.file_size for member in members)
        if total > MAX_ARCHIVE * 3:
            raise UpdateError("The update is too large.")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != bundle or ":" in member.filename:
                raise UpdateError("The update archive is not valid.")
        package.extractall(destination)


def _wait_for_exit(pid: int | None, timeout: float) -> None:
    if not pid or os.name != "nt":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        try:
            kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        finally:
            kernel32.CloseHandle(handle)


def _retry(action, attempts: int = 20):
    for attempt in range(attempts):
        try:
            return action()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def finish(arguments: list[str], executable: str, staged: Path | None = None, launch=_launch) -> int:
    """Run by the staged program: replace the installed entries, then reopen the app.

    The installation folder is derived from where this program was staged, so
    the command line never chooses which folder is changed.
    """
    staged = (staged or Path(sys.executable).resolve().parent)
    target = staged.parent.parent
    if staged.parent.name != STAGING or not (target / executable).is_file() or not (staged / executable).is_file():
        return 2
    pid = int(arguments[0]) if arguments and arguments[0].isdigit() else None
    _wait_for_exit(pid, 60)
    backup = staged.parent / "previous"
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir()
    moved, placed = [], []
    try:
        for entry in sorted(staged.iterdir(), key=lambda item: item.name):
            current = target / entry.name
            if current.exists() or current.is_symlink():
                _retry(lambda: current.rename(backup / entry.name))
                moved.append(entry.name)
            placed.append(entry.name)
            if entry.is_dir():
                shutil.copytree(entry, current)
            else:
                shutil.copy2(entry, current)
    except OSError:
        for name in reversed(placed):
            try:
                _remove(target / name)
            except OSError:
                pass
        for name in reversed(moved):
            try:
                _retry(lambda: (backup / name).rename(target / name))
            except OSError:
                pass
        launch(target / executable)
        return 1
    launch(target / executable)
    return 0
