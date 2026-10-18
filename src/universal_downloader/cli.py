"""English terminal menus and command-line arguments."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from yt_dlp.version import __version__ as ytdlp_version

from . import __version__
from .dependencies import find_ffmpeg, find_js_runtime
from .downloader import DownloadError, DownloadRequest, download, inspect_media, inspect_url, validate_url
from .formats import (
    ALL_FORMATS, AUDIO_BITRATES, AUDIO_FORMATS, IMAGE_FORMATS, LOSSY_AUDIO_FORMATS,
    VIDEO_FORMATS, VIDEO_QUALITIES, normalize_format,
)
from .images import available_image_formats


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


def _format_menu(formats: dict[str, str], name: str, default: str) -> str:
    choices = {str(index): key for index, key in enumerate(formats, 1)}
    default_choice = next(index for index, key in choices.items() if key == default)
    menu = "\n" + name + "\n" + "\n".join(f"{index}. {formats[key]}" for index, key in choices.items())
    return _choose(menu + f"\nFormat [{default_choice}]: ", choices, default_choice)


def _show_formats(information: dict, *, show_title=True) -> None:
    if show_title:
        _show_info(information)
    formats = information.get("formats") or []
    if formats:
        print("\nAvailable source formats (reported by the website):")
        rows = [("ID", "TYPE", "EXT", "RESOLUTION", "VIDEO CODEC", "AUDIO CODEC")]
        for item in formats:
            video = item.get("vcodec") not in (None, "none")
            audio = item.get("acodec") not in (None, "none")
            kind = "video+audio" if video and audio else "video" if video else "audio" if audio else "media"
            resolution = f"{item['width']}x{item['height']}" if item.get("width") and item.get("height") else item.get("resolution") or "-"
            rows.append(tuple(str(value) for value in (
                item.get("format_id", "-"), kind, item.get("ext", "-"), resolution,
                item.get("vcodec") or "-", item.get("acodec") or "-",
            )))
        widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
        for row in rows:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
        print('\nSelect an ID with --source-format ID, or combine video+audio IDs as "VIDEO_ID+AUDIO_ID".')
    else:
        print("\nNo audio/video source format list was reported for this URL.")
    thumbnails = information.get("thumbnails") or []
    if thumbnails or information.get("thumbnail") or information.get("image_url"):
        print("\nImages/thumbnails are available. Use --format jpg, png, webp, avif, gif, bmp, tiff, ico, or image.")
        resolutions = sorted({(item.get("width"), item.get("height")) for item in thumbnails if item.get("width") and item.get("height")})
        if resolutions:
            print("Thumbnail sizes: " + ", ".join(f"{width}x{height}" for width, height in resolutions))
    else:
        print("\nNo thumbnail was reported for this URL.")


def _validate_options(parser, media_format, args) -> None:
    if args.quality is not None and media_format not in VIDEO_FORMATS:
        parser.error("--quality is only available for video formats")
    if args.audio_bitrate is not None and media_format not in LOSSY_AUDIO_FORMATS:
        parser.error("--audio-bitrate is only available for MP3, M4A, AAC, Opus, and OGG")
    if args.image_quality is not None and media_format not in ("jpg", "webp", "avif"):
        parser.error("--image-quality is only available for JPG, WebP, and AVIF")
    if args.source_format is not None:
        if media_format in IMAGE_FORMATS:
            parser.error("--source-format is only available for audio and video")
        if args.quality is not None:
            parser.error("--source-format cannot be combined with --quality")


def _choose_type(kinds: tuple[str, ...]) -> str:
    if len(kinds) == 1:
        return kinds[0]
    labels = {"video": "Video", "audio": "Audio", "image": "Image / thumbnail", "list": "Available source formats"}
    choices = {str(index): kind for index, kind in enumerate((*kinds, "list"), 1)}
    prompt = "\n" + "\n".join(f"{index}. {labels[kind]}" for index, kind in choices.items())
    return _choose(prompt + "\nType [1]: ", choices, "1")


def _choose_quality(heights: tuple[int, ...]) -> str:
    values = ["best", *(value for value in VIDEO_QUALITIES[1:] if heights and min(heights) <= int(value) <= max(heights))]
    if len(values) == 1:
        return "best"
    choices = {str(index): value for index, value in enumerate(values, 1)}
    labels = {"best": "Best available", "2160": "Up to 4K (2160p)"}
    prompt = "\n" + "\n".join(f"{index}. {labels.get(value, f'Up to {value}p')}" for index, value in choices.items())
    return _choose(prompt + "\nQuality [1]: ", choices, "1")


def _doctor() -> int:
    print(f"Universal Downloader {__version__} | yt-dlp {ytdlp_version}")
    ffmpeg = find_ffmpeg()
    runtime = find_js_runtime()
    print(f"FFmpeg + ffprobe: {ffmpeg if ffmpeg else 'not found'}")
    print(f"JavaScript: {runtime[0] + ' at ' + str(runtime[1]) if runtime else 'not found'}")
    image_formats = available_image_formats()
    print("Image encoders: " + ", ".join(name.upper() for name in image_formats))
    if not ffmpeg or not runtime or len(image_formats) != 8:
        print("Run install.cmd on Windows or see the manual installation steps in the README.")
        return 1
    print("Dependencies found. URL compatibility depends on the source website.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="unidown", description="Download video, audio, thumbnails, and images from a URL.",
    )
    parser.add_argument("url", nargs="?", help="video, webpage, or direct image URL; opens the menu when omitted")
    parser.add_argument("--format", type=normalize_format, choices=ALL_FORMATS, help="output format (video/audio/image keep the original format)")
    parser.add_argument("--quality", choices=VIDEO_QUALITIES, help="maximum video resolution (default: best)")
    parser.add_argument("--audio-bitrate", choices=AUDIO_BITRATES, help="lossy audio bitrate in kbps (default: 192)")
    parser.add_argument("--image-quality", type=int, choices=range(1, 96), metavar="1-95", help="JPG/WebP/AVIF quality (default: 90)")
    parser.add_argument("--list-formats", action="store_true", help="show the source formats and thumbnails without saving media")
    parser.add_argument("--source-format", help='source format ID or selector, e.g. "137+140"; see --list-formats')
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="output directory (default: downloads)")
    parser.add_argument("--doctor", action="store_true", help="check dependencies without downloading anything")
    parser.add_argument("--verbose", action="store_true", help="show technical details for troubleshooting")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        if args.doctor:
            return _doctor()
        implied_format = args.format or ("mp3" if args.audio_bitrate else "jpg" if args.image_quality else "video" if args.quality or args.source_format else None)
        if implied_format:
            _validate_options(parser, implied_format, args)
        quality = args.quality or "best"
        audio_bitrate = args.audio_bitrate or "192"
        interactive = args.url is None
        if interactive:
            if not sys.stdin.isatty():
                parser.error("provide a URL or run without arguments in an interactive terminal")
            print("\nUNIVERSAL DOWNLOADER\nVideo, audio, images, and thumbnails from a URL.\n")
            raw_url = input("Paste the URL (Enter to exit): ").strip()
            if not raw_url:
                return 0
            url = validate_url(raw_url)
        else:
            url = validate_url(args.url)
        print("\nChecking the URL...")
        logger = _Logger(args.verbose)
        if args.list_formats:
            _show_formats(inspect_url(url, logger=logger))
            return 0
        inspected = inspect_media(url, media_format=implied_format, logger=logger)
        _show_info(inspected.information)
        labels = {"video": "Video", "audio": "Audio", "image": "Image / thumbnail"}
        print("Available: " + ", ".join(labels[kind] for kind in inspected.kinds))
        media_format = implied_format or inspected.kinds[0]
        if interactive:
            if not implied_format:
                kind = _choose_type(inspected.kinds)
                if kind == "list":
                    _show_formats(inspected.information, show_title=False)
                    return 0
                if kind == "video":
                    media_format = _format_menu(VIDEO_FORMATS, "Video format", "video")
                elif kind == "audio":
                    media_format = _format_menu(AUDIO_FORMATS, "Audio format", "mp3")
                elif inspected.animated:
                    print("Animated image detected. Keeping the original format and animation.")
                    media_format = "image"
                else:
                    available = available_image_formats()
                    choices = {key: label for key, label in IMAGE_FORMATS.items() if key in available or key == "image"}
                    media_format = _format_menu(choices, "Image format", "jpg" if "jpg" in choices else "image")
            if media_format in VIDEO_FORMATS and args.quality is None and not args.source_format:
                quality = _choose_quality(inspected.heights)
            if media_format in LOSSY_AUDIO_FORMATS and args.audio_bitrate is None:
                audio_bitrate = _choose(
                    "\n1. 64 kbps\n2. 96 kbps\n3. 128 kbps\n4. 160 kbps\n5. 192 kbps\n6. 256 kbps\n7. 320 kbps\nAudio bitrate [5]: ",
                    {str(index): value for index, value in enumerate(AUDIO_BITRATES, 1)}, "5",
                )
        _validate_options(parser, media_format, args)
        inspected.validate_format(media_format)
        result = download(
            DownloadRequest(url, media_format, quality, args.output, audio_bitrate, args.image_quality or 90, args.source_format),
            progress_hook=_Progress(), logger=logger, inspected=inspected,
        )
        print(f"\nDone! File saved to:\n{result.path}")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCanceled. Run again to resume a partial download when supported.")
        return 130
    except DownloadError as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
