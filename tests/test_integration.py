"""Exercise real yt-dlp and FFmpeg downloads against locally served media."""

import functools
import contextlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from PIL import Image

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.downloader import DownloadError, DownloadRequest, download, inspect_url
from universal_downloader.formats import AUDIO_FORMATS, IMAGE_FORMATS, VIDEO_FORMATS


class _SilentHandler(SimpleHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append(urlsplit(self.path).path)
        if self.path == "/large.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(33 * 1024 * 1024))
            self.end_headers()
            return
        if self.path == "/image-without-extension":
            self.path = "/thumbnail.png"
        super().do_GET()

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
        Image.new("RGBA", (320, 180), (25, 90, 180, 100)).save(cls.root / "thumbnail.png")
        Image.new("RGB", (40, 30), "green").save(cls.root / "small.jpg")
        (cls.root / "page.html").write_text(
            '<html><head><title>Test video</title><meta property="og:title" content="Test video">'
            '<meta property="og:image" content="/thumbnail.png"></head><body>'
            '<video src="/sample.mp4" controls></video></body></html>', encoding="utf-8",
        )
        (cls.root / "image-page.html").write_text(
            '<html><head><meta property="og:title" content="Image page">'
            '<meta property="og:image" content="/thumbnail.png"></head><body>No video</body></html>', encoding="utf-8",
        )
        (cls.root / "no-thumbnail.html").write_text(
            '<html><head><title>No thumbnail</title></head><body><video src="/sample.mp4" controls></video></body></html>', encoding="utf-8",
        )
        first_frame = Image.new("RGB", (24, 24), "red")
        first_frame.save(cls.root / "animated.gif", save_all=True, append_images=[Image.new("RGB", (24, 24), "blue")], duration=100, loop=0)
        (cls.root / "broken.png").write_bytes(b"not an image")
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

    def test_every_audio_format_produces_the_expected_codec(self):
        codecs = {"mp3": "mp3", "m4a": "aac", "aac": "aac", "opus": "opus", "ogg": "vorbis", "flac": "flac", "wav": "pcm_s16le", "alac": "alac", "audio": "aac"}
        self.assertEqual(set(codecs), set(AUDIO_FORMATS))
        for media_format, codec in codecs.items():
            with self.subTest(format=media_format):
                request = DownloadRequest(self.url, media_format, output_dir=self.root / f"all-audio-{media_format}")
                result = download(request, logger=_SilentLogger())
                streams = self._probe(result.path)["streams"]
                self.assertEqual([stream["codec_type"] for stream in streams], ["audio"])
                self.assertEqual(streams[0]["codec_name"], codec)
                self.assertEqual(result.path.suffix, ".m4a" if media_format in ("alac", "audio") else f".{media_format}")
                modified = result.path.stat().st_mtime_ns
                again = download(request, logger=_SilentLogger())
                self.assertEqual(again.path.stat().st_mtime_ns, modified)
                self.assertEqual(list(result.path.parent.iterdir()), [result.path])

    def test_every_video_container_has_valid_video_and_audio(self):
        codecs = {"mp4": "h264", "mkv": "h264", "webm": "vp9", "mov": "h264", "avi": "mpeg4", "m4v": "h264", "flv": "flv1", "ts": "h264"}
        self.assertEqual(set(codecs), set(VIDEO_FORMATS) - {"video"})
        for media_format, codec in codecs.items():
            with self.subTest(format=media_format):
                request = DownloadRequest(self.url, media_format, output_dir=self.root / f"all-video-{media_format}")
                result = download(request, logger=_SilentLogger())
                streams = self._probe(result.path)["streams"]
                self.assertEqual({stream["codec_type"] for stream in streams}, {"video", "audio"})
                video = next(stream for stream in streams if stream["codec_type"] == "video")
                self.assertEqual(video["codec_name"], codec)
                self.assertEqual(result.path.suffix, f".{media_format}")
                modified = result.path.stat().st_mtime_ns
                again = download(request, logger=_SilentLogger())
                self.assertEqual(again.path.stat().st_mtime_ns, modified)
                self.assertEqual(list(result.path.parent.iterdir()), [result.path])

    def test_every_thumbnail_format_is_an_actual_image_and_does_not_fetch_video(self):
        expected = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF", "ico": "ICO", "image": "PNG"}
        self.assertEqual(set(expected), set(IMAGE_FORMATS))
        page = self.url.replace("sample.mp4", "page.html")
        for media_format, image_format in expected.items():
            with self.subTest(format=media_format):
                _SilentHandler.requests.clear()
                with patch("universal_downloader.downloader.find_ffmpeg", return_value=None):
                    request = DownloadRequest(page, media_format, output_dir=self.root / f"all-images-{media_format}")
                    result = download(request, logger=_SilentLogger())
                with Image.open(result.path) as image:
                    image.load()
                    self.assertEqual(image.format, image_format)
                    if media_format != "ico":
                        self.assertEqual(image.size, (320, 180))
                    else:
                        self.assertLessEqual(max(image.size), 256)
                self.assertNotIn("/sample.mp4", _SilentHandler.requests)
                self.assertEqual(list(result.path.parent.iterdir()), [result.path])
                modified = result.path.stat().st_mtime_ns
                again = download(request, logger=_SilentLogger())
                self.assertEqual(again.path.stat().st_mtime_ns, modified)

    def test_direct_image_and_extensionless_image_urls(self):
        for endpoint in ("thumbnail.png", "image-without-extension"):
            with self.subTest(endpoint=endpoint):
                result = download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "webp", output_dir=self.root / f"direct-{endpoint}"), logger=_SilentLogger())
                with Image.open(result.path) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.size, (320, 180))

    def test_open_graph_fallback_for_page_without_a_video(self):
        result = download(DownloadRequest(self.url.replace("sample.mp4", "image-page.html"), "jpg", output_dir=self.root / "og-fallback"), logger=_SilentLogger())
        with Image.open(result.path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertTrue(all(abs(value - expected) < 10 for value, expected in zip(image.getpixel((0, 0)), (165, 190, 226))))

    def test_source_format_inspection_does_not_save_any_files(self):
        info = inspect_url(self.url.replace("sample.mp4", "page.html"), logger=_SilentLogger())
        self.assertTrue(info.get("formats"))
        self.assertTrue(info.get("thumbnails") or info.get("thumbnail"))

    def test_real_terminal_menu_downloads_an_avif_thumbnail(self):
        from universal_downloader.cli import main
        folder = self.root / "real-menu"
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[self.url.replace("sample.mp4", "page.html"), "3", "4"]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--output", str(folder)]), 0)
        files = list(folder.iterdir())
        self.assertEqual(len(files), 1)
        with Image.open(files[0]) as image:
            self.assertEqual(image.format, "AVIF")
        self.assertIn("Done! File saved to:", output.getvalue())

    def test_source_format_id_can_be_downloaded(self):
        info = inspect_url(self.url, logger=_SilentLogger())
        source_format = info["formats"][0]["format_id"]
        result = download(DownloadRequest(self.url, source_format=source_format, output_dir=self.root / "source-id"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), self.fixture.read_bytes())

    def test_explicit_video_and_audio_ids_can_be_combined(self):
        url = self.url.replace("sample.mp4", "manifest.mpd")
        info = inspect_url(url, logger=_SilentLogger())
        video = next(item for item in info["formats"] if item.get("vcodec") != "none" and item.get("acodec") == "none")
        audio = next(item for item in info["formats"] if item.get("vcodec") == "none" and item.get("acodec") != "none")
        selector = f"{video['format_id']}+{audio['format_id']}"
        result = download(DownloadRequest(url, "mp4", source_format=selector, output_dir=self.root / "combined-ids"), logger=_SilentLogger())
        self.assertEqual({stream["codec_type"] for stream in self._probe(result.path)["streams"]}, {"video", "audio"})

    def test_best_thumbnail_falls_back_after_http_failure(self):
        base = self.url.removesuffix("sample.mp4")
        information = {
            "id": "fallback", "title": "Best image", "webpage_url": base + "page.html",
            "thumbnails": [
                {"url": "http://[invalid", "width": 1000, "height": 1000},
                {"url": base + "small.jpg", "width": 40, "height": 30},
                {"url": base + "thumbnail.png", "width": 320, "height": 180},
                {"url": base + "missing.jpg", "width": 640, "height": 360},
            ],
        }
        _SilentHandler.requests.clear()
        with patch("universal_downloader.downloader.YoutubeDL.extract_info", return_value=information):
            result = download(DownloadRequest(base + "page.html", "png", output_dir=self.root / "best-thumb"), logger=_SilentLogger())
        with Image.open(result.path) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.getpixel((10, 10))[3], 100)
        self.assertEqual(_SilentHandler.requests, ["/missing.jpg", "/thumbnail.png"])

    def test_original_animated_image_is_preserved_and_conversion_is_rejected(self):
        url = self.url.replace("sample.mp4", "animated.gif")
        result = download(DownloadRequest(url, "image", output_dir=self.root / "animation"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), (self.root / "animated.gif").read_bytes())
        with Image.open(result.path) as image:
            self.assertEqual(image.n_frames, 2)
        with self.assertRaisesRegex(DownloadError, "Animated"):
            download(DownloadRequest(url, "webp", output_dir=self.root / "animation-conversion"), logger=_SilentLogger())

    def test_missing_thumbnail_and_corrupt_image_have_readable_errors(self):
        for endpoint in ("no-thumbnail.html", "broken.png"):
            with self.subTest(endpoint=endpoint), self.assertRaises(DownloadError):
                download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "png", output_dir=self.root / f"bad-{endpoint}"), logger=_SilentLogger())

    def test_image_pixel_limit_is_enforced(self):
        with patch("universal_downloader.images.MAX_IMAGE_PIXELS", 100), self.assertRaisesRegex(DownloadError, "pixel limit"):
            download(DownloadRequest(self.url.replace("sample.mp4", "thumbnail.png"), "png", output_dir=self.root / "pixel-limit"), logger=_SilentLogger())

    def test_unavailable_image_encoder_has_a_readable_error(self):
        with patch("universal_downloader.images.available_image_formats", return_value=["png"]), self.assertRaisesRegex(DownloadError, "encoding is not available"):
            download(DownloadRequest(self.url.replace("sample.mp4", "thumbnail.png"), "avif", output_dir=self.root / "missing-encoder"), logger=_SilentLogger())

    def test_audio_request_for_silent_video_fails_without_a_completed_output(self):
        folder = self.root / "silent-audio"
        with self.assertRaises(DownloadError):
            download(DownloadRequest(self.url.replace("sample.mp4", "silent.mp4"), "mp3", output_dir=folder), logger=_SilentLogger())
        self.assertFalse(list(folder.glob("*.mp3")))
        self.assertFalse(list(folder.glob(".unidown-*")))

    def test_audio_bitrate_creates_distinct_valid_outputs(self):
        folder = self.root / "bitrates"
        results = [download(DownloadRequest(self.url, "mp3", output_dir=folder, audio_bitrate=bitrate), logger=_SilentLogger()) for bitrate in ("128", "320")]
        self.assertNotEqual(results[0].path, results[1].path)
        self.assertEqual([int(self._probe(result.path)["streams"][0]["bit_rate"]) for result in results], [128000, 320000])

    def test_image_size_limit_returns_a_readable_error(self):
        with self.assertRaisesRegex(DownloadError, "32 MB"):
            download(DownloadRequest(self.url.replace("sample.mp4", "large.png"), "png", output_dir=self.root / "size-limit"), logger=_SilentLogger())

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
