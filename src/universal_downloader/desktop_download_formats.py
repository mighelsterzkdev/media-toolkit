"""Offer desktop download outputs from the existing local conversion engines."""

from __future__ import annotations

from io import BytesIO
import math

from PIL import Image

from . import local_conversion, local_media
from .errors import DownloadError
from .local_animation import ANIMATION_FORMATS, MAX_ANIMATION_FRAMES, MAX_ANIMATION_PIXELS, animation_targets

DOWNLOAD_TARGETS_BY_KIND = {
    "video": frozenset((*local_media._VIDEO, *local_media._ANIMATION)),
    "audio": frozenset(local_media._AUDIO),
    # Providers such as X expose an MP4 animation as an image-kind asset.
    "image": frozenset((*local_conversion._ENCODERS, "apng", *local_media._VIDEO)),
}
DOWNLOAD_TARGETS = frozenset().union(*DOWNLOAD_TARGETS_BY_KIND.values())


def _white_background(note):
    return note.replace("the selected background", "a white background")


def _bounded_video_source(inspected, max_height):
    if max_height is None:
        return True
    even_height = max_height - max_height % 2
    sources = inspected.information.get("formats") or [inspected.information]
    heights = [item.get("height") for item in sources
               if not item.get("has_drm") and item.get("ext") != "mhtml" and item.get("protocol") != "mhtml"
               and item.get("vcodec") not in ("none", "images")]
    heights.extend(inspected.heights)
    return any(isinstance(height, (int, float)) and not isinstance(height, bool)
               and math.isfinite(height) and 0 < height <= even_height for height in heights)


def _av_options(inspected, kind, max_height):
    try:
        ffmpeg, _ffprobe = local_media._tools()
        encoders, muxers = local_media._capabilities(ffmpeg)
    except DownloadError:
        return {}
    native = inspected.original_extension(kind)
    if kind == "audio":
        channels = inspected.information.get("audio_channels")
        return {target: "Compatible audio is copied without re-encoding. When encoding is needed: " + local_media._NOTES[target]
                for target, (encoder, muxer, _options) in local_media._AUDIO.items()
                if target != native and encoder in encoders and muxer in muxers
                and (target != "flac" or not isinstance(channels, (int, float)) or channels <= 8)}
    has_audio = "audio" in inspected.kinds
    options = {}
    for target, (encoder, audio_encoder, muxer) in local_media._VIDEO.items():
        if encoder not in encoders or muxer not in muxers or has_audio and audio_encoder not in encoders:
            continue
        if target == native and target != "mp4":
            continue
        if target == "mp4":
            if max_height is not None and max_height < 2:
                continue
        elif not _bounded_video_source(inspected, max_height):
            # Only the existing GIF/MP4 compatibility path resizes sources.
            continue
        options[target] = "Compatible streams are copied without re-encoding. When encoding is needed: " + local_media._NOTES[target]
        if target == "mp4" and max_height is not None:
            options[target] += " Resizes to the maximum video height when needed."
    for target, (encoder, muxer) in local_media._ANIMATION.items():
        if encoder in encoders and muxer in muxers:
            options[target] = local_media._ANIMATION_NOTES[target]
            if max_height is not None:
                options[target] += " Resizes to the maximum video height when needed."
    return options


def available_download_conversions(inspected, kind, *, max_height=None):
    """Return usable targets only; original bytes remain a separate option."""
    inspected.validate_format(kind)
    if kind in ("video", "audio"):
        return _av_options(inspected, kind, max_height)
    if inspected.animation_video:
        return _av_options(inspected, kind, max_height)
    if not inspected.image_data:
        return {}
    try:
        with Image.open(BytesIO(inspected.image_data)) as image:
            count = getattr(image, "n_frames", 1)
            if inspected.animated:
                native = ANIMATION_FORMATS.get(image.format)
                count -= 1 if image.info.get("default_image") else 0
                if (native is None or count < 2 or count > MAX_ANIMATION_FRAMES
                        or image.width * image.height * count > MAX_ANIMATION_PIXELS):
                    return {}
                targets = animation_targets(native, mp4_available=local_media._can_mp4(None))
                notes = {
                    "gif": "Animated GIF: up to 256 colors per frame. Timing rounds to 10 ms; partial transparency uses a white background.",
                    "webp": "Lossless animated WebP. Frames, transparency and playback timing are kept.",
                    "apng": "Lossless animated PNG. Frames, transparency and playback timing are kept.",
                    "mp4": "H.264 video of one animation cycle. Transparency uses a white background; dimensions pad to even pixels.",
                }
                if max_height is not None and image.height > max_height:
                    # Keep the established explicit resize options; other
                    # animation outputs preserve the original frame dimensions.
                    targets = [target for target in animation_targets(None, mp4_available=local_media._can_mp4(None))
                               if target in ("gif", "mp4")]
                    notes["gif"] = "Resizes the animation as GIF. Frame timing rounds to 10 ms; partial transparency uses a white background. Uses up to 256 colors per frame."
                    notes["mp4"] = "Resizes one animation cycle as H.264 video. Frame timing is kept; transparency uses a white background."
                elif max_height is not None and image.height + image.height % 2 > max_height:
                    notes["mp4"] = "Resizes one animation cycle as H.264 video to fit the maximum height. Frame timing is kept; transparency uses a white background."
                return {target: notes[target] for target in targets
                        if target != "mp4" or max_height is None or max_height >= 2}
            native = local_conversion.LOCAL_IMAGE_FORMATS.get(image.format)
            if (native is None or count != 1 or image.width * image.height > local_conversion.MAX_LOCAL_IMAGE_PIXELS
                    or len(inspected.image_data) > local_conversion.MAX_LOCAL_IMAGE_BYTES):
                return {}
            return {target: _white_background(local_conversion.IMAGE_TARGET_NOTES.get(target,
                    "Converts the image at its original dimensions."))
                    for target in local_conversion._targets(native, image.width, image.height)}
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return {}


def validate_download_conversion(inspected, kind, target, *, max_height=None):
    # Completed/interrupted queue entries release their checked image bytes.
    # The history fingerprint may satisfy a retry immediately; otherwise the
    # source is fetched and content-inspected again before this target encodes.
    if kind == "image" and not inspected.animation_video and inspected.image_data is None:
        inspected.validate_format(kind)
        allowed = ("gif", "webp", "apng", "mp4") if inspected.animated else local_conversion._ENCODERS
        if target in allowed:
            return
    if target not in available_download_conversions(inspected, kind, max_height=max_height):
        raise DownloadError("This output format is not available for the checked media. Choose another compatible format.")
