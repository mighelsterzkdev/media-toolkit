"""Download logic, kept separate from the CLI so other interfaces can reuse it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadError as YtDlpError

from .dependencies import find_ffmpeg, find_js_runtime


class DownloadError(Exception):
    """An error that can be displayed directly to the user."""


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    media_format: str = "video"
    quality: str = "best"
    output_dir: Path = Path("downloads")


@dataclass(frozen=True)
class DownloadResult:
    title: str
    path: Path


def validate_url(value: str) -> str:
    url = value.strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
        valid = valid and not parts.username and not parts.password
        # Accessing port also checks malformed port numbers.
        parts.port
    except ValueError:
        valid = False
    if not valid or any(character.isspace() or ord(character) < 32 for character in url):
        raise DownloadError("Invalid URL. Paste a complete URL starting with http:// or https://.")
    return url


class _FinalFile(PostProcessor):
    def __init__(self, paths: list[Path]):
        super().__init__()
        self.paths = paths

    def run(self, information: dict) -> tuple[list, dict]:
        self.paths.append(Path(information["filepath"]).resolve())
        return [], information


def download(
    request: DownloadRequest,
    *,
    progress_hook: Callable[[dict], None] | None = None,
    on_info: Callable[[dict], None] | None = None,
    logger: object | None = None,
) -> DownloadResult:
    url = validate_url(request.url)
    if request.media_format not in ("video", "mp3"):
        raise DownloadError("Choose video or mp3 as the output format.")
    if request.quality not in ("best", "1080", "720", "480"):
        raise DownloadError("Invalid quality. Use best, 1080, 720, or 480.")
    if request.media_format == "mp3" and request.quality != "best":
        raise DownloadError("The resolution option is only available for video downloads.")

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise DownloadError(
            "FFmpeg and ffprobe not found. On Windows, run install.cmd. "
            "On other systems, install FFmpeg and add both programs to PATH."
        )

    output_dir = request.output_dir.expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DownloadError(f"Could not create the output directory: {error}") from error

    options = {
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": "%(title).100B [%(id).40B].%(ext)s"},
        "windowsfilenames": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "lazy_playlist": True,
        "overwrites": False,
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "ffmpeg_location": str(ffmpeg),
        "quiet": True,
        "noprogress": True,
        "color": {"stdout": "never", "stderr": "never"},
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if logger is not None:
        options["logger"] = logger
    runtime = find_js_runtime()
    if runtime:
        name, executable = runtime
        options["js_runtimes"] = {name: {"path": str(executable)}}

    if request.media_format == "mp3":
        options["format"] = "bestaudio/best"
        options["final_ext"] = "mp3"
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192",
        }]
    else:
        limit = "" if request.quality == "best" else f"[height<=?{request.quality}]"
        options["format"] = f"bestvideo{limit}+bestaudio/best{limit}/bestvideo{limit}"
        # MKV is a fallback for codec combinations that cannot be merged into MP4.
        options["merge_output_format"] = "mp4/mkv"

    final_paths: list[Path] = []
    try:
        with YoutubeDL(options) as downloader:
            downloader.add_post_processor(_FinalFile(final_paths), when="after_move")
            information = downloader.extract_info(url, download=False)
            if not information:
                raise DownloadError("No video found at this URL.")
            if information.get("_type") in ("playlist", "multi_video") or "entries" in information:
                raise DownloadError("This version downloads one video at a time. Use the URL of a video in the playlist.")
            if information.get("is_live") or information.get("live_status") == "is_upcoming":
                raise DownloadError("Wait for the live stream or premiere to finish, then try again.")
            if on_info:
                on_info(information)
            downloader.process_ie_result(information, download=True)
    except YtDlpError as error:
        raise DownloadError(
            f"Download failed: {error}\n"
            "Check that the URL is accessible and update yt-dlp using update.cmd. "
            "Some sites require a login or restrict downloads."
        ) from error
    except OSError as error:
        raise DownloadError(f"Could not save the file: {error}") from error

    if not final_paths or not final_paths[-1].is_file():
        raise DownloadError("The download finished without producing a file. Try updating yt-dlp.")
    return DownloadResult(title=information.get("title", final_paths[-1].stem), path=final_paths[-1])
