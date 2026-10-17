"""English terminal menus and command-line arguments."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from yt_dlp.version import __version__ as ytdlp_version

from . import __version__
from .dependencies import find_ffmpeg, find_js_runtime
from .downloader import DownloadError, DownloadRequest, download, validate_url


class _Logger:
    def __init__(self, verbose: bool):
        self.verbose = verbose

    def debug(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def warning(self, message: str) -> None:
        print(f"\nWarning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        # DownloadError is displayed once by main().
        pass


class _Progress:
    def __init__(self):
        self.last_update = 0.0

    def __call__(self, status: dict) -> None:
        if status["status"] == "finished":
            print("\rDownload received. Finalizing the file...".ljust(90))
            return
        if status["status"] != "downloading" or time.monotonic() - self.last_update < 0.2:
            return
        self.last_update = time.monotonic()
        received = status.get("downloaded_bytes", 0)
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        percentage = f"{received / total:5.1%}" if total else f"{received / 1_048_576:.1f} MiB"
        speed = status.get("speed")
        rate = f" | {speed / 1_048_576:.1f} MiB/s" if speed else ""
        eta = status.get("eta")
        remaining = f" | {int(eta)}s remaining" if eta is not None else ""
        print(f"\rDownloading: {percentage}{rate}{remaining}".ljust(90), end="", flush=True)


def _show_info(information: dict) -> None:
    title = str(information.get("title", "Untitled"))
    print(f"\nTitle: {title}")
    duration = information.get("duration")
    if duration is not None:
        minutes, seconds = divmod(int(duration), 60)
        print(f"Duration: {minutes}:{seconds:02d}")


def _choose(prompt: str, choices: dict[str, str], default: str) -> str:
    while True:
        answer = input(prompt).strip() or default
        if answer in choices:
            return choices[answer]
        print("Invalid option. Try again.")


def _doctor() -> int:
    print(f"Universal Downloader {__version__} | yt-dlp {ytdlp_version}")
    ffmpeg = find_ffmpeg()
    runtime = find_js_runtime()
    print(f"FFmpeg + ffprobe: {ffmpeg if ffmpeg else 'not found'}")
    print(f"JavaScript: {runtime[0] + ' at ' + str(runtime[1]) if runtime else 'not found'}")
    if not ffmpeg or not runtime:
        print("Run install.cmd on Windows or see the manual installation steps in the README.")
        return 1
    print("Dependencies found. URL compatibility depends on the source website.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="unidown", description="Download a video or extract MP3 audio from a URL.",
    )
    parser.add_argument("url", nargs="?", help="complete video URL; opens the menu when omitted")
    parser.add_argument("--format", choices=("video", "mp3"), help="output format (default: video)")
    parser.add_argument("--quality", choices=("best", "1080", "720", "480"), help="maximum video resolution")
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="output directory (default: downloads)")
    parser.add_argument("--doctor", action="store_true", help="check dependencies without downloading anything")
    parser.add_argument("--verbose", action="store_true", help="show technical details for troubleshooting")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        if args.doctor:
            return _doctor()
        if args.format == "mp3" and args.quality is not None:
            parser.error("--quality can only be used with --format video")
        if args.url is None:
            if not sys.stdin.isatty():
                parser.error("provide a URL or run without arguments in an interactive terminal")
            print("\nUNIVERSAL DOWNLOADER\nVideo and MP3 downloads from a URL.\n")
            raw_url = input("Paste the URL (Enter to exit): ").strip()
            if not raw_url:
                return 0
            url = validate_url(raw_url)
            media_format = args.format or _choose(
                "\n1. Video\n2. MP3 audio\nFormat [1]: ", {"1": "video", "2": "mp3"}, "1",
            )
            quality = args.quality or "best"
            if media_format == "video" and args.quality is None:
                quality = _choose(
                    "\n1. Best available\n2. Up to 1080p\n3. Up to 720p\n4. Up to 480p\nQuality [1]: ",
                    {"1": "best", "2": "1080", "3": "720", "4": "480"}, "1",
                )
        else:
            url, media_format, quality = args.url, args.format or "video", args.quality or "best"
        print("\nChecking the URL...")
        result = download(
            DownloadRequest(url, media_format, quality, args.output),
            progress_hook=_Progress(), on_info=_show_info, logger=_Logger(args.verbose),
        )
        print(f"\nDone! File saved to:\n{result.path}")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCanceled. Run again to resume a partial download when supported.")
        return 130
    except DownloadError as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
