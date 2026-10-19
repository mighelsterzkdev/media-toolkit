"""Apply explicit source and output limits without changing default downloads."""

from __future__ import annotations

import json
import math
from itertools import chain
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from .errors import DownloadError
from .dependencies import hidden_process_options


def validate_limits(max_height: int | None, max_size: int | None) -> None:
    for name, value in (("--max-height", max_height), ("--max-size", max_size)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise DownloadError(f"{name} must be a positive integer.")


def validate_inspected_limits(inspected, kind: str, *, target: str | None, max_height: int | None, max_size: int | None) -> None:
    if max_height is not None:
        if kind == "audio":
            raise DownloadError("--max-height applies to video or animation, not audio.")
        if kind == "image" and not inspected.animated:
            raise DownloadError("--max-height applies to video or animation. Original images keep their dimensions.")
        if kind == "image" and not inspected.animation_video and target is None:
            dimensions = inspected.image_dimensions
            if dimensions is None:
                raise DownloadError("The original animation height could not be verified. Remove --max-height or choose a conversion.")
            if dimensions[1] > max_height:
                raise DownloadError("The original animation exceeds --max-height. Choose GIF or MP4 conversion to resize it.")
    if max_size is not None and kind == "image" and inspected.image_data and len(inspected.image_data) > max_size:
        raise DownloadError("The source file exceeds --max-size. Increase the limit.")


def _size(source: dict) -> int | None:
    for field in ("filesize", "filesize_approx"):
        value = source.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            return int(value)
    return None


def _height(source: dict, information: dict) -> int | None:
    value = source.get("height")
    if value is None and not information.get("formats"):
        value = information.get("height")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return int(value)
    return None


def _context(formats: list[dict]) -> dict:
    return {
        "formats": formats,
        "has_merged_format": any("none" not in (item.get("acodec"), item.get("vcodec")) for item in formats),
        "incomplete_formats": all(item.get("vcodec") == "none" for item in formats) or all(item.get("acodec") == "none" for item in formats),
    }


def limited_format_selector(downloader, specification: str, kind: str, information: dict, *, max_height: int | None, max_size: int | None, allow_resize: bool = False):
    """Keep yt-dlp's native merging while budgeting all selected source streams."""
    selector = downloader.build_format_selector(specification)

    def select(context):
        formats = [item for item in context["formats"] if not item.get("has_drm") and item.get("ext") != "mhtml" and item.get("protocol") != "mhtml"]
        if max_size is not None:
            formats = [item for item in formats if (item.get("protocol") or urlsplit(item.get("url", "")).scheme) in ("http", "https", "http_dash_segments", "http_dash_segments_generator", "ism")]
            if not formats or any(information.get(field) is not None for field in ("section_start", "section_end")):
                raise DownloadError("--max-size cannot monitor this streaming protocol. Remove --max-size for this URL.")
        if max_height is not None:
            bounded = [item for item in formats if item.get("vcodec") == "none" or (_height(item, information) is not None and _height(item, information) <= max_height)]
            # An explicitly selected conversion may resize an otherwise
            # unavailable height. Still prefer bounded source renditions.
            tiers = [bounded, formats] if allow_resize else [bounded]
        else:
            tiers = [formats]
        for tier in tiers:
            audio = [item for item in tier if item.get("vcodec") == "none" and item.get("acodec") != "none"]
            video = [item for item in tier if item.get("vcodec") != "none" and item.get("acodec") == "none"]
            combined = [item for item in tier if item.get("vcodec") != "none" and item.get("acodec") != "none"]
            if kind == "audio":
                candidates = ((item,) for item in reversed(audio or combined))
            elif specification == "bestvideo/best":
                candidates = ((item,) for item in chain(reversed(video), reversed(combined)))
            else:
                # Never drop a real audio track just to satisfy a byte limit.
                silent = () if any(item.get("acodec") != "none" for item in formats) else reversed(video)
                candidates = chain(
                    ((v, a) for v in reversed(video) for a in reversed(audio)),
                    ((item,) for item in reversed(combined)),
                    ((item,) for item in silent),
                )
            for candidate in candidates:
                if max_size is not None and sum(_size(item) or 0 for item in candidate) > max_size:
                    continue
                selected = list(selector(_context(list(candidate))))
                if selected:
                    yield selected[0]
                    return
        raise DownloadError("No available source meets the requested --max-height or --max-size limits. Increase or remove the limit.")

    return select


class TransferBudget:
    """Count completed and active bytes together across video/audio downloads."""

    def __init__(self, maximum: int | None, progress_hook=None):
        self.maximum = maximum
        self.progress_hook = progress_hook
        self.received = {}

    def __call__(self, status: dict) -> None:
        if self.maximum is not None and status.get("status") in ("downloading", "finished"):
            information = status.get("info_dict") or {}
            key = status.get("filename") or information.get("format_id") or information.get("url") or "source"
            received = status.get("downloaded_bytes")
            if isinstance(received, (int, float)) and math.isfinite(received) and received >= 0:
                self.received[key] = max(self.received.get(key, 0), received)
            total = sum(self.received.values())
            declared = status.get("total_bytes")
            if isinstance(declared, (int, float)) and math.isfinite(declared) and declared > 0:
                if status["status"] == "finished":
                    self.received[key] = max(self.received.get(key, 0), declared)
                total = max(total, sum(amount for item, amount in self.received.items() if item != key) + declared)
            if total > self.maximum:
                raise DownloadError("The source download exceeds --max-size. Increase the limit or select a smaller source.")
        if self.progress_hook:
            self.progress_hook(status)


def validate_file_limits(path: Path, *, max_size: int | None, max_height: int | None = None, ffmpeg: Path | None = None) -> None:
    """Verify actual completed bytes and dimensions before final publication."""
    if max_size is not None and path.stat().st_size > max_size:
        raise DownloadError("The completed file exceeds --max-size. Increase the limit or select a smaller source.")
    if max_height is None:
        return
    try:
        with Image.open(path) as image:
            height = image.height
    except (UnidentifiedImageError, OSError, ValueError):
        if ffmpeg is None:
            raise DownloadError("FFmpeg and ffprobe are required to verify --max-height. Run install.cmd.")
        executable = ffmpeg / ("ffprobe.exe" if (ffmpeg / "ffprobe.exe").is_file() else "ffprobe")
        try:
            result = subprocess.run([str(executable), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=True, **hidden_process_options())
            streams = json.loads(result.stdout).get("streams") or []
            height = streams[0].get("height") if streams else None
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise DownloadError("Could not verify the completed media height for --max-height.") from error
    if not isinstance(height, (int, float)) or not math.isfinite(height) or height <= 0:
        raise DownloadError("The completed media height could not be verified for --max-height.")
    if height > max_height:
        raise DownloadError("The completed file exceeds --max-height. Choose a smaller source or explicit conversion.")
