"""Content-based local media inspection and non-destructive conversion dispatch."""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import warnings

from PIL import Image, UnidentifiedImageError

from . import local_conversion
from .dependencies import find_ffmpeg, hidden_process_options
from .errors import DownloadError
from .local_animation import ANIMATION_FORMATS, convert_animation, inspect_animation

# Only self-contained formats: never resolve playlists, concat scripts, DASH
# manifests, image sequences or external URLs supplied by a local text file.
_INPUT_FORMATS = ",".join(("mov", "matroska", "webm", "avi", "asf", "flv", "mpeg", "mpegts", "mp3", "wav", "flac",
                          "aac", "ogg", "aiff", "ac3", "eac3", "dts", "amr", "ape", "wv", "tta", "au", "caf", "voc",
                          "mpc", "mpc8", "rm", "mxf", "dsf", "iff", "w64", "tak", "mlp", "truehd", "loas", "oma", "nut", "dv"))
_AUDIO = {
    "mp3": ("libmp3lame", "mp3", ["-q:a", "2"]),
    "wav": ("pcm_s24le", "wav", []),
    "flac": ("flac", "flac", ["-compression_level", "5"]),
    "m4a": ("aac", "ipod", ["-b:a", "192k"]),
    "aac": ("aac", "adts", ["-b:a", "192k"]),
    "opus": ("libopus", "opus", ["-b:a", "128k"]),
    "ogg": ("libvorbis", "ogg", ["-q:a", "5"]),
    "aiff": ("pcm_s24be", "aiff", []),
}
_VIDEO = {"mp4": ("libx264", "aac", "mp4"), "mkv": ("libx264", "aac", "matroska"),
          "mov": ("libx264", "aac", "mov"), "webm": ("libvpx-vp9", "libopus", "webm")}
_NOTES = {
    "mp3": "High-quality VBR MP3 audio (quality 2).",
    "wav": "Uncompressed 24-bit PCM audio. Files can be large.",
    "flac": "Lossless FLAC audio. Does not restore detail lost in the source.",
    "m4a": "AAC audio at 192 kb/s in an M4A container.", "aac": "AAC audio at 192 kb/s in an ADTS container.",
    "opus": "Opus audio at 128 kb/s.", "ogg": "Ogg Vorbis audio (quality 5).",
    "aiff": "Uncompressed 24-bit PCM audio in an AIFF container.",
    "mp4": "H.264 video (CRF 20) with AAC audio. Dimensions pad to even pixels.",
    "mkv": "H.264 video (CRF 20) with AAC audio in Matroska. Dimensions pad to even pixels.",
    "mov": "H.264 video (CRF 20) with AAC audio in QuickTime. Dimensions pad to even pixels.",
    "webm": "VP9 video (CRF 30) with Opus audio. Dimensions pad to even pixels.",
}


def _tools(location=None):
    directory = Path(location) if location is not None else find_ffmpeg()
    if directory is None:
        raise DownloadError("Install the media tools to check and convert audio or video files.")
    if directory.is_file():
        directory = directory.parent
    suffix = ".exe" if (directory / "ffmpeg.exe").is_file() else ""
    ffmpeg, ffprobe = directory / f"ffmpeg{suffix}", directory / f"ffprobe{suffix}"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise DownloadError("Install the media tools to check and convert audio or video files.")
    return ffmpeg.resolve(), ffprobe.resolve()


