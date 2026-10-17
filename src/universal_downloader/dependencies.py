"""Find local tools without changing the system PATH."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _executable(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def find_ffmpeg() -> Path | None:
    """yt-dlp needs ffmpeg and ffprobe in the same directory."""
    directories = [Path.cwd() / ".tools/ffmpeg/bin", PROJECT_ROOT / ".tools/ffmpeg/bin"]
    installed = shutil.which("ffmpeg")
    if installed:
        directories.append(Path(installed).parent)
    for directory in directories:
        if all((directory / _executable(name)).is_file() for name in ("ffmpeg", "ffprobe")):
            return directory.resolve()
    return None


def find_js_runtime() -> tuple[str, Path] | None:
    candidates = [
        ("deno", Path.cwd() / ".tools/deno" / _executable("deno")),
        ("deno", PROJECT_ROOT / ".tools/deno" / _executable("deno")),
    ]
    for name in ("deno", "node"):
        installed = shutil.which(name)
        if installed:
            candidates.append((name, Path(installed)))
    for name, executable in candidates:
        if not executable.is_file():
            continue
        try:
            result = subprocess.run(
                [str(executable), "--version"], capture_output=True, text=True, timeout=5,
            )
            version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
            minimum = (2, 3, 0) if name == "deno" else (22, 0, 0)
            if result.returncode == 0 and version and tuple(map(int, version.groups())) >= minimum:
                return name, executable.resolve()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None
