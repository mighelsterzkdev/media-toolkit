"""A small English terminal interface for downloading original media."""

from __future__ import annotations

import argparse
from collections import Counter
import math
import re
import sys
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import __version__ as pillow_version
from yt_dlp.version import __version__ as ytdlp_version

from . import __version__
from .dependencies import find_ffmpeg, find_js_runtime
from .conversions import available_conversions, conversion_kind
from .compatibility import compatibility_issues
from .downloader import DownloadError, DownloadRequest, download, inspect_media, validate_url
from .formats import CONVERSIONS, MEDIA_TYPES, normalize_conversion, normalize_type


def _positive_height(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("use a positive height in pixels, for example 720")
    return int(value)


def _positive_item(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("use a positive item number, for example 1")
    return int(value)


def _parse_size(value: str) -> int:
    """Require units so a size limit never silently means a different scale."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value, re.IGNORECASE | re.ASCII)
    if not match:
        raise argparse.ArgumentTypeError("include a size unit, for example 100MB or 100MiB")
    unit = match.group(2).lower()
    powers = {"b": 1, **{prefix + "b": 1000 ** index for index, prefix in enumerate(("k", "m", "g", "t"), 1)}, **{prefix + "ib": 1024 ** index for index, prefix in enumerate(("k", "m", "g", "t"), 1)}}
    size = Decimal(match.group(1)) * powers[unit]
    if size < 1 or size != int(size):
        raise argparse.ArgumentTypeError("the limit must be a positive whole number of bytes")
    return int(size)


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
        if status["status"] == "converting":
            print(f"\rConverting to {status['format'].upper()}...".ljust(90))
            return
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
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
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


def _choose_asset(inspected, item: int | None, *, interactive: bool):
    alternatives = inspected.alternatives
    if not alternatives:
        if item not in (None, 1):
            raise DownloadError("This URL contains one media item. Use --item 1 or omit --item.")
        return inspected
    if item is not None:
        if item > len(alternatives):
            raise DownloadError(f"Choose --item from 1 to {len(alternatives)} for this URL.")
        return alternatives[item - 1]
    print("\nMedia found:")
    choices = {str(index): asset for index, asset in enumerate(alternatives, 1)}
    descriptions = {index: _asset_description(asset) for index, asset in choices.items()}
    repetitions = Counter((asset.information.get("title") or "Untitled", descriptions[index]) for index, asset in choices.items())
    for index, asset in choices.items():
        title = asset.information.get("title") or "Untitled"
        source = ""
        if repetitions[title, descriptions[index]] > 1:
            source_url = asset.information.get("image_url") or asset.information.get("url") or next((item.get("url") for item in asset.information.get("formats") or [] if item.get("url")), asset.url)
            filename = Path(unquote(urlsplit(source_url).path)).name
            filename = " ".join("".join(character for character in filename if ord(character) >= 32 and ord(character) != 127).split())
            if filename:
                source = " - " + (filename[:53] + "..." if len(filename) > 56 else filename)
        print(f"{index}. {title} [{descriptions[index]}]{source}")
    if not interactive:
        raise DownloadError("This page contains several media items. Select one with --item NUMBER.")
    print("0. Another URL")
    while True:
        answer = input("Media [1]: ").strip() or "1"
        if answer == "0":
            return None
        if answer in choices:
            return choices[answer]
        print("Invalid option. Try again.")


def _asset_description(asset) -> str:
    labels = []
    for kind in asset.kinds:
        extension = asset.original_extension(kind)
        label = asset.labels[kind] + (f" {extension.upper()}" if extension else "")
        dimensions = None
        multiple_sizes = False
        if kind == "image" and not asset.animation_video:
            dimensions = asset.image_dimensions
        if dimensions is None and (kind == "video" or asset.animation_video or (kind == "image" and asset.image_role == "image")):
            width, height = asset.information.get("width"), asset.information.get("height")
            if all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in (width, height)):
                dimensions = int(width), int(height)
            else:
                known = {
                    (int(source["width"]), int(source["height"]))
                    for source in asset.information.get("formats") or []
                    if source.get("vcodec") != "none" and not source.get("has_drm") and source.get("ext") != "mhtml"
                    and all(isinstance(source.get(key), (int, float)) and math.isfinite(source[key]) and source[key] > 0 for key in ("width", "height"))
                }
                if known:
                    dimensions = max(known, key=lambda size: (size[1], size[0]))
                    multiple_sizes = len(known) > 1
        if dimensions:
            label += f", {'up to ' if multiple_sizes else ''}{dimensions[0]}×{dimensions[1]}"
        labels.append(label)
    return "; ".join(labels)


def _choose_output(inspected, kind: str, *, max_height: int | None = None) -> str | None:
    dimensions = inspected.image_dimensions
    height = dimensions[1] if dimensions else inspected.information.get("height")
    original_allowed = not (
        kind == "image" and inspected.animated and not inspected.animation_video and max_height
        and isinstance(height, (int, float)) and height > max_height
    )
    targets = available_conversions(inspected, kind, max_height=max_height)
    options = (("original",) if original_allowed else ()) + targets
    if not options:
        raise DownloadError("No available output meets --max-height. Increase the limit or install FFmpeg for resizing.")
    choices = {str(index): target for index, target in enumerate(options, 1)}
    extension = inspected.original_extension(kind)
    original = f"Original {extension.upper()}" if extension else "Original"
    print()
    for index, target in choices.items():
        label = original if target == "original" else "MP4 video (H.264 / AAC)" if target == "mp4" else CONVERSIONS[target]
        recommended = " (recommended)" if index == "1" else ""
        print(f"{index}. {label}{recommended}")
    print("0. Another URL")
    while True:
        answer = input("Save as [1]: ").strip() or "1"
        if answer == "0":
            return None
        if answer in choices:
            return choices[answer]
        print("Invalid option. Try again.")


def _save(url: str, args, logger: _Logger, *, interactive: bool) -> None:
    print("\nChecking the URL...")
    target = args.convert
    inspection_type = args.media_type or {"mp3": "audio", "png": "image", "jpg": "image"}.get(target)
    inspected = inspect_media(url, media_format=inspection_type, logger=logger)
    inspected = _choose_asset(inspected, args.item, interactive=interactive)
    if inspected is None:
        return
    url = inspected.url
    _show_info(inspected.information)
    labels = inspected.labels
    print("Available: " + ", ".join(labels[kind] for kind in inspected.kinds))
    kind = args.media_type
    if target:
        kind = conversion_kind(inspected, target, kind)
    elif kind is None:
        kind = _choose_type(inspected.kinds, labels) if interactive else inspected.kinds[0]
    if kind is None:
        return
    inspected.validate_format(kind)
    if args.max_size is not None and kind == "image" and inspected.image_data and len(inspected.image_data) > args.max_size:
        raise DownloadError("The source file exceeds --max-size. Increase the limit.")
    if args.max_height and kind not in ("video", "image"):
        raise DownloadError("--max-height applies to video or animation, not audio.")
    if args.max_height and kind == "image" and not inspected.animated:
        raise DownloadError("--max-height applies to video or animation. Original images keep their dimensions.")
    if interactive and target is None:
        selection = _choose_output(inspected, kind, max_height=args.max_height)
        if selection is None:
            return
        target = None if selection == "original" else selection
    result = download(
        DownloadRequest(url, kind, args.output, target, max_height=args.max_height, max_size=args.max_size),
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
    issues = compatibility_issues()
    if issues:
        print("Dependency compatibility:")
        for issue in issues:
            print(f"- {issue}")
    if not ffmpeg or not runtime or issues:
        if getattr(sys, "frozen", False):
            print("Run unidown.exe --install-tools to prepare missing tools.")
            if issues:
                print("Extract the complete Windows release again to restore its bundled dependencies.")
        else:
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
    parser.add_argument("--convert", type=normalize_conversion, choices=CONVERSIONS, help="convert only when requested (default: original)")
    parser.add_argument("--item", type=_positive_item, metavar="NUMBER", help="select a media item when a page contains several (numbered from 1)")
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="output directory (default: downloads)")
    parser.add_argument("--max-height", type=_positive_height, metavar="PIXELS", help="limit video source height; scale converted animation/video down when needed (example: 720)")
    parser.add_argument("--max-size", type=_parse_size, metavar="SIZE", help="maximum source and completed size for native HTTP/DASH/ISM sources; requires units (100MB or 100MiB); does not recompress to a target size")
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
        print("\nUNIVERSAL DOWNLOADER\nCheck a link, then choose the original or another format.\n")
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
