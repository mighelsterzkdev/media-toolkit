"""Frame-preserving GIF, APNG and WebP conversion for checked local files."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import warnings

from PIL import Image, features

from .conversions import _image_has_transparency
from .errors import DownloadError
from .local_conversion import _publish_unique, _snapshot, _work_directory

MAX_ANIMATION_FRAMES = 1_000
MAX_ANIMATION_PIXELS = 80_000_000
ANIMATION_FORMATS = {"GIF": "gif", "PNG": "apng", "WEBP": "webp"}


@contextmanager
def _frames(data: bytes):
    frames = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as opened:
                native = ANIMATION_FORMATS.get(opened.format)
                count = getattr(opened, "n_frames", 1)
                if native is None or not getattr(opened, "is_animated", False) or count < 2:
                    raise DownloadError("This file is not a supported animation.")
                # APNG may carry a separate static poster. It is not an animation frame.
                first = 1 if opened.info.get("default_image") else 0
                count -= first
                if count > MAX_ANIMATION_FRAMES or opened.width * opened.height * count > MAX_ANIMATION_PIXELS:
                    raise DownloadError("This animation exceeds the 1,000 frame or 80 million decoded pixel limit.")
                loop = opened.info.get("loop")
                plays = (0 if loop == 0 else int(loop) + 1) if native == "gif" and loop is not None else int(loop or 0) if native != "gif" else 1
                durations = []
                transparent = False
                for index in range(first, first + count):
                    opened.seek(index)
                    opened.load()
                    frame = opened.convert("RGBA")
                    frames.append(frame)
                    transparent |= _image_has_transparency(frame)
                    # Zero-delay frames have no portable playback timing. Use the
                    # smallest GIF timing unit instead of dropping those frames.
                    duration = float(opened.info.get("duration", 100))
                    durations.append(max(1, round(duration)) if duration > 0 else 10)
                yield native, frames, durations, plays, transparent
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError) as error:
        raise DownloadError("This animation is too large to process.") from error
    except (OSError, ValueError, EOFError, SyntaxError) as error:
        raise DownloadError("This animation is incomplete or could not be decoded.") from error
    finally:
        for frame in frames:
            frame.close()


def animation_targets(native, *, mp4_available=False):
    Image.init()
    result = [target for target, encoder in (("gif", "GIF"), ("webp", "WEBP"), ("apng", "PNG"))
              if target != native and encoder in Image.SAVE_ALL
              and (target != "webp" or features.check("webp"))]
    if mp4_available:
        result.append("mp4")
    return result


def inspect_animation(source, data, signature, *, mp4_available=False):
    with _frames(data) as (native, frames, durations, plays, transparent):
        targets = animation_targets(native, mp4_available=mp4_available)
        notes = {
            "gif": "Animated GIF: up to 256 colors per frame and 1-bit transparency. Timing rounds to 10 ms; partial transparency uses the selected background.",
            "webp": "Lossless animated WebP. Animation frames, transparency and playback timing are kept.",
            "apng": "Lossless animated PNG. Animation frames, transparency and playback timing are kept.",
            "mp4": "H.264 video of one animation cycle. Transparency uses the selected background; dimensions pad to even pixels.",
        }
        return {"name": source.name, "source": str(source), "signature": signature,
                "format": native, "width": frames[0].width, "height": frames[0].height,
                "size": len(data), "media_type": "animation", "animated": True, "pages": len(frames),
                "duration": sum(durations) / 1_000, "has_audio": False, "transparent": transparent,
                "targets": targets, "target_notes": {key: notes[key] for key in targets},
                "requires_tools": False, "error": None}


def _gif_frame(frame, background):
    with Image.new("RGB", frame.size, background) as flattened, frame.getchannel("A") as alpha:
        flattened.paste(frame, mask=alpha)
        palette = flattened.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        with alpha.point(lambda value: 255 if value == 0 else 0) as transparent:
            palette.paste(255, mask=transparent)
        return palette


def _save_sequence(frames, durations, plays, target, output, background):
    first, *remaining = frames
    if target == "gif":
        converted = [_gif_frame(frame, background) for frame in frames]
        try:
            options = {"loop": 0 if plays == 0 else plays - 1} if plays != 1 else {}
            converted[0].save(output, "GIF", save_all=True, append_images=converted[1:],
                              duration=[max(10, round(value / 10) * 10) for value in durations],
                              disposal=2, transparency=255, background=255, optimize=False, **options)
        finally:
            for frame in converted:
                frame.close()
    elif target == "webp":
        first.save(output, "WEBP", save_all=True, append_images=remaining, duration=durations,
                   loop=plays, lossless=True, exact=True, method=4)
    else:
        first.save(output, "PNG", save_all=True, append_images=remaining, duration=durations,
                   loop=plays, disposal=0, blend=0)


def convert_animation(media, target, output_dir, background, progress_hook, *, work_dir=None, encode_mp4=None):
    source = Path(media["source"]).resolve()
    data, signature = _snapshot(source)
    if signature != media["signature"]:
        raise DownloadError("The source file changed after it was checked. Add it again before converting.")
    try:
        with _frames(data) as (native, frames, durations, plays, _transparent):
            if target not in animation_targets(native, mp4_available=encode_mp4 is not None):
                raise DownloadError("Choose a different compatible output format for this animation.")
            destination = Path(output_dir).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            with _work_directory(destination, work_dir) as temporary:
                output = temporary / f"output.{target}"
                if progress_hook:
                    progress_hook({"status": "converting"})
                if target == "mp4":
                    # FFmpeg cannot decode all animated WebP variants. Feed a
                    # checked APNG snapshot so every supported source keeps frames.
                    flattened = []
                    try:
                        for frame in frames:
                            rgb = Image.new("RGB", frame.size, background)
                            with frame.getchannel("A") as alpha:
                                rgb.paste(frame, mask=alpha)
                            flattened.append(rgb)
                        prepared = temporary / "animation.apng"
                        _save_sequence(flattened, durations, 1, "apng", prepared, background)
                        encode_mp4(prepared, output)
                    finally:
                        for frame in flattened:
                            frame.close()
                else:
                    with output.open("xb") as encoded:
                        _save_sequence(frames, durations, plays, target, encoded, background)
                        encoded.flush()
                        os.fsync(encoded.fileno())
                return _publish_unique(output, source, target, destination)
    except PermissionError as error:
        raise DownloadError("The converted animation could not be saved. Check the destination folder's permissions.") from error
    except (OSError, ValueError) as error:
        raise DownloadError("The animation could not be converted. Choose another destination or try again.") from error
