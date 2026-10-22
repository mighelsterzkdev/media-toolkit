"""Exercise release source downloads over HTTP without using a populated cache."""

from collections import Counter
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts import build_windows


class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-toolkit-build-sources-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.requests = Counter()
        self.responses = {}
        requests, responses = self.requests, self.responses

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests[self.path] += 1
                sequence = responses[self.path]
                status, content = sequence[min(requests[self.path] - 1, len(sequence) - 1)]
                self.send_response(status)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        worker.start()
        # Shutdown precedes server_close and temporary-directory cleanup.
        self.addCleanup(self.server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)

    def record(self, path="/source.tar.gz", content=b"verified upstream archive"):
        self.responses[path] = [(200, content)]
        return {"name": "test-source", "version": "1.0", "filename": path.rsplit("/", 1)[-1],
                "url": f"http://127.0.0.1:{self.server.server_port}{path}",
                "size": len(content), "sha256": sha256(content).hexdigest()}

    def test_uncached_source_is_verified_and_licenses_are_copied(self):
        record = self.record()
        notices = self.root / "third_party/pi-heif"
        notices.mkdir(parents=True)
        (notices / "SOURCES.json").write_text(json.dumps([record]), encoding="utf-8")
        (notices / "LICENSE.txt").write_text("Upstream source license\n", encoding="utf-8")
        destination = self.root / "bundle/licenses"
        with patch.object(build_windows, "ROOT", self.root):
            build_windows.write_heif_sources(destination)
            build_windows.write_heif_sources(destination)
        cached = self.root / ".tools/third-party-sources" / record["filename"]
        packaged = destination / "pi-heif/sources" / record["filename"]
        self.assertEqual(cached.read_bytes(), b"verified upstream archive")
        self.assertEqual(packaged.read_bytes(), cached.read_bytes())
        self.assertEqual((destination / "pi-heif/LICENSE.txt").read_text(encoding="utf-8"), "Upstream source license\n")
        self.assertEqual(self.requests["/source.tar.gz"], 1)
        self.assertFalse(list(cached.parent.glob("*.partial")))

    def test_transient_service_error_is_retried_then_publishes_only_verified_bytes(self):
        record = self.record()
        self.responses["/source.tar.gz"].insert(0, (503, b"temporarily unavailable"))
        cached = self.root / record["filename"]
        with patch.object(build_windows.time, "sleep") as delay:
            build_windows._download_source(record, cached)
        self.assertEqual(self.requests["/source.tar.gz"], 2)
        delay.assert_called_once_with(1)
        self.assertEqual(sha256(cached.read_bytes()).hexdigest(), record["sha256"])
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_permanent_http_error_is_reported_without_retry_or_partial_files(self):
        record = self.record()
        self.responses["/source.tar.gz"] = [(404, b"not found")]
        cached = self.root / record["filename"]
        with patch.object(build_windows.time, "sleep") as delay:
            with self.assertRaisesRegex(RuntimeError, r"source\.tar\.gz \(HTTP 404\)"):
                build_windows._download_source(record, cached)
        self.assertEqual(self.requests["/source.tar.gz"], 1)
        delay.assert_not_called()
        self.assertFalse(cached.exists())
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_repeated_transient_error_stops_after_three_attempts(self):
        record = self.record()
        self.responses["/source.tar.gz"] = [(503, b"unavailable")]
        cached = self.root / record["filename"]
        with patch.object(build_windows.time, "sleep") as delay:
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                build_windows._download_source(record, cached)
        self.assertEqual(self.requests["/source.tar.gz"], 3)
        self.assertEqual([call.args[0] for call in delay.call_args_list], [1, 2])
        self.assertFalse(cached.exists())
        self.assertFalse(list(self.root.glob("*.partial")))

    def test_short_oversized_or_corrupt_archives_are_never_published(self):
        for variant, content in (("short", b"short"), ("oversize", b"unexpected upstream content" * 10),
                                 ("corrupt", b"x" * len(b"verified upstream archive"))):
            with self.subTest(variant=variant):
                route = f"/{variant}.tar.gz"
                record = self.record(route)
                self.responses[route] = [(200, content)]
                cached = self.root / record["filename"]
                with patch.object(build_windows.time, "sleep") as delay:
                    with self.assertRaisesRegex(RuntimeError, "pinned size|size or SHA-256 check"):
                        build_windows._download_source(record, cached)
                self.assertEqual(self.requests[route], 1)
                delay.assert_not_called()
                self.assertFalse(cached.exists())
                self.assertFalse(list(self.root.glob("*.partial")))


if __name__ == "__main__":
    unittest.main()
