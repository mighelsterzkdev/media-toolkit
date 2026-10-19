"""Exercise the built executable against a local server with Python absent from PATH."""

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from PIL import Image


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def verify(executable: Path, *, bootstrap: bool = False) -> None:
    executable = executable.resolve()
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="unidown-portable-check-") as temporary:
        root = Path(temporary)
        environment["UNIDOWN_DATA_DIR"] = str(root / "history")

        def run(*arguments):
            process = subprocess.run([str(executable), *arguments], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=300)
            if process.returncode:
                raise RuntimeError(f"Portable command failed ({process.returncode}): {' '.join(arguments)}\n{process.stdout}\n{process.stderr}")
            return process.stdout

        print(run("--version").strip(), flush=True)
        if bootstrap:
            print(run("--install-tools").strip(), flush=True)
        print(run("--doctor").strip(), flush=True)
        tools = executable.parent / ".tools/ffmpeg/bin"
        ffmpeg = tools / "ffmpeg.exe"
        ffprobe = tools / "ffprobe.exe"
        image = Image.new("RGBA", (48, 32), "red")
        fixture = root / "animated.gif"
        image.save(fixture, save_all=True, append_images=[Image.new("RGBA", image.size, "blue")], duration=200, loop=0)
        video_fixture = root / "sample.mp4"
        subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=48x32:r=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video_fixture)],
            capture_output=True, timeout=30, check=True,
        )

        def probe(path):
            result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
            return json.loads(result.stdout)["streams"]

        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=root))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/animated.gif"
            original = root / "original"
            converted = root / "converted"
            print(run(url, "--type", "image", "--output", str(original)).strip(), flush=True)
            saved = list(original.glob("*.gif"))
            if len(saved) != 1 or saved[0].read_bytes() != fixture.read_bytes():
                raise RuntimeError("The portable app did not preserve the original GIF bytes.")
            print(run(url, "--convert", "mp4", "--output", str(converted)).strip(), flush=True)
            videos = list(converted.glob("*.mp4"))
            if len(videos) != 1:
                raise RuntimeError("The portable conversion did not produce exactly one MP4.")
            streams = probe(videos[0])
            if not any(stream.get("codec_name") == "h264" and int(stream.get("nb_frames") or 0) > 1 for stream in streams):
                raise RuntimeError("The converted MP4 did not preserve real animation frames in H.264.")
            modified = videos[0].stat().st_mtime_ns
            repeat = run(url, "--convert", "mp4", "--output", str(converted))
            if "Already downloaded" not in repeat or videos[0].stat().st_mtime_ns != modified:
                raise RuntimeError("Repeating the conversion replaced the existing file.")
            if list(converted.glob(".unidown*")):
                raise RuntimeError("Portable conversion left temporary work files behind.")
            media_url = f"http://127.0.0.1:{server.server_port}/sample.mp4"
            video_output = root / "video"
            print(run(media_url, "--type", "video", "--output", str(video_output)).strip(), flush=True)
            originals = list(video_output.glob("*.mp4"))
            if len(originals) != 1 or originals[0].read_bytes() != video_fixture.read_bytes():
                raise RuntimeError("The portable video download changed its original bytes.")
            audio_output = root / "audio"
            print(run(media_url, "--type", "audio", "--output", str(audio_output)).strip(), flush=True)
            audios = list(audio_output.glob("*.m4a"))
            if len(audios) != 1 or [stream["codec_type"] for stream in probe(audios[0])] != ["audio"]:
                raise RuntimeError("Portable original audio extraction did not produce an audio-only AAC file.")
            mp3_output = root / "mp3"
            print(run(media_url, "--convert", "mp3", "--output", str(mp3_output)).strip(), flush=True)
            mp3s = list(mp3_output.glob("*.mp3"))
            if len(mp3s) != 1 or [stream.get("codec_name") for stream in probe(mp3s[0])] != ["mp3"]:
                raise RuntimeError("Portable MP3 conversion did not produce an audio-only MP3 file.")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    print("Portable checks passed: isolated tool discovery, exact image/video originals, animated MP4, AAC extraction, MP3 conversion and cached repeat.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--bootstrap", action="store_true", help="download the pinned external tools before checking")
    args = parser.parse_args()
    verify(args.executable, bootstrap=args.bootstrap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
