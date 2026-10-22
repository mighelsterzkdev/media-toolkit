"""Content-based local media inspection and non-destructive conversion dispatch."""

from __future__ import annotations

from functools import lru_cache
from fractions import Fraction
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import warnings
import zlib

from PIL import Image, UnidentifiedImageError

from . import local_conversion
from .dependencies import find_ffmpeg, hidden_process_options
from .errors import DownloadError
from .ffmpeg_progress import run as run_with_progress
from .local_animation import ANIMATION_FORMATS, MAX_ANIMATION_FRAMES, MAX_ANIMATION_PIXELS, convert_animation, inspect_animation
from .media_alpha import composite_filters, decoder_options, has_alpha
from .media_geometry import analyze as analyze_geometry, input_options as geometry_input_options

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
_ANIMATION = {"gif": ("gif", "gif"), "webp": ("libwebp", "webp"), "apng": ("apng", "apng")}
_ENCODING_DIMENSION_LIMITS = {"mp4": 16_384, "mkv": 16_384, "mov": 16_384, "webp": 16_383, "gif": 65_535}
_ANIMATION_NOTES = {
    "gif": "Animated GIF of the video, looping without audio. Up to 256 colors per frame and 1-bit transparency; timing rounds to 10 ms. Partial transparency uses the selected background. Files can be large.",
    "webp": "Lossless animated WebP of the video, looping without audio. Keeps dimensions and transparency; timing rounds to 1 ms. Files can be large.",
    "apng": "Lossless animated PNG of the video, looping without audio. Keeps dimensions and transparency. Files can be large.",
}
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


def _copy_audio(stream, target):
    """Keep only the codec/channel profiles already accepted by our outputs."""
    if not stream:
        return False
    codec = stream.get("codec_name")
    channels = int(stream.get("channels") or 0)
    if target in ("m4a", "aac"):
        return codec == "aac" and stream.get("profile") == "LC" and 0 < channels <= 2
    if target == "mp3":
        return codec == "mp3" and 0 < channels <= 2
    if target == "opus":
        return codec == "opus" and 0 < channels <= 2
    if target == "ogg":
        return codec == "vorbis" and 0 < channels <= 2
    if target == "flac":
        return codec == "flac" and 0 < channels <= 8
    pcm = {"wav": {"pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le"},
           "aiff": {"pcm_s8", "pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_f32be", "pcm_f64be"}}
    return codec in pcm.get(target, ()) and channels > 0


def _copy_video(stream, target):
    if not stream or stream.get("pix_fmt") != "yuv420p":
        return False
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        return False
    if target == "webm":
        return stream.get("codec_name") == "vp9" and stream.get("profile") in ("Profile 0", 0)
    return (target in ("mp4", "mkv", "mov") and stream.get("codec_name") == "h264"
            and stream.get("profile") in ("Constrained Baseline", "Baseline", "Main", "High"))


def _stream_plan(video, audio, target):
    audio_target = "opus" if target == "webm" else "m4a" if target in _VIDEO else target
    video_copy = bool(video and target in _VIDEO and _copy_video(video[0], target))
    audio_copy = bool(audio and _copy_audio(audio[0], audio_target))
    copies_all = audio_copy if target in _AUDIO else video_copy and (not audio or audio_copy)
    return {"video_copy": video_copy, "audio_copy": audio_copy,
            "operation": "copying" if copies_all else "encoding"}


def _conversion_note(target, plan, *, has_audio, audio_stream=None):
    if target in _AUDIO:
        if plan["audio_copy"]:
            codec = {"m4a": "AAC", "aac": "AAC", "ogg": "Vorbis"}.get(target, target.upper())
            if target in ("wav", "aiff"):
                sample = re.fullmatch(r"pcm_([suf])(\d+)(?:le|be)?", (audio_stream or {}).get("codec_name", ""))
                codec = f"{sample[2]}-bit {'float ' if sample[1] == 'f' else ''}PCM" if sample else "PCM"
            return f"Copies the existing {codec} audio without re-encoding."
        return _NOTES[target]
    codec = "VP9" if target == "webm" else "H.264"
    container = {"mp4": "MP4", "mkv": "Matroska", "mov": "QuickTime", "webm": "WebM"}[target]
    if plan["video_copy"]:
        note = f"Copies the existing {codec} video into {container} without re-encoding."
    else:
        quality = 30 if target == "webm" else 20
        note = f"{codec} video (CRF {quality}) in {container}. Dimensions pad to even pixels."
    if has_audio:
        audio = "Opus" if target == "webm" else "AAC"
        if plan["audio_copy"]:
            note += f" Copies the {audio} audio without re-encoding."
        else:
            note += f" Encodes audio as {audio} at {128 if target == 'webm' else 192} kb/s."
    return note


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


