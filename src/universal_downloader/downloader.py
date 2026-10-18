"""Inspect links once and save their original video, audio, or image."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadError as YtDlpError

from .dependencies import find_ffmpeg, find_js_runtime
from .errors import DownloadError
from .formats import MEDIA_TYPES, normalize_type
from .images import IMAGE_EXTENSIONS, check_image, fetch_image, looks_like_image, page_or_image, save_image
from .inspection import MediaInspection, inspect_streams
from .media import AUDIO_EXTENSIONS, NativeAudio, original_path


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    media_format: str = "video"
    output_dir: Path = Path("downloads")


@dataclass(frozen=True)
class DownloadResult:
    title: str
    path: Path
    already_exists: bool = False


def validate_url(value: str) -> str:
    url = value.strip()
    if len(url) >= 2 and url[0] == url[-1] and url[0] in ("'", '"'):
        url = url[1:-1].strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
        valid = valid and not parts.username and not parts.password
        parts.port
    except ValueError:
        valid = False
    if not valid or any(character.isspace() or ord(character) < 32 for character in url):
        raise DownloadError("Invalid URL. Paste a complete URL starting with http:// or https://.")
    return url


def _common_options(logger=None) -> dict:
    options = {
        "windowsfilenames": True, "noplaylist": True, "extract_flat": "in_playlist",
        "lazy_playlist": True, "overwrites": False, "continuedl": True,
        "retries": 3, "fragment_retries": 3, "socket_timeout": 20,
        "quiet": True, "noprogress": True,
        "color": {"stdout": "never", "stderr": "never"},
    }
    if logger is not None:
        options["logger"] = logger
    runtime = find_js_runtime()
    if runtime:
        name, executable = runtime
        options["js_runtimes"] = {name: {"path": str(executable)}}
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        options["ffmpeg_location"] = str(ffmpeg)
    return options


def _check_information(information, *, image=False) -> None:
    if not information:
        raise DownloadError("No media found at this URL.")
    if information.get("_type") in ("playlist", "multi_video") or "entries" in information:
        raise DownloadError("Download one item at a time. Use the URL of an individual video or image.")
    if not image and (information.get("is_live") or information.get("live_status") == "is_upcoming"):
        raise DownloadError("Wait for the live stream or premiere to finish, then try again.")


def _read_information(downloader, url: str) -> tuple[dict, bytes | None]:
    if looks_like_image(url):
        information, data = page_or_image(downloader, url)
    else:
        try:
            information, data = downloader.extract_info(url, download=False), None
        except YtDlpError:
            information, data = page_or_image(downloader, url)
    _check_information(information, image=True)
    if not (information.get("thumbnails") or information.get("thumbnail") or information.get("image_url")) and (
        information.get("ext") in (*IMAGE_EXTENSIONS.values(), "jpeg", "tif")
        or not (information.get("formats") or information.get("url"))
    ):
        information, data = page_or_image(downloader, url)
    return information, data


def inspect_media(url: str, *, media_format: str | None = None, logger=None) -> MediaInspection:
    """Verify available content, keeping metadata, session cookies, and image bytes."""
    url = validate_url(url)
    if media_format is not None:
        media_format = normalize_type(media_format)
        if media_format not in MEDIA_TYPES:
            raise DownloadError("Choose video, audio, or image. Files keep their original format.")
    options = {**_common_options(logger), "skip_download": True, "ignore_no_formats_error": True}
    try:
        with YoutubeDL(options) as downloader:
            information, data = _read_information(downloader, url)
            live = information.get("is_live") or information.get("live_status") == "is_upcoming"
            video, audio, heights = False, False, ()
            if not live and media_format != "image":
                video, audio, heights = inspect_streams(information, find_ffmpeg(), downloader.cookiejar)
            image_error = None
            animated = False
            if media_format is None or media_format == "image":
                try:
                    if data is None and media_format == "image" and not (information.get("image_url") or information.get("thumbnails") or information.get("thumbnail")):
                        information, data = page_or_image(downloader, url)
                    data = data if data is not None else fetch_image(downloader, information)
                    _, animated = check_image(data)
                except DownloadError as error:
                    image_error = str(error)
                    data = None
            kinds = tuple(kind for kind, available in (("video", video), ("audio", audio), ("image", data is not None)) if available)
            if not kinds:
                if live and media_format != "image":
                    raise DownloadError("Wait for the live stream or premiere to finish, then try again.")
                raise DownloadError(image_error or "No downloadable video, audio, or image could be verified at this URL.")
            result = MediaInspection(
                url, information, kinds, heights, data, animated, image_error,
                tuple(deepcopy(cookie) for cookie in downloader.cookiejar),
            )
            if media_format:
                result.validate_format(media_format)
            return result
    except (YtDlpError, RequestError, OSError) as error:
        raise DownloadError(f"Could not inspect the URL: {error}") from error


class _FinalFile(PostProcessor):
    def __init__(self, results: list[tuple[Path, bool]]):
        super().__init__()
        self.results = results

    def run(self, information: dict) -> tuple[list, dict]:
        self.results.append((
            Path(information["filepath"]).resolve(),
            not information.get("__real_download", True),
        ))
        return [], information


def download(
    request: DownloadRequest,
    *,
    progress_hook: Callable[[dict], None] | None = None,
    logger: object | None = None,
    inspected: MediaInspection | None = None,
) -> DownloadResult:
    url = validate_url(request.url)
    kind = normalize_type(request.media_format)
    if kind not in MEDIA_TYPES:
        raise DownloadError("Choose video, audio, or image. Files keep their original format.")
    if inspected:
        if inspected.url != url:
            raise DownloadError("The inspected URL does not match the download URL. Check the new URL first.")
        inspected.validate_format(kind)
    if kind != "image" and find_ffmpeg() is None:
        raise DownloadError("FFmpeg and ffprobe not found. Run install.cmd on Windows, or install FFmpeg and add both programs to PATH.")
    inspected = inspected or inspect_media(url, media_format=kind, logger=logger)
    inspected.validate_format(kind)
    _check_information(inspected.information, image=kind == "image")
    output_dir = Path(request.output_dir).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DownloadError(f"Could not create the output directory: {error}") from error

    qualifier = f" [{kind}]" if kind != "video" else ""
    source_tag = ".source" if kind == "audio" else ""
    options = {
        **_common_options(logger),
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": f"%(title).100B [%(id).40B]{qualifier}{source_tag}.%(ext)s"},
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if kind == "image":
        options.update(skip_download=True, ignore_no_formats_error=True)
    else:
        options["format"] = "bestaudio/best" if kind == "audio" else "bestvideo+bestaudio/best/bestvideo"
    results: list[tuple[Path, bool]] = []
    try:
        with YoutubeDL(options) as downloader:
            for cookie in inspected.cookies:
                downloader.cookiejar.set_cookie(deepcopy(cookie))
            information = deepcopy(inspected.information)
            if kind == "image":
                path, exists = save_image(downloader, information, inspected.image_data)
                return DownloadResult(information.get("title", path.stem), path, exists)
            if kind == "audio":
                reported = [information.get("ext"), *(source.get("ext") for source in information.get("formats") or [])]
                extensions = dict.fromkeys((*AUDIO_EXTENSIONS, *(ext for ext in reported if isinstance(ext, str) and ext.isalnum())))
                for extension in extensions:
                    existing = original_path(Path(downloader.prepare_filename({**information, "ext": extension})).resolve(), extension)
                    if existing.is_file():
                        return DownloadResult(information.get("title", existing.stem), existing, True)
                downloader.add_post_processor(NativeAudio(downloader))
            downloader.add_post_processor(_FinalFile(results), when="after_move")
            downloader.process_ie_result(information, download=True)
    except YtDlpError as error:
        raise DownloadError(f"Download failed: {error}\nCheck the URL or run update.cmd and try again.") from error
    except RequestError as error:
        raise DownloadError(f"Could not retrieve the media: {error}") from error
    except OSError as error:
        raise DownloadError(f"Could not save the file: {error}") from error
    if not results or not results[-1][0].is_file():
        raise DownloadError("The download finished without producing a file. Try updating yt-dlp.")
    path, exists = results[-1]
    return DownloadResult(information.get("title", path.stem), path, exists)
