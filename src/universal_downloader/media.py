"""Extract audio with stream copying; never fall back to re-encoding."""

from pathlib import Path
import shutil
import tempfile

from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import PostProcessingError


AUDIO_EXTENSIONS = ("m4a", "mp3", "opus", "ogg", "flac", "wav", "aac", "aiff", "mka", "webm", "mp4", "wma", "ac3", "eac3", "dts", "amr")


def original_path(path: Path, extension: str) -> Path:
    return path.with_name(f"{path.stem.removesuffix('.source')}.{extension}")


def _publish_file(source: Path, destination: Path) -> bool:
    try:
        output = destination.open("xb")
    except FileExistsError:
        if not destination.is_file():
            raise PostProcessingError(f"The output path is not a file: {destination}")
        return False
    try:
        with output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return True


class NativeAudio(FFmpegPostProcessor):
    def run(self, information):
        source = Path(information["filepath"])
        try:
            streams = self.get_metadata_object(str(source)).get("streams") or []
        except (ValueError, OSError) as error:
            raise PostProcessingError("Could not read the audio stream in the downloaded file.") from error
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if audio is None:
            raise PostProcessingError("No audio track was found in the downloaded media.")
        has_video = any(
            stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")
            for stream in streams
        )
        extension = information["ext"]
        if has_video:
            codec = audio.get("codec_name") or ""
            extension = {
                "aac": "m4a", "alac": "m4a", "mp3": "mp3", "opus": "opus",
                "vorbis": "ogg", "flac": "flac", "ac3": "ac3", "eac3": "eac3",
                "dts": "dts", "amr_nb": "amr", "amr_wb": "amr",
                "wmav1": "wma", "wmav2": "wma", "wmapro": "wma", "wmalossless": "wma",
            }.get(codec, "wav" if codec.startswith("pcm_") else "mka")
        destination = original_path(source, extension)
        if has_video and not destination.is_file():
            with tempfile.TemporaryDirectory(prefix=".unidown-", dir=source.parent) as temporary:
                extracted = Path(temporary) / f"audio.{extension}"
                self.run_ffmpeg(str(source), str(extracted), [
                    "-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "copy",
                ])
                _publish_file(extracted, destination)
        elif not has_video:
            _publish_file(source, destination)
        information["filepath"] = str(destination)
        information["ext"] = extension
        return [str(source)], information
