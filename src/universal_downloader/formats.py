"""Media choices and the small set of optional conversion targets."""

MEDIA_TYPES = {"video": "Video", "audio": "Audio", "image": "Image / thumbnail"}
CONVERSIONS = {"mp3": "MP3 audio", "mp4": "MP4 video", "gif": "GIF animation", "png": "PNG image", "jpg": "JPG image"}


def normalize_type(value: str) -> str:
    value = value.lower()
    return "image" if value == "thumbnail" else value


def normalize_conversion(value: str) -> str:
    value = value.lower()
    return "jpg" if value == "jpeg" else value
