"""Inspect local still images and publish deliberate, non-destructive conversions."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import stat
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError, features

try:
    from pi_heif import register_heif_opener
except ImportError:  # The image-only core remains usable without optional HEIF.
    pass
else:
    register_heif_opener(thumbnails=False)

from .conversions import _encode_static_image, _image_has_transparency
from .errors import DownloadError
from .storage import PreparedFile

MAX_LOCAL_IMAGE_BYTES = 128 * 1024 * 1024
MAX_LOCAL_IMAGE_PIXELS = 40_000_000
LOCAL_IMAGE_FORMATS = {
    "PNG": "png", "JPEG": "jpg", "WEBP": "webp", "AVIF": "avif",
    "HEIF": "heic", "HEIC": "heic", "BMP": "bmp", "DIB": "bmp",
    "TIFF": "tiff", "GIF": "gif", "ICO": "ico", "TGA": "tga",
    "JPEG2000": "jp2", "PCX": "pcx", "PPM": "pnm", "DDS": "dds",
    "PSD": "psd", "QOI": "qoi", "SGI": "sgi", "XBM": "xbm",
}
_ENCODERS = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "avif": "AVIF",
    "bmp": "BMP", "tiff": "TIFF", "ico": "ICO", "tga": "TGA",
    "gif": "GIF", "jp2": "JPEG2000",
}
_ENCODER_FEATURES = {"png": "zlib", "jpg": "jpg", "webp": "webp", "avif": "avif", "tiff": "libtiff", "jp2": "jpg_2000"}
# Pixel-count limits alone do not cover very wide or tall images. These are
# the actual limits of the encoders and of the AVIF decoder shipped with them.
# libavif's default 32768-pixel edge limit also applies when reopening output.
_OUTPUT_MAX_EDGE = {"jpg": 65_500, "webp": 16_383, "avif": 32_768, "gif": 65_535, "tga": 65_535}
IMAGE_TARGET_NOTES = {
    "jpg": "Lossy image. Transparency uses the selected background.",
    "bmp": "Uncompressed image. Transparency uses the selected background.",
    "gif": "Still image with up to 256 colors. Fully transparent pixels stay transparent; partial transparency uses the selected background.",
    "ico": "One icon at the original size; square images up to 256 pixels.",
    "avif": "Compact image using high-quality lossy compression.",
}
_BACKGROUNDS = frozenset(("#FFFFFF", "#000000"))


def _stat_identity(record):
    # Windows/Python versions disagree on whether stat/fstat ctime represents
    # creation or change time. Compare the same handle's ctime separately.
    return record.st_dev, record.st_ino, record.st_size, record.st_mtime_ns


def _snapshot(source: Path) -> tuple[bytes, dict]:
    """Read a bounded immutable snapshot; decoding never races a second path open."""
    try:
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            raise DownloadError("Select an image file, rather than a folder or device.")
        if before.st_size > MAX_LOCAL_IMAGE_BYTES:
            raise DownloadError("This file exceeds the 128 MB image limit.")
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_identity(opened) != _stat_identity(before):
                raise DownloadError("The source file changed while it was being read. Add it again.")
            data = stream.read(MAX_LOCAL_IMAGE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = source.stat()
        if len(data) > MAX_LOCAL_IMAGE_BYTES:
            raise DownloadError("This file exceeds the 128 MB image limit.")
        if _stat_identity(before) != _stat_identity(after) or _stat_identity(after) != _stat_identity(current) or opened.st_ctime_ns != after.st_ctime_ns or len(data) != after.st_size:
            raise DownloadError("The source file changed while it was being read. Add it again.")
        return data, {"size": len(data), "mtime_ns": current.st_mtime_ns, "ctime_ns": current.st_ctime_ns, "sha256": sha256(data).hexdigest()}
    except FileNotFoundError as error:
        raise DownloadError("The selected file is no longer available. Add it again.") from error
    except PermissionError as error:
        raise DownloadError("The selected file could not be read. Check its permissions.") from error
    except OSError as error:
        raise DownloadError("The selected file could not be read. Check that it is available.") from error


@contextmanager
def _decoded(data: bytes):
    oriented = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as checked:
                native = LOCAL_IMAGE_FORMATS.get(checked.format)
                if native is None:
                    raise DownloadError("This image format is not supported by the installed image decoders.")
                if checked.width * checked.height > MAX_LOCAL_IMAGE_PIXELS:
                    raise DownloadError("This image exceeds the 40 million pixel limit.")
                # A flat PSD reports zero editable layers while still exposing
                # its one merged image. Multiple pages/layers must stay intact.
                if getattr(checked, "is_animated", False) or getattr(checked, "n_frames", 1) > 1:
                    raise DownloadError("Animated or multipage images are not supported. The original file was kept.")
                checked.verify()
            with Image.open(BytesIO(data)) as opened:
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError, MemoryError) as error:
        raise DownloadError("This image is too large to process.") from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise DownloadError("This file is not a readable supported image.") from error
    try:
        yield native, oriented
    finally:
        if oriented is not None:
            oriented.close()


def _targets(native: str, width: int | None = None, height: int | None = None) -> list[str]:
    Image.init()
    supported_features = frozenset(features.get_supported())
    return [
        target for target, encoder in _ENCODERS.items()
        if target != native and encoder in Image.SAVE
        and (target not in _ENCODER_FEATURES or _ENCODER_FEATURES[target] in supported_features)
        and (target not in _OUTPUT_MAX_EDGE or all(value is None or value <= _OUTPUT_MAX_EDGE[target] for value in (width, height)))
        and (target != "ico" or width is not None and height is not None and width == height and 1 <= width <= 256)
    ]


def inspect_local_files(paths) -> list[dict]:
    """Return per-file results; paths and fingerprints stay private to the backend."""
    results = []
    for selected in paths:
        result = {"name": "Image", "format": None, "width": None, "height": None, "size": None,
                  "media_type": "image", "animated": False, "pages": 1,
                  "transparent": False, "targets": [], "target_notes": {}, "error": None, "source": None, "signature": None}
        try:
            if not isinstance(selected, (str, os.PathLike)):
                raise DownloadError("Select a local image file.")
            source = Path(selected).resolve()
            result.update(name=source.name or "Image", source=str(source))
            data, signature = _snapshot(source)
            result.update(size=len(data), signature=signature)
            with _decoded(data) as (native, image):
                targets = _targets(native, image.width, image.height)
                result.update(format=native, width=image.width, height=image.height,
                              transparent=_image_has_transparency(image), targets=targets,
                              target_notes={target: IMAGE_TARGET_NOTES[target] for target in targets if target in IMAGE_TARGET_NOTES})
        except DownloadError as error:
            result["error"] = str(error)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            result["error"] = "The selected file could not be checked. Add it again."
        results.append(result)
    return results


def _safe_stem(source: Path) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", source.stem).strip(" .")
    stem = stem.encode("utf-8")[:120].decode("utf-8", errors="ignore").strip(" .") or "image"
    if re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", stem):
        stem = f"_{stem}"
    return stem


def _publish_unique(staged: Path, source: Path, target: str, output_dir: Path) -> Path:
    stem = _safe_stem(source)
    prepared = PreparedFile(staged)
    for number in range(10_000):
        suffix = f" ({number})" if number else ""
        destination = output_dir / f"{stem}{suffix}.{target}"
        if destination == source or destination.exists() or destination.is_symlink():
            continue
        try:
            if prepared.publish(destination):
                return destination
        except DownloadError:
            # A simultaneous creator may have claimed this name with a folder.
            if destination.exists() or destination.is_symlink():
                continue
            raise
    raise DownloadError("No unused output filename was available. Choose another destination folder.")


@contextmanager
def _work_directory(output_dir: Path, work_dir: Path | None):
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix=".unidown-convert-", dir=output_dir) as temporary:
            yield Path(temporary)
        return
    provided = Path(work_dir)
    if provided.is_symlink() or not provided.is_dir():
        raise DownloadError("The conversion workspace is unavailable. Try again.")
    resolved = provided.resolve()
    if resolved.parent != output_dir or not resolved.name.startswith(".unidown-convert-"):
        raise DownloadError("The conversion workspace is invalid. Try again.")
    # The parent owns this attempt's directory and removes it after killing a
    # worker. The engine never guesses which stale directories are safe to erase.
    yield resolved


def convert_local_image(image: dict, target: str, output_dir: Path, background="#FFFFFF", progress_hook=None, *, work_dir: Path | None = None) -> Path:
    """Convert a checked snapshot; publish a unique complete file atomically."""
    if not isinstance(target, str) or target not in _ENCODERS:
        raise DownloadError("Choose a supported image output format.")
    if not isinstance(background, str) or background not in _BACKGROUNDS:
        raise DownloadError("Choose a white or black conversion background.")
    if not isinstance(image, dict) or image.get("error") or not image.get("source") or not isinstance(image.get("signature"), dict):
        raise DownloadError("Check this image before converting it.")
    source = Path(image["source"]).resolve()
    data, signature = _snapshot(source)
    if signature != image["signature"]:
        raise DownloadError("The source file changed after it was checked. Add it again before converting.")
    try:
        with _decoded(data) as (native, opened):
            if target not in _targets(native, opened.width, opened.height):
                raise DownloadError("Choose a different compatible output format for this image.")
            destination_dir = Path(output_dir).resolve()
            destination_dir.mkdir(parents=True, exist_ok=True)
            with _work_directory(destination_dir, work_dir) as temporary:
                output = temporary / f"output.{target}"
                if progress_hook:
                    progress_hook({"status": "converting"})
                with output.open("xb") as encoded:
                    _encode_static_image(opened, target, encoded, background=background)
                    encoded.flush()
                    os.fsync(encoded.fileno())
                return _publish_unique(output, source, target, destination_dir)
    except PermissionError as error:
        raise DownloadError("The converted image could not be saved. Check the destination folder's permissions.") from error
    except MemoryError as error:
        raise DownloadError("This image is too large to process.") from error
    except (OSError, ValueError) as error:
        raise DownloadError(f"The image could not be converted to {target.upper()}. Choose another destination or try again.") from error