@lru_cache(maxsize=8)
def _display_override_cached(executable, size, modified):
    try:
        result = subprocess.run([executable, "-hide_banner", "-h", "full"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15, check=True, **hidden_process_options())
        return "-display_rotation" in result.stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise DownloadError("The media tools could not start. Reinstall them and try again.") from error


def _geometry_input_options(ffmpeg, stream, geometry):
    options = geometry_input_options(stream, geometry)
    if options:
        recorded = ffmpeg.stat()
        if not _display_override_cached(str(ffmpeg), recorded.st_size, recorded.st_mtime_ns):
            raise DownloadError("These video display transforms require FFmpeg 6 or newer. Update the media tools and add the file again.")
    return options


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


def _fits_video_output(geometry, target, max_height):
    try:
        size = geometry.output_size(max_height, even=target in _VIDEO)
        return max(size) <= _ENCODING_DIMENSION_LIMITS.get(target, 65_535)
    except DownloadError:
        return False


def _media_details(metadata, source, ffmpeg, *, max_height=None):
    video, audio = _streams(metadata)
    if not video and not audio:
        raise DownloadError("No playable audio or video stream was found in this file.")
    native = _native(metadata, source, bool(video))
    encoders, muxers = _capabilities(ffmpeg)
    channels = int(audio[0].get("channels", 0)) if audio else 0
    geometry = analyze_geometry(video[0]) if video else None
    if geometry:
        _geometry_input_options(ffmpeg, video[0], geometry)
    transparent = bool(video and has_alpha(video[0]))
    if max_height is not None and (isinstance(max_height, bool) or not isinstance(max_height, int) or max_height < 1):
        raise DownloadError("Choose a positive maximum video height.")
    plans = {target: _stream_plan(video, audio, target) for target in (*_AUDIO, *_VIDEO)}
    resize = bool(geometry and max_height is not None
                  and geometry.display_height + geometry.display_height % 2 > max_height)
    if resize:
        for target in _VIDEO:
            plans[target]["video_copy"] = False
            plans[target]["operation"] = "encoding"
    targets = [target for target, (encoder, muxer, _options) in _AUDIO.items()
               if audio and (plans[target]["audio_copy"] or encoder in encoders) and muxer in muxers
               and (video or target != native or not plans[target]["audio_copy"])
               and (target != "flac" or channels <= 8)]
    if video:
        for target, (encoder, audio_encoder, muxer) in _VIDEO.items():
            plan = plans[target]
            compatible_native = plan["video_copy"] and (not audio or plan["audio_copy"])
            if ((plan["video_copy"] or encoder in encoders) and muxer in muxers
                    and (not audio or plan["audio_copy"] or audio_encoder in encoders)
                    and (max_height is None or max_height >= 2)
                    and (plan["video_copy"] or _fits_video_output(geometry, target, max_height))
                    and (target != native or not compatible_native)):
                targets.append(target)
        for target, (encoder, muxer) in _ANIMATION.items():
            if encoder in encoders and muxer in muxers and _fits_video_output(geometry, target, max_height):
                if target == "webp" and transparent:
                    # FFmpeg's per-frame WebP muxer blends translucent frames
                    # over one another. Use the existing checked Pillow adapter
                    # through APNG instead; its bounded decoder is authoritative.
                    frames = _positive(video[0].get("nb_frames"))
                    size = geometry.output_size(max_height)
                    if ("apng" not in encoders or "apng" not in muxers or frames is not None
                            and (frames > MAX_ANIMATION_FRAMES or size[0] * size[1] * frames > MAX_ANIMATION_PIXELS)):
                        continue
                targets.append(target)
                plans[target] = {"video_copy": False, "audio_copy": False, "operation": "encoding"}
    notes = {}
    extra = len(video) > 1 or len(audio) > 1 or any(stream.get("codec_type") in ("subtitle", "data", "attachment") for stream in metadata.get("streams", []))
    for target in targets:
        plan = plans[target]
        note = _ANIMATION_NOTES[target] if target in _ANIMATION else _conversion_note(target, plan, has_audio=bool(audio),
                                                                                   audio_stream=audio[0] if audio else None)
        if video and target in (*_VIDEO, *_ANIMATION) and max_height is not None:
            note += f" Scales down to fit a maximum height of {max_height} pixels when needed."
        if target in _AUDIO and video:
            note += " Extracts the first audio track; video is omitted."
        elif target in _AUDIO and len(audio) > 1:
            note += " Keeps the first audio track only."
        elif target in _VIDEO and extra:
            note += " Keeps the first video and first audio track; other tracks and subtitles are omitted."
        if channels > 2 and not plan["audio_copy"] and target not in (*_ANIMATION, "wav", "flac", "aiff"):
            note += " Downmixes multichannel audio to stereo."
        if video and target in _VIDEO and not plan["video_copy"]:
            if transparent:
                note += " Transparent areas use the selected background."
            if video[0].get("color_transfer") in ("smpte2084", "arib-std-b67"):
                note += " HDR is encoded without tone mapping; brightness and colors may change."
        if video and target in _ANIMATION and video[0].get("color_transfer") in ("smpte2084", "arib-std-b67"):
            note += " HDR is encoded without tone mapping; brightness and colors may change."
        if target == "webp" and transparent:
            note += " Transparent video uses the image animation limits: 1,000 frames, 80 million decoded pixels and a 128 MiB intermediate."
        notes[target] = note
    duration = _positive(metadata.get("format", {}).get("duration"))
    if duration is None:
        duration = max((_positive(stream.get("duration")) or 0 for stream in video + audio), default=0) or None
    return {"media_type": "video" if video else "audio", "format": native, "width": geometry.display_width if geometry else None,
            "height": geometry.display_height if geometry else None, "has_audio": bool(audio), "duration": duration,
            "transparent": transparent,
            "background_targets": [target for target in targets if transparent and (target == "gif" or target in _VIDEO and not plans[target]["video_copy"])],
            "targets": targets, "target_notes": notes, "requires_tools": True,
            "_video_index": video[0]["index"] if video else None, "_audio_index": audio[0]["index"] if audio else None,
            "_video_duration": _positive(video[0].get("duration")) if video else None,
            "_native_compatible": plans.get(native, {}).get("operation") == "copying",
            "_audio_channels": channels, "_plans": {target: plans[target] for target in targets}}


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


def inspect_local_files(paths, *, ffmpeg_location=None, max_height=None):
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
            result.update(signature=signature, size=signature["size"], **_media_details(metadata, source, ffmpeg, max_height=max_height))
        except DownloadError as error:
            result.update(error=str(error), targets=[])
        except (OSError, ValueError, TypeError, RuntimeError, KeyError):
            result.update(error="The selected file could not be checked. Add it again.", targets=[])
        results.append(result)
    return results


def _run_ffmpeg(ffmpeg, arguments, output, *, duration=None, progress_hook=None, operation="encoding"):
    try:
        run_with_progress([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats",
                           "-progress", "pipe:1", "-stats_period", "0.25", "-n", *arguments, str(output)],
                          duration=duration, operation=operation, progress_hook=progress_hook,
                          process_options=hidden_process_options())
        if not output.is_file() or output.stat().st_size == 0:
            raise DownloadError("The conversion did not produce a complete file. Try another output format.")
        with output.open("r+b") as stream:
            os.fsync(stream.fileno())
    except subprocess.TimeoutExpired as error:
        raise DownloadError("This conversion exceeded the 30 minute limit. Try a shorter file.") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise DownloadError("The media could not be converted to this format. Its codec or channel layout may be unsupported.") from error


def _encoder_time_base(stream=None):
    # A framerate-derived default can round distinct VFR timestamps together.
    # An explicit positive ratio works with both older and current FFmpeg.
    try:
        value = Fraction((stream or {}).get("time_base", "1/1000000"))
        if value <= 0 or max(value.numerator, value.denominator) > 2_147_483_647:
            raise ValueError
    except (ValueError, TypeError, ZeroDivisionError):
        value = Fraction(1, 1_000_000)
    return f"{value.numerator}:{value.denominator}"


def _video_arguments(target, *, audio=True, video_copy=False, audio_copy=False, geometry=None, max_height=None, time_base="1:1000000",
                     transparent=False, background="#FFFFFF"):
    video_encoder, audio_encoder, muxer = _VIDEO[target]
    if video_copy:
        options = ["-c:v", "copy"]
        if transparent:
            options += ["-metadata:s:v:0", "alpha_mode=1"]
    else:
        filters = geometry.filters(max_height=max_height, even=True) if geometry else "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1"
        if transparent:
            filters += "," + composite_filters(background)
        filters += ",sidedata=mode=delete:type=DISPLAYMATRIX"
        options = ["-c:v", video_encoder, "-vf", filters, "-metadata:s:v:0", "rotate=0", "-pix_fmt", "yuv420p", "-threads", "2",
                   "-fps_mode", "passthrough", "-enc_time_base", time_base]
        # H.264 frame reordering can shorten the last sample's reported
        # duration in variable-rate MP4. Keep timestamps and final duration
        # unambiguous; compatible packet copies retain their original encoding.
        options += ["-crf", "30", "-b:v", "0", "-deadline", "good", "-cpu-used", "3"] if target == "webm" else ["-crf", "20", "-preset", "medium", "-bf", "0"]
    if audio:
        options += ["-c:a", "copy"] if audio_copy else ["-c:a", audio_encoder, "-b:a", "128k" if target == "webm" else "192k"]
    else:
        options += ["-an"]
    if target in ("mp4", "mov"):
        options += ["-movflags", "+faststart"]
    return options + ["-f", muxer]


def _animation_arguments(target, video_index, *, geometry, max_height=None, time_base, transparent=False, background="#FFFFFF"):
    # Encode one frame at a time. A global GIF palette or libwebp_anim would
    # buffer the whole video, making longer clips unnecessarily expensive.
    normalize = geometry.filters(max_height=max_height) + ","
    if target == "gif":
        if transparent:
            normalize += composite_filters(background, preserve_zero=True) + ","
        filters = f"[0:{video_index}]{normalize}split[a][b];[a]palettegen=stats_mode=single[p];[b][p]paletteuse=new=1[animation]"
        options = ["-filter_complex", filters, "-map", "[animation]", "-c:v", "gif", "-loop", "0", "-f", "gif"]
    elif target == "webp":
        options = ["-map", f"0:{video_index}", "-vf", normalize + "format=bgra", "-c:v", "libwebp", "-lossless", "1", "-loop", "0", "-f", "webp"]
    else:
        options = ["-map", f"0:{video_index}", "-vf", normalize + "format=rgba", "-c:v", "apng", "-plays", "0", "-f", "apng"]
    return ["-an", "-sn", "-dn", "-metadata:s:v:0", "rotate=0", "-fps_mode", "passthrough", "-enc_time_base", time_base,
            "-threads", "2", "-filter_complex_threads", "1", *options]


def _finish_apng_timing(output, duration):
    """Set the final frame's delay when the source gives an exact duration.

    FFmpeg's APNG muxer repeats the preceding delay for the last frame. Read
    only frame-control chunks to correct that delay for variable-rate video;
    pixel data stays on disk and is never decoded or buffered here.
    """
    if duration is None:
        return
    elapsed = Fraction(0)
    last = None
    with output.open("r+b") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise DownloadError("The animation encoder did not produce a valid PNG file.")
        while header := stream.read(8):
            if len(header) != 8:
                raise DownloadError("The animation encoder produced an incomplete PNG file.")
            length, kind = struct.unpack(">I4s", header)
            if kind == b"fcTL" and length == 26:
                data = stream.read(length)
                if len(data) != length:
                    raise DownloadError("The animation encoder produced an incomplete frame.")
                numerator, denominator = struct.unpack(">HH", data[20:24])
                last = (stream.tell() - length, data, elapsed)
                elapsed += Fraction(numerator, denominator or 100)
                stream.seek(4, 1)
            else:
                stream.seek(length + 4, 1)
            if kind == b"IEND":
                break
        if last is not None:
            offset, data, before_last = last
            delay = Fraction(str(duration)) - before_last
            if 0 < delay <= 65_535:
                # Prefer an exact small fraction (300 ms is 3/10), rather
                # than introducing drift by always using a large denominator.
                bounded = delay.limit_denominator(65_535)
                if bounded.numerator > 65_535:
                    bounded = delay.limit_denominator(max(1, int(65_535 / delay)))
                numerator, denominator = max(1, bounded.numerator), bounded.denominator
                data = data[:20] + struct.pack(">HH", numerator, denominator) + data[24:]
                stream.seek(offset)
                stream.write(data + struct.pack(">I", zlib.crc32(b"fcTL" + data)))


def convert_local_file(media, target, output_dir, background="#FFFFFF", progress_hook=None, *, work_dir=None, ffmpeg_location=None,
                       max_height=None):
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
                                     "-map", "0:v:0", "-map_metadata", "-1",
                                     *_video_arguments("mp4", audio=False)], output,
                            duration=media.get("duration"), progress_hook=progress_hook)

        return convert_animation(media, target, output_dir, background, progress_hook, work_dir=work_dir, encode_mp4=encode_mp4)
    if kind not in ("audio", "video"):
        raise DownloadError("This file type is not supported for conversion.")
    ffmpeg, ffprobe = _tools(ffmpeg_location)
    source = Path(media["source"]).resolve()
    destination = Path(output_dir).resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with local_conversion._work_directory(destination, work_dir) as temporary:
            if progress_hook:
                progress_hook({"status": "converting", "operation": "preparing", "progress": None, "speed": None, "eta": None})
            snapshot = temporary / "source.media"
            with snapshot.open("xb") as copied:
                signature = _stream_snapshot(source, copied)
                copied.flush()
                os.fsync(copied.fileno())
            if signature != media["signature"]:
                raise DownloadError("The source file changed after it was checked. Add it again before converting.")
            metadata = _probe(snapshot, ffprobe)
            checked = _media_details(metadata, snapshot, ffmpeg, max_height=max_height)
            if target not in checked["targets"]:
                raise DownloadError("Choose a different compatible output format for this file.")
            plan = checked["_plans"][target]
            geometry = analyze_geometry(_streams(metadata)[0][0]) if kind == "video" else None
            time_base = _encoder_time_base(_streams(metadata)[0][0]) if kind == "video" else None
            alpha_webp = target == "webp" and checked["transparent"]
            encoded_target = "apng" if alpha_webp else target
            output = temporary / f"output.{encoded_target}"
            arguments = ["-protocol_whitelist", "file,pipe", "-format_whitelist", _INPUT_FORMATS]
            if target in _ANIMATION or target in _VIDEO and not plan["video_copy"]:
                arguments += ["-noautorotate"]
                arguments += _geometry_input_options(ffmpeg, _streams(metadata)[0][0], geometry)
                arguments += decoder_options(_streams(metadata)[0][0])
            arguments += ["-i", str(snapshot), "-map_metadata", "-1", "-map_chapters", "-1"]
            if target in _AUDIO:
                encoder, muxer, options = _AUDIO[target]
                arguments += ["-map", f"0:{checked['_audio_index']}", "-vn", "-sn", "-dn"]
                arguments += ["-c:a", "copy"] if plan["audio_copy"] else ["-c:a", encoder, *options]
                arguments += ["-f", muxer]
            elif target in _ANIMATION:
                if max_height is not None and (isinstance(max_height, bool) or not isinstance(max_height, int) or max_height < 1):
                    raise DownloadError("Choose a positive maximum video height.")
                arguments += _animation_arguments(encoded_target, checked['_video_index'], geometry=geometry, max_height=max_height, time_base=time_base,
                                                  transparent=checked["transparent"], background=background)
            else:
                arguments += ["-map", f"0:{checked['_video_index']}"]
                if checked["has_audio"]:
                    arguments += ["-map", f"0:{checked['_audio_index']}"]
                arguments += ["-sn", "-dn", *_video_arguments(target, audio=checked["has_audio"],
                                                            video_copy=plan["video_copy"], audio_copy=plan["audio_copy"],
                                                            geometry=geometry, max_height=max_height, time_base=time_base,
                                                            transparent=checked["transparent"], background=background)]
            if checked["_audio_channels"] > 2 and not plan["audio_copy"] and target not in (*_ANIMATION, "wav", "flac", "aiff"):
                arguments += ["-ac", "2"]
            duration = checked.get("_video_duration") or checked["duration"] if target in _ANIMATION else checked["duration"]
            _run_ffmpeg(ffmpeg, arguments, output, duration=duration,
                        progress_hook=progress_hook, operation=plan["operation"])
            if encoded_target == "apng":
                _finish_apng_timing(output, checked.get("_video_duration"))
            if alpha_webp:
                animation = inspect_local_files([output], ffmpeg_location=ffmpeg.parent)[0]
                if animation.get("error"):
                    raise DownloadError(animation["error"])
                output = convert_local_file(animation, "webp", temporary, background, progress_hook,
                                            ffmpeg_location=ffmpeg.parent)
            return local_conversion._publish_unique(output, source, target, destination)
    except PermissionError as error:
        raise DownloadError("The converted file could not be saved. Check the destination folder's permissions.") from error
    except OSError as error:
        raise DownloadError("The converted file could not be saved. Check disk space and the destination folder.") from error
