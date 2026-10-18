"""Optional file conversion, independent of URL extraction and downloads."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps

from .dependencies import find_ffmpeg
from .errors import DownloadError
from .formats import CONVERSIONS
from .images import check_image
from .inspection import MediaInspection
from .storage import publish_file

MAX_GIF_SECONDS = 30


def conversion_targets(inspected: MediaInspection, kind: str) -> tuple[str, ...]:
    inspected.validate_format(kind)
    if inspected.animated and (kind == "image" or inspected.animation_video):
        return ("gif", "mp4")
    return {"video": ("mp4",), "audio": ("mp3",), "image": ("png", "jpg")}[kind]


def conversion_kind(inspected: MediaInspection, target: str, selected: str | None = None) -> str:
    if target not in CONVERSIONS:
        raise DownloadError("Unsupported conversion. Choose mp3, mp4, gif, png, or jpg.")
    if selected:
        inspected.validate_format(selected)
        candidates = (selected,)
    else:
        preferred = {"mp3": "audio", "mp4": "video", "gif": "image", "png": "image", "jpg": "image"}[target]
        candidates = tuple(dict.fromkeys((preferred, *inspected.kinds)))
    for kind in candidates:
        if kind not in inspected.kinds and not (kind == "video" and inspected.animation_video):
            continue
        if target in conversion_targets(inspected, kind):
            return kind
    raise DownloadError(f"{target.upper()} conversion is not available for this content. Choose the original download or another compatible format.")


def validate_conversion(inspected: MediaInspection, kind: str, target: str) -> None:
    conversion_kind(inspected, target, kind)
    duration = inspected.information.get("duration")
    native_gif = False
    if target == "gif" and inspected.image_data:
        native_gif = check_image(inspected.image_data)[0] == "gif"
    if target == "gif" and not native_gif and isinstance(duration, (int, float)):
        if not math.isfinite(duration) or not 0 < duration <= MAX_GIF_SECONDS:
            raise DownloadError(f"GIF conversion supports animations with a duration of up to {MAX_GIF_SECONDS} seconds. Keep the original for other animations.")
    if target in ("mp3", "mp4", "gif") and not native_gif and find_ffmpeg() is None:
        raise DownloadError("FFmpeg and ffprobe are required for this conversion. Run install.cmd.")


def available_conversions(inspected: MediaInspection, kind: str) -> tuple[str, ...]:
    """Offer only conversions that pass the same preflight as the downloader."""
    available = []
    for target in conversion_targets(inspected, kind):
        try:
            validate_conversion(inspected, kind, target)
        except DownloadError:
            continue
        available.append(target)
    return tuple(available)


def _tools() -> tuple[Path, Path]:
    directory = find_ffmpeg()
    if directory is None:
        raise DownloadError("FFmpeg and ffprobe are required for this conversion. Run install.cmd.")
    suffix = ".exe" if (directory / "ffmpeg.exe").is_file() else ""
    return directory / f"ffmpeg{suffix}", directory / f"ffprobe{suffix}"


def _probe(path: Path, executable: Path) -> dict:
    try:
        result = subprocess.run([
            str(executable), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise DownloadError("Could not read the downloaded media for conversion.") from error


def _convert_image(source: Path, target: str, output: Path) -> None:
    extension, animated = check_image(source.read_bytes())
    if animated:
        raise DownloadError("This conversion would discard the animation. Use the original, GIF, or MP4 instead.")
    if extension == target:
        shutil.copyfile(source, output)
        return
    with Image.open(source) as opened, ImageOps.exif_transpose(opened) as oriented:
        image = oriented.convert("RGBA")
    try:
        if target == "jpg":
            with Image.new("RGB", image.size, "white") as flattened:
                flattened.paste(image, mask=image.getchannel("A"))
                flattened.save(output, "JPEG", quality=90, optimize=True)
        else:
            image.save(output, "PNG", optimize=True)
    finally:
        image.close()


def convert_file(source: Path, target: str, destination: Path, *, logger=None) -> bool:
    """Convert an original file and publish only the completed result."""
    if target not in CONVERSIONS:
        raise DownloadError("Unsupported conversion target.")
    try:
        with tempfile.TemporaryDirectory(prefix=".unidown-convert-", dir=destination.parent) as temporary:
            output = Path(temporary) / f"output.{target}"
            if target in ("png", "jpg"):
                _convert_image(source, target, output)
            elif target == "gif" and source.suffix.lower() == ".gif":
                if check_image(source.read_bytes())[0] != "gif":
                    raise DownloadError("The source file is not a readable GIF.")
                shutil.copyfile(source, output)
            else:
                ffmpeg, ffprobe = _tools()
                metadata = _probe(source, ffprobe)
                streams = metadata.get("streams") or []
                original_format = metadata.get("format", {}).get("format_name")
                if target == "gif":
                    try:
                        duration = float(metadata.get("format", {}).get("duration", 0))
                    except (TypeError, ValueError):
                        duration = 0
                    if not math.isfinite(duration) or not 0 < duration <= MAX_GIF_SECONDS:
                        raise DownloadError(f"GIF conversion requires a known animation duration of up to {MAX_GIF_SECONDS} seconds.")
                if target == "mp3" and original_format == "mp3" and source.suffix.lower() == ".mp3" and any(stream.get("codec_name") == "mp3" for stream in streams):
                    shutil.copyfile(source, output)
                else:
                    if target == "mp3":
                        options = ["-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "libmp3lame", "-b:a", "192k"]
                    elif target == "mp4":
                        options = [
                            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        ]
                    else:
                        graph = (
                            "fps=15,scale=w='min(720,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease,"
                            "split[a][b];[a]palettegen[palette];[b][palette]paletteuse"
                        )
                        options = ["-filter_complex", graph, "-an", "-loop", "0"]
                    result = subprocess.run([
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                        *options, str(output),
                    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
                    if result.returncode:
                        if logger:
                            logger.debug(result.stderr)
                        raise DownloadError(f"Could not convert this media to {target.upper()}. Use --verbose for details.")
            return publish_file(output, destination)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise DownloadError(f"Could not convert the file to {target.upper()}: {error}") from error
