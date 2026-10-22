import functools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image
from yt_dlp import YoutubeDL

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.downloader import DownloadRequest, download
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection
from universal_downloader.limits import TransferBudget, limited_format_selector, validate_download_source_limits


class _Logger:
    def debug(self, message):
        pass

    warning = error = debug


def _format(identifier, *, height=None, video="h264", audio="none", size=None):
    return {"format_id": identifier, "url": f"https://example.com/{identifier}.mp4", "ext": "mp4", "vcodec": video, "acodec": audio, "height": height, "filesize": size, "protocol": "https"}


class LimitSelectionTests(unittest.TestCase):
    def select(self, formats, *, height=None, size=None, kind="video", resize=False):
        specification = "bestaudio/best" if kind == "audio" else "bestvideo+bestaudio/best/bestvideo"
        with YoutubeDL({"quiet": True, "logger": _Logger()}) as downloader:
            selector = limited_format_selector(downloader, specification, kind, {"formats": formats}, max_height=height, max_size=size, allow_resize=resize)
            return list(selector({"formats": formats}))[0]

    def test_separate_streams_are_budgeted_together(self):
        audio = _format("audio", video="none", audio="aac", size=40)
        low = _format("low", height=360, size=50)
        high = _format("high", height=720, size=80)
        selected = self.select([audio, low, high], size=100)
        self.assertEqual([item["format_id"] for item in selected["requested_formats"]], ["low", "audio"])

    def test_audio_is_not_silently_discarded_to_fit_limit(self):
        audio = _format("audio", video="none", audio="aac", size=80)
        video = _format("video", height=720, size=80)
        with self.assertRaisesRegex(DownloadError, "No available source"):
            self.select([audio, video], size=100)

    def test_muxed_source_can_fit_when_separate_pair_does_not(self):
        audio = _format("audio", video="none", audio="aac", size=80)
        video = _format("video", height=720, size=80)
        muxed = _format("combined", height=360, audio="aac", size=90)
        self.assertEqual(self.select([muxed, audio, video], size=100)["format_id"], "combined")

    def test_original_height_is_strict_but_explicit_conversion_can_resize(self):
        unknown = _format("unknown", audio="aac", size=10)
        high = _format("high", height=1080, audio="aac", size=10)
        for formats in ([unknown], [high]):
            with self.subTest(formats=formats), self.assertRaises(DownloadError):
                self.select(formats, height=720)
        self.assertEqual(self.select([high], height=720, resize=True)["format_id"], "high")

    def test_explicit_resize_prefers_an_available_bounded_source(self):
        low = _format("low", height=360, audio="aac", size=10)
        high = _format("high", height=1080, audio="aac", size=10)
        self.assertEqual(self.select([low, high], height=720, resize=True)["format_id"], "low")

    def test_audio_only_size_cap_selects_smaller_native_source(self):
        low = _format("low", video="none", audio="aac", size=40)
        high = _format("high", video="none", audio="aac", size=80)
        self.assertEqual(self.select([low, high], size=50, kind="audio")["format_id"], "low")

    def test_audio_can_use_muxed_source_when_native_audio_sources_exceed_budget(self):
        native = _format("audio", video="none", audio="aac", size=80)
        combined = _format("combined", height=240, audio="aac", size=40)
        self.assertEqual(self.select([combined, native], size=50, kind="audio")["format_id"], "combined")

    def test_checked_original_height_and_conversion_options_use_actual_selection_policy(self):
        source = _format("source", height=1080, audio="aac", size=40)
        inspected = MediaInspection("https://example.com/video", {"formats": [source]}, ("video", "audio"))
        with self.assertRaisesRegex(DownloadError, "No available source"):
            validate_download_source_limits(inspected, "video", target=None, max_height=720, max_size=None)
        for target in ("mp4", "mkv", "mov", "webm", "gif", "webp", "apng"):
            validate_download_source_limits(inspected, "video", target=target, max_height=720, max_size=None)
        source["height"] = None
        with self.assertRaisesRegex(DownloadError, "No available source"):
            validate_download_source_limits(inspected, "video", target=None, max_height=720, max_size=None)

    def test_checked_options_budget_all_required_streams_and_omit_unmonitorable_protocols(self):
        audio = _format("audio", video="none", audio="aac", size=80)
        video = _format("video", height=720, size=80)
        inspected = MediaInspection("https://example.com/video", {"formats": [audio, video]}, ("video", "audio"))
        for target in (None, "mp4"):
            with self.assertRaisesRegex(DownloadError, "No available source"):
                validate_download_source_limits(inspected, "video", target=target, max_height=None, max_size=100)
        # An animation does not download an audio stream that it cannot use.
        validate_download_source_limits(inspected, "video", target="gif", max_height=None, max_size=100)
        video["protocol"] = "m3u8_native"
        with self.assertRaises(DownloadError):
            validate_download_source_limits(inspected, "video", target="gif", max_height=None, max_size=100)

    def test_checked_original_ignores_low_drm_and_storyboard_renditions(self):
        high = _format("high", height=1080, audio="aac")
        drm = {**_format("drm", height=240, audio="aac"), "has_drm": True}
        storyboard = {**_format("storyboard", height=120), "ext": "mhtml", "protocol": "mhtml"}
        inspected = MediaInspection("https://example.com/video", {"formats": [drm, storyboard, high]}, ("video", "audio"))
        with self.assertRaisesRegex(DownloadError, "No available source"):
            validate_download_source_limits(inspected, "video", target=None, max_height=720, max_size=None)

    def test_unmonitorable_hls_or_external_protocol_cannot_claim_a_transfer_cap(self):
        for protocol in ("m3u8", "m3u8_native", "rtmp", "rtsp"):
            with self.subTest(protocol=protocol):
                source = {**_format("source", height=720, audio="aac", size=10), "protocol": protocol}
                with self.assertRaisesRegex(DownloadError, "cannot monitor this streaming protocol"):
                    self.select([source], size=100)
                # Height-only selection keeps the normal supported download.
                self.assertEqual(self.select([source], height=720)["format_id"], "source")

    def test_invalid_limits_fail_before_network(self):
        with patch("universal_downloader.downloader.inspect_media") as inspect:
            for options in ({"max_size": 0}, {"max_height": -1}, {"max_size": True}, {"max_height": 1.5}):
                with self.subTest(options=options), self.assertRaises(DownloadError):
                    download(DownloadRequest("https://example.com/a", **options))
            inspect.assert_not_called()

    def test_budget_aggregates_streams_and_ignores_repeated_progress(self):
        guard = TransferBudget(100)
        guard({"status": "finished", "downloaded_bytes": 60, "info_dict": {"format_id": "video"}})
        guard({"status": "finished", "downloaded_bytes": 60, "info_dict": {"format_id": "video"}})
        guard({"status": "downloading", "downloaded_bytes": 40, "info_dict": {"format_id": "audio"}})
        with self.assertRaisesRegex(DownloadError, "source download exceeds"):
            guard({"status": "downloading", "downloaded_bytes": 41, "info_dict": {"format_id": "audio"}})

    def test_declared_remaining_stream_size_aborts_before_full_transfer(self):
        guard = TransferBudget(100)
        guard({"status": "finished", "downloaded_bytes": 60, "info_dict": {"format_id": "video"}})
        with self.assertRaises(DownloadError):
            guard({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 50, "info_dict": {"format_id": "audio"}})

    def test_finished_sizes_without_byte_progress_still_accumulate(self):
        guard = TransferBudget(100)
        guard({"status": "finished", "total_bytes": 60, "filename": "video.mp4"})
        with self.assertRaises(DownloadError):
            guard({"status": "finished", "total_bytes": 50, "filename": "audio.m4a"})


