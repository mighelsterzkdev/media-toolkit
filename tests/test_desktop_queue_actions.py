"""Queue review actions keep recoverable work and persist explicit retries."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from universal_downloader import gui
from universal_downloader.desktop_queue_store import RESTORED_MESSAGE
from universal_downloader.desktop_service import DesktopService
from universal_downloader.errors import DownloadError
from universal_downloader.local_conversion import inspect_local_files


class DesktopQueueActionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-queue-actions-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        # Inspect queue mutations deterministically without starting any media work.
        runner = patch.object(DesktopService, "_run_queue", return_value=None)
        runner.start()
        self.addCleanup(runner.stop)
        self.service = DesktopService(self.root / "downloads")
        self.addCleanup(lambda: self.service.shutdown())

    def download(self, name, status):
        with self.service._lock:
            task = self.service._add_task(f"https://example.com/{name}")
            task.update(status=status, error="Network unavailable" if status == "failed" else None)
            if status in ("completed", "existing"):
                output = self.root / "downloads" / f"{name}.mp4"
                output.parent.mkdir(exist_ok=True)
                output.write_bytes(b"completed media")
                task.update(path=str(output), can_convert=True, progress=100)
            return task

    def conversion(self, name, status):
        source = self.root / f"{name}.png"
        Image.new("RGB", (24, 16), "green").save(source)
        image = inspect_local_files([source])[0]
        self.assertIsNone(image["error"])
        self.service._conversion["status"] = "ready"
        self.service._local_images[name] = image
        self.service.enqueue_conversions([name], "jpg")
        task = self.service._tasks[-1]
        task.update(status=status, error="Conversion interrupted" if status == "failed" else None)
        return task

    def saved_records(self):
        return json.loads(self.service._queue_store.path.read_text(encoding="utf-8"))["tasks"]

    def restart(self):
        self.service.shutdown()
        self.service = DesktopService(self.root / "downloads")
        return self.service.get_state()["tasks"]

    def test_clear_removes_successes_only_and_keeps_failed_cancelled_and_active(self):
        completed = self.download("complete", "completed")
        existing = self.download("cached", "existing")
        failure = self.download("failure", "failed")
        cancelled = self.download("cancelled", "cancelled")
        active = self.download("active", "downloading")
        recoverable = deepcopy([failure, cancelled, active])

        state = self.service.remove_finished()

        self.assertEqual([task["id"] for task in state["tasks"]], [task["id"] for task in recoverable])
        self.assertEqual(self.service._tasks, recoverable)
        self.assertTrue(Path(completed["path"]).exists())
        self.assertTrue(Path(existing["path"]).exists())
        self.assertEqual([task["id"] for task in self.saved_records()], [task["id"] for task in recoverable])

    def test_clear_keeps_recoverable_errors_after_restart(self):
        self.download("success", "completed")
        failure = self.download("failure", "failed")
        cancelled = self.download("cancelled", "cancelled")
        expected = [(task["id"], task["status"], task["error"]) for task in (failure, cancelled)]
        self.service.remove_finished()

        restored = self.restart()

        self.assertEqual([(task["id"], task["status"], task["error"]) for task in restored], expected)

    def test_manual_removal_forgets_only_chosen_terminal_record_and_keeps_files(self):
        completed = self.download("complete", "completed")
        existing = self.download("cached", "existing")
        failed = self.download("failure", "failed")
        cancelled = self.download("cancelled", "cancelled")
        partial = self.root / "downloads" / "failure.mp4.part"
        partial.write_bytes(b"partial transfer")
        for task in (failed, cancelled, completed, existing):
            before_ids = {entry["id"] for entry in self.service.get_state()["tasks"]}
            state = self.service.remove_task(task["id"])
            self.assertEqual({entry["id"] for entry in state["tasks"]}, before_ids - {task["id"]})
            self.assertEqual({entry["id"] for entry in self.saved_records()}, before_ids - {task["id"]})
            self.assertEqual(partial.read_bytes(), b"partial transfer")
            self.assertEqual(Path(completed["path"]).read_bytes(), b"completed media")
            self.assertEqual(Path(existing["path"]).read_bytes(), b"completed media")
        self.assertEqual(self.restart(), [])

    def test_manual_removal_rejects_active_jobs_and_untrusted_identifiers(self):
        tasks = [self.download(status, status) for status in
                 ("queued", "checking", "downloading", "converting", "finalizing", "cancelling")]
        before = deepcopy(self.service._tasks)
        for task in tasks:
            with self.subTest(status=task["status"]), self.assertRaises(DownloadError):
                self.service.remove_task(task["id"])
        for value in (None, 7, False, [], {}, "", "x" * 101, "unknown-id", str(self.root)):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                self.service.remove_task(value)
        self.assertEqual(self.service._tasks, before)

    def test_retry_failed_resets_only_failures_and_persists_them_before_execution(self):
        failure = self.download("failure", "failed")
        failure.update(progress=37, downloaded_bytes=80, total_bytes=200, speed=40, eta=3, operation="encoding")
        converted = self.conversion("picture", "failed")
        unchanged = [self.download("cancelled", "cancelled"), self.download("success", "completed"),
                     self.download("cached", "existing"), self.download("active", "downloading")]
        before = deepcopy(unchanged)
        failed_ids = {failure["id"], converted["id"]}

        state = self.service.retry_failed()

        for task in self.service._tasks:
            if task["id"] in failed_ids:
                self.assertEqual(task["status"], "queued")
                self.assertEqual(task["downloaded_bytes"], 0)
                self.assertFalse(task["can_convert"])
                for field in ("progress", "total_bytes", "speed", "eta", "path", "error", "operation"):
                    self.assertIsNone(task[field], field)
        self.assertEqual(unchanged, before)
        self.assertEqual(sum(task["status"] == "queued" for task in state["tasks"]), 2)
        self.assertEqual({task["id"] for task in self.saved_records() if task["status"] == "queued"}, failed_ids)
        self.assertTrue(self.service._wake.is_set())

    def test_without_download_tools_bulk_retry_queues_only_local_conversions(self):
        failed_download = self.download("failure", "failed")
        failed_conversion = self.conversion("picture", "failed")
        cancelled_conversion = self.conversion("cancelled-picture", "cancelled")

        self.service.retry_failed(include_downloads=False)

        self.assertEqual(failed_download["status"], "failed")
        self.assertEqual(failed_conversion["status"], "queued")
        self.assertEqual(cancelled_conversion["status"], "cancelled")
        records = {task["id"]: task for task in self.saved_records()}
        self.assertEqual(records[failed_download["id"]]["status"], "failed")
        self.assertEqual(records[failed_conversion["id"]]["status"], "queued")

    def test_bulk_retry_preserves_failure_when_identical_work_is_already_active(self):
        failure = self.download("same-source", "failed")
        active = self.download("same-source", "queued")
        before = deepcopy(failure)

        self.service.retry_failed()

        self.assertEqual(failure, before)
        self.assertEqual(active["status"], "queued")
        self.assertEqual(sum(task["status"] == "queued" for task in self.service._tasks), 1)

    def test_multiple_failed_copies_retry_only_one_per_identity(self):
        first = self.download("same-source", "failed")
        second = self.download("same-source", "failed")

        self.service.retry_failed()
        self.service.retry_failed()

        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["error"], "Network unavailable")
        self.assertEqual(len(self.service._tasks), 2)

    def test_queued_bulk_retry_is_restored_stopped_and_waits_for_explicit_retry(self):
        task = self.download("failure", "failed")
        self.service.retry_failed()

        restored = self.restart()

        self.assertEqual(restored[0]["id"], task["id"])
        self.assertEqual(restored[0]["status"], "cancelled")
        self.assertEqual(restored[0]["error"], RESTORED_MESSAGE)
        self.assertFalse(self.service.active_jobs())

    def test_invalid_tool_gate_does_not_mutate_failures(self):
        failure = self.download("failure", "failed")
        before = deepcopy(failure)
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                self.service.retry_failed(value)
        self.assertEqual(failure, before)

    def test_closed_service_rejects_queue_mutations(self):
        self.download("failure", "failed")
        self.service.shutdown()
        for action in (self.service.retry_failed, self.service.remove_finished):
            with self.subTest(action=action.__name__), self.assertRaises(DownloadError):
                action()
        with self.assertRaises(DownloadError):
            self.service.remove_task(self.service._tasks[0]["id"])


class BulkRetryBridgeTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.get_state.return_value = {"tasks": [], "settings": {"output": "folder"}}
        self.service.retry_failed.return_value = self.service.get_state.return_value
        with patch.object(gui, "find_ffmpeg", return_value=Path("ffmpeg")), \
                patch.object(gui, "find_js_runtime", return_value=("deno", Path("deno"))):
            self.api = gui.DesktopApi(self.service)

    def test_bulk_retry_returns_response_envelope_and_derives_download_readiness(self):
        for status in ("ready", "missing", "installing", "failed"):
            with self.subTest(status=status):
                self.service.retry_failed.reset_mock()
                self.api._tools["status"] = status
                response = self.api.retry_failed()
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["state"]["tools"]["status"], status)
                self.service.retry_failed.assert_called_once_with(status == "ready")
                json.dumps(response)

    def test_bulk_retry_errors_use_the_same_safe_envelope_as_other_mutations(self):
        self.service.retry_failed.side_effect = DownloadError("The application is closing.")
        self.assertEqual(self.api.retry_failed(), {"ok": False, "error": "The application is closing."})
        self.service.retry_failed.side_effect = RuntimeError("credential=secret C:/private/file")
        response = self.api.retry_failed()
        self.assertFalse(response["ok"])
        self.assertNotIn("secret", response["error"])
        self.assertNotIn("private", response["error"])

    def test_removal_bridge_passes_only_the_recorded_id_and_reports_errors(self):
        self.service.remove_task.return_value = self.service.get_state.return_value
        self.assertTrue(self.api.remove_task("finished-id")["ok"])
        self.service.remove_task.assert_called_once_with("finished-id")
        self.service.remove_task.side_effect = DownloadError("This queue item no longer exists.")
        self.assertEqual(self.api.remove_task("unknown-id"), {"ok": False, "error": "This queue item no longer exists."})


if __name__ == "__main__":
    unittest.main()
