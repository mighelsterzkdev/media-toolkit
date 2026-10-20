"""Check the bridge boundary and bundled-only HTTP host without a GUI runtime."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_downloader import gui
from universal_downloader.errors import DownloadError


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.get_state.return_value = {"tasks": [], "settings": {"output": "folder"}}
        with patch.object(gui, "find_ffmpeg", return_value=Path("ffmpeg")), patch.object(gui, "find_js_runtime", return_value=("deno", Path("deno"))):
            self.api = gui.DesktopApi(self.service)

    def test_all_public_methods_use_the_response_envelope(self):
        calls = (("get_state", ()), ("check_url", ("https://example.com",)), ("cancel_check", ()), ("enqueue", ("inspection", "asset", "video", "original")), ("enqueue_links", ("https://example.com",)), ("cancel_task", ("task",)), ("retry_task", ("task",)), ("remove_finished", ()), ("save_settings", ({"max_height": None},)))
        for method, args in calls:
            with self.subTest(method=method):
                getattr(self.service, method).return_value = self.service.get_state.return_value
                response = getattr(self.api, method)(*args)
                self.assertTrue(response["ok"])
                self.assertEqual(response["state"]["tools"]["status"], "ready")
                json.dumps(response)
                getattr(self.service, method).assert_called_with(*args)

    def test_unexpected_state_errors_do_not_leak_sensitive_exception_text(self):
        self.service.get_state.side_effect = RuntimeError("credential=secret C:/private/folder")
        response = self.api.get_state()
        self.assertFalse(response["ok"])
        self.assertNotIn("secret", response["error"])
        self.assertNotIn("private", response["error"])

    def test_expected_errors_are_bounded_and_control_cleaned(self):
        self.service.check_url.side_effect = DownloadError("Invalid\nlink\x00 " + "x" * 1000)
        response = self.api.check_url("not-a-link")
        self.assertFalse(response["ok"])
        self.assertLessEqual(len(response["error"]), 600)
        self.assertNotIn("\n", response["error"])
        self.assertNotIn("\x00", response["error"])

    def test_open_folder_accepts_recorded_task_ids_never_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed = Path(temporary) / "saved.mp4"
            completed.write_bytes(b"complete")
            self.service.completed_path.return_value = completed
            with patch.object(gui, "_open_directory") as opening:
                self.assertTrue(self.api.open_folder("task-id")["ok"])
                opening.assert_called_once_with(completed.parent)
                self.service.completed_path.assert_called_once_with("task-id")
                self.service.completed_path.side_effect = DownloadError("This queue item no longer exists.")
                self.assertFalse(self.api.open_folder("C:/Windows")["ok"])
                self.assertEqual(opening.call_count, 1)

    def test_folder_picker_cancellation_keeps_preferences(self):
        self.api._window = Mock()
        self.api._window.create_file_dialog.return_value = None
        self.api._webview = Mock()
        self.service.output_directory.return_value = Path.cwd()
        self.assertTrue(self.api.browse_folder()["ok"])
        self.service.save_settings.assert_not_called()

    def test_folder_picker_uses_only_native_selection(self):
        self.api._window = Mock()
        self.api._window.create_file_dialog.return_value = (str(Path.cwd()),)
        self.api._webview = Mock()
        self.service.output_directory.return_value = Path.cwd()
        self.assertTrue(self.api.browse_folder()["ok"])
        self.service.save_settings.assert_called_once_with({"output": str(Path.cwd().resolve())})

    def test_image_picker_uses_native_multiple_selection_and_cancellation_keeps_state(self):
        self.api._window = Mock()
        self.api._webview = Mock()
        self.service.output_directory.return_value = Path.cwd()
        selected = [str(Path.cwd() / "one.png"), str(Path.cwd() / "two.webp")]
        self.api._window.create_file_dialog.return_value = tuple(selected)
        response = self.api.select_conversion_files()
        self.assertTrue(response["ok"])
        self.service.add_conversion_files.assert_called_once_with(selected)
        options = self.api._window.create_file_dialog.call_args.kwargs
        self.assertTrue(options["allow_multiple"])
        self.assertIn("All files (*.*)", options["file_types"])
        self.api._window.create_file_dialog.return_value = None
        self.assertTrue(self.api.select_conversion_files()["ok"])
        self.service.add_conversion_files.assert_called_once()

    def test_conversion_shortcut_uses_only_a_recorded_completed_file(self):
        source = Path.cwd() / "completed.png"
        self.service.completed_path.return_value = source
        self.assertTrue(self.api.prepare_conversion("completed-id")["ok"])
        self.service.completed_path.assert_called_once_with("completed-id")
        self.service.clear_conversion_files.assert_called_once_with()
        self.service.add_conversion_files.assert_called_once_with([str(source)])
        self.service.completed_path.side_effect = DownloadError("This queue item no longer exists.")
        self.assertFalse(self.api.prepare_conversion("C:/private/image.png")["ok"])
        self.service.add_conversion_files.assert_called_once()

    def test_local_conversion_and_retry_do_not_require_download_tools(self):
        self.api._tools["status"] = "missing"
        self.assertTrue(self.api.enqueue_conversions(["file-id"], "jpg", "#000000")["ok"])
        self.service.enqueue_conversions.assert_called_once_with(["file-id"], "jpg", "#000000")
        self.service.get_state.return_value["tasks"] = [{"id": "conversion", "action": "convert"}]
        self.assertTrue(self.api.retry_task("conversion")["ok"])
        self.service.retry_task.assert_called_once_with("conversion")
        self.assertFalse(self.api.retry_task("download")["ok"])
        self.service.retry_task.assert_called_once()

    def test_conversion_selection_actions_use_the_response_envelope(self):
        self.assertTrue(self.api.remove_conversion_file("file-id")["ok"])
        self.service.remove_conversion_file.assert_called_once_with("file-id")
        self.assertTrue(self.api.clear_conversion_files()["ok"])
        self.service.clear_conversion_files.assert_called_once_with()

    def test_retry_state_failure_is_sanitized(self):
        self.service.get_state.side_effect = RuntimeError("private source path and credential=secret")
        response = self.api.retry_task("task")
        self.assertFalse(response["ok"])
        self.assertNotIn("secret", response["error"])

    def test_tool_setup_is_asynchronous_and_duplicate_requests_do_not_spawn_installers(self):
        entered, finish = threading.Event(), threading.Event()
        def install(**kwargs):
            entered.set()
            finish.wait(3)
            return 0
        with self.api._tools_lock:
            self.api._tools["status"] = "missing"
        with patch("universal_downloader.tool_setup.prepare_tools", side_effect=install) as installer, patch.object(gui, "find_ffmpeg", return_value=Path("ffmpeg")), patch.object(gui, "find_js_runtime", return_value=("deno", Path("deno"))):
            try:
                self.assertTrue(self.api.install_tools()["ok"])
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.api.get_state()["state"]["tools"]["status"], "installing")
                self.api.install_tools()
                self.assertEqual(installer.call_count, 1)
            finally:
                finish.set()
                self.api._installer.join(3)
        self.assertEqual(self.api.get_state()["state"]["tools"]["status"], "ready")
        self.service.refresh_conversion_files.assert_called_once_with()

    def test_tool_failure_preserves_specific_reason_and_clears_old_progress(self):
        from universal_downloader.tool_setup import ToolSetupError
        self.api._tools["status"] = "missing"
        def install(**kwargs):
            kwargs["progress"]({"message": "Downloading Deno…", "downloaded_bytes": 8, "total_bytes": 16})
            raise ToolSetupError("The publisher could not provide Deno (HTTP 503). Retry installation later.")
        with patch("universal_downloader.tool_setup.prepare_tools", side_effect=install):
            self.api.install_tools()
            self.api._installer.join(3)
        tools = self.api.get_state()["state"]["tools"]
        self.assertEqual(tools["status"], "failed")
        self.assertIn("HTTP 503", tools["error"])
        self.assertIsNone(tools["message"])

    def test_partial_setup_refreshes_converter_when_ffmpeg_is_ready_but_deno_failed(self):
        from universal_downloader.tool_setup import ToolSetupError
        self.api._tools.update(status="missing", ffmpeg=False, javascript=False)
        with patch("universal_downloader.tool_setup.prepare_tools", side_effect=ToolSetupError("Deno download failed.")), patch.object(gui, "find_ffmpeg", return_value=Path("ffmpeg")), patch.object(gui, "find_js_runtime", return_value=None):
            self.api.install_tools()
            self.api._installer.join(3)
        tools = self.api.get_state()["state"]["tools"]
        self.assertEqual(tools["status"], "failed")
        self.assertTrue(tools["ffmpeg"])
        self.assertFalse(tools["javascript"])
        self.service.refresh_conversion_files.assert_called_once_with()

    def test_resume_cleanup_uses_only_current_output_scope(self):
        self.service.output_directory.return_value = Path.cwd()
        with patch.object(gui, "discard_resumes", return_value=2) as cleanup:
            result = self.api.clear_resume()
        cleanup.assert_called_once_with(Path.cwd())
        self.assertTrue(result["ok"])
        self.assertIn("2 inactive", result["state"]["notice"])

    def test_media_jobs_cannot_start_while_tools_are_missing_or_being_installed(self):
        for status in ("missing", "installing", "failed"):
            with self.api._tools_lock:
                self.api._tools["status"] = status
            for method, args in (("check_url", ("https://example.com",)), ("enqueue", ("inspection", "asset", "video", "original")), ("enqueue_links", ("https://example.com",)), ("retry_task", ("task",))):
                with self.subTest(status=status, method=method):
                    response = getattr(self.api, method)(*args)
                    self.assertFalse(response["ok"])
                    self.assertIn("Install tools", response["error"])
                    getattr(self.service, method).assert_not_called()


class StaticHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.assets = Path(self.temporary.name) / "assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text('<script src="./app.js"></script>', encoding="utf-8")
        (self.assets / "app.js").write_text('document.title="Downloader"', encoding="utf-8")
        (Path(self.temporary.name) / "private.txt").write_text("private", encoding="utf-8")
        self.server = gui.AssetServer(self.assets)

    def tearDown(self):
        self.server.close()
        self.temporary.cleanup()

    def test_only_tokenized_bundled_assets_are_served_with_csp(self):
        with urlopen(self.server.url) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Security-Policy"], gui.CSP)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with urlopen(self.server.url.replace("index.html", "app.js")) as response:
            self.assertEqual(response.headers["Content-Type"], "text/javascript")

    def test_missing_token_path_traversal_and_queries_are_refused(self):
        origin = self.server.url.split("/", 3)[:3]
        for url in ("/".join(origin) + "/index.html", self.server.url.replace("index.html", "../private.txt"), self.server.url.replace("index.html", "%2e%2e/private.txt"), self.server.url + "?file=private.txt"):
            with self.subTest(url=url), self.assertRaises(HTTPError) as failure:
                urlopen(url)
            self.assertEqual(failure.exception.code, 404)

    def test_foreign_host_header_is_refused_without_cors(self):
        with self.assertRaises(HTTPError) as failure:
            urlopen(Request(self.server.url, headers={"Host": "attacker.example"}))
        self.assertEqual(failure.exception.code, 403)
        self.assertIsNone(failure.exception.headers.get("Access-Control-Allow-Origin"))

    def test_navigation_guard_allows_only_the_exact_local_document(self):
        self.assertTrue(gui._allowed_navigation(self.server.url, self.server.url))
        self.assertTrue(gui._allowed_navigation(self.server.url + "#queue", self.server.url))
        for target in ("https://example.com", "file:///C:/private.txt", self.server.url + "?x=1", self.server.url.replace("index.html", "app.js"), "javascript:alert(1)"):
            self.assertFalse(gui._allowed_navigation(target, self.server.url))


if __name__ == "__main__":
    unittest.main()