class _Handler(SimpleHTTPRequestHandler):
    requests = []
    unknown_size = False

    def send_header(self, keyword, value):
        if self.unknown_size and keyword.lower() == "content-length":
            return
        return super().send_header(keyword, value)

    def do_GET(self):
        self.requests.append(self.path)
        if self.path == "/unknown.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            try:
                with (Path(self.directory) / "video.mp4").open("rb") as source:
                    while chunk := source.read(512):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        return super().do_GET()

    def log_message(self, *args):
        pass


@unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required")
class LimitDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.tools = find_ffmpeg()
        suffix = ".exe" if (cls.tools / "ffmpeg.exe").is_file() else ""
        cls.ffmpeg = cls.tools / f"ffmpeg{suffix}"
        cls.ffprobe = cls.tools / f"ffprobe{suffix}"
        subprocess.run([str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=96x72:rate=10", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.root / "video.mp4")], check=True, capture_output=True, timeout=30)
        for name, options in (
            ("high.mp4", ["-an", "-c:v", "copy"]),
            ("low.mp4", ["-an", "-vf", "scale=48:36", "-c:v", "libx264", "-crf", "30"]),
            ("audio.m4a", ["-vn", "-c:a", "copy"]),
        ):
            subprocess.run([str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(cls.root / "video.mp4"), *options, str(cls.root / name)], check=True, capture_output=True, timeout=30)
        help_text = subprocess.run([str(cls.ffmpeg), "-hide_banner", "-h", "full"], capture_output=True, text=True, timeout=20).stdout
        rotation = (["-display_rotation:v:0", "90", "-i", str(cls.root / "video.mp4"), "-c", "copy"] if "-display_rotation" in help_text else
                    ["-i", str(cls.root / "video.mp4"), "-c", "copy", "-metadata:s:v:0", "rotate=90"])
        subprocess.run([str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", *rotation, str(cls.root / "rotated.mp4")], check=True, capture_output=True, timeout=30)
        first = Image.new("RGB", (96, 72), "red")
        first.save(cls.root / "animation.gif", save_all=True, append_images=[Image.new("RGB", (96, 72), "blue")], duration=200)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(cls.root)))
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        self.output = tempfile.TemporaryDirectory()
        self.addCleanup(self.output.cleanup)
        self.folder = Path(self.output.name)
        self.environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.folder / "state")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        _Handler.requests.clear()
        _Handler.unknown_size = False

    def inspected(self, path="/video.mp4", *, declared_size=None, declared_height=72):
        url = self.base + path
        source = {"format_id": "source", "url": url, "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": declared_height, "filesize": declared_size}
        return MediaInspection(url, {"id": "fixture", "title": "Limit fixture", "extractor_key": "Generic", "webpage_url": url, "formats": [source]}, ("video", "audio"))

    def test_unknown_size_transfer_aborts_and_never_publishes(self):
        inspected = self.inspected("/unknown.mp4")
        with self.assertRaisesRegex(DownloadError, "source download exceeds --max-size"):
            download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_size=1024), inspected=inspected, logger=_Logger())
        self.assertIn("/unknown.mp4", _Handler.requests)
        self.assertEqual(list((self.folder / "downloads").iterdir()), [])

    def test_known_size_rejection_never_fetches_media(self):
        inspected = self.inspected(declared_size=10_000)
        with self.assertRaisesRegex(DownloadError, "No available source"):
            download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_size=1000), inspected=inspected, logger=_Logger())
        self.assertEqual(_Handler.requests, [])

    def test_actual_height_is_verified_before_history_publish(self):
        inspected = self.inspected(declared_height=24)
        with self.assertRaisesRegex(DownloadError, "completed file exceeds --max-height"):
            download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_height=24), inspected=inspected, logger=_Logger())
        self.assertEqual(list((self.folder / "downloads").iterdir()), [])

    def test_actual_display_height_is_verified_when_rotation_exceeds_coded_height(self):
        inspected = self.inspected("/rotated.mp4", declared_height=72)
        with self.assertRaisesRegex(DownloadError, "completed file exceeds --max-height"):
            download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_height=80), inspected=inspected, logger=_Logger())
        self.assertEqual(list((self.folder / "downloads").iterdir()), [])

    def test_direct_video_inspection_uses_probed_display_height_for_checked_options(self):
        from universal_downloader.downloader import inspect_media
        from universal_downloader.desktop_service import _asset_options
        inspected = inspect_media(self.base + "/rotated.mp4", logger=_Logger())
        self.assertEqual(inspected.heights, (96,))
        options = _asset_options(inspected, {"max_height": 80, "max_size": None})
        video = next(item for item in options if item["id"] == "video")
        self.assertNotIn("original", {option["id"] for option in video["options"]})
        self.assertTrue({"mp4", "mkv", "mov", "webm"}.issubset({option["id"] for option in video["options"]}))

    def test_explicit_mp4_conversion_resizes_and_preserves_audio(self):
        inspected = self.inspected()
        result = download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", convert_to="mp4", max_height=36, max_size=100_000), inspected=inspected, logger=_Logger())
        metadata = json.loads(subprocess.run([str(self.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(result.path)], check=True, capture_output=True, text=True).stdout)
        video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
        self.assertEqual(video["height"], 36)
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))
        self.assertLessEqual(result.path.stat().st_size, 100_000)

    def test_raw_animation_original_is_rejected_but_explicit_resize_works(self):
        data = (self.root / "animation.gif").read_bytes()
        url = self.base + "/animation.gif"
        inspected = MediaInspection(url, {"title": "GIF", "image_url": url, "duration": 0.4}, ("image",), image_data=data, animated=True, image_extension="gif")
        with self.assertRaisesRegex(DownloadError, "original animation exceeds --max-height"):
            download(DownloadRequest(url, "image", self.folder / "downloads", max_height=36), inspected=inspected, logger=_Logger())
        self.assertFalse((self.folder / "downloads").exists())
        result = download(DownloadRequest(url, "image", self.folder / "downloads", "gif", max_height=36), inspected=inspected, logger=_Logger())
        with Image.open(result.path) as image:
            self.assertEqual(image.height, 36)
            self.assertTrue(image.is_animated)
        self.assertEqual(_Handler.requests, [])

    def test_final_size_guard_never_publishes_or_modifies_existing_files(self):
        folder = self.folder / "downloads"
        folder.mkdir()
        existing = folder / "keep.mp4"
        existing.write_bytes(b"keep existing")
        inspected = self.inspected()

        def staged(url, kind, output_dir, *args, **kwargs):
            from universal_downloader.downloader import DownloadResult
            path = output_dir / "oversized.mp4"
            path.write_bytes(b"x" * 1025)
            return DownloadResult("fixture", path)

        with patch("universal_downloader.downloader._download_original", side_effect=staged), self.assertRaisesRegex(DownloadError, "completed file exceeds --max-size"):
            download(DownloadRequest(inspected.url, output_dir=folder, max_size=1024), inspected=inspected, logger=_Logger())
        self.assertEqual(list(folder.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b"keep existing")

    def separate_streams(self, *, known=True):
        formats = []
        for name, height, video, audio in (("audio.m4a", None, "none", "aac"), ("low.mp4", 36, "h264", "none"), ("high.mp4", 72, "h264", "none")):
            formats.append({"format_id": name, "url": self.base + "/" + name, "ext": Path(name).suffix[1:], "vcodec": video, "acodec": audio, "height": height, "filesize": (self.root / name).stat().st_size if known else None})
        url = self.base + "/adaptive-page"
        return MediaInspection(url, {"id": "adaptive", "title": "Adaptive streams", "extractor_key": "Generic", "webpage_url": url, "formats": formats}, ("video", "audio"))

    def test_real_separate_stream_selection_sums_sizes_and_keeps_audio(self):
        inspected = self.separate_streams()
        limit = sum((self.root / name).stat().st_size for name in ("low.mp4", "audio.m4a"))
        self.assertGreater((self.root / "high.mp4").stat().st_size, (self.root / "low.mp4").stat().st_size)
        result = download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_size=limit), inspected=inspected, logger=_Logger())
        self.assertNotIn("/high.mp4", _Handler.requests)
        self.assertIn("/low.mp4", _Handler.requests)
        self.assertIn("/audio.m4a", _Handler.requests)
        metadata = json.loads(subprocess.run([str(self.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(result.path)], check=True, capture_output=True, text=True).stdout)
        self.assertEqual(next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")["height"], 36)
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))
        self.assertLessEqual(result.path.stat().st_size, limit)

    def test_real_unknown_separate_sizes_abort_on_combined_budget(self):
        _Handler.unknown_size = True
        inspected = self.separate_streams(known=False)
        limit = sum((self.root / name).stat().st_size for name in ("high.mp4", "audio.m4a")) - 1
        self.assertGreater(limit, max((self.root / name).stat().st_size for name in ("high.mp4", "audio.m4a")))
        with self.assertRaisesRegex(DownloadError, "source download exceeds --max-size"):
            download(DownloadRequest(inspected.url, output_dir=self.folder / "downloads", max_size=limit), inspected=inspected, logger=_Logger())
        self.assertIn("/high.mp4", _Handler.requests)
        self.assertIn("/audio.m4a", _Handler.requests)
        self.assertEqual(list((self.folder / "downloads").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
