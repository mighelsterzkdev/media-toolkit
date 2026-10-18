"""Describe usable media before the terminal asks for download options."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DownloadError
from .formats import MEDIA_TYPES


@dataclass(frozen=True)
class MediaInspection:
    url: str
    information: dict
    kinds: tuple[str, ...]
    heights: tuple[int, ...] = ()
    image_data: bytes | None = field(default=None, repr=False)
    animated: bool = False
    image_error: str | None = None
    cookies: tuple = field(default=(), repr=False)

    def validate_format(self, media_format: str) -> None:
        if media_format not in MEDIA_TYPES:
            raise DownloadError("Choose video, audio, or image. Files keep their original format.")
        kind = media_format
        if kind not in self.kinds:
            message = {
                "video": "No downloadable video was found at this URL.",
                "audio": "No audio track was found at this URL.",
                "image": self.image_error or "No readable image or thumbnail was found at this URL.",
            }[kind]
            raise DownloadError(message)


def _probe_streams(source: dict, information: dict, ffmpeg: Path, cookiejar=None) -> dict | None:
    """Only probe sources whose extractor did not identify the stream types."""
    url = source.get("url") or ""
    if not url.startswith(("https://", "http://")):
        return None
    executable = ffmpeg / ("ffprobe.exe" if (ffmpeg / "ffprobe.exe").is_file() else "ffprobe")
    command = [
        str(executable), "-v", "error", "-rw_timeout", "5000000",
        "-probesize", "1048576", "-analyzeduration", "5000000",
    ]
    headers = {**(information.get("http_headers") or {}), **(source.get("http_headers") or {})}
    if cookiejar and (cookie := cookiejar.get_cookie_header(url)):
        headers.setdefault("Cookie", cookie)
    if headers:
        command.extend(["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())])
    command.extend(["-show_streams", "-show_format", "-of", "json", url])
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if result.returncode:
            return None
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def inspect_streams(information: dict, ffmpeg: Path | None, cookiejar=None) -> tuple[bool, bool, tuple[int, ...]]:
    sources = information.get("formats") or ([information] if information.get("url") else [])
    video = audio = False
    heights = set()
    probed = {}
    for source in sources:
        if source.get("has_drm") or source.get("ext") == "mhtml" or source.get("protocol") == "mhtml":
            continue
        if source.get("vcodec") in (None, "unknown") or source.get("acodec") in (None, "unknown"):
            url = source.get("url")
            # Large adaptive format lists normally report codecs already. Bound
            # unknown-source probes so a broken page cannot stall the menu.
            if ffmpeg and url and url not in probed and len(probed) < 4:
                probed[url] = _probe_streams(source, information, ffmpeg, cookiejar)
            metadata = probed.get(url)
            if metadata:
                streams = metadata.get("streams") or []
                video_stream = next((item for item in streams if item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")), None)
                audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
                container = metadata.get("format", {}).get("format_name", "")
                brand = metadata.get("format", {}).get("tags", {}).get("major_brand", "").strip()
                if not audio_stream and (container.endswith("_pipe") or container in ("image2", "gif", "apng", "ico") or brand in ("avif", "avis")):
                    information["image_url"] = url
                    video_stream = None
                source["vcodec"] = (video_stream.get("codec_name") or "unidentified") if video_stream else "none"
                source["acodec"] = (audio_stream.get("codec_name") or "unidentified") if audio_stream else "none"
                if video_stream:
                    source.update(width=video_stream.get("width"), height=video_stream.get("height"))
                if not information.get("duration"):
                    try:
                        information["duration"] = float(metadata.get("format", {}).get("duration", 0)) or None
                    except (TypeError, ValueError):
                        pass
        has_video = source.get("vcodec") not in (None, "none", "unknown", "images")
        video |= has_video
        audio |= source.get("acodec") not in (None, "none", "unknown")
        height = source.get("height")
        if has_video and isinstance(height, (int, float)) and height > 0:
            heights.add(int(height))
    return video, audio, tuple(sorted(heights, reverse=True))
