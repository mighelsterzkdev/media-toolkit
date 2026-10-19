"""Opt-in real-site checks, separate from deterministic offline integration tests."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CASES = (
    {"name": "twitter-animation", "url": "https://x.com/RealJohnWif/status/2111442239649911004", "kind": "image", "animated": True},
    {"name": "tiktok-sound", "url": "https://www.tiktok.com/music/som-original-7697963541225163528", "kind": "audio"},
    {"name": "direct-gif", "url": "https://upload.wikimedia.org/wikipedia/commons/2/2c/Rotating_earth_%28large%29.gif", "kind": "image", "animated": True},
    {"name": "google-image-link", "url": "https://www.google.com/imgres?imgurl=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F2%2F2c%2FRotating_earth_%2528large%2529.gif&imgrefurl=https%3A%2F%2Fcommons.wikimedia.org%2Fwiki%2FFile%3ARotating_earth_%28large%29.gif", "kind": "image", "animated": True},
    {"name": "youtube-video", "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "kind": "video"},
    {"name": "responsive-image", "url": "https://mdn.github.io/learning-area/html/multimedia-and-embedding/responsive-images/srcset-resolutions.html", "kind": "image", "width": 640, "height": 427},
)


class _QuietLogger:
    def debug(self, message):
        pass

    warning = debug
    error = debug


@contextmanager
def isolated_history(root: Path):
    previous = os.environ.get("UNIDOWN_DATA_DIR")
    os.environ["UNIDOWN_DATA_DIR"] = str(root / "history")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("UNIDOWN_DATA_DIR", None)
        else:
            os.environ["UNIDOWN_DATA_DIR"] = previous


def validate_media(path: Path, *, kind: str, animated: bool) -> dict:
    """Decode media metadata; a successful HTTP response or nonempty file is insufficient."""
    from PIL import Image, UnidentifiedImageError
    from universal_downloader.dependencies import find_ffmpeg
    try:
        with Image.open(path) as image:
            details = {"format": image.format, "width": image.width, "height": image.height, "frames": getattr(image, "n_frames", 1)}
            image.verify()
        if kind != "image":
            raise ValueError(f"Expected {kind}, but the downloaded file is a still-image container.")
        if animated and details["frames"] <= 1:
            raise ValueError("The downloaded animation has only one frame.")
        return details
    except UnidentifiedImageError:
        pass
    tools = find_ffmpeg()
    if tools is None:
        raise ValueError("FFmpeg and ffprobe are required to validate downloaded audio/video.")
    suffix = ".exe" if (tools / "ffprobe.exe").is_file() else ""
    process = subprocess.run(
        [str(tools / f"ffprobe{suffix}"), "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    )
    metadata = json.loads(process.stdout)
    streams = metadata.get("streams") or []
    videos = [stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if kind == "audio" and (not audios or videos):
        raise ValueError("The downloaded audio did not contain an audio-only media stream.")
    if kind in ("video", "image") and not videos:
        raise ValueError("The downloaded video/animation did not contain a video stream.")
    frames = max((int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0) for stream in videos), default=0)
    if animated and frames <= 1:
        raise ValueError("The downloaded animation did not contain multiple decoded frames.")
    return {"format": metadata.get("format", {}).get("format_name"), "frames": frames, "video_codecs": [stream.get("codec_name") for stream in videos], "audio_codecs": [stream.get("codec_name") for stream in audios]}


def check_case(case: dict, *, save: bool = False) -> dict:
    from universal_downloader.downloader import DownloadRequest, download, inspect_media
    result = {"name": case["name"], "url": case["url"]}
    try:
        inspected = inspect_media(case["url"], logger=_QuietLogger())
        kind = case.get("kind") or inspected.kinds[0]
        if kind not in inspected.kinds:
            raise ValueError(f"Expected {kind}, detected {', '.join(inspected.kinds)}.")
        if "animated" in case and inspected.animated != case["animated"]:
            raise ValueError("The animation was not recognized as animated media.")
        result.update(title=inspected.information.get("title"), kinds=inspected.kinds, animated=inspected.animated)
        if "width" in case or "height" in case:
            dimensions = inspected.image_dimensions
            for key, index in (("width", 0), ("height", 1)):
                if key in case and (dimensions is None or dimensions[index] != case[key]):
                    raise ValueError(f"The inspected {key} does not match the expected {case[key]} pixels.")
            result["dimensions"] = dimensions
        if save:
            with tempfile.TemporaryDirectory(prefix="unidown-live-") as temporary, isolated_history(Path(temporary)):
                downloaded = download(DownloadRequest(case["url"], kind, Path(temporary)), inspected=inspected, logger=_QuietLogger())
                size = downloaded.path.stat().st_size
                if size == 0:
                    raise ValueError("The downloaded file is empty.")
                details = validate_media(downloaded.path, kind=kind, animated=inspected.animated)
                for key in ("width", "height"):
                    if key in case and details.get(key) != case[key]:
                        raise ValueError(f"The decoded {key} does not match the expected {case[key]} pixels.")
                result.update(downloaded_bytes=size, extension=downloaded.path.suffix, decoded=details)
                if kind == "image" and not inspected.animation_video and inspected.image_data and downloaded.path.read_bytes() != inspected.image_data:
                    raise ValueError("The original image bytes changed during download.")
                if inspected.animated:
                    converted = download(DownloadRequest(case["url"], kind, Path(temporary) / "converted", "mp4"), inspected=inspected, logger=_QuietLogger())
                    details = validate_media(converted.path, kind="video", animated=True)
                    if details.get("video_codecs") != ["h264"]:
                        raise ValueError("The converted animation is not an H.264 MP4.")
                    result["conversion"] = {"target": "mp4", "bytes": converted.path.stat().st_size, "decoded": details}
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=[case["name"] for case in CASES], help="run only these representative cases")
    parser.add_argument("--url", action="append", default=[], help="also inspect another public URL")
    parser.add_argument("--download", action="store_true", help="download and decode actual media; also convert animations to MP4, then clean temporary files")
    parser.add_argument("--timeout", type=int, default=150, help="maximum seconds per site (default: 150)")
    parser.add_argument("--output", type=Path, help="write a machine-readable JSON report")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        print(json.dumps(check_case(json.loads(args.worker), save=args.download)))
        return 0
    from universal_downloader import __version__
    from universal_downloader.compatibility import compatibility_issues
    issues = compatibility_issues()
    if issues:
        print("Dependency contract failed: " + "; ".join(issues), file=sys.stderr)
        return 1
    selected = [case for case in CASES if not args.case or case["name"] in args.case]
    selected += [{"name": f"custom-{index}", "url": url} for index, url in enumerate(args.url, 1)]
    results = []
    for case in selected:
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", json.dumps(case)]
        if args.download:
            command.append("--download")
        try:
            process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=args.timeout)
            if process.returncode:
                raise ValueError(process.stderr.strip() or "The site check exited unexpectedly.")
            result = json.loads(process.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            result = {"name": case["name"], "url": case["url"], "status": "failed", "error": str(error)}
        results.append(result)
        print(f"{result['status'].upper()}: {case['name']}" + (f" — {result['error']}" if result.get("error") else ""), flush=True)
    report = {
        "version": __version__, "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Selected public examples only; site access, login rules and availability can change.",
        "downloaded": args.download, "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
