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

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.desktop_service import DesktopService, _thumbnail, safe_error
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection


class _Handler(BaseHTTPRequestHandler):
    bodies = {}
    ranges = []
    slow = False

    def log_message(self, *args): pass

    def do_HEAD(self): self._serve(True)
    def do_GET(self): self._serve(False)

    def _serve(self, head):
        path = self.path.split("?", 1)[0]
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
        _Handler.bodies = {
            "/image.png": (image.getvalue(), "image/png"),
            "/other.jpg": (other.getvalue(), "image/jpeg"),
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
