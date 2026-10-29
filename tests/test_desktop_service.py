"""Exercise desktop queues and cancellation against real local HTTP media."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.desktop_service import DesktopService, _asset_description, _asset_options, _thumbnail, safe_error
from universal_downloader.desktop_queue_store import RESTORED_MESSAGE
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection


class DesktopDescriptionTests(unittest.TestCase):
    def test_media_resolution_never_uses_its_cover_image_dimensions(self):
        preview = BytesIO()
        Image.new("RGB", (640, 360)).save(preview, "PNG")
        video = MediaInspection("https://example.com/video", {"duration": 5}, ("video", "audio", "image"), heights=(180,), image_data=preview.getvalue())
        audio = MediaInspection("https://example.com/audio", {"duration": 5}, ("audio", "image"), image_data=preview.getvalue())
        image = MediaInspection("https://example.com/image", {}, ("image",), image_data=preview.getvalue())
        self.assertEqual(_asset_description(video), "Up to 180p · 0:05")
        self.assertEqual(_asset_description(audio), "0:05")
        self.assertEqual(_asset_description(image), "640 × 360")

    def test_expanded_options_use_descriptive_labels_and_keep_original_first(self):
        preview = BytesIO()
        Image.new("RGBA", (32, 16), (10, 20, 30, 100)).save(preview, "PNG")
        inspected = MediaInspection("https://example.com/image", {}, ("image",), image_data=preview.getvalue())
        options = _asset_options(inspected, {"max_height": None, "max_size": None})[0]["options"]
        self.assertEqual(options[0]["id"], "original")
        self.assertTrue(options[0]["recommended"])
        labels = {option["id"]: option["label"] for option in options}
        self.assertEqual(labels["webp"], "WebP image")
        self.assertEqual(labels["gif"], "GIF image")
        self.assertEqual(labels["jpg"], "JPEG image")
        self.assertIn("bmp", labels)
        self.assertIn("tiff", labels)
        self.assertIn("tga", labels)
        self.assertNotIn("png", labels)
        self.assertNotIn("ico", labels)
        self.assertNotIn("mp3", labels)
        jpeg = next(option for option in options if option["id"] == "jpg")
        self.assertIn("transparency", jpeg["description"].lower())
        self.assertNotIn("selected background", jpeg["description"].lower())

    def test_video_animation_options_are_labeled_as_animation(self):
        inspected = MediaInspection("https://example.com/video", {"ext": "mp4"}, ("video",))
        with patch("universal_downloader.desktop_service.available_download_conversions", return_value={
                "gif": "Loops without audio.", "webp": "Lossless animation.", "apng": "Animated PNG."}):
            options = _asset_options(inspected, {"max_height": None, "max_size": None})[0]["options"]
        labels = {option["id"]: option["label"] for option in options}
        self.assertEqual(labels["gif"], "GIF animation")
        self.assertEqual(labels["webp"], "WebP animation")
        self.assertEqual(labels["apng"], "APNG animation")

    def test_native_av_targets_are_labeled_as_compatibility_conversions(self):
        for kind, native, source in (("video", "mkv", {"vcodec": "ffv1", "acodec": "none"}),
                                     ("audio", "m4a", {"vcodec": "none", "acodec": "alac"})):
            inspected = MediaInspection("https://example.com/media", {"ext": native, **source}, (kind,))
            with patch("universal_downloader.desktop_service.available_download_conversions", return_value={native: "Checks compatible codecs."}):
                options = _asset_options(inspected, {"max_height": None, "max_size": None})[0]["options"]
            self.assertEqual(options[0]["id"], "original")
            self.assertTrue(options[0]["recommended"])
            self.assertEqual(options[1]["label"], f"{native.upper()} compatibility")

    def test_checked_options_hide_impossible_combined_budget_but_keep_video_only_animation(self):
        formats = [
            {"format_id": "audio", "url": "https://example.com/audio.m4a", "protocol": "https", "ext": "m4a", "vcodec": "none", "acodec": "aac", "filesize": 40},
            {"format_id": "video", "url": "https://example.com/video.mp4", "protocol": "https", "ext": "mp4", "vcodec": "h264", "acodec": "none", "height": 720, "filesize": 80},
        ]
        inspected = MediaInspection("https://example.com/media", {"formats": formats}, ("video", "audio"))
        with patch("universal_downloader.desktop_service.available_download_conversions", return_value={"mp4": "Video with audio.", "gif": "Animation without audio."}):
            kinds = _asset_options(inspected, {"max_height": None, "max_size": 100})
        video = next(kind for kind in kinds if kind["id"] == "video")
        self.assertEqual([option["id"] for option in video["options"]], ["gif"])


class _Handler(BaseHTTPRequestHandler):
    bodies = {}
    ranges = []
    slow = False
    rejected = set()

    def log_message(self, *args): pass

    def do_HEAD(self): self._serve(True)
    def do_GET(self): self._serve(False)

    def _serve(self, head):
        path = self.path.split("?", 1)[0]
        if self.path in self.rejected:
            self.send_error(404)
            return
        if path == "/slow.html":
            time.sleep(1)
        if path not in self.bodies:
            self.send_error(404)
            return
        body, content_type = self.bodies[path]
        start, end = 0, len(body) - 1
        requested = self.headers.get("Range")
        if requested:
            bounds = requested.removeprefix("bytes=").split("-")
            start = int(bounds[0] or 0)
            end = min(int(bounds[1]), end) if bounds[1] else end
            if start >= len(body):
                self.send_response(416); self.end_headers(); return
        if not head:
            self.ranges.append((path, requested))
        self.send_response(206 if requested else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("ETag", '"fixture-v1"')
        self.send_header("Accept-Ranges", "bytes")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        if not head:
            try:
                for offset in range(start, end + 1, 4096):
                    self.wfile.write(body[offset:min(offset + 4096, end + 1)])
                    self.wfile.flush()
                    if self.slow and path == "/video.mp4":
                        time.sleep(0.002)
            except (ConnectionError, OSError):
                pass


class DesktopServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = BytesIO()
        Image.new("RGB", (320, 200), "green").save(image, "PNG")
        other = BytesIO()
        Image.new("RGB", (500, 300), "blue").save(other, "JPEG")
        animation = BytesIO()
        Image.new("RGB", (30, 20), "red").save(animation, "GIF", save_all=True, append_images=[Image.new("RGB", (30, 20), "blue")], duration=100, loop=0)
        _Handler.bodies = {
            "/image.png": (image.getvalue(), "image/png"),
            "/other.jpg": (other.getvalue(), "image/jpeg"),
            "/animation.gif": (animation.getvalue(), "image/gif"),
            "/slow.html": (b'<html><head><title>Slow page</title></head><body><img src="/other.jpg"></body></html>', "text/html"),
            "/multiple.html": (b'<html><head><title>Several images</title></head><body><img src="/image.png"><img src="/other.jpg"></body></html>', "text/html"),
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="unidown-desktop-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start(); self.addCleanup(environment.stop)
        _Handler.slow = False
        _Handler.ranges = []
        _Handler.rejected = set()
        self.service = DesktopService(self.root / "downloads")
        self.addCleanup(self.service.shutdown)

    def wait(self, predicate, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.service.get_state()
            if predicate(state):
                return state
            time.sleep(0.03)
        self.fail(f"Desktop operation timed out: {self.service.get_state()}")

    def inspect(self, path="/image.png"):
        self.service.check_url(self.base + path)
        state = self.wait(lambda state: state["inspection"]["status"] in ("ready", "failed"))
        self.assertEqual(state["inspection"]["status"], "ready", state)
        return state["inspection"]

    def enqueue_inspected(self, inspection, asset_index=0, kind="image", output_format="original"):
        return self.service.enqueue(inspection["id"], inspection["assets"][asset_index]["id"], kind, output_format)

    def restart(self):
        self.service.shutdown()
        self.service = DesktopService(self.root / "downloads")
        self.addCleanup(self.service.shutdown)
        return self.service.get_state()

    def test_theme_is_validated_and_survives_cli_preference_saves(self):
        from universal_downloader.preferences import save_preferences
        self.assertEqual(self.service.get_state()["theme"], "system")
        for theme in ("blue", None, ["dark"]):
            with self.assertRaises(DownloadError):
                self.service.set_theme(theme)
        self.assertEqual(self.service.set_theme("light")["theme"], "light")
        save_preferences({"output": str(self.root / "elsewhere"), "max_height": None, "max_size": None})
        restarted = DesktopService(self.root / "downloads")
        self.addCleanup(restarted.shutdown)
        self.assertEqual(restarted.get_state()["theme"], "light")
        (self.root / "state" / "appearance.json").write_text('{"theme": "neon"}')
        again = DesktopService(self.root / "downloads")
        self.addCleanup(again.shutdown)
        self.assertEqual(again.get_state()["theme"], "system")

    def test_checked_original_requires_explicit_enqueue_and_returns_private_raster_preview(self):
        inspection = self.inspect()
        state = self.service.get_state()
        self.assertEqual(state["tasks"], [])
        self.assertFalse((self.root / "downloads").exists())
        asset = inspection["assets"][0]
        self.assertTrue(asset["thumbnail"].startswith("data:image/png;base64,"))
        self.assertNotIn("information", json.dumps(state))
        self.assertNotIn("cookies", json.dumps(state))
        options = asset["kinds"][0]["options"]
        self.assertEqual(options[0]["id"], "original")
        self.assertTrue(options[0]["recommended"])
        self.assertNotIn("png", {option["id"] for option in options})
        self.enqueue_inspected(inspection)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        path = self.service.completed_path(state["tasks"][0]["id"])
        self.assertEqual(path.read_bytes(), _Handler.bodies["/image.png"][0])
        self.assertEqual(state["tasks"][0]["progress"], 100)
        self.assertEqual(state["tasks"][0]["action"], "download")
        self.assertTrue(state["tasks"][0]["can_convert"])

    def test_original_animation_offers_frame_preserving_conversion_shortcut(self):
        inspection = self.inspect("/animation.gif")
        self.enqueue_inspected(inspection)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertTrue(state["tasks"][0]["can_convert"])

    def test_expanded_image_download_converts_actual_bytes_and_retries_after_restart(self):
        inspection = self.inspect()
        options = inspection["assets"][0]["kinds"][0]["options"]
        self.assertIn("webp", {option["id"] for option in options})
        with self.service._lock:
            with self.assertRaises(DownloadError):
                self.enqueue_inspected(inspection, output_format="mp3")
            self.assertEqual(self.service.get_state()["tasks"], [])
            self.enqueue_inspected(inspection, output_format="webp")
            self.enqueue_inspected(inspection, output_format="webp")
            self.assertEqual(len(self.service.get_state()["tasks"]), 1)
            self.service.cancel_task(self.service.get_state()["tasks"][0]["id"])
        state = self.restart()
        task = state["tasks"][0]
        self.assertEqual(task["format"], "webp")
        self.assertEqual(task["status"], "cancelled")
        self.assertIsNone(self.service._tasks[0]["_inspected"])
        self.service.retry_task(task["id"])
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        path = Path(state["tasks"][0]["path"])
        self.assertEqual(path.suffix, ".webp")
        with Image.open(path) as converted:
            self.assertEqual(converted.format, "WEBP")
            self.assertEqual(converted.size, (320, 200))
        self.assertEqual(list(path.parent.glob("*.png")), [])

    def test_animation_download_offers_only_frame_preserving_targets(self):
        inspection = self.inspect("/animation.gif")
        options = inspection["assets"][0]["kinds"][0]["options"]
        targets = {option["id"] for option in options}
        self.assertIn("apng", targets)
        self.assertIn("webp", targets)
        self.assertFalse(targets & {"png", "jpg", "bmp", "tiff", "ico", "tga", "jp2", "mp3"})
        self.enqueue_inspected(inspection, output_format="apng")
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        with Image.open(state["tasks"][0]["path"]) as converted:
            self.assertEqual(converted.format, "PNG")
            self.assertEqual(converted.n_frames, 2)
            self.assertEqual(converted.info["loop"], 0)

    def test_completed_expanded_image_retry_reuses_cache_after_inspection_bytes_are_released(self):
        inspection = self.inspect()
        self.enqueue_inspected(inspection, output_format="webp")
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        first = state["tasks"][0]
        self.assertIsNone(self.service._tasks[0]["_inspected"].image_data)
        requests = list(_Handler.ranges)
        self.service.retry_task(first["id"])
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("existing", "completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "existing", state)
        self.assertEqual(state["tasks"][0]["path"], first["path"])
        self.assertEqual(_Handler.ranges, requests)

    @unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required")
    def test_download_audio_and_video_expanded_outputs_have_real_selected_containers(self):
        tools = find_ffmpeg()
        suffix = ".exe" if (tools / "ffmpeg.exe").is_file() else ""
        fixture = self.root / "expanded.mp4"
        subprocess.run([str(tools / f"ffmpeg{suffix}"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                        "-f", "lavfi", "-i", "color=c=blue:s=32x24:r=10:d=0.3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(fixture)],
                       check=True, capture_output=True, timeout=30, **hidden_process_options())
        _Handler.bodies["/expanded.mp4"] = (fixture.read_bytes(), "video/mp4")
        inspection = self.inspect("/expanded.mp4")
        kinds = {kind["id"]: kind for kind in inspection["assets"][0]["kinds"]}
        audio = {option["id"]: option for option in kinds["audio"]["options"]}
        video = {option["id"]: option for option in kinds["video"]["options"]}
        self.assertTrue({"mp3", "wav", "flac", "m4a", "aac", "opus", "ogg", "aiff"}.issubset(audio))
        self.assertTrue({"mkv", "mov", "webm"}.issubset(video))
        self.assertFalse(set(audio) & {"png", "gif", "mkv"})
        self.assertTrue({"gif", "webp", "apng"}.issubset(video))
        self.assertFalse(set(video) & {"flac", "png"})
        self.assertEqual(audio["flac"]["label"], "FLAC audio")
        self.assertEqual(video["mkv"]["label"], "MKV video")
        with self.service._lock:
            self.enqueue_inspected(inspection, kind="audio", output_format="flac")
            self.enqueue_inspected(inspection, kind="video", output_format="mkv")
        state = self.wait(lambda state: len(state["tasks"]) == 2 and all(task["status"] in ("completed", "failed") for task in state["tasks"]))
        self.assertTrue(all(task["status"] == "completed" for task in state["tasks"]), state)
        for task in state["tasks"]:
            path = Path(task["path"])
            self.assertEqual(path.suffix, f'.{task["format"]}')
            probe = subprocess.run([str(tools / f"ffprobe{suffix}"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                   check=True, capture_output=True, text=True, timeout=30, **hidden_process_options())
            result = json.loads(probe.stdout)
            if task["format"] == "flac":
                self.assertEqual(result["format"]["format_name"], "flac")
                self.assertEqual([stream["codec_type"] for stream in result["streams"]], ["audio"])
            else:
                self.assertIn("matroska", result["format"]["format_name"])
                self.assertEqual({stream["codec_type"] for stream in result["streams"]}, {"audio", "video"})

    @unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required")
    def test_checked_height_limit_omits_unavailable_original_and_resizes_mkv(self):
        tools = find_ffmpeg()
        suffix = ".exe" if (tools / "ffmpeg.exe").is_file() else ""
        fixture = self.root / "height-limited.mp4"
        subprocess.run([str(tools / f"ffmpeg{suffix}"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=32x24:rate=10:duration=0.3", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1",
                        "-c:a", "aac", str(fixture)], check=True, capture_output=True, timeout=30, **hidden_process_options())
        _Handler.bodies["/height-limited.mp4"] = (fixture.read_bytes(), "video/mp4")
        self.service.save_settings({"max_height": 13})
        inspection = self.inspect("/height-limited.mp4")
        video = next(kind for kind in inspection["assets"][0]["kinds"] if kind["id"] == "video")
        options = {option["id"] for option in video["options"]}
        self.assertNotIn("original", options)
        self.assertTrue({"mp4", "mkv", "mov", "webm"}.issubset(options), options)
        self.enqueue_inspected(inspection, kind="video", output_format="mkv")
        state = self.wait(lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
        task = state["tasks"][-1]
        self.assertEqual(task["status"], "completed", task)
        metadata = json.loads(subprocess.run([str(tools / f"ffprobe{suffix}"), "-v", "error", "-show_streams", "-of", "json", task["path"]],
                                             check=True, capture_output=True, text=True, timeout=30, **hidden_process_options()).stdout)
        video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (16, 12))
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))

    def test_local_conversions_share_the_download_queue_and_independent_inspection(self):
        source = self.root / "local.png"
        source.write_bytes(_Handler.bodies["/image.png"][0])
        self.service.add_conversion_files([source])
        self.service.check_url(self.base + "/slow.html")
        self.service.check_url(self.base + "/image.png")
        state = self.wait(lambda state: state["conversion"]["status"] == "ready" and state["inspection"]["status"] == "ready")
        self.assertEqual(state["inspection"]["url"], self.base + "/image.png")
        with self.service._lock:
            self.service.enqueue_links(self.base + "/image.png")
            self.service.enqueue_conversions([state["conversion"]["files"][0]["id"]], "jpg", "#FFFFFF")
        state = self.wait(lambda state: len(state["tasks"]) == 2 and all(task["status"] in ("failed", "completed") for task in state["tasks"]))
        self.assertEqual([task["status"] for task in state["tasks"]], ["completed", "completed"], state)
        self.assertEqual([task["action"] for task in state["tasks"]], ["download", "convert"])
        self.assertTrue(all(task["can_convert"] for task in state["tasks"]))

    def test_checked_video_background_metadata_controls_queue_deduplication(self):
        tools = find_ffmpeg()
        if tools is None:
            self.skipTest("FFmpeg and ffprobe are not installed")
        ffmpeg = tools / ("ffmpeg.exe" if (tools / "ffmpeg.exe").is_file() else "ffmpeg")
        source = self.root / "transparent.mkv"
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=red@0.5:size=32x16:rate=2:duration=1,format=rgba", "-c:v", "ffv1", "-threads", "1", str(source)],
                       check=True, capture_output=True, timeout=20, **hidden_process_options())
        self.service.add_conversion_files([source])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        file = state["conversion"]["files"][0]
        self.assertTrue(file["transparent"], file)
        self.assertTrue({"mkv", "mov", "webm"}.issubset(file["background_targets"]), file)
        with self.service._lock:
            for target in ("mkv", "mov", "webm"):
                for background in ("#FFFFFF", "#000000", "#FFFFFF"):
                    self.service.enqueue_conversions([file["id"]], target, background)
            self.assertEqual(len(self.service._tasks), 6)
            self.assertEqual([task["_identity"][4] for task in self.service._tasks], ["#FFFFFF", "#000000"] * 3)
            for task in self.service._tasks:
                self.service.cancel_task(task["id"])

    def test_bulk_failure_continues_and_duplicate_urls_are_deduplicated(self):
        self.service.enqueue_links(f"# URLs\n{self.base}/missing.png\n{self.base}/image.png {self.base}/image.png\n{self.base}/other.jpg")
        state = self.wait(lambda state: len(state["tasks"]) == 3 and all(task["status"] in ("failed", "completed") for task in state["tasks"]))
        self.assertEqual([task["status"] for task in state["tasks"]], ["failed", "completed", "completed"])
        self.assertIn("404", state["tasks"][0]["error"])
        self.assertIsNone(state["tasks"][0]["path"])
        self.assertIsNone(state["tasks"][0]["progress"])

    def test_bulk_ambiguous_page_fails_with_actionable_selection_then_next_completes(self):
        self.service.enqueue_links(f"{self.base}/multiple.html\n{self.base}/image.png")
        state = self.wait(lambda state: len(state["tasks"]) == 2 and all(task["status"] in ("failed", "completed") for task in state["tasks"]))
        self.assertEqual(state["tasks"][0]["status"], "failed", state)
        self.assertIn("Use Check", state["tasks"][0]["error"])
        self.assertEqual(state["tasks"][1]["status"], "completed")

    def test_explicit_page_asset_selection_downloads_correct_asset(self):
        inspection = self.inspect("/multiple.html")
        self.assertEqual(len(inspection["assets"]), 2)
        self.enqueue_inspected(inspection, 1)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("failed", "completed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertEqual(Path(state["tasks"][0]["path"]).read_bytes(), _Handler.bodies["/other.jpg"][0])

    def test_new_check_cannot_be_overwritten_by_stale_inspector(self):
        self.service.check_url(self.base + "/slow.html")
        time.sleep(0.10)
        self.service.check_url(self.base + "/image.png")
        self.wait(lambda state: state["inspection"]["status"] == "ready")
        time.sleep(1.10)
        self.assertEqual(self.service.get_state()["inspection"]["url"], self.base + "/image.png")

    def test_invalid_url_preserves_existing_inspection_and_cancelling_rejects_old_ids(self):
        inspection = self.inspect()
        with self.assertRaises(DownloadError):
            self.service.check_url("javascript:alert(1)")
        self.assertEqual(self.service.get_state()["inspection"]["id"], inspection["id"])
        self.service.cancel_check()
        with self.assertRaisesRegex(DownloadError, "out of date"):
            self.enqueue_inspected(inspection)

    def test_limit_changes_invalidate_inspection_and_do_not_block_still_images(self):
        inspection = self.inspect()
        self.service.save_settings({"max_height": 100, "max_size": 1_048_576})
        with self.assertRaisesRegex(DownloadError, "out of date"):
            self.enqueue_inspected(inspection)
        inspection = self.inspect()
        self.enqueue_inspected(inspection)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("failed", "completed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        saved = json.loads((self.root / "state" / "preferences.json").read_text())
        self.assertEqual(set(saved), {"version", "output", "max_height", "max_size"})
        self.assertNotIn("http", json.dumps(saved))

    def test_invalid_settings_are_rejected_without_mutation(self):
        before = self.service.get_state()["settings"]
        for settings in ({"output": "relative"}, {"max_size": True}, {"max_height": -1}, {"unknown": 1}):
            with self.subTest(settings=settings), self.assertRaises(DownloadError):
                self.service.save_settings(settings)
            self.assertEqual(self.service.get_state()["settings"], before)

    def test_output_change_preserves_ready_inspection_and_uses_new_folder(self):
        inspection = self.inspect()
        output = self.root / "chosen-folder"
        state = self.service.save_settings({"output": str(output)})
        self.assertEqual(state["inspection"]["id"], inspection["id"])
        self.assertEqual(state["inspection"]["status"], "ready")
        self.enqueue_inspected(inspection)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertEqual(Path(state["tasks"][0]["path"]).parent, output)

    def test_pending_duplicate_is_prevented_and_retry_completed_uses_cache(self):
        inspection = self.inspect()
        with self.service._lock:
            self.enqueue_inspected(inspection)
            state = self.enqueue_inspected(inspection)
            self.assertEqual(len(state["tasks"]), 1)
            state = self.service.enqueue_links(self.base + "/image.png")
            self.assertEqual(len(state["tasks"]), 1)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        with self.service._lock:
            self.assertIsNone(self.service._tasks[0]["_inspected"].image_data)
        self.service.retry_task(state["tasks"][0]["id"])
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("existing", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "existing", state)

    def test_image_queue_memory_limit_rejects_addition_without_losing_existing_tasks(self):
        first = MediaInspection(self.base + "/image.png", {"title": "First"}, ("image",), image_data=b"first-data")
        second = MediaInspection(self.base + "/other.jpg", {"title": "Second"}, ("image",), image_data=b"second-data")
        with self.service._lock, patch("universal_downloader.desktop_service.MAX_QUEUED_IMAGE_BYTES", 15):
            self.service._add_task(first.url, first, "image")
            with self.assertRaisesRegex(DownloadError, "too much image data"):
                self.service._add_task(second.url, second, "image")
            self.assertEqual(len(self.service.get_state()["tasks"]), 1)
            self.service.cancel_task(self.service.get_state()["tasks"][0]["id"])
            self.service._add_task(second.url, second, "image")
            self.assertEqual(len(self.service.get_state()["tasks"]), 2)

    def test_invalid_batch_validates_every_entry_before_any_download(self):
        with self.assertRaises(DownloadError):
            self.service.enqueue_links(f"{self.base}/image.png\nfile:///etc/passwd")
        self.assertEqual(self.service.get_state()["tasks"], [])
        self.assertFalse((self.root / "downloads").exists())

    def test_cancel_queued_retry_and_remove_finished_are_isolated(self):
        with self.service._lock:
            self.service.enqueue_links(f"{self.base}/slow.html\n{self.base}/image.png")
            identifier = self.service.get_state()["tasks"][1]["id"]
            self.service.cancel_task(identifier)
        self.service.retry_task(identifier)
        state = self.wait(lambda state: all(task["status"] in ("completed", "failed") for task in state["tasks"]))
        self.assertEqual(state["tasks"][1]["status"], "completed", state)
        self.service.remove_finished()
        self.assertEqual(self.service.get_state()["tasks"], [])

    def test_completed_paths_cannot_be_opened_before_success_and_shutdown_is_idempotent(self):
        with self.service._lock:
            self.service.enqueue_links(self.base + "/image.png")
            task = self.service.get_state()["tasks"][0]
            with self.assertRaises(DownloadError):
                self.service.completed_path(task["id"])
        self.service.shutdown()
        self.service.shutdown()
        with self.assertRaises(DownloadError):
            self.service.enqueue_links(self.base + "/image.png")

    def test_restart_restores_completed_history_and_remove_finished_is_durable(self):
        self.service.enqueue_links(self.base + "/image.png")
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        identifier = state["tasks"][0]["id"]
        output = Path(state["tasks"][0]["path"])
        state = self.restart()
        self.assertEqual(state["tasks"][0]["id"], identifier)
        self.assertEqual(self.service.completed_path(identifier), output)
        self.assertEqual(state["tasks"][0]["status"], "completed")
        self.service.remove_finished()
        self.assertEqual(self.restart()["tasks"], [])

    def test_restart_never_auto_downloads_and_retries_exact_second_gallery_image(self):
        _Handler.bodies["/expiring.html"] = (b'<html><head><title>Gallery</title></head><body><img src="/image.png"><img src="/other.jpg?token=old"></body></html>', "text/html")
        inspection = self.inspect("/expiring.html")
        with self.service._lock:
            self.enqueue_inspected(inspection, 0)
            state = self.enqueue_inspected(inspection, 1)
            self.assertEqual(len(state["tasks"]), 2)
            self.assertTrue(all(task["url"] == self.base + "/expiring.html" for task in state["tasks"]))
            identifier = state["tasks"][1]["id"]
            self.service.shutdown()
        _Handler.bodies["/expiring.html"] = (b'<html><head><title>Gallery</title></head><body><img src="/image.png"><img src="/other.jpg?token=new"></body></html>', "text/html")
        _Handler.rejected = {"/other.jpg?token=old"}
        self.assertNotIn("token=old", self.service._queue_store.path.read_text(encoding="utf-8"))
        before = list(_Handler.ranges)
        state = self.restart()
        self.assertEqual(state["tasks"][0]["status"], "cancelled")
        self.assertEqual(state["tasks"][0]["error"], RESTORED_MESSAGE)
        self.assertFalse(self.service.active_jobs())
        time.sleep(0.15)
        self.assertEqual(_Handler.ranges, before)
        self.assertFalse((self.root / "downloads").exists())
        self.service.retry_task(identifier)
        state = self.wait(lambda state: state["tasks"][1]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "cancelled", state)
        self.assertEqual(state["tasks"][1]["status"], "completed", state)
        self.assertEqual(Path(state["tasks"][1]["path"]).read_bytes(), _Handler.bodies["/other.jpg"][0])

    def test_restored_local_conversion_revalidates_fingerprint_before_retry(self):
        source = self.root / "local.png"
        source.write_bytes(_Handler.bodies["/image.png"][0])
        self.service.add_conversion_files([source])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        with self.service._lock:
            self.service.enqueue_conversions([state["conversion"]["files"][0]["id"]], "jpg")
            identifier = self.service.get_state()["tasks"][0]["id"]
            self.service.shutdown()
        state = self.restart()
        self.assertEqual(state["tasks"][0]["status"], "cancelled")
        self.assertEqual(state["tasks"][0]["error"], RESTORED_MESSAGE)
        source.write_bytes(_Handler.bodies["/other.jpg"][0])
        self.service.retry_task(identifier)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "failed", state)
        self.assertIn("source file changed", state["tasks"][0]["error"])
        self.assertFalse(list((self.root / "downloads").glob("*.jpg")))

    def test_restored_local_conversion_can_retry_unchanged_source(self):
        source = self.root / "local.png"
        source.write_bytes(_Handler.bodies["/image.png"][0])
        self.service.add_conversion_files([source])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        with self.service._lock:
            self.service.enqueue_conversions([state["conversion"]["files"][0]["id"]], "jpg")
            identifier = self.service.get_state()["tasks"][0]["id"]
            self.service.shutdown()
        self.restart()
        self.service.retry_task(identifier)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        with Image.open(state["tasks"][0]["path"]) as converted:
            self.assertEqual(converted.format, "JPEG")
            self.assertEqual(converted.size, (320, 200))

    def test_secondary_instance_has_volatile_queue_and_cannot_remove_owner_history(self):
        self.service.enqueue_links(self.base + "/image.png")
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        other = DesktopService(self.root / "downloads")
        self.addCleanup(other.shutdown)
        self.assertIn("will not be saved", other.get_state()["notice"])
        self.assertEqual(other.get_state()["tasks"], [])
        other.remove_finished()
        other.shutdown()
        self.assertEqual(self.restart()["tasks"][0]["status"], "completed")

    def test_queue_scope_stays_anchored_when_output_preference_changes(self):
        original_state_path = self.service._queue_store.path
        self.service.save_settings({"output": str(self.root / "chosen-output")})
        self.service.enqueue_links(self.base + "/image.png")
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        state = self.restart()
        self.assertEqual(self.service._queue_store.path, original_state_path)
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(Path(state["tasks"][0]["path"]).parent, self.root / "chosen-output")

    def test_save_failure_is_informational_and_does_not_block_queue(self):
        with patch.object(self.service._queue_store, "save", return_value=False):
            self.service._queue_store.notice = "The queue could not be saved. Current tasks can continue."
            self.service.enqueue_links(self.base + "/image.png")
            state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"))
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertIn("could not be saved", state["notice"])

    def test_conversion_progress_preserves_measurements_and_sanitizes_worker_fields(self):
        source = self.root / "local.png"
        source.write_bytes(_Handler.bodies["/image.png"][0])
        self.service.add_conversion_files([source])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        with self.service._lock:
            self.service.enqueue_conversions([state["conversion"]["files"][0]["id"]], "jpg")
            task = self.service._tasks[0]
            task.update(status="converting", _attempt="progress-test")
        observed = []
        service = self.service
        destination = self.root / "downloads" / "local.jpg"

        class Worker:
            def begin(self, payload):
                Image.new("RGB", (10, 10)).save(destination, "JPEG")

            def events(self):
                for payload in (
                    {"status": "preparing", "operation": "preparing"},
                    {"status": "converting", "operation": "encoding", "progress": 42.5, "eta": 7, "speed": 999},
                    {"status": "converting", "operation": "copying", "progress": 200, "eta": -3},
                    {"status": "converting", "operation": "unknown", "progress": float("nan"), "eta": True},
                ):
                    yield "progress", payload
                    observed.append(service.get_state()["tasks"][0])
                yield "result", {"path": str(destination), "already_exists": False, "title": "local.png", "can_convert": True}

            def close(self):
                pass

        with patch.object(self.service, "_create_worker", return_value=Worker()):
            self.service._run_task(task, "progress-test")
        self.assertEqual([(item["progress"], item["eta"], item["operation"]) for item in observed],
                         [(None, None, "preparing"), (42.5, 7, "encoding"), (99.9, None, "copying"), (None, None, "encoding")])
        self.assertTrue(all(item["speed"] is None for item in observed))
        self.assertEqual(self.service.get_state()["tasks"][0]["status"], "completed")

    def test_download_then_conversion_progress_keeps_percent_separate_from_transfer_bytes(self):
        inspection = self.inspect()
        with self.service._lock:
            self.enqueue_inspected(inspection, output_format="webp")
            task = self.service._tasks[0]
            task.update(status="downloading", _attempt="download-progress-test")
        observed = []
        service = self.service
        destination = self.root / "downloads" / "downloaded.webp"

        class Worker:
            def begin(self, payload):
                self.payload = payload
                destination.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (10, 10)).save(destination, "WEBP")

            def events(self):
                for payload in (
                    {"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100, "speed": 20, "eta": 4},
                    {"status": "finished"},
                    {"status": "preparing"},
                    {"status": "converting", "operation": "copying", "progress": 42.5, "eta": 2, "speed": 1.2},
                    {"status": "converting", "operation": "encoding", "progress": float("inf"), "eta": True},
                ):
                    yield "progress", payload
                    observed.append(service.get_state()["tasks"][0])
                yield "result", {"path": str(destination), "already_exists": False, "title": "downloaded", "can_convert": True}

            def close(self):
                pass

        worker = Worker()
        with patch.object(self.service, "_create_worker", return_value=worker):
            self.service._run_task(task, "download-progress-test")
        self.assertEqual(worker.payload["action"], "download")
        self.assertEqual(worker.payload["format"], "webp")
        self.assertEqual([(item["status"], item["progress"], item["eta"], item["operation"]) for item in observed],
                         [("downloading", 25, 4, None), ("finalizing", None, None, None),
                          ("converting", None, None, "preparing"), ("converting", 42.5, 2, "copying"),
                          ("converting", None, None, "encoding")])
        self.assertEqual(observed[0]["speed"], 20)
        self.assertTrue(all(item["speed"] is None for item in observed[1:]))
        self.assertTrue(all(item["downloaded_bytes"] == 25 for item in observed))
        self.assertEqual(self.service.get_state()["tasks"][0]["status"], "completed")

    def test_thumbnail_is_bounded_png_and_never_a_remote_url(self):
        data = BytesIO()
        Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3)).save(data, "PNG")
        inspected = MediaInspection("https://example.com/picture", {"thumbnail": "https://example.com/evil.svg"}, ("image",), image_data=data.getvalue())
        thumbnail = _thumbnail(inspected)
        self.assertTrue(thumbnail.startswith("data:image/png;base64,"))
        self.assertLess(len(thumbnail), 140_000)
        inspected = MediaInspection("https://example.com/video", {"thumbnail": "https://example.com/evil.svg"}, ("video",))
        self.assertIsNone(_thumbnail(inspected))

    def test_user_facing_errors_describe_desktop_controls_and_hide_technical_context(self):
        conversion = safe_error(DownloadError("Could not convert this media to MP4. Use --verbose for details."))
        self.assertEqual(conversion, "Could not convert this media to MP4.")
        limit = safe_error(DownloadError("The source file exceeds --max-size. Increase the limit."))
        self.assertIn("maximum file size in Settings", limit)
        self.assertNotIn("--max-size", limit)
        selection = safe_error(DownloadError("This page contains several media items. Select one in the menu or with --item NUMBER."))
        self.assertIn("Use Check and choose the item", selection)
        self.assertNotIn("--item", selection)
        expected = safe_error(DownloadError("Unavailable: https://user:secret@example.com/file?token=private"))
        unexpected = safe_error(RuntimeError("Authorization: secret https://example.com/private"))
        for message in (expected, unexpected):
            self.assertNotIn("http", message)
            self.assertNotIn("secret", message)
            self.assertNotIn("private", message)

    @unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required")
    def test_cancel_real_video_then_retry_resumes_bytes_and_preserves_original(self):
        tools = find_ffmpeg()
        ffmpeg = tools / ("ffmpeg.exe" if (tools / "ffmpeg.exe").exists() else "ffmpeg")
        fixture = self.root / "fixture.mp4"
        subprocess.run([str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(fixture)], check=True, capture_output=True, timeout=30)
        padding = 8 * 1024 * 1024
        body = fixture.read_bytes() + struct.pack(">I4s", padding, b"free") + b"\0" * (padding - 8)
        _Handler.bodies["/video.mp4"] = (body, "video/mp4")
        inspection = self.inspect("/video.mp4")
        _Handler.slow = True
        self.enqueue_inspected(inspection, kind="video")
        state = self.wait(lambda state: state["tasks"][0]["status"] == "downloading" and state["tasks"][0]["downloaded_bytes"] >= 131_072)
        identifier = state["tasks"][0]["id"]
        self.service.cancel_task(identifier)
        state = self.service.get_state()
        self.assertEqual(state["tasks"][0]["status"], "cancelled", state)
        self.assertIsNone(state["tasks"][0]["path"])
        self.assertGreater(state["resume_count"], 0)
        _Handler.ranges = []
        self.service.retry_task(identifier)
        state = self.wait(lambda state: state["tasks"][0]["status"] in ("completed", "failed"), timeout=45)
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertEqual(Path(state["tasks"][0]["path"]).read_bytes(), body)
        self.assertTrue(any(path == "/video.mp4" and value and int(value.split("=")[1].split("-")[0]) > 0 for path, value in _Handler.ranges), _Handler.ranges)


if __name__ == "__main__":
    unittest.main()
