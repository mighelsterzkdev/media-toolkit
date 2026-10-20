"""Optional file conversion, independent of URL extraction and downloads."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps

from .dependencies import find_ffmpeg, hidden_process_options
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


def validate_conversion(inspected: MediaInspection, kind: str, target: str, *, max_height: int | None = None) -> None:
    conversion_kind(inspected, target, kind)
    if target == "mp4" and max_height is not None and max_height < 2:
        raise DownloadError("MP4 compatibility conversion requires --max-height of at least 2 pixels.")
    duration = inspected.information.get("duration")
    native_gif = False
    if target == "gif" and inspected.image_data:
        dimensions = inspected.image_dimensions
        resizing = max_height is not None and dimensions is not None and dimensions[1] > max_height
        native_gif = check_image(inspected.image_data)[0] == "gif" and not resizing
    if target == "gif" and not native_gif and isinstance(duration, (int, float)):
        if not math.isfinite(duration) or not 0 < duration <= MAX_GIF_SECONDS:
            raise DownloadError(f"GIF conversion supports animations with a duration of up to {MAX_GIF_SECONDS} seconds. Keep the original for other animations.")
    if target in ("mp3", "mp4", "gif") and not native_gif and find_ffmpeg() is None:
        raise DownloadError("FFmpeg and ffprobe are required for this conversion. Run install.cmd.")


def available_conversions(inspected: MediaInspection, kind: str, *, max_height: int | None = None) -> tuple[str, ...]:
    """Hide exact native copies and offer only usable, meaningful conversions."""
    available = []
    original = inspected.original_extension(kind)
    if kind == "image" and not inspected.animation_video and inspected.image_data:
        try:
            original = check_image(inspected.image_data)[0]
        except DownloadError:
            # The inspection may describe a deferred image fetch. Its known
            # metadata can still determine the offered options.
            pass
    dimensions = inspected.image_dimensions
    resizing = max_height is not None and dimensions is not None and dimensions[1] > max_height
    for target in conversion_targets(inspected, kind):
        # MP4 still means a compatibility conversion: the same container may
        # contain codecs that are not H.264/AAC. Other native copies are exact.
        if target != "mp4" and target == original and not resizing:
            continue
        try:
            validate_conversion(inspected, kind, target, max_height=max_height)
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
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=True, **hidden_process_options())
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise DownloadError("Could not read the downloaded media for conversion.") from error


def _image_has_transparency(image: Image.Image) -> bool:
    if "A" in image.getbands():
        with image.getchannel("A") as alpha:
            return alpha.getextrema()[0] < 255
    if "transparency" in image.info:
        with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
            return alpha.getextrema()[0] < 255
    return False


def _encode_static_image(image: Image.Image, target: str, output, *, background="#FFFFFF") -> None:
    """One encoder for checked, oriented downloads and local still images."""
    # Keep an embedded color profile only when the pixels remain in its RGB
    # color space. A CMYK profile must never be attached to converted RGB data.
    profile = image.info.get("icc_profile") if image.mode in ("RGB", "RGBA", "P") else None
    metadata = {"icc_profile": profile} if isinstance(profile, bytes) and profile else {}
    if target in ("jpg", "bmp"):
        with image.convert("RGBA") as rgba, Image.new("RGB", rgba.size, background) as flattened, rgba.getchannel("A") as alpha:
            flattened.paste(rgba, mask=alpha)
            if target == "jpg":
                flattened.save(output, "JPEG", quality=90, optimize=True, **metadata)
            else:
                flattened.save(output, "BMP")
    elif target == "gif":
        # GIF has 1-bit alpha. Reserve index 255 for fully transparent pixels;
        # quantize opaque colors separately so palette optimization cannot turn
        # an opaque black pixel into the transparent entry.
        with image.convert("RGBA") as rgba, Image.new("RGB", rgba.size, background) as flattened, rgba.getchannel("A") as alpha:
            flattened.paste(rgba, mask=alpha)
            with flattened.quantize(colors=255, method=Image.Quantize.MEDIANCUT) as palette:
                if alpha.getextrema()[0] == 0:
                    with alpha.point(lambda value: 255 if value == 0 else 0) as transparent:
                        palette.paste(255, mask=transparent)
                    palette.save(output, "GIF", transparency=255, optimize=False)
                else:
                    palette.save(output, "GIF", optimize=False)
    elif target in ("png", "webp", "avif", "tiff", "ico", "tga", "jp2"):
        with image.convert("RGBA" if _image_has_transparency(image) else "RGB") as converted:
            if target == "webp":
                converted.save(output, "WEBP", lossless=True, exact=True, **metadata)
            elif target == "png":
                converted.save(output, "PNG", optimize=True, **metadata)
            elif target == "avif":
                converted.save(output, "AVIF", quality=90, subsampling="4:4:4", speed=6, **metadata)
            elif target == "tiff":
                converted.save(output, "TIFF", compression="tiff_deflate", **metadata)
            elif target == "ico":
                if converted.width != converted.height or not 1 <= converted.width <= 256:
                    raise DownloadError("ICO output requires a square image up to 256 pixels. The source size was kept.")
                converted.save(output, "ICO", sizes=[converted.size])
            elif target == "tga":
                converted.save(output, "TGA", compression="tga_rle")
            else:
                converted.save(output, "JPEG2000", irreversible=False, **metadata)
    else:
        raise DownloadError("Unsupported still-image conversion target.")


def _convert_image(source: Path, target: str, output: Path) -> None:
    extension, animated = check_image(source.read_bytes())
    if animated:
        raise DownloadError("This conversion would discard the animation. Use the original, GIF, or MP4 instead.")
    if extension == target:
        shutil.copyfile(source, output)
        return
    with Image.open(source) as opened, ImageOps.exif_transpose(opened) as oriented:
        _encode_static_image(oriented, target, output)


def _fits_height(source: Path, max_height: int | None) -> bool:
    if max_height is None:
        return True
    with Image.open(source) as image:
        return image.height <= max_height


def convert_file(source: Path, target: str, destination: Path, *, logger=None, max_size: int | None = None, max_height: int | None = None) -> bool:
    """Convert an original file and publish only the completed result."""
    if target not in CONVERSIONS:
        raise DownloadError("Unsupported conversion target.")
    for name, limit in (("--max-size", max_size), ("--max-height", max_height)):
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
            raise DownloadError(f"{name} must be a positive integer.")
    try:
        with tempfile.TemporaryDirectory(prefix=".unidown-convert-", dir=destination.parent) as temporary:
            output = Path(temporary) / f"output.{target}"
            if target in ("png", "jpg"):
                _convert_image(source, target, output)
            elif target == "gif" and source.suffix.lower() == ".gif" and _fits_height(source, max_height):
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
                        if max_height is not None and max_height < 2:
                            raise DownloadError("MP4 compatibility conversion requires --max-height of at least 2 pixels.")
                        even_height = max_height - max_height % 2 if max_height else None
                        scale = f"scale=w=-2:h='min(ih,{even_height})'," if even_height else ""
                        options = [
                            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                            "-vf", scale + "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        ]
                    else:
                        height = min(720, max_height) if max_height else 720
                        graph = (
                            f"fps=15,scale=w='min(720,iw)':h='min({height},ih)':force_original_aspect_ratio=decrease,"
                            "split[a][b];[a]palettegen[palette];[b][palette]paletteuse"
                        )
                        options = ["-filter_complex", graph, "-an", "-loop", "0"]
                    result = subprocess.run([
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                        *options, str(output),
                    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, **hidden_process_options())
                    if result.returncode:
                        if logger:
                            logger.debug(result.stderr)
                        raise DownloadError(f"Could not convert this media to {target.upper()}. Use --verbose for details.")
            if max_size is not None and output.stat().st_size > max_size:
                raise DownloadError("The converted file exceeds --max-size. Increase the limit or keep the original.")
            return publish_file(output, destination)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise DownloadError(f"Could not convert the file to {target.upper()}: {error}") from error
