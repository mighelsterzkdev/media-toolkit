"""Download logic, kept separate from the CLI so other interfaces can reuse it."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadError as YtDlpError

from .dependencies import find_ffmpeg, find_js_runtime
from .errors import DownloadError
from .formats import (
    ALL_FORMATS, AUDIO_BITRATES, AUDIO_FORMATS, IMAGE_FORMATS, LOSSY_AUDIO_FORMATS,
    VIDEO_FORMATS, VIDEO_QUALITIES, normalize_format, output_extension,
)
from .images import looks_like_image, page_or_image, save_image
from .media import ConvertMedia, NativeAudio, converted_path


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    media_format: str = "video"
    quality: str = "best"
    output_dir: Path = Path("downloads")
    audio_bitrate: str = "192"
    image_quality: int = 90
    source_format: str | None = None


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


def _common_options(logger=None) -> dict:
    options = {
        "windowsfilenames": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "lazy_playlist": True,
        "overwrites": False,
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "quiet": True,
        "noprogress": True,
        "color": {"stdout": "never", "stderr": "never"},
    }
    if logger is not None:
        options["logger"] = logger
    runtime = find_js_runtime()
    if runtime:
        name, executable = runtime
        options["js_runtimes"] = {name: {"path": str(executable)}}
    return options


def _check_information(information, *, image=False) -> None:
    if not information:
        raise DownloadError("No media found at this URL.")
    if information.get("_type") in ("playlist", "multi_video") or "entries" in information:
        raise DownloadError("Download one item at a time. Use the URL of an individual video or image.")
    if not image and (information.get("is_live") or information.get("live_status") == "is_upcoming"):
        raise DownloadError("Wait for the live stream or premiere to finish, then try again.")


def inspect_url(url: str, *, logger=None) -> dict:
    """Read available source formats and thumbnails without saving media."""
    url = validate_url(url)
    options = {**_common_options(logger), "skip_download": True, "ignore_no_formats_error": True}
    try:
        with YoutubeDL(options) as downloader:
            if looks_like_image(url):
                information, _ = page_or_image(downloader, url)
            else:
                try:
                    information = downloader.extract_info(url, download=False)
                except YtDlpError:
                    information, _ = page_or_image(downloader, url)
            _check_information(information, image=True)
            if not (information.get("thumbnails") or information.get("thumbnail") or information.get("image_url")) and information.get("ext") in (*IMAGE_FORMATS, "jpeg", "tif"):
                information, _ = page_or_image(downloader, url)
            return information
    except (YtDlpError, RequestError, OSError) as error:
        raise DownloadError(f"Could not inspect the URL: {error}") from error


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
    media_format = normalize_format(request.media_format)
    if media_format not in ALL_FORMATS:
        raise DownloadError("Unsupported output format. Use --help to see the available formats.")
    if request.quality not in VIDEO_QUALITIES:
        raise DownloadError("Invalid quality. Use best, 2160, 1440, 1080, 720, 480, or 360.")
    if media_format not in VIDEO_FORMATS and request.quality != "best":
        raise DownloadError("The resolution option is only available for video downloads.")
    if request.audio_bitrate not in AUDIO_BITRATES:
        raise DownloadError("Invalid audio bitrate. Use 64, 96, 128, 160, 192, 256, or 320.")
    if media_format not in LOSSY_AUDIO_FORMATS and request.audio_bitrate != "192":
        raise DownloadError("Audio bitrate is only available for lossy audio formats.")
    if not isinstance(request.image_quality, int) or not 1 <= request.image_quality <= 95:
        raise DownloadError("Image quality must be a whole number from 1 to 95.")
    if request.image_quality != 90 and media_format not in ("jpg", "webp", "avif"):
        raise DownloadError("Image quality is only available for JPG, WebP, and AVIF.")
    if request.source_format is not None:
        if media_format in IMAGE_FORMATS:
            raise DownloadError("Source format IDs are only available for audio and video.")
        if request.quality != "best":
            raise DownloadError("Use a source format ID or a resolution limit, not both.")
        if not request.source_format.strip() or any(ord(character) < 32 for character in request.source_format):
            raise DownloadError("The source format selector cannot be empty or contain control characters.")

    image = media_format in IMAGE_FORMATS
    ffmpeg = find_ffmpeg() if not image else None
    if not image and ffmpeg is None:
        raise DownloadError(
            "FFmpeg and ffprobe not found. On Windows, run install.cmd. "
            "On other systems, install FFmpeg and add both programs to PATH."
        )

    output_dir = Path(request.output_dir).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DownloadError(f"Could not create the output directory: {error}") from error

    conversion = not image and media_format not in ("video", "audio")
    qualifier = ""
    if image:
        qualifier = " [image]"
    elif media_format != "video" or request.quality != "best" or request.source_format:
        label = media_format
        if media_format in LOSSY_AUDIO_FORMATS:
            label += f"-{request.audio_bitrate}k"
        if request.quality != "best":
            label += f"-{request.quality}p"
        if request.source_format:
            label += "-" + sha256(request.source_format.encode()).hexdigest()[:8]
        qualifier = f" [{label}]"
    source_tag = ".source" if not image and media_format != "video" else ""
    options = {
        **_common_options(logger),
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": f"%(title).100B [%(id).40B]{qualifier}{source_tag}.%(ext)s"},
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if ffmpeg:
        options["ffmpeg_location"] = str(ffmpeg)
    if image:
        options.update(skip_download=True, ignore_no_formats_error=True)
    elif media_format in AUDIO_FORMATS:
        options["format"] = "bestaudio/best"
    else:
        limit = "" if request.quality == "best" else f"[height<=?{request.quality}]"
        options["format"] = f"bestvideo{limit}+bestaudio/best{limit}/bestvideo{limit}"
        # MKV is a fallback for codec combinations that cannot be merged into MP4.
        options["merge_output_format"] = "mp4/mkv"
    if request.source_format:
        options["format"] = request.source_format

    final_paths: list[Path] = []
    try:
        with YoutubeDL(options) as downloader:
            if conversion:
                downloader.add_post_processor(ConvertMedia(downloader, media_format, request.audio_bitrate))
            elif media_format == "audio":
                downloader.add_post_processor(NativeAudio(downloader))
            downloader.add_post_processor(_FinalFile(final_paths), when="after_move")
            direct_data = None
            if image and looks_like_image(url):
                information, direct_data = page_or_image(downloader, url, progress_hook)
            else:
                try:
                    information = downloader.extract_info(url, download=False)
                except YtDlpError:
                    if not image:
                        raise
                    information, direct_data = page_or_image(downloader, url, progress_hook)
            _check_information(information, image=image)
            if image and direct_data is None and not (information.get("thumbnails") or information.get("thumbnail")):
                information, direct_data = page_or_image(downloader, url, progress_hook)
            if on_info:
                on_info(information)
            if image:
                path = save_image(downloader, information, media_format, request.image_quality, direct_data, progress_hook)
                return DownloadResult(title=information.get("title", path.stem), path=path)
            if conversion:
                extension = output_extension(media_format)
                existing = converted_path(Path(downloader.prepare_filename({**information, "ext": extension})).resolve(), extension)
                if existing.is_file():
                    return DownloadResult(title=information.get("title", existing.stem), path=existing)
            elif media_format == "audio":
                for extension in ("m4a", "mp3", "opus", "ogg", "flac", "wav", "aac", "aiff", "alac", "m4b", "mka", "weba", "wma"):
                    existing = converted_path(Path(downloader.prepare_filename({**information, "ext": extension})).resolve(), extension)
                    if existing.is_file():
                        return DownloadResult(title=information.get("title", existing.stem), path=existing)
            downloader.process_ie_result(information, download=True)
    except YtDlpError as error:
        raise DownloadError(
            f"Download failed: {error}\n"
            "Check that the URL is accessible and update yt-dlp using update.cmd. "
            "Some sites require a login or restrict downloads."
        ) from error
    except RequestError as error:
        raise DownloadError(f"Could not retrieve the image: {error}") from error
    except SyntaxError as error:
        raise DownloadError(f"Invalid source format selector: {error}") from error
    except OSError as error:
        raise DownloadError(f"Could not save the file: {error}") from error

    if not final_paths or not final_paths[-1].is_file():
        raise DownloadError("The download finished without producing a file. Try updating yt-dlp.")
    return DownloadResult(title=information.get("title", final_paths[-1].stem), path=final_paths[-1])
