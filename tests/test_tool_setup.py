"""Exercise setup retries and user-facing failure reasons without publisher traffic."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

from universal_downloader import tool_setup


class ToolSetupTests(unittest.TestCase):
    def test_transient_http_failure_retries_then_installs_verified_bytes(self):
        archive = BytesIO()
        with ZipFile(archive, "w") as package:
            package.writestr("bin/deno.exe", b"verified executable")
        data = archive.getvalue()
        statuses = [503, 200]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                status = statuses.pop(0)
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)) if status == 200 else "0")
                self.end_headers()
                if status == 200:
                    self.wfile.write(data)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary, patch.object(tool_setup, "ROOT", Path(temporary)), patch.object(tool_setup.time, "sleep"):
                updates = []
                tool_setup._prepare_asset("Deno", {"url": f"http://127.0.0.1:{server.server_port}/deno.zip", "sha256": hashlib.sha256(data).hexdigest(), "destination": ".tools/deno", "binaries": {"deno.exe"}}, updates.append)
                self.assertEqual((Path(temporary) / ".tools/deno/deno.exe").read_bytes(), b"verified executable")
                self.assertEqual(statuses, [])
                self.assertTrue(any("(2/3)" in update["message"] for update in updates))
                self.assertTrue(any(update["message"] == "Installing Deno…" for update in updates))
        finally:
            server.shutdown()
            server.server_close()

    def test_permanent_http_failure_is_not_retried_or_replaced_by_connection_guess(self):
        failure = HTTPError("https://publisher.example/private?token=secret", 404, "missing", {}, None)
        with patch.object(tool_setup, "install_asset", side_effect=failure) as install, patch.object(tool_setup.time, "sleep") as sleep:
            with self.assertRaises(tool_setup.ToolSetupError) as raised:
                tool_setup._prepare_asset("Deno", tool_setup.TOOLS["deno"])
        self.assertEqual(install.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("HTTP 404", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_server_failure_has_bounded_retries(self):
        with patch.object(tool_setup, "install_asset", side_effect=HTTPError("https://publisher.example", 503, "unavailable", {}, None)) as install, patch.object(tool_setup.time, "sleep"):
            with self.assertRaisesRegex(tool_setup.ToolSetupError, "HTTP 503"):
                tool_setup._prepare_asset("FFmpeg", tool_setup.TOOLS["ffmpeg"])
        self.assertEqual(install.call_count, 3)

    def test_permission_and_integrity_failures_have_distinct_actionable_messages(self):
        for error, expected in ((PermissionError("C:/private/folder"), "writable folder"), (RuntimeError("SHA-256 does not match"), "integrity check")):
            with self.subTest(error=type(error).__name__), patch.object(tool_setup, "install_asset", side_effect=error) as install:
                with self.assertRaisesRegex(tool_setup.ToolSetupError, expected) as raised:
                    tool_setup._prepare_asset("FFmpeg", tool_setup.TOOLS["ffmpeg"])
                self.assertEqual(install.call_count, 1)
                self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
