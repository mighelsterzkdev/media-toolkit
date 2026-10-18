"""Output choices shared by the terminal menus and download engine."""

VIDEO_FORMATS = {
    "video": "Original / automatic (no conversion)",
    "mp4": "MP4",
    "mkv": "MKV",
    "webm": "WebM",
    "mov": "MOV",
    "avi": "AVI",
    "m4v": "M4V",
    "flv": "FLV",
    "ts": "TS (MPEG transport stream)",
}
AUDIO_FORMATS = {
    "mp3": "MP3",
    "m4a": "M4A (AAC)",
    "aac": "AAC",
    "opus": "Opus",
    "ogg": "OGG (Vorbis)",
    "flac": "FLAC (lossless)",
    "wav": "WAV (PCM)",
    "alac": "ALAC (lossless M4A)",
    "audio": "Original audio (copy when possible)",
}
IMAGE_FORMATS = {
    "jpg": "JPG / JPEG",
    "png": "PNG",
    "webp": "WebP",
    "avif": "AVIF",
    "gif": "GIF (static)",
    "bmp": "BMP",
    "tiff": "TIFF",
    "ico": "ICO (Windows icon)",
    "image": "Original image / thumbnail",
}
FORMAT_ALIASES = {"jpeg": "jpg", "tif": "tiff", "vorbis": "ogg", "thumbnail": "image"}
ALL_FORMATS = (*VIDEO_FORMATS, *AUDIO_FORMATS, *IMAGE_FORMATS)
LOSSY_AUDIO_FORMATS = frozenset({"mp3", "m4a", "aac", "opus", "ogg"})
VIDEO_QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
AUDIO_BITRATES = ("64", "96", "128", "160", "192", "256", "320")


def normalize_format(value: str) -> str:
    value = value.lower()
    return FORMAT_ALIASES.get(value, value)


def output_extension(media_format: str) -> str:
    return "m4a" if media_format == "alac" else media_format
