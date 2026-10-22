"""Convert a private downloaded source with the existing local media engines."""

from pathlib import Path
import os
import tempfile

from PIL import Image

from . import local_conversion, local_media
from .errors import DownloadError
from .limits import validate_file_limits
from .local_animation import _frames, _save_sequence
from .storage import publish_file


def _resized_animation(checked, target, maximum, directory):
    """Prepare bounded APNG frames so every supported animation keeps timing."""
    source = Path(checked["source"])
    data, signature = local_conversion._snapshot(source)
    if signature != checked["signature"]:
        raise DownloadError("The downloaded animation changed while it was being checked. Check the link again.")
    maximum = maximum - maximum % 2 if target == "mp4" else maximum
    if maximum < 1:
        raise DownloadError("This output needs a larger maximum video height.")
    prepared = directory / "resized.apng"
    resized = []
    try:
        with _frames(data) as (_native, frames, durations, plays, _transparent):
            height = min(frames[0].height, maximum)
            width = max(1, round(frames[0].width * height / frames[0].height))
            for frame in frames:
                resized.append(frame.resize((width, height), Image.Resampling.LANCZOS))
            with prepared.open("xb") as output:
                _save_sequence(resized, durations, plays, "apng", output, "#FFFFFF")
                output.flush()
                os.fsync(output.fileno())
    finally:
        for frame in resized:
            frame.close()
    result = local_media.inspect_local_files([prepared])[0]
    if result.get("error"):
        raise DownloadError(result["error"])
    return result


def convert_downloaded_file(source, target, destination, *, inspected, kind, max_height=None, max_size=None,
                            progress_hook=None, logger=None):
    source, destination = Path(source), Path(destination)
    checked = local_media.inspect_local_files([source], max_height=max_height)[0]
    if checked.get("error"):
        raise DownloadError(checked["error"])
    expected = "animation" if kind == "image" and inspected.animated and not inspected.animation_video else "video" if inspected.animation_video else kind
    if checked["media_type"] != expected:
        raise DownloadError("The downloaded file does not match the checked media type. Check the link again.")
    # Every video target uses the same checked geometry/height plan. Image
    # animations keep their bounded frame-preserving preparation when resized.
    height = checked.get("height")
    resize = max_height is not None and (height is None or height > max_height
             or target == "mp4" and height + height % 2 > max_height)
    resize_animation = resize and checked["media_type"] == "animation" and target in ("gif", "webp", "apng", "mp4")
    if target not in checked["targets"] and not resize_animation:
        # Adaptive renditions may already match the chosen codec profile. A
        # matching extension alone is insufficient: encoding can also be absent
        # because its encoder or dimension requirements could not be satisfied.
        if (checked["media_type"] in ("audio", "video") and checked["format"] == target
                and checked.get("_native_compatible")):
            validate_file_limits(source, max_size=max_size, max_height=max_height, ffmpeg=local_media._tools()[0].parent)
            return publish_file(source, destination)
        raise DownloadError("The downloaded file cannot be converted to the chosen format. Choose another compatible output.")
    with tempfile.TemporaryDirectory(prefix=".desktop-convert-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        if resize_animation:
            if progress_hook:
                progress_hook({"status": "converting", "operation": "encoding", "progress": None, "speed": None, "eta": None})
            checked = _resized_animation(checked, target, max_height, directory)
        if resize_animation and target == "apng":
            # The checked intermediate already is the final resized APNG.
            converted = Path(checked["source"])
        else:
            converted = local_media.convert_local_file(checked, target, directory,
                                                   background="#FFFFFF", progress_hook=progress_hook,
                                                   max_height=max_height)
        validate_file_limits(converted, max_size=max_size, max_height=max_height,
                             ffmpeg=local_media._tools()[0].parent if max_height is not None and target in local_media._VIDEO else None)
        return publish_file(converted, destination)
