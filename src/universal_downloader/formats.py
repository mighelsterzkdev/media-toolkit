"""The only download choices: keep the source media, without conversion."""

MEDIA_TYPES = {"video": "Video", "audio": "Audio", "image": "Image / thumbnail"}


def normalize_type(value: str) -> str:
    value = value.lower()
    return "image" if value == "thumbnail" else value
