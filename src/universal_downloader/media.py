"""Explicit FFmpeg profiles for the media formats offered by the app."""

from pathlib import Path
import shutil
import tempfile

from yt_dlp.postprocessor.ffmpeg import FFmpegExtractAudioPP, FFmpegPostProcessor
from yt_dlp.utils import PostProcessingError

from .formats import AUDIO_FORMATS, output_extension


def converted_path(path: Path, extension: str) -> Path:
    return path.with_name(f"{path.stem.removesuffix('.source')}.{extension}")


def _publish_file(source: Path, destination: Path) -> None:
    try:
        output = destination.open("xb")
    except FileExistsError:
        if not destination.is_file():
            raise PostProcessingError(f"The output path is not a file: {destination}")
    else:
        try:
            with output, source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


class NativeAudio(FFmpegExtractAudioPP):
    def __init__(self, downloader):
        super().__init__(downloader, preferredcodec="best")

    def run(self, information):
        obsolete, information = super().run(information)
        source = Path(information["filepath"])
        destination = converted_path(source, information["ext"])
        _publish_file(source, destination)
        information["filepath"] = str(destination)
        return [*obsolete, str(source)], information


def encoding_options(media_format: str, bitrate: str) -> list[str]:
    if media_format in AUDIO_FORMATS:
        common = ["-map", "0:a:0", "-vn", "-sn", "-dn"]
        profiles = {
            "mp3": ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"],
            "m4a": ["-c:a", "aac", "-b:a", f"{bitrate}k", "-movflags", "+faststart"],
            "aac": ["-c:a", "aac", "-b:a", f"{bitrate}k", "-f", "adts"],
            "opus": ["-c:a", "libopus", "-b:a", f"{bitrate}k"],
            "ogg": ["-c:a", "libvorbis", "-b:a", f"{bitrate}k"],
            "flac": ["-c:a", "flac"],
            "wav": ["-c:a", "pcm_s16le"],
            "alac": ["-c:a", "alac", "-f", "ipod"],
        }
        return common + profiles[media_format]
    common = [
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p",
    ]
    if media_format == "webm":
        return common + [
            "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-cpu-used", "2",
            "-c:a", "libopus", "-b:a", "192k",
        ]
    if media_format == "avi":
        return common + ["-c:v", "libxvid", "-q:v", "4", "-vtag", "XVID", "-c:a", "libmp3lame", "-b:a", "192k"]
    if media_format == "flv":
        return common + ["-c:v", "flv", "-q:v", "4", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100"]
    options = common + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
    ]
    if media_format in ("mp4", "mov", "m4v"):
        options += ["-movflags", "+faststart"]
    if media_format == "m4v":
        options += ["-f", "ipod"]
    return options


class ConvertMedia(FFmpegPostProcessor):
    def __init__(self, downloader, media_format: str, bitrate: str):
        super().__init__(downloader)
        self.media_format = media_format
        self.bitrate = bitrate

    def run(self, information):
        source = Path(information["filepath"])
        extension = output_extension(self.media_format)
        destination = converted_path(source, extension)
        if not destination.is_file():
            self.to_screen(f"Converting to {self.media_format.upper()}...")
            # A separate source name allows a failed conversion to be retried safely.
            # Only files created by this operation are placed in this temporary directory.
            with tempfile.TemporaryDirectory(prefix=".unidown-", dir=source.parent) as temporary:
                encoded = Path(temporary) / f"output.{extension}"
                self.run_ffmpeg(str(source), str(encoded), encoding_options(self.media_format, self.bitrate))
                _publish_file(encoded, destination)
        information["filepath"] = str(destination)
        information["ext"] = extension
        return [str(source)], information
