"""Install portable Windows tools and verify their published SHA-256 checksums."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import socket
import ssl
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

from universal_downloader.dependencies import PROJECT_ROOT, find_ffmpeg, find_js_runtime
from universal_downloader.errors import DownloadError

ROOT = PROJECT_ROOT
TOOLS = {
    "ffmpeg": {
        "url": "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
        "sha256": "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
        "destination": ".tools/ffmpeg/bin",
        "binaries": {"ffmpeg.exe", "ffprobe.exe"},
    },
    "deno": {
        "url": "https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip",
        "sha256": "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238",
        "destination": ".tools/deno",
        "binaries": {"deno.exe"},
    },
}


def _fetch(url: str):
    headers = {"User-Agent": "universal-downloader-setup"}
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname == "api.github.com" and (token := os.environ.get("GITHUB_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    return urlopen(Request(url, headers=headers), timeout=60)


class ToolSetupError(DownloadError):
    """A setup failure that can be shown in either the desktop app or terminal."""


def install_asset(url: str, expected: str, destination: Path, binaries: set[str], *, progress=None, name="tools") -> None:
    tool_label = name
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError("A pinned SHA-256 checksum is required before installing tools.")
    print(f"Downloading {url.rsplit('/', 1)[-1]}...", flush=True)
    if progress:
        progress({"message": f"Downloading {name}…", "downloaded_bytes": 0, "total_bytes": None})
    with tempfile.TemporaryDirectory(prefix="unidown-") as temporary:
        archive = Path(temporary) / "tools.zip"
        checksum = hashlib.sha256()
        with _fetch(url) as response, archive.open("wb") as output:
            try:
                total = int(response.headers.get("Content-Length", "0")) or None
            except (AttributeError, TypeError, ValueError):
                total = None
            received = 0
            while chunk := response.read(1_048_576):
                checksum.update(chunk)
                output.write(chunk)
                received += len(chunk)
                if progress:
                    suffix = f" {min(100, int(received * 100 / total))}%" if total and total > 0 else ""
                    progress({"message": f"Downloading {name}{suffix}…", "downloaded_bytes": received, "total_bytes": total})
        if progress:
            progress({"message": f"Verifying {name}…", "downloaded_bytes": received, "total_bytes": total})
        if checksum.hexdigest() != expected:
            raise RuntimeError("SHA-256 does not match the published checksum. The file will not be installed.")
        # Copy only the expected binaries and licenses; never extract arbitrary archive paths.
        with ZipFile(archive) as package:
            selected = {}
            for name in binaries:
                matches = [entry for entry in package.infolist() if Path(entry.filename).name == name]
                if len(matches) != 1:
                    raise RuntimeError(f"The package must contain exactly one copy of {name}.")
                selected[name] = matches[0]
            if progress:
                progress({"message": f"Installing {tool_label}…", "downloaded_bytes": received, "total_bytes": total})
            destination.mkdir(parents=True, exist_ok=True)
            # Keep a canceled installation from replacing a working executable
            # with a partially copied one. Stage each file on its target volume.
            for name, entry in selected.items():
                with tempfile.TemporaryDirectory(prefix=".unidown-install-", dir=destination) as staging:
                    staged = Path(staging) / name
                    with package.open(entry) as source, staged.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    os.replace(staged, destination / name)
            for entry in package.infolist():
                name = Path(entry.filename).name
                if not entry.is_dir() and name.lower().startswith(("license", "copying")):
                    with package.open(entry) as source, (destination / name).open("wb") as output:
                        shutil.copyfileobj(source, output)
        (destination / "SOURCE.txt").write_text(
            f"Source: {url}\nSHA-256: {expected}\n"
            "This dependency has its own license. See the upstream release.\n", encoding="utf-8",
        )
    print(f"Installed to {destination}", flush=True)


def _installation_error(name: str, error: Exception) -> ToolSetupError:
    if isinstance(error, HTTPError):
        return ToolSetupError(f"The publisher could not provide {name} (HTTP {error.code}). Retry installation later.")
    if isinstance(error, PermissionError):
        return ToolSetupError(f"Could not install {name} in this folder. Extract the app to a writable folder and retry.")
    if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(error, URLError) and isinstance(error.reason, (TimeoutError, socket.timeout)):
        return ToolSetupError(f"The {name} download timed out. Retry installation.")
    if isinstance(error, ssl.SSLError) or isinstance(error, URLError) and isinstance(error.reason, ssl.SSLError):
        return ToolSetupError(f"The secure connection to the {name} publisher could not be verified. Check your system date and connection, then retry.")
    if isinstance(error, (URLError, ConnectionError)):
        return ToolSetupError(f"Could not connect to the {name} publisher. Check your connection and retry.")
    if isinstance(error, RuntimeError) and "SHA-256" in str(error):
        return ToolSetupError(f"The {name} download failed its integrity check. Retry installation.")
    if isinstance(error, (BadZipFile, ValueError, RuntimeError)):
        return ToolSetupError(f"The {name} package could not be installed. Retry installation or download a fresh application release.")
    return ToolSetupError(f"Could not save the {name} tools. Check that the application folder is writable and has enough free space.")


def _prepare_asset(name: str, tool: dict, progress=None) -> None:
    for attempt in range(1, 4):
        try:
            install_asset(tool["url"], tool["sha256"], ROOT / tool["destination"], tool["binaries"], progress=progress, name=name)
            return
        except (OSError, RuntimeError, ValueError, BadZipFile) as error:
            transient = (
                isinstance(error, HTTPError) and error.code in (408, 429, 500, 502, 503, 504)
                or isinstance(error, (TimeoutError, ConnectionError))
                or isinstance(error, URLError) and not isinstance(error, HTTPError) and not isinstance(error.reason, ssl.SSLError)
            )
            if transient and attempt < 3:
                if progress:
                    progress({"message": f"Retrying {name} download ({attempt + 1}/3)…", "downloaded_bytes": 0, "total_bytes": None})
                time.sleep(attempt)
                continue
            raise _installation_error(name, error) from error


def prepare_tools(*, progress=None) -> None:
    """Prepare pinned tools, preserving a useful failure reason for the caller."""
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ToolSetupError("Automatic tool setup supports Windows x64. Install FFmpeg and a supported JavaScript runtime manually on this system.")
    if find_ffmpeg() is None:
        _prepare_asset("FFmpeg", TOOLS["ffmpeg"], progress)
    else:
        print("FFmpeg and ffprobe are already available.")
    runtime = find_js_runtime()
    if runtime is None or not (ROOT / ".tools/deno/deno.exe").is_file():
        _prepare_asset("Deno", TOOLS["deno"], progress)
    else:
        print("The portable JavaScript runtime is already available.")


def main() -> int:
    try:
        prepare_tools()
    except KeyboardInterrupt:
        print("\nCanceled.", file=sys.stderr)
        return 130
    except DownloadError as error:
        print(f"Tool installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
