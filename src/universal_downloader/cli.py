"""A small English terminal interface for downloading original media."""

from __future__ import annotations

import argparse
from copy import copy
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

from . import APP_NAME, __version__
from .dependencies import find_ffmpeg, find_js_runtime
from .conversions import available_conversions, conversion_kind
from .compatibility import compatibility_issues
from .downloader import DownloadError, DownloadRequest, download, inspect_media, validate_url
from .formats import CONVERSIONS, MEDIA_TYPES, normalize_conversion, normalize_type
from .preferences import load_preferences, preferences_path, reset_preferences, save_preferences
from .queue import QueueIncompatible, load_batch, pasted_urls


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
        if status["status"] == "resuming":
            print("Resuming...")
            return
        if status["status"] == "resume_saved":
            print("\nProgress saved. Paste the same link to continue.")
            return
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


def _save(url: str, args, logger: _Logger, *, interactive: bool, queued: bool = False, prepared=None):
    print("\nChecking the URL...")
    target = args.convert
    inspection_type = None if queued else args.media_type or {"mp3": "audio", "png": "image", "jpg": "image"}.get(target)
    inspected = prepared or inspect_media(url, media_format=inspection_type, logger=logger)
    if prepared is None:
        inspected = _choose_asset(inspected, args.item, interactive=interactive)
    if inspected is None:
        return
    url = inspected.url
    _show_info(inspected.information)
    labels = inspected.labels
    print("Available: " + ", ".join(labels[kind] for kind in inspected.kinds))
    kind = args.media_type
    try:
        if target:
            kind = conversion_kind(inspected, target, kind)
        elif kind is None:
            kind = _choose_type(inspected.kinds, labels) if interactive and not queued else inspected.kinds[0]
        if kind is not None:
            inspected.validate_format(kind)
    except DownloadError as error:
        if queued:
            raise QueueIncompatible(str(error)) from error
        raise
    if kind is None:
        return
    max_height = args.max_height
    if max_height is not None and not args.explicit_height and (kind == "audio" or (kind == "image" and not inspected.animated)):
        print("Saved height limit does not apply to this content.")
        max_height = None
    if args.max_size is not None and kind == "image" and inspected.image_data and len(inspected.image_data) > args.max_size:
        raise DownloadError("The source file exceeds --max-size. Increase the limit.")
    if max_height and kind not in ("video", "image"):
        raise DownloadError("--max-height applies to video or animation, not audio.")
    if max_height and kind == "image" and not inspected.animated:
        raise DownloadError("--max-height applies to video or animation. Original images keep their dimensions.")
    if interactive and not queued and target is None:
        selection = _choose_output(inspected, kind, max_height=max_height)
        if selection is None:
            return
        target = None if selection == "original" else selection
    result = download(
        DownloadRequest(url, kind, args.output, target, max_height=max_height, max_size=args.max_size),
        progress_hook=_Progress(), logger=logger, inspected=inspected,
    )
    status = "Already downloaded" if result.already_exists else "Saved"
    print(f"\n{status}:\n{result.path}")
    return result


