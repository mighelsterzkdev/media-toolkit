"""Find local tools without changing the system PATH."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def hidden_process_options() -> dict:
    """Keep background tool probes/conversions from opening console windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if platform.system() == "Windows" else {}


def _executable(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _tool_roots() -> list[Path]:
    # A portable app must not accidentally depend on another checkout's tools
    # merely because it was launched from that checkout's working directory.
    return [PROJECT_ROOT] if getattr(sys, "frozen", False) else [Path.cwd(), PROJECT_ROOT]


def find_ffmpeg() -> Path | None:
    """yt-dlp needs ffmpeg and ffprobe in the same directory."""
    directories = [root / ".tools/ffmpeg/bin" for root in _tool_roots()]
    installed = shutil.which("ffmpeg")
    if installed:
        directories.append(Path(installed).parent)
    for directory in directories:
        if all((directory / _executable(name)).is_file() for name in ("ffmpeg", "ffprobe")):
            return directory.resolve()
    return None


def find_js_runtime() -> tuple[str, Path] | None:
    candidates = [("deno", root / ".tools/deno" / _executable("deno")) for root in _tool_roots()]
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
                **hidden_process_options(),
            )
            version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
            minimum = (2, 3, 0) if name == "deno" else (22, 0, 0)
            if result.returncode == 0 and version and tuple(map(int, version.groups())) >= minimum:
                return name, executable.resolve()
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None
