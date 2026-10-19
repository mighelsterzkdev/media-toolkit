"""Install portable Windows tools and verify their published SHA-256 checksums."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from zipfile import ZipFile

from universal_downloader.dependencies import PROJECT_ROOT, find_ffmpeg, find_js_runtime

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


def install_asset(url: str, expected: str, destination: Path, binaries: set[str]) -> None:
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError("A pinned SHA-256 checksum is required before installing tools.")
    print(f"Downloading {url.rsplit('/', 1)[-1]}...", flush=True)
    with tempfile.TemporaryDirectory(prefix="unidown-") as temporary:
        archive = Path(temporary) / "tools.zip"
        checksum = hashlib.sha256()
        with _fetch(url) as response, archive.open("wb") as output:
            while chunk := response.read(1_048_576):
                checksum.update(chunk)
                output.write(chunk)
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


def main() -> int:
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        print("Automatic installation supports Windows x64. See the manual installation steps in the README.")
        return 1
    try:
        if find_ffmpeg() is None:
            tool = TOOLS["ffmpeg"]
            install_asset(tool["url"], tool["sha256"], ROOT / tool["destination"], tool["binaries"])
        else:
            print("FFmpeg and ffprobe are already available.")
        runtime = find_js_runtime()
        if runtime is None or not (ROOT / ".tools/deno/deno.exe").is_file():
            tool = TOOLS["deno"]
            install_asset(tool["url"], tool["sha256"], ROOT / tool["destination"], tool["binaries"])
        else:
            print("The portable JavaScript runtime is already available.")
    except KeyboardInterrupt:
        print("\nCanceled.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Tool installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