def _run_queue(urls, args, logger, *, interactive: bool) -> int:
    policy = copy(args)
    preflight = {}
    if interactive and policy.media_type is None and policy.convert is None:
        print(f"\nQueue: {len(urls)} links\n1. Original media (recommended; detect each link)\n2. Choose type and format\n0. Cancel")
        while True:
            answer = input("Queue mode [1]: ").strip() or "1"
            if answer == "0":
                print("Queue canceled.")
                return 0
            if answer == "1":
                break
            if answer == "2":
                for index, url in enumerate(urls, 1):
                    try:
                        prepared = _choose_asset(inspect_media(url, logger=logger), policy.item, interactive=True)
                    except DownloadError as error:
                        preflight[index] = DownloadError(str(error))
                        print(f"\nError checking queue item {index}: {error}", file=sys.stderr)
                        continue
                    if prepared is None:
                        return 0
                    preflight[index] = prepared
                    break
                else:
                    _queue_summary({"completed": 0, "already": 0, "failed": len(preflight), "skipped": 0})
                    return 1
                _show_info(prepared.information)
                policy.media_type = _choose_type(prepared.kinds, prepared.labels)
                if policy.media_type is None:
                    return 0
                height = policy.max_height
                if not policy.explicit_height and (policy.media_type == "audio" or (policy.media_type == "image" and not prepared.animated)):
                    height = None
                target = _choose_output(prepared, policy.media_type, max_height=height)
                if target is None:
                    return 0
                policy.convert = None if target == "original" else target
                break
            print("Invalid option. Try again.")
    selected = policy.media_type or "detected media"
    output = CONVERSIONS[policy.convert] if policy.convert else "original"
    print(f"\nQueue: {len(urls)} links | {selected} | {output}")
    if interactive and input(f"Download {len(urls)} queued links? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("Queue canceled.")
        return 0
    totals = {"completed": 0, "already": 0, "failed": 0, "skipped": 0}
    for index, url in enumerate(urls, 1):
        print(f"\n[{index}/{len(urls)}]")
        cached = preflight.get(index)
        if isinstance(cached, DownloadError):
            totals["failed"] += 1
            print("Failed during preflight; continuing.")
            continue
        try:
            result = _save(url, policy, logger, interactive=interactive, queued=True, prepared=cached)
            totals["skipped" if result is None else "already" if result.already_exists else "completed"] += 1
        except QueueIncompatible as error:
            totals["skipped"] += 1
            print(f"Skipped: {error}")
        except DownloadError as error:
            totals["failed"] += 1
            print(f"\nError: {error}", file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            totals["skipped"] += len(urls) - index + 1
            _queue_summary(totals)
            print("Queue canceled.")
            return 130
    _queue_summary(totals)
    return 1 if totals["failed"] else 0


def _queue_summary(totals) -> None:
    print("\nQueue summary: " + " | ".join(f"{name} {count}" for name, count in totals.items()))


def _apply_preferences(args) -> None:
    args.explicit_height = args.max_height is not None
    explicit = {name: getattr(args, name) for name in ("output", "max_height", "max_size") if getattr(args, name) is not None}
    saved = {}
    if not args.no_preferences and not args.reset_preferences:
        try:
            saved = load_preferences()
        except DownloadError as error:
            if args.show_preferences:
                raise
            print(str(error), file=sys.stderr)
    for name in ("output", "max_height", "max_size"):
        if getattr(args, name) is None:
            value = saved.get(name)
            setattr(args, name, Path(value) if name == "output" and value is not None else value)
    args.output = args.output or Path("downloads")
    try:
        resolved_output = args.output.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise DownloadError("Could not resolve the output directory. Choose another --output path.") from error
    if args.reset_preferences:
        reset_preferences()
        print("Preferences reset.")
    if args.show_preferences:
        print(f"Preferences: {preferences_path()}\nOutput: {args.output}\nMax height: {args.max_height or 'none'}\nMax size: {str(args.max_size) + ' bytes' if args.max_size else 'none'}")
    elif saved and any(name not in explicit and saved.get(name) is not None for name in ("output", "max_height", "max_size")):
        parts = [f"output {args.output}"]
        if args.max_height is not None:
            parts.append(f"height {args.max_height}p")
        if args.max_size is not None:
            parts.append(f"size {args.max_size} bytes")
        print("Saved preferences: " + " | ".join(parts))
    if not args.no_preferences and (args.save_preferences or explicit):
        values = {"output": str(resolved_output), "max_height": args.max_height, "max_size": args.max_size}
        try:
            save_preferences(values)
            if args.save_preferences:
                print("Preferences saved.")
        except DownloadError as error:
            if args.save_preferences:
                raise
            print(str(error), file=sys.stderr)


def _doctor() -> int:
    print(f"{APP_NAME} {__version__} | yt-dlp {ytdlp_version}")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unidown", description="Download original video, audio, or images from a URL.",
    )
    parser.add_argument("url", nargs="?", help="media URL; opens the interactive menu when omitted")
    parser.add_argument("--batch", type=Path, metavar="FILE", help="download a UTF-8 queue with one URL per line")
    parser.add_argument("--type", dest="media_type", type=normalize_type, choices=MEDIA_TYPES, help="what to download (default: detect automatically)")
    parser.add_argument("--format", dest="media_type", type=normalize_type, choices=MEDIA_TYPES, help=argparse.SUPPRESS)
    parser.add_argument("--convert", type=normalize_conversion, choices=CONVERSIONS, help="convert only when requested (default: original)")
    parser.add_argument("--item", type=_positive_item, metavar="NUMBER", help="select a media item when a page contains several (numbered from 1)")
    parser.add_argument("--output", type=Path, help="output directory (default: saved preference or downloads; explicit choices are remembered)")
    parser.add_argument("--max-height", type=_positive_height, metavar="PIXELS", help="limit video source height; scale converted animation/video down when needed (example: 720)")
    parser.add_argument("--max-size", type=_parse_size, metavar="SIZE", help="maximum source and completed size for native HTTP/DASH/ISM sources; requires units (100MB or 100MiB); does not recompress to a target size")
    parser.add_argument("--doctor", action="store_true", help="check installed dependencies")
    parser.add_argument("--clear-resume", action="store_true", help="clear inactive partial downloads; use alone without a URL or --batch")
    preferences = parser.add_mutually_exclusive_group()
    preferences.add_argument("--save-preferences", action="store_true", help="save the current output directory and limits")
    preferences.add_argument("--show-preferences", action="store_true", help="show the saved output directory and limits")
    preferences.add_argument("--reset-preferences", action="store_true", help="clear saved output directory and limits")
    parser.add_argument("--no-preferences", action="store_true", help="ignore saved preferences and do not save changes")
    parser.add_argument("--verbose", action="store_true", help="show technical details for troubleshooting")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.url is not None and args.batch is not None:
        parser.error("use a URL or --batch FILE, not both")
    if args.no_preferences and (args.save_preferences or args.show_preferences or args.reset_preferences):
        parser.error("--no-preferences cannot be combined with preference management commands")
    if args.clear_resume and any((args.url is not None, args.batch is not None, args.doctor, args.save_preferences, args.show_preferences, args.reset_preferences, args.media_type, args.convert, args.item, args.output, args.max_height, args.max_size)):
        parser.error("use --clear-resume alone without a URL, --batch, or download options")
    try:
        if args.clear_resume:
            from .resume import clear_resume
            count = clear_resume()
            print(f"Cleared {count} inactive partial downloads. Active downloads were kept.")
            return 0
        if args.doctor:
            return _doctor()
        logger = _Logger(args.verbose)
        urls = load_batch(args.batch) if args.batch is not None else None
        if args.url is not None:
            args.url = validate_url(args.url)
        _apply_preferences(args)
        if args.batch is not None:
            return _run_queue(urls, args, logger, interactive=False)
        if args.url is not None:
            _save(args.url, args, logger, interactive=False)
            return 0
        if args.save_preferences or args.show_preferences or args.reset_preferences:
            return 0
        if not sys.stdin.isatty():
            parser.error("provide a URL or run without arguments in an interactive terminal")
        print(f"\n{APP_NAME.upper()}\nCheck a link, then choose the original or another format.\nPaste several links to queue them, or type batch followed by a text-file path.\n")
        session_status = 0
        while True:
            try:
                raw_url = input("\nPaste the URL (Enter to exit): ").strip()
                if not raw_url:
                    return session_status
                if raw_url.lower().startswith("batch "):
                    filename = raw_url[6:].strip()
                    if len(filename) > 1 and filename[0] == filename[-1] and filename[0] in ("'", '"'):
                        filename = filename[1:-1]
                    queued_urls = load_batch(Path(filename))
                else:
                    queued_urls = pasted_urls(raw_url)
                if len(queued_urls) > 1 or raw_url.lower().startswith("batch "):
                    status = _run_queue(queued_urls, args, logger, interactive=True)
                    if status == 130:
                        return status
                    session_status = max(session_status, status)
                else:
                    _save(queued_urls[0], args, logger, interactive=True)
            except DownloadError as error:
                print(f"\nError: {error}", file=sys.stderr)
            except EOFError:
                return session_status
    except KeyboardInterrupt:
        print("\nCanceled.")
        return 130
    except DownloadError as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
