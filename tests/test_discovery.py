"""Use real HTTP responses to verify discovery, ambiguity, and original bytes."""

import contextlib
import functools
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlencode
from unittest.mock import patch

from PIL import Image
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpError, ExtractorError

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.downloader import DownloadRequest, download, inspect_media
from universal_downloader.errors import DownloadError
from universal_downloader.urls import google_target


class URLTests(unittest.TestCase):
    def test_google_target_preserves_asset_query_and_percent_escapes(self):
        asset = "https://cdn.example/media?token=a%2Bb%26c&size=original"
        for host in ("www.google.com", "google.com.br", "www.google.co.uk"):
            wrapper = f"https://{host}/imgres?" + urlencode({"imgurl": asset, "imgrefurl": "https://example.com/page"})
            self.assertEqual(google_target(wrapper), asset)
            nested = "https://www.google.com/url?" + urlencode({"q": wrapper})
            self.assertEqual(google_target(nested), asset)

    def test_google_search_is_not_an_arbitrary_media_selection(self):
        for url in ("https://google.com/search?q=cat+gif", "https://google.com/imgres?imgrefurl=https://example.com"):
            with self.assertRaisesRegex(DownloadError, "does not select"):
                google_target(url)
        fake = "https://www.google.com.example.com/url?q=https://example.com"
        self.assertEqual(google_target(fake), fake)

    def test_unsafe_wrapped_urls_are_rejected(self):
        for value in ("file:///C:/private", "javascript:alert(1)", "https://user:password@example.com/a"):
            with self.assertRaises(DownloadError):
                google_target("https://google.com/url?" + urlencode({"q": value}))

    @patch("universal_downloader.downloader.discover")
    @patch("universal_downloader.downloader.YoutubeDL")
    def test_known_extraction_failure_never_becomes_a_cover(self, factory, discover):
        inner = ExtractorError("Video is private", expected=True)
        wrapped = YtDlpError("private", exc_info=(type(inner), inner, None))
        factory.return_value.__enter__.return_value.extract_info.side_effect = wrapped
        with self.assertRaisesRegex(DownloadError, "private"):
            inspect_media("https://example.com/private")
        discover.assert_not_called()


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/animated.gif")
            self.end_headers()
            return
        if self.path == "/denied.mp4":
            self.send_error(403)
            return
        if self.path == "/extensionless":
            self.path = "/animated.gif"
        super().do_GET()


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="unidown-discovery-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(cls.root / "state")})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        a = Image.new("RGB", (80, 60), "red")
        a.save(cls.root / "animated.gif", save_all=True, append_images=[Image.new("RGB", (80, 60), "blue")], duration=100, loop=0)
        Image.new("RGB", (80, 60), "yellow").save(cls.root / "cover.jpg")
        Image.new("RGB", (90, 70), "green").save(cls.root / "second.png")
        pages = {
            "img.html": '<img src="/animated.gif"><img src="/cover.jpg" width="16" height="16">',
            "structured.html": '<script type="application/ld+json">' + json.dumps({"@type": "ImageObject", "contentUrl": "/animated.gif"}) + '</script>',
            "multiple.html": '<img src="/cover.jpg"><img src="/second.png">',
            "mixed.html": '<img src="/animated.gif"><img src="/second.png">',
            "animated-poster.html": '<video src="/denied.mp4" poster="/animated.gif"></video>',
            "static-preview.html": '<meta property="og:image" content="/cover.jpg"><img src="/animated.gif">',
            "lazy.html": '<img src="/cover.jpg" data-src="/animated.gif">',
            "unsafe-lazy.html": '<img src="/animated.gif" data-src="javascript:alert(1)">',
            "broken-video.html": '<meta property="og:image" content="/cover.jpg"><video src="/denied.mp4"></video>',
            "misleading.mp4": '<meta property="og:image" content="/animated.gif">',
            "empty.html": '<p>No media</p>',
        }
        for name, body in pages.items():
            (cls.root / name).write_text('<!doctype html><html><head><title>Fixture</title></head><body>' + body + '</body></html>', encoding="utf-8")
        cls.tools = find_ffmpeg()
        if cls.tools:
            suffix = ".exe" if (cls.tools / "ffmpeg.exe").is_file() else ""
            cls.ffprobe = cls.tools / f"ffprobe{suffix}"
            subprocess.run([str(cls.tools / f"ffmpeg{suffix}"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:size=80x60:rate=10", "-t", "0.3", "-c:v", "libx264", str(cls.root / "clip.mp4")], check=True, capture_output=True, timeout=30)
            (cls.root / "video.html").write_text('<html><meta property="og:image" content="/cover.jpg"><video><source src="/clip.mp4"></video></html>', encoding="utf-8")
        handler = functools.partial(_Handler, directory=str(cls.root))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def inspect(self, path, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return inspect_media(self.base + path, **kwargs)

    def test_direct_extensionless_redirected_and_embedded_gifs_keep_animation(self):
        for path in ("/animated.gif", "/extensionless", "/redirect", "/img.html", "/structured.html", "/misleading.mp4"):
            with self.subTest(path=path):
                inspected = self.inspect(path)
                self.assertTrue(inspected.animated)
                self.assertEqual(inspected.kinds, ("image",))
                self.assertEqual(inspected.image_data, (self.root / "animated.gif").read_bytes())

    def test_google_wrapper_download_reuses_direct_asset_and_converts_to_real_mp4(self):
        asset = self.base + "/animated.gif"
        wrapped = "https://google.com/imgres?" + urlencode({"imgurl": asset})
        inspected = inspect_media(wrapped)
        self.assertTrue(inspected.animated)
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            request = DownloadRequest(wrapped, "image", Path(folder))
            original = download(request, inspected=inspected)
            again = download(DownloadRequest(asset, "image", Path(folder)))
            self.assertEqual(original.path, again.path)
            self.assertTrue(again.already_exists)
            if self.tools:
                result = download(DownloadRequest(wrapped, "image", Path(folder), "mp4"), inspected=inspected)
                metadata = json.loads(subprocess.check_output([str(self.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(result.path)]))
                self.assertEqual(metadata["streams"][0]["codec_name"], "h264")

    def test_multiple_images_are_alternatives_not_silently_selected(self):
        inspected = self.inspect("/multiple.html")
        self.assertEqual(len(inspected.alternatives), 2)
        self.assertEqual({child.image_extension for child in inspected.alternatives}, {"png", "jpg"})

    def test_failed_primary_video_never_returns_its_static_cover(self):
        for path in ("/broken-video.html", "/animated-poster.html"):
            with self.subTest(path=path), self.assertRaisesRegex(DownloadError, "403|denied"):
                self.inspect(path)

    def test_independent_static_image_is_preserved_beside_animation(self):
        inspected = self.inspect("/mixed.html")
        self.assertEqual(len(inspected.alternatives), 2)
        self.assertEqual({child.image_extension for child in inspected.alternatives}, {"gif", "png"})

    def test_static_preview_does_not_hide_original_animation(self):
        inspected = self.inspect("/static-preview.html")
        self.assertEqual({child.image_extension for child in inspected.alternatives}, {"gif", "jpg"})

    def test_lazy_image_prefers_real_asset_and_unsafe_lazy_falls_back(self):
        for path in ("/lazy.html", "/unsafe-lazy.html"):
            with self.subTest(path=path):
                inspected = self.inspect(path)
                self.assertTrue(inspected.animated)
                self.assertEqual(inspected.image_data, (self.root / "animated.gif").read_bytes())

    def test_html_video_source_exposes_media_and_separate_thumbnail(self):
        if not self.tools:
            self.skipTest("FFmpeg required")
        inspected = self.inspect("/video.html")
        self.assertIn("video", inspected.kinds)
        self.assertIn("image", inspected.kinds)
        self.assertEqual(inspected.image_role, "thumbnail")

    def test_empty_and_missing_pages_fail_without_a_file(self):
        with self.assertRaises(DownloadError):
            self.inspect("/empty.html")
        with self.assertRaisesRegex(DownloadError, "404|not found"):
            self.inspect("/missing.gif")


if __name__ == "__main__":
    unittest.main()
