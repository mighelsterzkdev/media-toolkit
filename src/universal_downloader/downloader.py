"""Inspect links once and save their original video, audio, or image."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
import tempfile
from typing import Callable
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadError as YtDlpError

from .dependencies import find_ffmpeg, find_js_runtime
from .conversions import convert_file, validate_conversion
from .errors import DownloadError, extraction_error, is_unsupported
from .discovery import discover
from .formats import CONVERSIONS, MEDIA_TYPES, normalize_conversion, normalize_type
from .images import IMAGE_EXTENSIONS, check_image, fetch_image, looks_like_image, page_or_image, save_image
from .identity import DownloadHistory, media_identity
from .inspection import MediaInspection, inspect_streams
from .limits import TransferBudget, limited_format_selector, validate_file_limits, validate_inspected_limits, validate_limits
from .media import AUDIO_EXTENSIONS, NativeAudio, original_path
from .sources import configure_sources, is_video_animation
from .resume import ResumeWork
from .tiktok import is_tiktok_sound_url
from .urls import google_target, validate_url


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    media_format: str = "video"
    output_dir: Path = Path("downloads")
    convert_to: str | None = None
    max_height: int | None = None
    max_size: int | None = None
    desktop_conversion: bool = False


@dataclass(frozen=True)
class DownloadResult:
    title: str
    path: Path
    already_exists: bool = False


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


def _read_information(downloader, url: str, *, image_only=False) -> tuple[dict, bytes | None]:
    original = url
    url = google_target(url)
    assets = None
    if looks_like_image(url) or urlsplit(url).hostname in ("images.app.goo.gl", "share.google"):
        assets = discover(downloader, url, image_only=image_only)
        if assets is None:
            information, data = downloader.extract_info(url, download=False), None
    else:
        try:
            information, data = downloader.extract_info(url, download=False), None
        except YtDlpError as error:
            if not is_unsupported(error):
                if is_tiktok_sound_url(url):
                    friendly = extraction_error(error)
                    raise DownloadError(f"Could not retrieve this TikTok sound: {friendly}", code=friendly.code) from error
                raise extraction_error(error) from error
            assets = discover(downloader, url, image_only=image_only)
            if assets is None:
                raise extraction_error(error) from error
        else:
            # Generic extraction can mistake extensionless images and even
            # HTML pages ending in .mp4 for video. Check the actual response.
            if str((information or {}).get("extractor_key", "")).lower() in ("generic", "html5mediaembed"):
                assets = discover(downloader, url, image_only=image_only)
    if assets:
        information, data = assets[0]
        if len(assets) > 1:
            information["_unidown_assets"] = assets
    _check_information(information, image=True)
    information.setdefault("original_url", original)
    if not (information.get("thumbnails") or information.get("thumbnail") or information.get("image_url")) and (
        information.get("ext") in (*IMAGE_EXTENSIONS.values(), "jpeg", "tif")
        or not (information.get("formats") or information.get("url"))
    ):
        if not data:
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
            configure_sources(downloader)
            information, data = _read_information(downloader, url, image_only=media_format == "image")
            assets = information.pop("_unidown_assets", None)
            if assets:
                verified = []
                for asset_info, asset_data in assets:
                    try:
                        verified.append(_inspect_information(
                            asset_info.get("image_url") or asset_info.get("webpage_url") or url,
                            asset_info, asset_data, downloader, media_format,
                        ))
                    except DownloadError:
                        continue
                if not verified:
                    raise DownloadError("No compatible media could be verified on this page.")
                return replace(verified[0], url=url, alternatives=tuple(verified)) if len(verified) > 1 else replace(verified[0], url=url)
            return _inspect_information(url, information, data, downloader, media_format)
    except (YtDlpError, RequestError) as error:
        if logger:
            logger.debug(str(error))
        raise extraction_error(error) from error
    except OSError as error:
        raise DownloadError(f"Could not inspect the URL: {error}") from error


def _inspect_information(url, information, data, downloader, media_format=None):
    live = information.get("is_live") or information.get("live_status") == "is_upcoming"
    animation_video = not live and is_video_animation(information)
    video, audio, heights = False, False, ()
    if not live and not animation_video and media_format != "image":
        video, audio, heights = inspect_streams(information, find_ffmpeg(), downloader.cookiejar)
        declared_media = "formats" in information or bool(information.get("url"))
        if declared_media and not video and not audio and not information.get("image_url"):
            raise DownloadError("The media stream could not be verified. Try again later, or use --type image to request only its thumbnail.")
    image_error = None
    image_extension = None
    animated = bool(animation_video)
    if not animation_video and (media_format is None or media_format == "image"):
        try:
            if data is None and media_format == "image" and not (information.get("image_url") or information.get("thumbnails") or information.get("thumbnail")):
                information, data = page_or_image(downloader, url)
            data = data if data is not None else fetch_image(downloader, information)
            image_extension, animated = check_image(data)
        except DownloadError as error:
            image_error = str(error)
            data = None
    if animation_video:
        kinds = ("image",)
    else:
        kinds = tuple(kind for kind, available in (
            ("video", video), ("audio", audio), ("image", data is not None),
        ) if available)
    if not kinds:
        if live and media_format != "image":
            raise DownloadError("Wait for the live stream or premiere to finish, then try again.")
        raise DownloadError(image_error or "No downloadable video, audio, or image could be verified at this URL.")
    result = MediaInspection(
        url, information, kinds, heights, data, animated, image_error,
        tuple(deepcopy(cookie) for cookie in downloader.cookiejar),
        animation_video=bool(animation_video),
        image_extension=image_extension,
    )
    if media_format:
        result.validate_format(media_format)
    return result

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
    validate_limits(request.max_height, request.max_size)
    url = validate_url(request.url)
    kind = normalize_type(request.media_format)
    if kind not in MEDIA_TYPES:
        raise DownloadError("Choose video, audio, or image. Files keep their original format.")
    target = normalize_conversion(request.convert_to) if request.convert_to else None
    if request.desktop_conversion:
        from .desktop_download_formats import DOWNLOAD_TARGETS
        allowed_targets = DOWNLOAD_TARGETS
    else:
        allowed_targets = CONVERSIONS
    if target is not None and target not in allowed_targets:
        raise DownloadError("Unsupported conversion. Choose mp3, mp4, gif, png, or jpg.")
    if request.max_height is not None and kind == "audio":
        raise DownloadError("--max-height applies to video or animation, not audio.")
    if inspected:
        if inspected.url != url:
            raise DownloadError("The inspected URL does not match the download URL. Check the new URL first.")
        inspected.validate_format(kind)
    if kind != "image" and find_ffmpeg() is None:
        raise DownloadError("FFmpeg and ffprobe not found. Run scripts/install.cmd on Windows, or install FFmpeg and add both programs to PATH.")
    inspected = inspected or inspect_media(url, media_format=kind, logger=logger)
    if inspected.alternatives:
        raise DownloadError("This page contains several media items. Select one in the menu or with --item NUMBER.")
    inspected.validate_format(kind)
    _check_information(inspected.information, image=kind == "image")
    validate_inspected_limits(inspected, kind, target=target, max_height=request.max_height, max_size=request.max_size)
    if target:
        if request.desktop_conversion:
            from .desktop_download_formats import validate_download_conversion
            validate_download_conversion(inspected, kind, target, max_height=request.max_height)
        else:
            validate_conversion(inspected, kind, target, max_height=request.max_height)
    output_dir = Path(request.output_dir).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DownloadError(f"Could not create the output directory: {error}") from error

    identity = media_identity(request, inspected, kind, target)
    history = DownloadHistory(output_dir, logger=logger)
    title = inspected.information.get("title") or "download"
    if existing := history.cached(identity):
        validate_file_limits(existing, max_size=request.max_size, max_height=request.max_height, ffmpeg=find_ffmpeg())
        return DownloadResult(title, existing, True)
    try:
        with ResumeWork(output_dir, identity, logger=logger, progress_hook=progress_hook) as work:
            for attempt in range(3):
                try:
                    if target:
                        result = _download_converted(url, kind, target, work.data, inspected, progress_hook, logger, request=request, resume_work=work)
                    else:
                        result = _download_original(url, kind, work.data, inspected, progress_hook, logger, request=request, resume_work=work)
                    break
                except DownloadError as error:
                    if error.code == "resume_unavailable" and attempt < 2:
                        work.fallback(error)
                        continue
                    if error.code != "resume_changed" or attempt == 2:
                        raise
                    work.reset()
            validate_file_limits(result.path, max_size=request.max_size, max_height=request.max_height, ffmpeg=find_ffmpeg())
            # publish_file stages on the destination filesystem, so app-data
            # work on C: can safely publish a completed download to D:.
            path, exists = history.publish(result.path, identity, title)
            work.complete()
    except OSError as error:
        raise DownloadError(f"Could not save the file: {error}") from error
    return DownloadResult(title, path, exists)


def _download_original(url, kind, output_dir, inspected, progress_hook, logger, *, request=None, resume_work=None) -> DownloadResult:
    """Retrieve original bytes inside an exclusively owned temporary folder."""
    image_file = kind == "image" and not inspected.animation_video
    qualifier = f" [{kind}]" if kind != "video" else ""
    if inspected.animation_video:
        qualifier = " [animation]"
    source_tag = ".source" if kind == "audio" else ""
    max_size = getattr(request, "max_size", None)
    max_height = getattr(request, "max_height", None)
    def report(status):
        if resume_work:
            resume_work.progress(status)
        if progress_hook:
            progress_hook(status)
    budget = TransferBudget(max_size, report)
    options = {
        **_common_options(logger),
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": f"source{source_tag}.%(ext)s"},
        "progress_hooks": [budget],
    }
    if max_size is not None:
        options.update(buffersize=min(65_536, max_size + 1), noresizebuffer=True)
    if image_file:
        options.update(skip_download=True, ignore_no_formats_error=True)
    elif inspected.animation_video or (getattr(request, "desktop_conversion", False)
                                      and getattr(request, "convert_to", None) in ("gif", "webp", "apng")):
        options["format"] = "bestvideo/best"
    else:
        options["format"] = "bestaudio/best" if kind == "audio" else "bestvideo+bestaudio/best/bestvideo"
    results: list[tuple[Path, bool]] = []
    try:
        with YoutubeDL(options) as downloader:
            if not image_file and (max_height is not None or max_size is not None):
                downloader.format_selector = limited_format_selector(
                    downloader, options["format"], "video" if inspected.animation_video else kind,
                    inspected.information, max_height=max_height, max_size=max_size,
                    allow_resize=bool(getattr(request, "convert_to", None)) and
                    (not getattr(request, "desktop_conversion", False) or request.convert_to in ("mp4", "mkv", "mov", "webm", "gif", "webp", "apng")),
                )
            for cookie in inspected.cookies:
                downloader.cookiejar.set_cookie(deepcopy(cookie))
            information = deepcopy(inspected.information)
            # Inspection already generated these compatibility aliases. Let
            # yt-dlp recreate them from the canonical lists when reprocessing.
            for field in ("artist", "album_artist", "composer", "creator", "genre"):
                if f"{field}s" in information:
                    information.pop(field, None)
            if image_file:
                data = inspected.image_data
                if data is None:
                    data = fetch_image(downloader, information, progress_hook=budget)
                if max_size is not None and len(data) > max_size:
                    raise DownloadError("The source file exceeds --max-size. Increase the limit.")
                if resume_work:
                    resume_work.prepare(downloader, [], information, image_data=data, maximum=max_size)
                    if completed := resume_work.completed("original"):
                        return DownloadResult(information.get("title", completed.stem), completed)
                path, exists = save_image(downloader, information, data)
                if resume_work:
                    resume_work.remember("original", path)
                return DownloadResult(information.get("title", path.stem), path, exists)
            if resume_work:
                formats = information.get("formats") or [information]
                context = {"formats": formats, "has_merged_format": any("none" not in (source.get("vcodec"), source.get("acodec")) for source in formats), "incomplete_formats": all(source.get("vcodec") == "none" for source in formats) or all(source.get("acodec") == "none" for source in formats)}
                selected = list(downloader.format_selector(context))
                if not selected:
                    raise DownloadError("No compatible source could be selected.")
                source = selected[-1]
                sources = source.get("requested_formats") or [source]
                resume_work.prepare(downloader, sources, information, maximum=max_size)
                if completed := resume_work.completed("original"):
                    return DownloadResult(information.get("title", completed.stem), completed)
                # Freeze the exact inspected selection, including If-Range
                # validators, for yt-dlp's normal download and merge pipeline.
                downloader.format_selector = lambda context: iter([source])
                for relative in resume_work.metadata.get("transfers", {}):
                    completed_path = output_dir / relative
                    partial = completed_path.with_name(completed_path.name + ".part")
                    existing = partial if partial.is_file() else completed_path
                    if existing.is_file():
                        budget.received[str(completed_path)] = existing.stat().st_size
                if max_size and sum(budget.received.values()) > max_size:
                    raise DownloadError("The existing partial sources exceed --max-size. Increase the limit.")
            if kind == "audio":
                reported = [information.get("ext"), *(source.get("ext") for source in information.get("formats") or [])]
                extensions = dict.fromkeys((*AUDIO_EXTENSIONS, *(ext for ext in reported if isinstance(ext, str) and ext.isalnum())))
                for extension in extensions:
                    existing = original_path(Path(downloader.prepare_filename({**information, "ext": extension})).resolve(), extension)
                    if existing.is_file():
                        return DownloadResult(information.get("title", existing.stem), existing, True)
                downloader.add_post_processor(NativeAudio(downloader))
            downloader.add_post_processor(_FinalFile(results), when="after_move")
            if resume_work:
                with resume_work.guard_requests(downloader):
                    downloader.process_ie_result(information, download=True)
            else:
                downloader.process_ie_result(information, download=True)
    except YtDlpError as error:
        if logger:
            logger.debug(str(error))
        raise extraction_error(error) from error
    except RequestError as error:
        if logger:
            logger.debug(str(error))
        raise extraction_error(error) from error
    except OSError as error:
        raise DownloadError(f"Could not save the file: {error}") from error
    if not results or not results[-1][0].is_file():
        raise DownloadError("The download finished without producing a file. Try updating yt-dlp.")
    path, exists = results[-1]
    validate_file_limits(path, max_size=max_size)
    if resume_work:
        resume_work.remember("original", path)
    return DownloadResult(information.get("title", path.stem), path, exists)


def _download_converted(url, kind, target, output_dir, inspected, progress_hook, logger, *, request=None, resume_work=None) -> DownloadResult:
    """Download once into a private work folder, then convert and publish."""
    destination = output_dir / f"converted.{target}"
    title = inspected.information.get("title") or destination.stem
    try:
        original = _download_original(url, kind, output_dir, inspected, progress_hook, logger, request=request, resume_work=resume_work)
        if resume_work and (completed := resume_work.completed("converted")):
            return DownloadResult(title, completed)
        if progress_hook:
            progress_hook({"status": "preparing" if getattr(request, "desktop_conversion", False) else "converting", "format": target})
        if getattr(request, "desktop_conversion", False):
            from .download_conversion import convert_downloaded_file
            created = convert_downloaded_file(
                original.path, target, destination, inspected=inspected, kind=kind,
                logger=logger, progress_hook=progress_hook,
                max_height=request.max_height, max_size=request.max_size,
            )
        else:
            created = convert_file(
                original.path, target, destination, logger=logger,
                max_height=getattr(request, "max_height", None), max_size=getattr(request, "max_size", None),
            )
        if resume_work:
            resume_work.remember("converted", destination)
    except OSError as error:
        raise DownloadError(f"Could not prepare the conversion: {error}") from error
    return DownloadResult(title, destination, not created)
