"""Exercise real yt-dlp and FFmpeg downloads against locally served media."""

import functools
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.downloader import DownloadError, DownloadRequest, download


class _SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class _SilentLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


@unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required for integration tests")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="unidown-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.tools = find_ffmpeg()
        suffix = ".exe" if (cls.tools / "ffmpeg.exe").exists() else ""
        cls.ffmpeg = cls.tools / f"ffmpeg{suffix}"
        cls.ffprobe = cls.tools / f"ffprobe{suffix}"
        cls.fixture = cls.root / "sample.mp4"
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "0.5", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-shortest", str(cls.fixture),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.fixture),
            "-c", "copy", "-an", str(cls.root / "silent.mp4"),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.fixture),
            "-c:v", "libx264", "-c:a", "aac", "-f", "dash", str(cls.root / "manifest.mpd"),
        ], check=True, capture_output=True, timeout=30, cwd=cls.root)
        handler = functools.partial(_SilentHandler, directory=str(cls.root))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/sample.mp4"

    def _probe(self, path):
        result = subprocess.run([
            str(self.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        ], check=True, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout)

    def test_video_download_preserves_video_and_audio(self):
        result = download(DownloadRequest(self.url, output_dir=self.root / "video"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), self.fixture.read_bytes())
        streams = self._probe(result.path)["streams"]
        self.assertEqual({stream["codec_type"] for stream in streams}, {"video", "audio"})

    def test_mp3_is_real_audio_with_no_video(self):
        result = download(
            DownloadRequest(self.url, "mp3", output_dir=self.root / "audio"), logger=_SilentLogger(),
        )
        self.assertEqual(result.path.suffix, ".mp3")
        probe = self._probe(result.path)
        self.assertGreater(float(probe["format"]["duration"]), 0)
        self.assertEqual([stream["codec_name"] for stream in probe["streams"]], ["mp3"])
        self.assertFalse(list(result.path.parent.glob("*.mp4")))

    def test_repeated_video_does_not_overwrite_completed_file(self):
        request = DownloadRequest(self.url, output_dir=self.root / "repeat")
        first = download(request, logger=_SilentLogger())
        modified = first.path.stat().st_mtime_ns
        second = download(request, logger=_SilentLogger())
        self.assertEqual(first.path, second.path)
        self.assertEqual(second.path.stat().st_mtime_ns, modified)

    def test_resolution_limit_works_with_direct_media(self):
        result = download(
            DownloadRequest(self.url, quality="480", output_dir=self.root / "quality"),
            logger=_SilentLogger(),
        )
        self.assertTrue(result.path.is_file())

    def test_silent_video_can_be_downloaded(self):
        result = download(
            DownloadRequest(self.url.replace("sample.mp4", "silent.mp4"), output_dir=self.root / "silent"),
            logger=_SilentLogger(),
        )
        self.assertEqual([stream["codec_type"] for stream in self._probe(result.path)["streams"]], ["video"])

    def test_separate_dash_video_and_audio_are_merged(self):
        result = download(
            DownloadRequest(self.url.replace("sample.mp4", "manifest.mpd"), output_dir=self.root / "dash"),
            logger=_SilentLogger(),
        )
        self.assertIn(result.path.suffix, (".mp4", ".mkv"))
        self.assertEqual(
            {stream["codec_type"] for stream in self._probe(result.path)["streams"]}, {"video", "audio"},
        )

    def test_repeated_mp3_does_not_overwrite_completed_file(self):
        request = DownloadRequest(self.url, "mp3", output_dir=self.root / "repeat-audio")
        first = download(request, logger=_SilentLogger())
        modified = first.path.stat().st_mtime_ns
        second = download(request, logger=_SilentLogger())
        self.assertEqual(second.path.stat().st_mtime_ns, modified)
        self.assertFalse(list(second.path.parent.glob("*.mp4")))

    def test_http_failure_returns_user_facing_error(self):
        with self.assertRaisesRegex(DownloadError, "Download failed"):
            download(
                DownloadRequest(self.url.replace("sample.mp4", "missing.mp4"), output_dir=self.root / "missing"),
                logger=_SilentLogger(),
            )


if __name__ == "__main__":
    unittest.main()