@lru_cache(maxsize=8)
def _capabilities_cached(executable, size, modified):
    try:
        encoders = subprocess.run([executable, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                  timeout=15, check=True, **hidden_process_options()).stdout
        muxers = subprocess.run([executable, "-hide_banner", "-muxers"], capture_output=True, text=True,
                                timeout=15, check=True, **hidden_process_options()).stdout
        return (frozenset(re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", encoders, re.MULTILINE)),
                frozenset(name for names in re.findall(r"^\s*E\s+(\S+)", muxers, re.MULTILINE) for name in names.split(",")))
    except (OSError, subprocess.SubprocessError) as error:
        raise DownloadError("The media tools could not start. Reinstall them and try again.") from error


def _capabilities(ffmpeg):
    recorded = ffmpeg.stat()
    return _capabilities_cached(str(ffmpeg), recorded.st_size, recorded.st_mtime_ns)


def _can_mp4(location):
    try:
        ffmpeg, _ffprobe = _tools(location)
        encoders, muxers = _capabilities(ffmpeg)
        return "libx264" in encoders and "mp4" in muxers
    except DownloadError:
        return False


def _stream_snapshot(source, destination=None):
    """Hash arbitrarily large media without reading the full file into memory."""
    try:
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            raise DownloadError("Select a local media file, rather than a folder or device.")
        digest = sha256()
        written = 0
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if local_conversion._stat_identity(opened) != local_conversion._stat_identity(before):
                raise DownloadError("The source file changed while it was being read. Add it again.")
            while chunk := stream.read(1_048_576):
                digest.update(chunk)
                written += len(chunk)
                if destination is not None:
                    destination.write(chunk)
            after = os.fstat(stream.fileno())
        current = source.stat()
        if (local_conversion._stat_identity(before) != local_conversion._stat_identity(after)
                or local_conversion._stat_identity(after) != local_conversion._stat_identity(current)
                or opened.st_ctime_ns != after.st_ctime_ns or written != after.st_size):
            raise DownloadError("The source file changed while it was being read. Add it again.")
        return {"size": written, "mtime_ns": current.st_mtime_ns, "ctime_ns": current.st_ctime_ns, "sha256": digest.hexdigest()}
    except FileNotFoundError as error:
        raise DownloadError("The selected file is no longer available. Add it again.") from error
    except PermissionError as error:
        raise DownloadError("The selected file could not be read. Check its permissions.") from error
    except OSError as error:
        raise DownloadError("The selected file could not be read or copied. Check available disk space and permissions.") from error


def _probe(source, executable):
    try:
        completed = subprocess.run([str(executable), "-v", "error", "-protocol_whitelist", "file,pipe",
                                    "-format_whitelist", _INPUT_FORMATS, "-show_streams", "-show_format", "-of", "json", str(source)],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                   check=True, **hidden_process_options())
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError("Invalid probe result")
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise DownloadError("This file is not a readable supported image, audio or video file. Playlists and documents are not media files.") from error


def _positive(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None


def _streams(metadata):
    streams = metadata.get("streams") or []
    video = [stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    return video, audio


def _native(metadata, source, has_video):
    formats = metadata.get("format", {}).get("format_name", "").split(",")
    if "mov" in formats:
        brand = str(metadata.get("format", {}).get("tags", {}).get("major_brand", "")).strip()
        if brand == "qt":
            return "mov"
        if brand.startswith("3g"):
            return "3gp"
        return "mp4" if has_video else "m4a"
    if "matroska" in formats or "webm" in formats:
        with source.open("rb") as stream:
            header = stream.read(4_096)
        return "webm" if b"\x42\x82\x84webm" in header else "mkv"
    if "ogg" in formats:
        codecs = {stream.get("codec_name") for stream in metadata.get("streams", [])}
        return "opus" if not has_video and "opus" in codecs else "ogg"
    native = next((item for item in formats if item), "media")
    return {"asf": "wmv" if has_video else "wma", "aiff": "aiff"}.get(native, native)


def _media_details(metadata, source, ffmpeg):
    video, audio = _streams(metadata)
    if not video and not audio:
        raise DownloadError("No playable audio or video stream was found in this file.")
    native = _native(metadata, source, bool(video))
    encoders, muxers = _capabilities(ffmpeg)
    channels = int(audio[0].get("channels", 0)) if audio else 0
    targets = [target for target, (encoder, muxer, _options) in _AUDIO.items()
               if audio and encoder in encoders and muxer in muxers and (video or target != native)
               and (target != "flac" or channels <= 8)]
    if video:
        for target, (encoder, audio_encoder, muxer) in _VIDEO.items():
            compatible_native_mp4 = native == "mp4" and video[0].get("codec_name") == "h264" and (not audio or audio[0].get("codec_name") == "aac")
            if encoder in encoders and muxer in muxers and (not audio or audio_encoder in encoders) and (target != native or target == "mp4" and not compatible_native_mp4):
                targets.append(target)
    notes = {}
    extra = len(video) > 1 or len(audio) > 1 or any(stream.get("codec_type") in ("subtitle", "data", "attachment") for stream in metadata.get("streams", []))
    for target in targets:
        note = _NOTES[target]
        if target in _AUDIO and video:
            note += " Extracts the first audio track; video is omitted."
        elif target in _AUDIO and len(audio) > 1:
            note += " Converts the first audio track only."
        elif target in _VIDEO and extra:
            note += " Keeps the first video and first audio track; other tracks and subtitles are omitted."
        if channels > 2 and target not in ("wav", "flac", "aiff"):
            note += " Downmixes multichannel audio to stereo."
        if video and target in _VIDEO:
            pixel_format = video[0].get("pix_fmt", "")
            if pixel_format.startswith(("yuva", "gbrap", "rgba", "bgra", "argb", "abgr")):
                note += " This video output does not preserve transparency."
            if video[0].get("color_transfer") in ("smpte2084", "arib-std-b67"):
                note += " HDR is encoded without tone mapping; brightness and colors may change."
        notes[target] = note
    duration = _positive(metadata.get("format", {}).get("duration"))
    if duration is None:
        duration = max((_positive(stream.get("duration")) or 0 for stream in video + audio), default=0) or None
    return {"media_type": "video" if video else "audio", "format": native, "width": video[0].get("width") if video else None,
            "height": video[0].get("height") if video else None, "has_audio": bool(audio), "duration": duration,
            "targets": targets, "target_notes": notes, "requires_tools": True,
            "_video_index": video[0]["index"] if video else None, "_audio_index": audio[0]["index"] if audio else None,
            "_audio_channels": channels}


def _image_header(source):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                return image.format, bool(getattr(image, "is_animated", False))
    except UnidentifiedImageError:
        return None, False
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise DownloadError("This image is too large to process.") from error
    except (OSError, ValueError, SyntaxError):
        return None, False


def inspect_local_files(paths, *, ffmpeg_location=None):
    results = []
    for selected in paths:
        result = {"name": "File", "source": None, "signature": None, "format": None, "width": None, "height": None,
                  "size": None, "media_type": "image", "animated": False, "pages": 1, "duration": None,
                  "has_audio": False, "transparent": False, "targets": [], "target_notes": {}, "requires_tools": False, "error": None}
        try:
            if not isinstance(selected, (str, os.PathLike)):
                raise DownloadError("Select a local media file.")
            source = Path(selected).resolve()
            result.update(name=source.name or "File", source=str(source))
            if not source.is_file():
                raise DownloadError("The selected file is unavailable. Select a local media file.")
            image_format, animated = _image_header(source)
            # Pillow also recognizes MPEG headers, although MPEG is a video
            # container rather than a supported still-image decoder here.
            if image_format is not None and image_format != "MPEG":
                if animated and image_format in ANIMATION_FORMATS:
                    data, signature = local_conversion._snapshot(source)
                    result.update(inspect_animation(source, data, signature, mp4_available=_can_mp4(ffmpeg_location)))
                else:
                    result.update(local_conversion.inspect_local_files([source])[0])
                results.append(result)
                continue
            ffmpeg, ffprobe = _tools(ffmpeg_location)
            signature = _stream_snapshot(source)
            metadata = _probe(source, ffprobe)
            after = source.stat()
            if after.st_size != signature["size"] or after.st_mtime_ns != signature["mtime_ns"] or after.st_ctime_ns != signature["ctime_ns"]:
                raise DownloadError("The source file changed while it was being checked. Add it again.")
            result.update(signature=signature, size=signature["size"], **_media_details(metadata, source, ffmpeg))
        except DownloadError as error:
            result.update(error=str(error), targets=[])
        except (OSError, ValueError, TypeError, RuntimeError, KeyError):
            result.update(error="The selected file could not be checked. Add it again.", targets=[])
        results.append(result)
    return results


def _run_ffmpeg(ffmpeg, arguments, output):
    try:
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n", *arguments, str(output)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1_800, check=True, **hidden_process_options())
        if not output.is_file() or output.stat().st_size == 0:
            raise DownloadError("The conversion did not produce a complete file. Try another output format.")
        with output.open("r+b") as stream:
            os.fsync(stream.fileno())
    except subprocess.TimeoutExpired as error:
        raise DownloadError("This conversion exceeded the 30 minute limit. Try a shorter file.") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise DownloadError("The media could not be converted to this format. Its codec or channel layout may be unsupported.") from error


def _video_arguments(target, *, audio=True):
    video_encoder, audio_encoder, muxer = _VIDEO[target]
    options = ["-c:v", video_encoder, "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", "-threads", "2"]
    options += ["-crf", "30", "-b:v", "0", "-deadline", "good", "-cpu-used", "3"] if target == "webm" else ["-crf", "20", "-preset", "medium"]
    if audio:
        options += ["-c:a", audio_encoder, "-b:a", "128k" if target == "webm" else "192k"]
    else:
        options += ["-an"]
    if target in ("mp4", "mov"):
        options += ["-movflags", "+faststart"]
    return options + ["-f", muxer]


def convert_local_file(media, target, output_dir, background="#FFFFFF", progress_hook=None, *, work_dir=None, ffmpeg_location=None):
    if not isinstance(media, dict) or media.get("error") or not media.get("source") or not isinstance(media.get("signature"), dict):
        raise DownloadError("Check this file before converting it.")
    if not isinstance(target, str) or target not in media.get("targets", []):
        raise DownloadError("Choose a different compatible output format for this file.")
    if background not in ("#FFFFFF", "#000000"):
        raise DownloadError("Choose a white or black conversion background.")
    kind = media.get("media_type", "image")
    if kind == "image":
        return local_conversion.convert_local_image(media, target, output_dir, background, progress_hook, work_dir=work_dir)
    if kind == "animation":
        encode_mp4 = None
        if target == "mp4":
            ffmpeg, _ffprobe = _tools(ffmpeg_location)
            if not _can_mp4(ffmpeg_location):
                raise DownloadError("The installed media tools do not include an H.264 MP4 encoder.")

            def encode_mp4(prepared, output):
                _run_ffmpeg(ffmpeg, ["-protocol_whitelist", "file,pipe", "-f", "apng", "-ignore_loop", "1", "-i", str(prepared),
                                     "-map", "0:v:0", "-map_metadata", "-1", *_video_arguments("mp4", audio=False)], output)

        return convert_animation(media, target, output_dir, background, progress_hook, work_dir=work_dir, encode_mp4=encode_mp4)
    if kind not in ("audio", "video"):
        raise DownloadError("This file type is not supported for conversion.")
    ffmpeg, ffprobe = _tools(ffmpeg_location)
    source = Path(media["source"]).resolve()
    destination = Path(output_dir).resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with local_conversion._work_directory(destination, work_dir) as temporary:
            snapshot = temporary / "source.media"
            with snapshot.open("xb") as copied:
                signature = _stream_snapshot(source, copied)
                copied.flush()
                os.fsync(copied.fileno())
            if signature != media["signature"]:
                raise DownloadError("The source file changed after it was checked. Add it again before converting.")
            checked = _media_details(_probe(snapshot, ffprobe), snapshot, ffmpeg)
            if target not in checked["targets"]:
                raise DownloadError("Choose a different compatible output format for this file.")
            output = temporary / f"output.{target}"
            arguments = ["-protocol_whitelist", "file,pipe", "-format_whitelist", _INPUT_FORMATS, "-i", str(snapshot), "-map_metadata", "-1", "-map_chapters", "-1"]
            if target in _AUDIO:
                encoder, muxer, options = _AUDIO[target]
                arguments += ["-map", f"0:{checked['_audio_index']}", "-vn", "-sn", "-dn", "-c:a", encoder, *options, "-f", muxer]
            else:
                arguments += ["-map", f"0:{checked['_video_index']}"]
                if checked["has_audio"]:
                    arguments += ["-map", f"0:{checked['_audio_index']}"]
                arguments += ["-sn", "-dn", *_video_arguments(target, audio=checked["has_audio"])]
            if checked["_audio_channels"] > 2 and target not in ("wav", "flac", "aiff"):
                arguments += ["-ac", "2"]
            if progress_hook:
                progress_hook({"status": "converting"})
            _run_ffmpeg(ffmpeg, arguments, output)
            return local_conversion._publish_unique(output, source, target, destination)
    except PermissionError as error:
        raise DownloadError("The converted file could not be saved. Check the destination folder's permissions.") from error
    except OSError as error:
        raise DownloadError("The converted file could not be saved. Check disk space and the destination folder.") from error
