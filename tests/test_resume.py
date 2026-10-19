"""Exercise durable byte-range continuation through real HTTP and process exits."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.downloader import DownloadRequest, download
from universal_downloader.errors import DownloadError
from universal_downloader.identity import media_identity
from universal_downloader.inspection import MediaInspection
from universal_downloader.resume import ResumeWork, _response_state, clear_resume, pending_resumes


class _Logger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


class _RangeHandler(BaseHTTPRequestHandler):
    bodies = {}
    etags = {}
    ranges = []
    ignore_range = False
    head_supported = True
    validators = True
    redirect_to = "/video.mp4"
    get_redirect_to = None
    deny_probe = False

    def log_message(self, *args): pass

    def do_HEAD(self):
        self._send(head=True)

    def do_GET(self):
        self._send()

    def _send(self, head=False):
        path = self.path.split("?", 1)[0]
        if path == "/redirect.mp4":
            target = self.get_redirect_to if not head and self.get_redirect_to else self.redirect_to
            self.send_response(302); self.send_header("Location", target); self.end_headers(); return
        if self.deny_probe and (head or self.headers.get("Range") == "bytes=0-0"):
            self.send_response(403); self.end_headers(); return
        if head and not self.head_supported:
            self.send_response(405); self.end_headers(); return
        body = self.bodies[path]
        requested = self.headers.get("Range")
        if not head:
            self.ranges.append((path, requested))
        start, end = 0, len(body) - 1
        ranged = requested and not self.ignore_range
        if ranged and self.headers.get("If-Range") and self.headers["If-Range"] != self.etags[path]:
            ranged = False
        if ranged:
            bounds = requested.removeprefix("bytes=").split("-")
            start = int(bounds[0] or 0)
            end = min(int(bounds[1]), end) if bounds[1] else end
            if start >= len(body):
                self.send_response(416); self.end_headers(); return
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", "audio/mp4" if path.endswith("m4a") else "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        if self.validators:
            self.send_header("ETag", self.etags[path])
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        if not head:
            try:
                for offset in range(start, end + 1, 2048):
                    self.wfile.write(body[offset:min(offset + 2048, end + 1)])
                    self.wfile.flush()
                    time.sleep(0.001)
            except (ConnectionError, OSError):
                pass


@unittest.skipUnless(find_ffmpeg(), "FFmpeg/ffprobe required")
class ResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="unidown-resume-fixtures-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        tools = find_ffmpeg()
        ffmpeg = tools / ("ffmpeg.exe" if (tools / "ffmpeg.exe").is_file() else "ffmpeg")
        subprocess.run([str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=20", "-f", "lavfi", "-i", "sine=sample_rate=44100", "-t", "4", "-c:v", "mpeg4", "-q:v", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(folder / "video.mp4")], check=True, capture_output=True, timeout=30)
        subprocess.run([str(ffmpeg), "-v", "error", "-i", str(folder / "video.mp4"), "-c:v", "copy", "-an", str(folder / "silent.mp4")], check=True, capture_output=True, timeout=30)
        subprocess.run([str(ffmpeg), "-v", "error", "-i", str(folder / "video.mp4"), "-c:a", "copy", "-vn", str(folder / "audio.m4a")], check=True, capture_output=True, timeout=30)
        cls.original_bodies = {f"/{name}": (folder / name).read_bytes() for name in ("video.mp4", "silent.mp4", "audio.m4a")}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="unidown-resume-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "downloads"
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start(); self.addCleanup(environment.stop)
        _RangeHandler.bodies = dict(self.original_bodies)
        _RangeHandler.etags = {path: f'"original-{path}"' for path in self.original_bodies}
        _RangeHandler.ranges = []
        _RangeHandler.ignore_range = False
        _RangeHandler.head_supported = True
        _RangeHandler.validators = True
        _RangeHandler.redirect_to = "/video.mp4"
        _RangeHandler.get_redirect_to = None
        _RangeHandler.deny_probe = False

    def inspected(self, *, separate=False, token="first"):
        url = self.base + "/page"
        formats = [{"format_id": "v", "url": self.base + "/video.mp4?token=" + token, "ext": "mp4", "vcodec": "mpeg4", "acodec": "aac", "height": 240, "filesize": len(_RangeHandler.bodies["/video.mp4"])}]
        if separate:
            formats = [{"format_id": "a", "url": self.base + "/audio.m4a", "ext": "m4a", "vcodec": "none", "acodec": "aac", "filesize": len(_RangeHandler.bodies["/audio.m4a"])}, {"format_id": "v", "url": self.base + "/silent.mp4", "ext": "mp4", "vcodec": "mpeg4", "acodec": "none", "height": 240, "filesize": len(_RangeHandler.bodies["/silent.mp4"])}]
        return MediaInspection(url, {"id": "fixture", "title": "Resume fixture", "extractor_key": "ResumeFixture", "webpage_url": url, "formats": formats}, ("video", "audio"), (240,))

    def request(self, inspected, target=None, maximum=None):
        return DownloadRequest(inspected.url, "video", self.output, target, max_size=maximum)

    def interrupt(self, inspected, *, target=None, on_audio=False, maximum=None):
        def stop(event):
            if event.get("status") == "downloading" and event.get("downloaded_bytes", 0) >= 8192:
                if not on_audio or (event.get("info_dict") or {}).get("format_id") == "a":
                    raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            download(self.request(inspected, target, maximum), inspected=inspected, logger=_Logger(), progress_hook=stop)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_ctrl_c_then_new_request_resumes_range_and_preserves_original_bytes(self):
        inspected = self.inspected()
        self.interrupt(inspected)
        self.assertEqual(pending_resumes(self.output), 1)
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertTrue(any(value and int(value.split("=")[1].split("-")[0]) > 0 for _, value in _RangeHandler.ranges))
        self.assertEqual(pending_resumes(self.output), 0)

    def test_process_exit_releases_lock_and_retains_a_real_partial(self):
        inspected = self.inspected()
        code = """import json, os, sys\nfrom pathlib import Path\nfrom universal_downloader.downloader import DownloadRequest, download\nfrom universal_downloader.inspection import MediaInspection\ni=MediaInspection(sys.argv[1],json.loads(sys.argv[3]),('video','audio'))\ndef stop(e):\n if e.get('status')=='downloading' and e.get('downloaded_bytes',0)>=131072: os._exit(23)\ndownload(DownloadRequest(i.url,'video',Path(sys.argv[2])),inspected=i,progress_hook=stop)\n"""
        process = subprocess.run([sys.executable, "-B", "-c", code, inspected.url, str(self.output), json.dumps(inspected.information)], capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 23, process.stderr.decode(errors="replace"))
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertTrue(any(value and not value.startswith("bytes=0-") for _, value in _RangeHandler.ranges))

    def test_ignored_ranges_and_changed_resources_restart_without_corruption(self):
        for mode in ("ignored", "changed"):
            with self.subTest(mode=mode):
                inspected = self.inspected()
                self.interrupt(inspected)
                _RangeHandler.ranges.clear()
                if mode == "ignored":
                    _RangeHandler.ignore_range = True
                else:
                    _RangeHandler.bodies["/video.mp4"] += b"\x00\x00\x00\x08free"
                    _RangeHandler.etags["/video.mp4"] = '"changed"'
                    inspected = self.inspected()
                result = download(self.request(inspected), inspected=inspected, logger=_Logger())
                self.assertEqual(result.path.read_bytes(), _RangeHandler.bodies["/video.mp4"])
                if mode == "changed":
                    self.assertFalse(any(value and not value.startswith("bytes=0-") for _, value in _RangeHandler.ranges))
                result.path.unlink()
                _RangeHandler.ignore_range = False

    def test_missing_validators_restart_instead_of_appending(self):
        _RangeHandler.validators = False
        inspected = self.inspected()
        self.interrupt(inspected)
        self.assertEqual(pending_resumes(self.output), 0)
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertFalse(any(value for _, value in _RangeHandler.ranges))

    def test_head_not_supported_uses_bounded_range_probe(self):
        _RangeHandler.head_supported = False
        inspected = self.inspected()
        self.interrupt(inspected)
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertTrue(any(value and value not in ("bytes=0-0", "bytes=0-") for _, value in _RangeHandler.ranges))

    def test_denied_head_and_range_preflight_allow_normal_get_without_resume(self):
        _RangeHandler.deny_probe = True
        inspected = self.inspected()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertTrue(any(value is None for _, value in _RangeHandler.ranges))
        self.assertEqual(pending_resumes(self.output), 0)

    def test_negative_response_length_cannot_be_a_trusted_validator(self):
        for headers in ({"Content-Length": "-1", "ETag": '"fixture"'}, {"Content-Length": "1", "Content-Range": "bytes 0-0/-1", "ETag": '"fixture"'}):
            self.assertEqual(_response_state(headers), (None, None, None))

    def test_timed_out_resume_preflight_does_not_block_normal_get(self):
        inspected = self.inspected()
        with patch("universal_downloader.resume._probe_response", side_effect=TimeoutError("HEAD timed out")):
            result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])

    def test_renewed_or_different_source_url_restarts_even_with_matching_etag_and_size(self):
        inspected = self.inspected()
        self.interrupt(inspected)
        renewed = self.inspected(token="renewed-secret")
        _RangeHandler.ranges.clear()
        result = download(self.request(renewed), inspected=renewed, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertFalse(any(value for _, value in _RangeHandler.ranges))

    def test_modified_partial_checkpoint_is_discarded_instead_of_appended(self):
        inspected = self.inspected()
        self.interrupt(inspected)
        partial = next((self.root / "state" / "resume").glob("*/data/*.part"))
        with partial.open("r+b") as stream:
            stream.write(b"damaged bytes")
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertFalse(any(value for _, value in _RangeHandler.ranges))

    def test_changed_redirect_target_restarts_even_with_matching_validator_and_size(self):
        inspected = self.inspected()
        inspected.information["formats"][0]["url"] = self.base + "/redirect.mp4"
        self.interrupt(inspected)
        _RangeHandler.bodies["/alternative.mp4"] = self.original_bodies["/video.mp4"]
        _RangeHandler.etags["/alternative.mp4"] = _RangeHandler.etags["/video.mp4"]
        _RangeHandler.redirect_to = "/alternative.mp4"
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertFalse(any(value for _, value in _RangeHandler.ranges))

    def test_unstable_head_get_redirects_restart_partial_then_allow_fresh_full_get(self):
        inspected = self.inspected()
        inspected.information["formats"][0]["url"] = self.base + "/redirect.mp4"
        self.interrupt(inspected)
        _RangeHandler.bodies["/alternative.mp4"] = self.original_bodies["/video.mp4"] + b"\x00\x00\x00\x08free"
        _RangeHandler.etags["/alternative.mp4"] = '"new-get-resource"'
        _RangeHandler.get_redirect_to = "/alternative.mp4"
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), _RangeHandler.bodies["/alternative.mp4"])
        self.assertEqual(pending_resumes(self.output), 0)

    def test_uncheckpointed_tail_is_truncated_before_resume(self):
        inspected = self.inspected()
        self.interrupt(inspected)
        partial = next((self.root / "state" / "resume").glob("*/data/*.part"))
        checkpoint_size = partial.stat().st_size
        with partial.open("ab") as stream:
            stream.write(b"unverified tail after interruption")
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertTrue(any(value == f"bytes={checkpoint_size}-" for _, value in _RangeHandler.ranges))

    def test_unavailable_resume_storage_falls_back_to_private_output_work(self):
        inspected = self.inspected()
        forbidden = self.root / "not-a-directory"
        forbidden.write_bytes(b"user file")
        with patch("universal_downloader.resume._state_directory", return_value=forbidden):
            result = download(self.request(inspected), inspected=inspected, logger=_Logger())
        self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
        self.assertEqual(forbidden.read_bytes(), b"user file")
        self.assertEqual(list(self.output.iterdir()), [result.path])

    def test_non_dict_or_oversized_state_is_not_trusted(self):
        for malformed in (b"[]", b"x" * (128 * 1024 + 1), b"[" * 1500 + b"0" + b"]" * 1500):
            inspected = self.inspected()
            self.interrupt(inspected)
            state = next((self.root / "state" / "resume").glob("*/state.json"))
            state.write_bytes(malformed)
            _RangeHandler.ranges.clear()
            result = download(self.request(inspected), inspected=inspected, logger=_Logger())
            self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
            self.assertFalse(any(value for _, value in _RangeHandler.ranges))
            result.path.unlink()

    def test_relative_state_directory_is_resolved_before_resumable_download(self):
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            with patch.dict(os.environ, {"UNIDOWN_DATA_DIR": "relative-state"}):
                inspected = self.inspected()
                self.interrupt(inspected)
                self.assertEqual(pending_resumes(self.output), 1)
                _RangeHandler.ranges.clear()
                result = download(self.request(inspected), inspected=inspected, logger=_Logger())
                self.assertEqual(result.path.read_bytes(), self.original_bodies["/video.mp4"])
                self.assertTrue(any(value for _, value in _RangeHandler.ranges))
                self.assertEqual(pending_resumes(self.output), 0)
        finally:
            os.chdir(previous)

    def test_separate_streams_and_conversion_reuse_completed_work(self):
        inspected = self.inspected(separate=True)
        self.interrupt(inspected, target="mp4", on_audio=True)
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected, "mp4"), inspected=inspected, logger=_Logger())
        self.assertTrue(result.path.is_file())
        self.assertFalse(any(path == "/silent.mp4" for path, _ in _RangeHandler.ranges))
        self.assertTrue(any(path == "/audio.m4a" and value and not value.startswith("bytes=0-") for path, value in _RangeHandler.ranges))
        self.assertEqual(pending_resumes(self.output), 0)

    def test_canceling_conversion_reuses_fingerprinted_original_without_source_get(self):
        inspected = self.inspected()
        def stop(event):
            if event.get("status") == "converting": raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            download(self.request(inspected, "mp4"), inspected=inspected, logger=_Logger(), progress_hook=stop)
        _RangeHandler.ranges.clear()
        result = download(self.request(inspected, "mp4"), inspected=inspected, logger=_Logger())
        self.assertTrue(result.path.is_file())
        self.assertEqual(_RangeHandler.ranges, [])

    def test_work_metadata_contains_hashes_not_source_secrets_and_clear_skips_lock(self):
        inspected = self.inspected(token="private-token-value")
        self.interrupt(inspected)
        text = next((self.root / "state" / "resume").glob("*/state.json")).read_text()
        self.assertNotIn("private-token-value", text)
        self.assertNotIn("http://", text)
        identity = media_identity(self.request(inspected), inspected, "video", None)
        with ResumeWork(self.output, identity):
            self.assertEqual(clear_resume(self.output), 0)
            with self.assertRaisesRegex(DownloadError, "already running"):
                with ResumeWork(self.output, identity): pass
        # A normal context without completion/interrupt is definitive cleanup.
        self.assertEqual(pending_resumes(self.output), 0)

    def test_maximum_source_size_is_checked_again_before_appending_existing_bytes(self):
        inspected = self.inspected()
        maximum = len(self.original_bodies["/video.mp4"]) + 1
        self.interrupt(inspected, maximum=maximum)
        _RangeHandler.bodies["/video.mp4"] += b"more than the old maximum"
        _RangeHandler.etags["/video.mp4"] = '"grown"'
        inspected = self.inspected()
        with self.assertRaisesRegex(DownloadError, "max-size"):
            download(self.request(inspected, maximum=maximum), inspected=inspected, logger=_Logger())
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(pending_resumes(self.output), 0)


if __name__ == "__main__":
    unittest.main()
