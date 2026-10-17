"""Install portable Windows tools and verify their published SHA-256 checksums."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from universal_downloader.dependencies import find_ffmpeg, find_js_runtime

ROOT = Path(__file__).resolve().parents[1]


def _fetch(url: str):
    return urlopen(Request(url, headers={"User-Agent": "universal-downloader-setup"}), timeout=60)


def install_asset(repository: str, match, destination: Path, binaries: set[str]) -> None:
    with _fetch(f"https://api.github.com/repos/{repository}/releases/latest") as response:
        release = json.load(response)
    assets = [asset for asset in release["assets"] if match(asset["name"])]
    if len(assets) != 1:
        raise RuntimeError(f"The {repository} release does not contain the expected package.")
    asset = assets[0]
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"The {repository} release has no SHA-256 checksum; installation stopped.")
    expected = digest.removeprefix("sha256:")
    url = asset["browser_download_url"]
    print(f"Downloading {asset['name']} ({asset['size'] / 1_048_576:.0f} MiB)...", flush=True)
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
            for name, entry in selected.items():
                with package.open(entry) as source, (destination / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
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
            install_asset(
                "GyanD/codexffmpeg", lambda name: name.endswith("-essentials_build.zip"),
                ROOT / ".tools/ffmpeg/bin", {"ffmpeg.exe", "ffprobe.exe"},
            )
        else:
            print("FFmpeg and ffprobe are already available.")
        runtime = find_js_runtime()
        if runtime is None or not (ROOT / ".tools/deno/deno.exe").is_file():
            install_asset(
                "denoland/deno", lambda name: name == "deno-x86_64-pc-windows-msvc.zip",
                ROOT / ".tools/deno", {"deno.exe"},
            )
        else:
            print("The portable JavaScript runtime is already available.")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Tool installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
