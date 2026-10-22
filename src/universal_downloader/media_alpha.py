"""Explicit alpha decoding and standard FFmpeg background compositing."""

from __future__ import annotations

import re

from .errors import DownloadError


def has_alpha(stream):
    """Include WebM's separate alpha plane, which ffprobe labels yuv420p."""
    pixel_format = str(stream.get("pix_fmt", "")).lower()
    if pixel_format == "pal8" or pixel_format.startswith(("yuva", "gbrap", "rgba", "bgra", "argb", "abgr", "ya8", "ya16", "ayuv", "vuya")):
        return True
    tags = stream.get("tags") or {}
    return any(str(key).lower() == "alpha_mode" and str(value) == "1" for key, value in tags.items())


def decoder_options(stream):
    """Select libvpx only when decoding the chosen VP8/VP9 alpha stream.

    FFmpeg's native VP8/VP9 decoders omit WebM's separate alpha plane. Input
    options must precede ``-i``; stream copies and audio extraction need none.
    """
    decoder = {"vp8": "libvpx", "vp9": "libvpx-vp9"}.get(stream.get("codec_name"))
    if not decoder or not has_alpha(stream):
        return []
    index = stream.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise DownloadError("The selected video stream has invalid metadata.")
    return [f"-c:{index}", decoder]


def composite_filters(background, *, preserve_zero=False, prefix="alpha"):
    """Composite straight alpha, optionally retaining fully transparent pixels.

    Use as a one-input/one-output FFmpeg filter graph. Splitting a frame to
    paint its background preserves timing and dimensions without a synthetic
    color input or assumptions about framerate and duration. GIF keeps alpha
    zero while partially transparent pixels use the selected background.
    """
    color = {"#FFFFFF": "white", "#000000": "black"}.get(background)
    if color is None:
        raise DownloadError("Choose a white or black conversion background.")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", prefix):
        raise ValueError("Invalid alpha filter label")
    foreground, painted = f"{prefix}_foreground", f"{prefix}_painted"
    original, base = f"{prefix}_original", f"{prefix}_base"
    composite, mask = f"{prefix}_composite", f"{prefix}_mask"
    split = f"split=3[{foreground}][{original}][{mask}]" if preserve_zero else f"split[{foreground}][{original}]"
    graph = (f"format=rgba,{split};[{original}]drawbox=color={color}:t=fill:replace=1[{painted}];"
             f"[{painted}][{foreground}]overlay=shortest=1:format=auto:alpha=straight")
    if preserve_zero:
        graph += (f"[{composite}];[{mask}]alphaextract,lut=c0='if(eq(val,0),0,255)'[{base}];"
                  f"[{composite}][{base}]alphamerge")
    return graph
