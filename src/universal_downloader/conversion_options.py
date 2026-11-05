"""Validated per-task conversion choices, independent of global download limits."""

from .errors import DownloadError

DEFAULT_OPTIONS = {"max_height": None, "max_width": None, "quality": "preserve", "max_size": None}
LOSSY_IMAGES = frozenset(("jpg", "webp", "avif"))
LOSSY_AUDIO = frozenset(("mp3", "m4a", "aac", "opus", "ogg"))
VIDEOS = frozenset(("mp4", "mkv", "mov", "webm"))


def validate_options(value=None):
    if value is None:
        return dict(DEFAULT_OPTIONS)
    if not isinstance(value, dict) or set(value) - set(DEFAULT_OPTIONS):
        raise DownloadError("Invalid conversion options.")
    result = {**DEFAULT_OPTIONS, **value}
    if not isinstance(result["quality"], str) or result["quality"] not in ("preserve", "smaller", "balanced", "best"):
        raise DownloadError("Choose an available conversion quality.")
    for field in ("max_height", "max_width"):
        number = result[field]
        if number is not None and (type(number) is not int or not 2 <= number <= 16_384):
            raise DownloadError("Conversion dimensions must be whole numbers between 2 and 16384 pixels.")
    limit = result["max_size"]
    if limit is not None and (type(limit) is not int or not 1_024 <= limit <= 1_073_741_824):
        raise DownloadError("The target file size must be between 1 KiB and 1 GiB.")
    return result


def options_for(media, target, value=None):
    if not isinstance(target, str):
        raise DownloadError("Choose an available output format.")
    result = validate_options(value)
    kind = media.get("media_type", "image")
    if kind != "image":
        result["max_width"] = None
    if kind != "video" or target not in VIDEOS:
        result["max_height"] = None
    lossy = target in (LOSSY_IMAGES if kind == "image" else VIDEOS | LOSSY_AUDIO if kind in ("video", "audio") else ())
    if result["max_size"] is not None and not lossy:
        raise DownloadError("Target file size is supported for JPG, WebP, AVIF, video and lossy audio outputs only.")
    if not lossy:
        result["quality"] = "preserve"
    return result


def changes_output(options):
    return options != DEFAULT_OPTIONS


def check_size(path, options):
    if options["max_size"] is not None and path.stat().st_size > options["max_size"]:
        raise DownloadError("The requested file size could not be reached. Increase the target size or reduce the dimensions. The original file was kept.")
