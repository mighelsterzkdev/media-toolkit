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
from universal_downloader.discovery import PageMedia, image_variants, srcset_candidates
from universal_downloader.urls import google_target


class URLTests(unittest.TestCase):
    def test_srcset_keeps_signed_query_commas_and_ignores_invalid_descriptors(self):
        signed = "https://cdn.example/file?token=a%2Bb%26c&crop=1,2"
        parsed = srcset_candidates(f"/small.png 1x, {signed} 2.5x, /bad.png 0x, /invalid.png 200w 2x")
        self.assertEqual(parsed, [("/small.png", 1.0, "x"), (signed, 2.5, "x")])
        self.assertEqual(image_variants({"srcset": "/small.png 320w, /large.png 1600w", "sizes": "(max-width: 40em) 100vw, 50vw"}), ("/large.png", "/small.png"))
        self.assertEqual(image_variants({"src": "/small.png", "width": "1200", "srcset": "/medium.png 0320w, /large.png 640w"}), ("/large.png", "/medium.png", "/small.png"))

    def test_malformed_or_unsafe_image_attributes_do_not_become_candidates(self):
        parser = PageMedia()
        parser.feed('<meta http-equiv><link rel><script type></script><a href>Related</a><img width height alt class src="http://[" data-src="javascript:alert(1)"><img src="/placeholder.gif"><img srcset="data:image/gif;base64,AAAA 1x, /real.gif 2x">')
        self.assertEqual(parser.images, [("/real.gif", "image")])

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
        if self.path.startswith("/signed.gif?"):
            if self.path != "/signed.gif?token=a%2Bb%26c&crop=1,2":
                self.send_error(403)
                return
            self.path = "/animated.gif"
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
        Image.new("RGB", (160, 120), "green").save(cls.root / "large.png")
        Image.new("RGB", (32, 32), "white").save(cls.root / "icon.png")
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
            "srcset.html": '<img src="/cover.jpg" sizes="(max-width: 30em) 100vw, 50vw" srcset="/second.png 90w, /large.png 160w">',
            "density.html": '<img width="80" height="60" src="/cover.jpg" srcset="/second.png 1x, /large.png 2x">',
            "picture.html": '<picture><source type="image/avif" srcset="/missing.avif 400w"><source type="image/png" srcset="/second.png 90w, /large.png 160w"><img src="/cover.jpg" width="80" height="60"></picture>',
            "picture-crops.html": '<picture><source media="(max-width: 799px)" srcset="/second.png"><source media="(min-width: 800px)" srcset="/large.png"><img src="/large.png"></picture>',
            "lazy-srcset.html": '<img width="1" height="1" src="/placeholder.gif" data-srcset="/cover.jpg 80w, /animated.gif 160w">',
            "original-srcset.html": '<img width="1" height="1" src="/placeholder.gif" data-original-srcset="/cover.jpg 80w, /animated.gif 160w">',
            "data-original.html": '<img src="/cover.jpg" srcset="/second.png 90w, /large.png 160w" data-original="/animated.gif">',
            "signed-srcset.html": '<img srcset="/cover.jpg 1x, /signed.gif?token=a%2Bb%26c&amp;crop=1,2 2x">',
            "responsive-gallery.html": '<img srcset="/cover.jpg 80w, /large.png 160w"><img srcset="/cover.jpg 1x, /animated.gif 2x">',
            "meta-refresh.html": '<meta http-equiv="refresh" content="0; URL=\'/animated.gif\'"><p>Redirecting...</p>',
            "delayed-refresh.html": '<meta http-equiv="refresh" content="30; url=/animated.gif"><p>This is a page, not an immediate redirect.</p>',
            "media-with-refresh.html": '<meta http-equiv="refresh" content="0; url=/animated.gif"><video src="/denied.mp4" poster="/cover.jpg"></video>',
            "refresh-loop.html": '<meta http-equiv="refresh" content="0; url=/refresh-loop.html">',
            "gallery.html": '<img src="/icon.png"><img src="/_next/image?url=%2Flarge.png&amp;w=64&amp;q=75">'
                            '<div style="background-image: url(\'/second.png\')"></div><link rel="preload" as="image" href="/cover.jpg">',
            "related-link.html": '<p>Related links:</p><a href="https://www.google.com/imgres?imgurl=https://example.com/related.gif">Another image</a>',
        }
        for name, body in pages.items():
            (cls.root / name).write_text('<!doctype html><html><head><title>Fixture</title></head><body>' + body + '</body></html>', encoding="utf-8")
        cls.tools = find_ffmpeg()
        if cls.tools:
            suffix = ".exe" if (cls.tools / "ffmpeg.exe").is_file() else ""
            cls.ffprobe = cls.tools / f"ffprobe{suffix}"
            subprocess.run([str(cls.tools / f"ffmpeg{suffix}"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:size=80x60:rate=10", "-t", "0.3", "-c:v", "libx264", str(cls.root / "clip.mp4")], check=True, capture_output=True, timeout=30)
            (cls.root / "video-gallery.html").write_text('<html><video><source src="/clip.mp4"></video><img src="/large.png"><img src="/second.png"></html>', encoding="utf-8")
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

    def test_page_pictures_form_a_gallery_of_originals_without_icons(self):
        inspected = self.inspect("/gallery.html")
        # The Next.js optimizer copy is replaced by its original; CSS and preloaded pictures count too.
        self.assertEqual({asset.image_dimensions for asset in inspected.alternatives}, {(160, 120), (90, 70), (80, 60)})
        self.assertEqual([asset.information["title"] for asset in inspected.alternatives], ["large", "second", "cover"])

    def test_page_video_keeps_its_pictures_as_further_choices(self):
        if not self.tools:
            self.skipTest("FFmpeg is not installed")
        inspected = self.inspect("/video-gallery.html")
        self.assertIn("video", inspected.alternatives[0].kinds)
        self.assertEqual([asset.image_dimensions for asset in inspected.alternatives[1:]], [(160, 120), (90, 70)])

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

    def test_responsive_images_choose_largest_declared_variant_without_gallery_duplicates(self):
        for path in ("/srcset.html", "/density.html", "/picture.html"):
            with self.subTest(path=path):
                inspected = self.inspect(path)
                self.assertEqual(inspected.image_data, (self.root / "large.png").read_bytes())
                self.assertEqual(inspected.image_dimensions, (160, 120))
                self.assertFalse(inspected.alternatives)

    def test_lazy_srcset_and_explicit_original_do_not_download_placeholders(self):
        for path in ("/lazy-srcset.html", "/original-srcset.html", "/data-original.html", "/signed-srcset.html"):
            with self.subTest(path=path):
                inspected = self.inspect(path)
                self.assertTrue(inspected.animated)
                self.assertEqual(inspected.image_data, (self.root / "animated.gif").read_bytes())

    def test_distinct_responsive_images_remain_explicit_alternatives(self):
        inspected = self.inspect("/responsive-gallery.html")
        self.assertEqual(len(inspected.alternatives), 2)
        self.assertEqual({asset.image_extension for asset in inspected.alternatives}, {"gif", "png"})
        directed = self.inspect("/picture-crops.html")
        self.assertEqual(len(directed.alternatives), 2)
        self.assertEqual(directed.alternatives[0].image_dimensions, (160, 120))
        self.assertEqual({asset.image_dimensions for asset in directed.alternatives}, {(160, 120), (90, 70)})

    def test_only_immediate_redirect_shells_follow_generic_meta_refresh(self):
        inspected = self.inspect("/meta-refresh.html")
        self.assertTrue(inspected.animated)
        with self.assertRaises(DownloadError):
            self.inspect("/delayed-refresh.html")
        with self.assertRaisesRegex(DownloadError, "403|denied"):
            self.inspect("/media-with-refresh.html")
        with self.assertRaisesRegex(DownloadError, "loop|media could not be accessed"):
            self.inspect("/refresh-loop.html")
        with self.assertRaisesRegex(DownloadError, "No downloadable"):
            self.inspect("/related-link.html")

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
