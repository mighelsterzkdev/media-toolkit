"""A small English terminal interface for downloading original media."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PIL import __version__ as pillow_version
from yt_dlp.version import __version__ as ytdlp_version

from . import __version__
from .dependencies import find_ffmpeg, find_js_runtime
from .downloader import DownloadError, DownloadRequest, download, inspect_media, validate_url
from .formats import MEDIA_TYPES, normalize_type


class _Logger:
    def __init__(self, verbose: bool):
        self.verbose = verbose

    def debug(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def warning(self, message: str) -> None:
        if self.verbose:
            print(f"\nWarning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)


class _Progress:
    def __init__(self):
        self.last_update = 0.0

    def __call__(self, status: dict) -> None:
        if status["status"] == "finished":
            print("\rFinalizing...".ljust(90), end="", flush=True)
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
    print(f"\nTitle: {information.get('title') or 'Untitled'}")
    duration = information.get("duration")
    if isinstance(duration, (int, float)) and duration >= 0:
        minutes, seconds = divmod(int(duration), 60)
        print(f"Duration: {minutes}:{seconds:02d}")


def _choose_type(kinds: tuple[str, ...], labels: dict[str, str]) -> str | None:
    if len(kinds) == 1:
        return kinds[0]
    choices = {str(index): kind for index, kind in enumerate(kinds, 1)}
    print("\n" + "\n".join(f"{index}. {labels[kind]}" for index, kind in choices.items()))
    print("0. Another URL")
    while True:
        answer = input("Download [1]: ").strip() or "1"
        if answer == "0":
            return None
        if answer in choices:
            return choices[answer]
        print("Invalid option. Try again.")


def _save(url: str, args, logger: _Logger, *, interactive: bool) -> None:
    print("\nChecking the URL...")
    inspected = inspect_media(url, media_format=args.media_type, logger=logger)
    _show_info(inspected.information)
    labels = {**MEDIA_TYPES, "image": "Animation" if inspected.animated else MEDIA_TYPES["image"]}
    print("Available: " + ", ".join(labels[kind] for kind in inspected.kinds))
    kind = args.media_type
    if kind is None:
        kind = _choose_type(inspected.kinds, labels) if interactive else inspected.kinds[0]
    if kind is None:
        return
    inspected.validate_format(kind)
    result = download(
        DownloadRequest(url, kind, args.output),
        progress_hook=_Progress(), logger=logger, inspected=inspected,
    )
    status = "Already downloaded" if result.already_exists else "Saved"
    print(f"\n{status}:\n{result.path}")


def _doctor() -> int:
    print(f"Universal Downloader {__version__} | yt-dlp {ytdlp_version}")
    ffmpeg = find_ffmpeg()
    runtime = find_js_runtime()
    print(f"FFmpeg + ffprobe: {ffmpeg if ffmpeg else 'not found'}")
    print(f"JavaScript: {runtime[0] + ' at ' + str(runtime[1]) if runtime else 'not found'}")
    print(f"Image reader: Pillow {pillow_version}")
    if not ffmpeg or not runtime:
        print("Run install.cmd on Windows or see the manual installation steps in the README.")
        return 1
    print("Dependencies found.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="unidown", description="Download original video, audio, or images from a URL.",
    )
    parser.add_argument("url", nargs="?", help="media URL; opens the interactive menu when omitted")
    parser.add_argument("--type", dest="media_type", type=normalize_type, choices=MEDIA_TYPES, help="what to download (default: detect automatically)")
    parser.add_argument("--format", dest="media_type", type=normalize_type, choices=MEDIA_TYPES, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="output directory (default: downloads)")
    parser.add_argument("--doctor", action="store_true", help="check installed dependencies")
    parser.add_argument("--verbose", action="store_true", help="show technical details for troubleshooting")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    try:
        if args.doctor:
            return _doctor()
        logger = _Logger(args.verbose)
        if args.url is not None:
            _save(validate_url(args.url), args, logger, interactive=False)
            return 0
        if not sys.stdin.isatty():
            parser.error("provide a URL or run without arguments in an interactive terminal")
        print("\nUNIVERSAL DOWNLOADER\nOriginal media. Best available quality.\n")
        while True:
            try:
                raw_url = input("\nPaste the URL (Enter to exit): ").strip()
                if not raw_url:
                    return 0
                _save(validate_url(raw_url), args, logger, interactive=True)
            except DownloadError as error:
                print(f"\nError: {error}", file=sys.stderr)
            except EOFError:
                return 0
    except KeyboardInterrupt:
        print("\nCanceled.")
        return 130
    except DownloadError as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
