"""Exercise the built executable against a local server with Python absent from PATH."""

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import threading
import time

from PIL import Image


class _QuietHandler(SimpleHTTPRequestHandler):
    resumed_offsets = []

    def log_message(self, *args):
        pass

    def _resumable(self, *, head=False):
        path = Path(self.directory) / "resume.mp4"
        size = path.stat().st_size
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        start = int(match[1]) if match else 0
        end = min(int(match[2]), size - 1) if match and match[2] else size - 1
        if start >= size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206 if match else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("ETag", '"portable-resume-fixture-v1"')
        self.send_header("Accept-Ranges", "bytes")
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.resumed_offsets.append(start)
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = source.read(min(16_384, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    remaining -= len(chunk)
                    time.sleep(0.005)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_HEAD(self):
        if self.path == "/resume.mp4":
            self._resumable(head=True)
        else:
            super().do_HEAD()

    def do_GET(self):
        if self.path == "/resume.mp4":
            self._resumable()
        else:
            super().do_GET()


def verify(executable: Path, *, bootstrap: bool = False) -> None:
    executable = executable.resolve()
    environment = dict(os.environ)
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="unidown-portable-check-") as temporary:
        root = Path(temporary).resolve()
        environment["UNIDOWN_DATA_DIR"] = str(root / "history")

        def run(*arguments, expected=0):
            process = subprocess.run([str(executable), *arguments], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=300)
            if process.returncode != expected:
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
        # A valid MP4 'free' box gives the interruption test enough data to
        # preserve a real partial file without generating a long video.
        resume_fixture = root / "resume.mp4"
        padding_size = 8 * 1024 * 1024
        resume_fixture.write_bytes(video_fixture.read_bytes() + struct.pack("!I", padding_size) + b"free" + bytes(padding_size - 8))

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

            # Batch uses one explicit policy and stores each original in full.
            batch_output = root / "batch"
            batch_file = root / "links.txt"
            batch_file.write_text(f"# Representative media\n{url}\n{media_url}\n{url}\n", encoding="utf-8-sig")
            batch_log = run("--batch", str(batch_file), "--output", str(batch_output), "--no-preferences")
            if len(list(batch_output.glob("*.gif"))) != 1 or len(list(batch_output.glob("*.mp4"))) != 1:
                raise RuntimeError("The portable queue did not save its two distinct original assets.")
            print(batch_log.strip(), flush=True)

            preference_output = root / "remembered"
            run("--output", str(preference_output), "--max-size", "2MiB", "--save-preferences")
            preferences = run("--show-preferences")
            if str(preference_output) not in preferences and str(preference_output).replace("\\", "\\\\") not in preferences:
                raise RuntimeError("The portable app did not remember its output folder.")
            run("--reset-preferences")

            # Terminate an actual frozen process, then reopen the same link.
            resume_url = f"http://127.0.0.1:{server.server_port}/resume.mp4"
            resume_output = root / "resumed"
            arguments = [str(executable), resume_url, "--type", "video", "--output", str(resume_output), "--no-preferences"]
            process = subprocess.Popen(arguments, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            partial = None
            try:
                deadline = time.monotonic() + 45
                while process.poll() is None and time.monotonic() < deadline:
                    candidates = list((root / "history").rglob("*.part"))
                    partial = None
                    for candidate in candidates:
                        try:
                            state_file = candidate.parent.parent / "state.json"
                            state = json.loads(state_file.read_text(encoding="utf-8"))
                            recorded = state.get("partials", {}).get(candidate.name, {}).get("size", 0)
                            if recorded > 0 and candidate.stat().st_size >= 131_072:
                                partial = candidate
                                break
                        except (OSError, ValueError):
                            continue
                    if partial is not None:
                        break
                    time.sleep(0.025)
                if partial is None:
                    raise RuntimeError("The portable process did not create a resumable partial download.")
            finally:
                if process.poll() is None:
                    process.terminate()
                stdout, stderr = process.communicate(timeout=20)
            state = json.loads((partial.parent.parent / "state.json").read_text(encoding="utf-8"))
            saved_offset = state["partials"][partial.name]["size"]
            _QuietHandler.resumed_offsets.clear()
            print(run(*arguments[1:]).strip(), flush=True)
            resumed = list(resume_output.glob("*.mp4"))
            if saved_offset not in _QuietHandler.resumed_offsets or len(resumed) != 1 or resumed[0].read_bytes() != resume_fixture.read_bytes():
                raise RuntimeError("The portable restart did not resume from its saved offset with exact original bytes.")
            if any((root / "history" / "resume").rglob("*.part")):
                raise RuntimeError("The portable restart left its completed partial download behind.")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    print("Portable checks passed: isolated tools, exact originals, conversions, cached repeat, queue, preferences and process-restart HTTP resume.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--bootstrap", action="store_true", help="download the pinned external tools before checking")
    args = parser.parse_args()
    verify(args.executable, bootstrap=args.bootstrap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
