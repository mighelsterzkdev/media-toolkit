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
from universal_downloader.downloader import DownloadError, DownloadRequest, download, inspect_media
from yt_dlp import YoutubeDL
from yt_dlp.networking import Request


class _SilentHandler(SimpleHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append(urlsplit(self.path).path)
        if self.path == "/cookie-page.html":
            body = b'<html><head><title>Session video</title></head><body><video src="/protected.mp4"></video></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "media_session=example; Path=/")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/protected.mp4":
            if "media_session=example" not in self.headers.get("Cookie", ""):
                self.send_error(403)
                return
            self.path = "/sample.mp4"
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
        exif = Image.Exif()
        exif[270] = "Original image metadata"
        Image.new("RGB", (40, 30), "green").save(cls.root / "small.jpg", exif=exif)
        for extension in ("webp", "avif", "bmp", "tiff", "ico"):
            Image.new("RGBA", (32, 32), (25, 90, 180, 100)).save(cls.root / f"original.{extension}")
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
        (cls.root / "empty.html").write_text("<html><body>No media here.</body></html>", encoding="utf-8")
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
            "-c:a", "copy", "-vn", str(cls.root / "song.m4a"),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.fixture),
            "-c:a", "libopus", "-vn", str(cls.root / "song.webm"),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.fixture),
            "-c:a", "libmp3lame", "-vn", str(cls.root / "song.mp3"),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.fixture),
            "-c:v", "copy", "-c:a", "mp2", str(cls.root / "uncommon.mkv"),
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

    def _packet_hashes(self, path, stream):
        result = subprocess.run([
            str(self.ffprobe), "-v", "error", "-select_streams", stream,
            "-show_packets", "-show_data_hash", "sha256",
            "-show_entries", "packet=data_hash", "-of", "json", str(path),
        ], check=True, capture_output=True, text=True, timeout=15)
        return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]

    @contextlib.contextmanager
    def _twitter_animation(self, media_url="https://video.twimg.com/tweet_video/fixture.mp4"):
        """Use the real Twitter extractor with a local API and media fixture."""
        base = self.url.removesuffix("sample.mp4")
        status = {
            "full_text": "animated clip", "user": {"name": "GIF Test", "screen_name": "gif_test"},
            "extended_entities": {"media": [{
                "id_str": "1234567890123456788", "type": "animated_gif",
                "media_url_https": base + "small.jpg",
                "video_info": {"variants": [{"url": media_url, "content_type": "video/mp4"}]},
            }]},
        }
        original_urlopen = YoutubeDL.urlopen

        def route(downloader, request):
            url = request if isinstance(request, str) else request.url
            if url == media_url:
                request = Request(base + "silent.mp4", headers=dict(request.headers) if not isinstance(request, str) else {})
            elif not url.startswith(base):
                raise AssertionError(f"Unexpected external network request in fixture: {url}")
            return original_urlopen(downloader, request)

        with patch("yt_dlp.extractor.twitter.TwitterIE._extract_status", return_value=status), patch.object(YoutubeDL, "urlopen", new=route):
            yield "https://x.com/gif_test/status/1234567890123456789"

    def test_twitter_api_type_survives_a_change_to_the_asset_url(self):
        with self._twitter_animation("https://video.twimg.com/new_media_route/fixture.mp4") as url:
            inspected = inspect_media(url, media_format="image", logger=_SilentLogger())
            self.assertEqual(inspected.information["media_type"], "animated_gif")
            self.assertTrue(inspected.animation_video)
            result = download(DownloadRequest(url, "image", self.root / "twitter-declared-type"), inspected=inspected, logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), (self.root / "silent.mp4").read_bytes())

    def test_mp3_conversion_produces_audio_only_and_reuses_completed_output(self):
        inspected = inspect_media(self.url, logger=_SilentLogger())
        request = DownloadRequest(self.url, "audio", self.root / "converted-mp3", "mp3")
        result = download(request, inspected=inspected, logger=_SilentLogger())
        streams = self._probe(result.path)["streams"]
        self.assertEqual([stream["codec_type"] for stream in streams], ["audio"])
        self.assertEqual(streams[0]["codec_name"], "mp3")
        self.assertEqual(int(streams[0]["bit_rate"]), 192000)
        self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])
        modified = result.path.stat().st_mtime_ns
        _SilentHandler.requests.clear()
        repeated = download(request, inspected=inspected, logger=_SilentLogger())
        self.assertTrue(repeated.already_exists)
        self.assertEqual(result.path.stat().st_mtime_ns, modified)
        self.assertEqual(_SilentHandler.requests, [])

    def test_mp4_conversion_uses_compatible_video_and_audio_codecs(self):
        result = download(DownloadRequest(self.url, "video", self.root / "converted-mp4", "mp4"), logger=_SilentLogger())
        streams = self._probe(result.path)["streams"]
        self.assertEqual([stream["codec_name"] for stream in streams], ["h264", "aac"])
        self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])

    def test_converting_an_existing_mp3_keeps_its_original_bytes(self):
        url = self.url.replace("sample.mp4", "song.mp3")
        result = download(DownloadRequest(url, "audio", self.root / "convert-native-mp3", "mp3"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), (self.root / "song.mp3").read_bytes())
        self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])

    def test_image_conversions_encode_real_formats_and_flatten_jpeg_transparency(self):
        for endpoint, target, expected in (("thumbnail.png", "jpg", "JPEG"), ("small.jpg", "png", "PNG"), ("thumbnail.png", "png", "PNG")):
            with self.subTest(endpoint=endpoint, target=target):
                url = self.url.replace("sample.mp4", endpoint)
                with patch("universal_downloader.downloader.find_ffmpeg", return_value=None), patch("universal_downloader.conversions.find_ffmpeg", return_value=None):
                    result = download(DownloadRequest(url, "image", self.root / f"convert-{endpoint}-{target}", target), logger=_SilentLogger())
                with Image.open(result.path) as image:
                    image.load()
                    self.assertEqual(image.format, expected)
                    if endpoint == "thumbnail.png" and target == "jpg":
                        self.assertTrue(all(abs(actual - value) <= 4 for actual, value in zip(image.getpixel((0, 0)), (165, 190, 226))))
                if endpoint == "thumbnail.png" and target == "png":
                    self.assertEqual(result.path.read_bytes(), (self.root / endpoint).read_bytes())
                self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])

    def test_native_gif_can_be_kept_or_converted_to_mp4_without_losing_animation(self):
        url = self.url.replace("sample.mp4", "animated.gif")
        for target in ("gif", "mp4"):
            with self.subTest(target=target):
                result = download(DownloadRequest(url, "image", self.root / f"convert-native-gif-{target}", target), logger=_SilentLogger())
                if target == "gif":
                    self.assertEqual(result.path.read_bytes(), (self.root / "animated.gif").read_bytes())
                else:
                    self.assertEqual([stream["codec_name"] for stream in self._probe(result.path)["streams"]], ["h264"])
                    self.assertGreater(len(self._packet_hashes(result.path, "v:0")), 1)
                self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])

    def test_twitter_animation_converts_to_a_real_animated_gif(self):
        with self._twitter_animation() as url:
            inspected = inspect_media(url, logger=_SilentLogger())
            result = download(DownloadRequest(url, "image", self.root / "converted-twitter-gif", "gif"), inspected=inspected, logger=_SilentLogger())
            alias = download(DownloadRequest(url, "video", self.root / "converted-twitter-gif", "gif"), inspected=inspected, logger=_SilentLogger())
            self.assertEqual(alias.path, result.path)
            self.assertTrue(alias.already_exists)
        with Image.open(result.path) as image:
            self.assertEqual(image.format, "GIF")
            self.assertGreater(image.n_frames, 1)
            self.assertEqual(image.size, (64, 48))
        self.assertEqual([path.resolve() for path in result.path.parent.iterdir()], [result.path])

    def test_incompatible_or_long_conversions_are_rejected_before_downloading(self):
        for endpoint, kind, target in (("animated.gif", "image", "png"), ("thumbnail.png", "image", "mp3"), ("sample.mp4", "video", "gif")):
            with self.subTest(endpoint=endpoint, target=target):
                folder = self.root / f"reject-{endpoint}-{target}"
                with self.assertRaises(DownloadError):
                    download(DownloadRequest(self.url.replace("sample.mp4", endpoint), kind, folder, target), logger=_SilentLogger())
                self.assertFalse(folder.exists())
        with self._twitter_animation() as url:
            inspected = inspect_media(url, logger=_SilentLogger())
            inspected.information["duration"] = 120
            folder = self.root / "long-gif"
            _SilentHandler.requests.clear()
            with self.assertRaisesRegex(DownloadError, "30 seconds"):
                download(DownloadRequest(url, "image", folder, "gif"), inspected=inspected, logger=_SilentLogger())
            self.assertFalse(folder.exists())
            self.assertEqual(_SilentHandler.requests, [])

    def test_failed_or_canceled_conversion_cleans_only_its_temporary_files(self):
        inspected = inspect_media(self.url, logger=_SilentLogger())
        for failure in (DownloadError("Conversion failed"), KeyboardInterrupt()):
            folder = self.root / f"failed-convert-{type(failure).__name__}"
            with patch("universal_downloader.downloader.convert_file", side_effect=failure), self.assertRaises(type(failure)):
                download(DownloadRequest(self.url, "audio", folder, "mp3"), inspected=inspected, logger=_SilentLogger())
            self.assertEqual(list(folder.iterdir()), [])

    def test_real_menu_converts_audio_after_pasting_url(self):
        from universal_downloader.cli import main
        folder = self.root / "convert-command"
        answers = [self.url, "2", "2", ""]
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--output", str(folder)]), 0)
        files = list(folder.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, ".mp3")
        self.assertIn("Original (recommended)", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_twitter_gif_keeps_animation_instead_of_downloading_its_jpeg_preview(self):
        with self._twitter_animation() as url:
            for selected in (None, "image", "video"):
                with self.subTest(selected=selected):
                    _SilentHandler.requests.clear()
                    inspected = inspect_media(url, media_format=selected, logger=_SilentLogger())
                    self.assertEqual(inspected.kinds, ("image",))
                    self.assertTrue(inspected.animated)
                    self.assertTrue(inspected.animation_video)
                    self.assertIsNone(inspected.image_data)
                    self.assertEqual(_SilentHandler.requests, [])
                    folder = self.root / f"twitter-{selected}"
                    folder.mkdir()
                    legacy_preview = folder / f"{inspected.information['title']} [{inspected.information['id']}] [image].jpg"
                    preview_bytes = (self.root / "small.jpg").read_bytes()
                    legacy_preview.write_bytes(preview_bytes)
                    request = DownloadRequest(url, selected or "image", folder)
                    result = download(request, inspected=inspected, logger=_SilentLogger())
                    self.assertEqual(result.path.suffix, ".mp4")
                    self.assertEqual(result.path.read_bytes(), (self.root / "silent.mp4").read_bytes())
                    self.assertEqual([stream["codec_type"] for stream in self._probe(result.path)["streams"]], ["video"])
                    self.assertGreater(len(self._packet_hashes(result.path, "v:0")), 1)
                    self.assertFalse(result.already_exists)
                    self.assertNotIn("/small.jpg", _SilentHandler.requests)
                    self.assertEqual(legacy_preview.read_bytes(), preview_bytes)
                    repeated = download(request, inspected=inspected, logger=_SilentLogger())
                    self.assertTrue(repeated.already_exists)
                    self.assertEqual({path.resolve() for path in folder.iterdir()}, {result.path.resolve(), legacy_preview.resolve()})

    def test_twitter_animation_waits_for_original_choice_and_works_without_ffmpeg(self):
        from universal_downloader.cli import main
        folder = self.root / "twitter-menu"
        with self._twitter_animation() as url, patch("universal_downloader.downloader.find_ffmpeg", return_value=None), patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[url, "1", ""]) as user_input, contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--output", str(folder)]), 0)
        self.assertEqual(user_input.call_count, 3)
        self.assertIn("Available: Animation", output.getvalue())
        self.assertIn("1. Original (recommended)", output.getvalue())
        self.assertNotIn("Download [1]", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        files = list(folder.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, ".mp4")
        self.assertEqual(files[0].read_bytes(), (self.root / "silent.mp4").read_bytes())

    def test_preflight_detects_actual_content_including_silent_video(self):
        for endpoint, kinds in (
            ("page.html", ("video", "audio", "image")),
            ("sample.mp4", ("video", "audio")), ("silent.mp4", ("video",)),
            ("song.m4a", ("audio",)), ("song.webm", ("audio",)),
            ("thumbnail.png", ("image",)), ("image-without-extension", ("image",)),
            ("image-page.html", ("image",)),
        ):
            with self.subTest(endpoint=endpoint):
                inspected = inspect_media(self.url.replace("sample.mp4", endpoint), logger=_SilentLogger())
                self.assertEqual(inspected.kinds, kinds)
                if "video" in kinds:
                    self.assertEqual(inspected.heights, (48,))

    def test_direct_video_keeps_original_bytes_and_codecs(self):
        result = download(DownloadRequest(self.url, output_dir=self.root / "video"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), self.fixture.read_bytes())
        self.assertEqual({stream["codec_type"] for stream in self._probe(result.path)["streams"]}, {"video", "audio"})
        self.assertFalse(result.already_exists)

    def test_audio_extraction_copies_packets_without_quality_loss(self):
        for endpoint, codec, extension in (("sample.mp4", "aac", ".m4a"), ("uncommon.mkv", "mp2", ".mka")):
            with self.subTest(codec=codec):
                result = download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "audio", self.root / f"extracted-{codec}"), logger=_SilentLogger())
                streams = self._probe(result.path)["streams"]
                self.assertEqual([stream["codec_type"] for stream in streams], ["audio"])
                self.assertEqual(streams[0]["codec_name"], codec)
                self.assertEqual(result.path.suffix, extension)
                self.assertEqual(self._packet_hashes(self.root / endpoint, "a:0"), self._packet_hashes(result.path, "a:0"))
                self.assertEqual(list(result.path.parent.iterdir()), [result.path])

    def test_audio_only_files_keep_original_container_and_bytes(self):
        for endpoint in ("song.m4a", "song.webm"):
            with self.subTest(endpoint=endpoint):
                result = download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "audio", self.root / f"native-{endpoint}"), logger=_SilentLogger())
                self.assertEqual(result.path.suffix, Path(endpoint).suffix)
                self.assertEqual(result.path.read_bytes(), (self.root / endpoint).read_bytes())
                self.assertEqual(list(result.path.parent.iterdir()), [result.path])

    def test_image_formats_keep_exact_original_bytes_metadata_and_animation(self):
        for endpoint in ("thumbnail.png", "small.jpg", "original.webp", "original.avif", "animated.gif", "original.bmp", "original.tiff", "original.ico"):
            with self.subTest(endpoint=endpoint), patch("universal_downloader.downloader.find_ffmpeg", return_value=None):
                result = download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "image", self.root / "original-images"), logger=_SilentLogger())
                self.assertEqual(result.path.read_bytes(), (self.root / endpoint).read_bytes())
                self.assertEqual(result.path.suffix, Path(endpoint).suffix)
                with Image.open(result.path) as image:
                    image.load()
                    if endpoint == "animated.gif":
                        self.assertEqual(image.n_frames, 2)
                    if endpoint == "small.jpg":
                        self.assertEqual(image.getexif().get(270), "Original image metadata")

    def test_explicit_thumbnail_skips_video_probe_and_reuses_image_bytes(self):
        for endpoint in ("thumbnail.png", "image-without-extension", "image-page.html", "page.html"):
            with self.subTest(endpoint=endpoint):
                _SilentHandler.requests.clear()
                url = self.url.replace("sample.mp4", endpoint)
                with patch("universal_downloader.downloader.find_ffmpeg", return_value=None):
                    inspected = inspect_media(url, media_format="image", logger=_SilentLogger())
                    requests = list(_SilentHandler.requests)
                    result = download(DownloadRequest(url, "image", self.root / f"prepared-{endpoint}"), inspected=inspected, logger=_SilentLogger())
                self.assertEqual(_SilentHandler.requests, requests)
                self.assertNotIn("/sample.mp4", requests)
                self.assertEqual(result.path.read_bytes(), (self.root / "thumbnail.png").read_bytes())

    def test_inspection_metadata_is_reused_for_video_audio_and_image(self):
        url = self.url.replace("sample.mp4", "page.html")
        calls = []
        original = YoutubeDL.extract_info

        def tracked(downloader, *args, **kwargs):
            calls.append(args[0])
            return original(downloader, *args, **kwargs)

        with patch.object(YoutubeDL, "extract_info", new=tracked):
            inspected = inspect_media(url, logger=_SilentLogger())
            inspected_calls = list(calls)
            for kind, target in (("video", None), ("audio", None), ("image", None), ("audio", "mp3"), ("image", "jpg")):
                result = download(DownloadRequest(url, kind, self.root / f"reused-{kind}-{target}", target), inspected=inspected, logger=_SilentLogger())
                self.assertTrue(result.path.is_file())
            self.assertEqual(calls, inspected_calls)
        self.assertFalse(inspected.information.get("filepath"))

    def test_repeated_downloads_preserve_completed_files_and_report_existing(self):
        for endpoint, kind in (("sample.mp4", "video"), ("sample.mp4", "audio"), ("thumbnail.png", "image")):
            with self.subTest(kind=kind):
                url = self.url.replace("sample.mp4", endpoint)
                inspected = inspect_media(url, media_format=kind, logger=_SilentLogger())
                request = DownloadRequest(url, kind, self.root / f"repeat-{kind}")
                first = download(request, inspected=inspected, logger=_SilentLogger())
                modified = first.path.stat().st_mtime_ns
                _SilentHandler.requests.clear()
                second = download(request, inspected=inspected, logger=_SilentLogger())
                self.assertEqual(first.path, second.path)
                self.assertEqual(second.path.stat().st_mtime_ns, modified)
                self.assertTrue(second.already_exists)
                self.assertEqual(list(second.path.parent.iterdir()), [second.path])
                self.assertEqual(_SilentHandler.requests, [])

    def test_silent_video_downloads_without_offering_or_attempting_audio(self):
        url = self.url.replace("sample.mp4", "silent.mp4")
        inspected = inspect_media(url, logger=_SilentLogger())
        self.assertEqual(inspected.kinds, ("video",))
        folder = self.root / "silent-audio"
        _SilentHandler.requests.clear()
        with self.assertRaisesRegex(DownloadError, "No audio track"):
            download(DownloadRequest(url, "audio", folder), inspected=inspected, logger=_SilentLogger())
        self.assertFalse(folder.exists())
        self.assertEqual(_SilentHandler.requests, [])
        result = download(DownloadRequest(url, output_dir=self.root / "silent-video"), inspected=inspected, logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), (self.root / "silent.mp4").read_bytes())

    def test_separate_dash_streams_are_merged_without_reencoding(self):
        manifest = self.root / "manifest.mpd"
        result = download(DownloadRequest(self.url.replace("sample.mp4", "manifest.mpd"), output_dir=self.root / "merged"), logger=_SilentLogger())
        self.assertEqual({stream["codec_type"] for stream in self._probe(result.path)["streams"]}, {"video", "audio"})
        for stream in ("v:0", "a:0"):
            self.assertEqual(self._packet_hashes(manifest, stream), self._packet_hashes(result.path, stream))
        self.assertEqual(list(result.path.parent.iterdir()), [result.path])

    def test_thumbnail_uses_best_reachable_original_after_http_failure(self):
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
            result = download(DownloadRequest(base + "page.html", "image", self.root / "best-thumb"), logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), (self.root / "thumbnail.png").read_bytes())
        self.assertEqual(_SilentHandler.requests, ["/missing.jpg", "/thumbnail.png"])

    def test_corrupt_oversized_and_unavailable_images_fail_before_creating_output(self):
        for endpoint, message in (("broken.png", "readable image"), ("large.png", "32 MB"), ("no-thumbnail.html", "thumbnail"), ("empty.html", "image")):
            with self.subTest(endpoint=endpoint):
                folder = self.root / f"invalid-{endpoint}"
                with self.assertRaisesRegex(DownloadError, message):
                    download(DownloadRequest(self.url.replace("sample.mp4", endpoint), "image", folder), logger=_SilentLogger())
                self.assertFalse(folder.exists())

    def test_image_pixel_limit_is_checked_before_saving(self):
        folder = self.root / "pixel-limit"
        with patch("universal_downloader.images.MAX_IMAGE_PIXELS", 100), self.assertRaisesRegex(DownloadError, "pixel limit"):
            download(DownloadRequest(self.url.replace("sample.mp4", "thumbnail.png"), "image", folder), logger=_SilentLogger())
        self.assertFalse(folder.exists())

    def test_session_cookies_are_kept_for_probe_and_download(self):
        url = self.url.replace("sample.mp4", "cookie-page.html")
        inspected = inspect_media(url, logger=_SilentLogger())
        self.assertEqual(inspected.kinds, ("video", "audio"))
        self.assertTrue(inspected.cookies)
        result = download(DownloadRequest(url, output_dir=self.root / "session-video"), inspected=inspected, logger=_SilentLogger())
        self.assertEqual(result.path.read_bytes(), self.fixture.read_bytes())

    def test_real_menu_checks_first_and_downloads_original_thumbnail(self):
        from universal_downloader.cli import main
        folder = self.root / "real-menu"
        answers = [self.url.replace("sample.mp4", "page.html"), "3", "1", ""]
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=answers) as user_input, contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--output", str(folder)]), 0)
        files = list(folder.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), (self.root / "thumbnail.png").read_bytes())
        self.assertEqual(user_input.call_count, 4)
        self.assertIn("Saved:", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_real_menu_continues_after_empty_page_and_downloads_two_images(self):
        from universal_downloader.cli import main
        folder = self.root / "multiple-menu"
        answers = [
            self.url.replace("sample.mp4", "empty.html"),
            self.url.replace("sample.mp4", "thumbnail.png"), "1",
            self.url.replace("sample.mp4", "animated.gif"), "1", "",
        ]
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=answers) as user_input, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--output", str(folder)]), 0)
        self.assertEqual(user_input.call_count, 6)
        self.assertEqual(sorted(path.read_bytes() for path in folder.iterdir()), sorted((self.root / endpoint).read_bytes() for endpoint in ("thumbnail.png", "animated.gif")))
        self.assertIn("Error:", errors.getvalue())
        self.assertNotIn("Warning", errors.getvalue())

    def test_http_failure_returns_a_readable_error_without_output(self):
        folder = self.root / "missing-video"
        with self.assertRaises(DownloadError):
            download(DownloadRequest(self.url.replace("sample.mp4", "missing.mp4"), output_dir=folder), logger=_SilentLogger())
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
