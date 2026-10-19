"""Exercise the desktop local-image workflow through its real process workers."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from universal_downloader.desktop_service import DesktopService
from universal_downloader.errors import DownloadError


class DesktopConversionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-toolkit-desktop-convert-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        self.output = self.root / "output"
        self.first = self.root / "transparent.png"
        self.second = self.root / "photo.jpg"
        Image.new("RGBA", (64, 32), (255, 0, 0, 0)).save(self.first, "PNG")
        Image.new("RGB", (80, 40), "blue").save(self.second, "JPEG")
        self.original = self.first.read_bytes()
        self.service = DesktopService(self.output)
        self.addCleanup(self.service.shutdown)

    def wait(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.service.get_state()
            if predicate(state):
                return state
            time.sleep(0.02)
        self.fail(f"Desktop operation timed out: {self.service.get_state()}")

    def select(self, *paths):
        self.service.add_conversion_files(list(paths or (self.first,)))
        state = self.wait(lambda state: state["conversion"]["status"] in ("ready", "failed"))
        self.assertEqual(state["conversion"]["status"], "ready", state)
        return state["conversion"]["files"]

    def complete(self, count=1):
        return self.wait(lambda state: len(state["tasks"]) == count and all(task["status"] in ("completed", "failed", "cancelled") for task in state["tasks"]))

    def test_selection_is_private_content_checked_and_requires_explicit_conversion(self):
        disguised = self.root / "really-an-image.txt"
        disguised.write_bytes(self.original)
        files = self.select(disguised)
        selected = files[0]
        self.assertEqual(selected["format"], "png")
        self.assertEqual((selected["width"], selected["height"]), (64, 32))
        self.assertTrue(selected["transparent"])
        self.assertNotIn("png", selected["targets"])
        self.assertIn("jpg", selected["targets"])
        public = json.dumps(self.service.get_state())
        self.assertNotIn(str(disguised), public)
        self.assertNotIn("signature", public)
        self.assertNotIn("sha256", public)
        self.assertEqual(self.service.get_state()["tasks"], [])
        self.assertFalse(self.output.exists())

    def test_appending_and_deduplicating_keeps_ids_and_other_files(self):
        first = self.select()[0]
        self.service.add_conversion_files([self.first, self.second, self.first])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        files = state["conversion"]["files"]
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["id"], first["id"])
        self.assertEqual([image["format"] for image in files], ["png", "jpg"])
        self.service.add_conversion_files([self.second])
        refreshed = self.wait(lambda state: state["conversion"]["status"] == "ready")
        self.assertEqual(refreshed["conversion"]["files"], files)
        self.assertNotEqual(refreshed["conversion"]["id"], state["conversion"]["id"])

    def test_reselecting_changed_file_refreshes_it_without_changing_old_queued_snapshot(self):
        original = self.select()[0]
        original_signature = self.service._local_images[original["id"]]["signature"].copy()
        with self.service._lock:
            self.service.enqueue_conversions([original["id"]], "jpg", "#FFFFFF")
            Image.new("RGB", (32, 64), "green").save(self.first, "PNG")
            self.service.add_conversion_files([self.first])
        state = self.wait(lambda state: state["conversion"]["status"] == "ready")
        files = state["conversion"]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["id"], original["id"])
        self.assertEqual((files[0]["width"], files[0]["height"]), (32, 64))
        self.assertFalse(files[0]["transparent"])
        self.assertNotEqual(self.service._local_images[original["id"]]["signature"], original_signature)
        self.assertEqual(self.service._tasks[0]["_image"]["signature"], original_signature)
        self.service.enqueue_conversions([original["id"]], "jpg", "#FFFFFF")
        state = self.complete(2)
        self.assertEqual([task["status"] for task in state["tasks"]], ["failed", "completed"], state)
        self.assertIn("changed", state["tasks"][0]["error"])
        with Image.open(state["tasks"][1]["path"]) as converted:
            self.assertEqual(converted.size, (32, 64))

    def test_bad_file_does_not_hide_valid_files_or_allow_its_enqueue(self):
        invalid = self.root / "broken.png"
        invalid.write_bytes(b"not an image")
        files = self.select(self.first, invalid, self.root / "missing.jpg")
        self.assertIsNone(files[0]["error"])
        self.assertTrue(files[1]["error"])
        self.assertTrue(files[2]["error"])
        with self.assertRaises(DownloadError):
            self.service.enqueue_conversions([image["id"] for image in files], "jpg", "#FFFFFF")
        self.assertEqual(self.service.get_state()["tasks"], [])
        self.assertFalse(self.output.exists())

    def test_every_batch_argument_and_capacity_is_validated_before_mutation(self):
        files = self.select(self.first, self.second)
        first, second = [image["id"] for image in files]
        invalid = (([first, second], "jpg", "#FFFFFF"), ([first, "stale-id"], "webp", "#FFFFFF"),
                   ([first, first], "jpg", "#FFFFFF"), ([], "jpg", "#FFFFFF"),
                   ([first], "gif", "#FFFFFF"), ([first], "jpg", "red"), ("first", "jpg", "#FFFFFF"),
                   ([True], "jpg", "#FFFFFF"))
        for identifiers, target, background in invalid:
            with self.subTest(identifiers=identifiers, target=target, background=background), self.assertRaises(DownloadError):
                self.service.enqueue_conversions(identifiers, target, background)
            self.assertEqual(self.service.get_state()["tasks"], [])
        with patch("universal_downloader.desktop_service.MAX_QUEUE_URLS", 1), self.assertRaises(DownloadError):
            self.service.enqueue_conversions([first, second], "webp", "#FFFFFF")
        self.assertEqual(self.service.get_state()["tasks"], [])

    def test_selection_capacity_and_invalid_paths_are_atomic(self):
        files = self.select()
        before = self.service.get_state()["conversion"]
        for paths in ([], "image.png", ["relative.png"], [True], [str(self.first) + "\n"], [self.first] * 101):
            with self.subTest(paths=paths), self.assertRaises(DownloadError):
                self.service.add_conversion_files(paths)
            self.assertEqual(self.service.get_state()["conversion"], before)
        with patch("universal_downloader.desktop_service.MAX_CONVERSION_FILES", 1), self.assertRaises(DownloadError):
            self.service.add_conversion_files([self.second])
        self.assertEqual(self.service.get_state()["conversion"]["files"], files)

    def test_local_conversion_works_without_media_tools_and_never_changes_source(self):
        files = self.select()
        with patch("universal_downloader.desktop_service.find_ffmpeg", return_value=None), patch("universal_downloader.desktop_service.find_js_runtime", return_value=None):
            self.assertEqual(self.service.tool_status(), {"ffmpeg": False, "javascript": False})
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#000000")
            state = self.complete()
        task = state["tasks"][0]
        self.assertEqual(task["action"], "convert")
        self.assertEqual(task["status"], "completed", state)
        self.assertEqual(task["source_format"], "png")
        self.assertTrue(task["can_convert"])
        self.assertEqual(task["url"], "")
        self.assertEqual(task["progress"], 100)
        self.assertNotIn("signature", json.dumps(task))
        path = self.service.completed_path(task["id"])
        self.assertEqual(path.parent, self.output)
        with Image.open(path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(self.first.read_bytes(), self.original)
        self.assertEqual(list(self.output.glob(".unidown-convert-*")), [])

    def test_subset_enqueue_active_dedup_and_retry_publish_unique_files(self):
        files = self.select(self.first, self.second)
        with self.service._lock:
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")
            self.assertEqual(len(self.service.get_state()["tasks"]), 1)
        state = self.complete()
        first_path = Path(state["tasks"][0]["path"])
        self.service.retry_task(state["tasks"][0]["id"])
        state = self.complete()
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertNotEqual(Path(state["tasks"][0]["path"]), first_path)
        self.assertTrue(first_path.is_file())
        self.assertTrue(Path(state["tasks"][0]["path"]).is_file())

    def test_irrelevant_background_and_limits_do_not_bypass_active_dedup(self):
        files = self.select(self.second)
        with self.service._lock:
            self.service.enqueue_conversions([files[0]["id"]], "png", "#FFFFFF")
            self.service.save_settings({"max_height": 144, "max_size": 1})
            self.service.enqueue_conversions([files[0]["id"]], "png", "#000000")
            self.assertEqual(len(self.service.get_state()["tasks"]), 1)
        state = self.complete()
        self.assertEqual(state["tasks"][0]["status"], "completed", state)

    def test_remove_and_clear_selection_preserve_sources_and_queued_snapshots(self):
        files = self.select(self.first, self.second)
        self.service.remove_conversion_file(files[1]["id"])
        self.wait(lambda state: state["conversion"]["status"] == "ready")
        with self.service._lock:
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")
            self.service.clear_conversion_files()
        self.assertEqual(self.service.get_state()["conversion"], {"status": "idle", "id": None, "files": [], "error": None})
        state = self.complete()
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertTrue(self.first.is_file())
        self.assertTrue(self.second.is_file())
        with self.assertRaises(DownloadError):
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")

    def test_changed_source_fails_its_task_and_next_valid_conversion_continues(self):
        files = self.select(self.first, self.second)
        Image.new("RGBA", (64, 32), "green").save(self.first, "PNG")
        self.service.enqueue_conversions([image["id"] for image in files], "webp", "#FFFFFF")
        state = self.complete(2)
        self.assertEqual([task["status"] for task in state["tasks"]], ["failed", "completed"], state)
        self.assertIn("changed", state["tasks"][0]["error"])
        self.assertIsNone(state["tasks"][0]["path"])
        self.assertEqual(list(self.output.glob(".unidown-convert-*")), [])

    def test_queued_conversion_keeps_its_destination_snapshot(self):
        files = self.select()
        with self.service._lock:
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")
            self.service.save_settings({"output": str(self.root / "later")})
        state = self.complete()
        self.assertEqual(state["tasks"][0]["status"], "completed", state)
        self.assertEqual(Path(state["tasks"][0]["path"]).parent, self.output)
        self.assertFalse((self.root / "later").exists())

    def test_cancelling_conversion_cleans_private_work_before_retry(self):
        files = self.select()
        started = threading.Event()

        class BlockedConversion:
            def __init__(self):
                self.closed = threading.Event()

            def begin(self, payload):
                self.payload = payload
                (Path(payload["work_dir"]) / "output.jpg").write_bytes(b"partial encoder output")

            def events(self):
                yield "progress", {"status": "converting"}
                started.set()
                self.closed.wait(10)

            def close(self):
                self.closed.set()

        with patch("universal_downloader.desktop_service.ProcessWorker", BlockedConversion):
            self.service.enqueue_conversions([files[0]["id"]], "jpg", "#FFFFFF")
            self.assertTrue(started.wait(10))
            task = self.service.get_state()["tasks"][0]
            self.assertEqual(task["status"], "converting")
            self.assertIsNone(task["progress"])
            self.service.cancel_task(task["id"])
            self.assertEqual(self.service.get_state()["tasks"][0]["status"], "cancelled")
            deadline = time.monotonic() + 10
            while list(self.output.glob(".unidown-convert-*")) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(list(self.output.iterdir()), [])
        self.service.retry_task(task["id"])
        state = self.complete()
        self.assertEqual(state["tasks"][0]["status"], "completed", state)

    def test_clear_while_inspecting_cannot_restore_stale_private_or_public_files(self):
        started, release = threading.Event(), threading.Event()

        class DelayedInspector:
            def begin(self, payload):
                self.payload = payload

            def events(self):
                started.set()
                release.wait(5)
                from universal_downloader.local_conversion import inspect_local_files
                yield "local_inspection", inspect_local_files(self.payload["paths"])

            def close(self):
                release.set()

        with patch("universal_downloader.desktop_service.ProcessWorker", DelayedInspector):
            self.service.add_conversion_files([self.first])
            self.assertTrue(started.wait(5))
            self.service.clear_conversion_files()
            deadline = time.monotonic() + 5
            while self.service._workers and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(self.service.get_state()["conversion"]["status"], "idle")
        self.assertEqual(self.service.get_state()["conversion"]["files"], [])
        self.assertEqual(self.service._local_images, {})
        self.assertEqual(self.service._local_sources, {})


if __name__ == "__main__":
    unittest.main()
