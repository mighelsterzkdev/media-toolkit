"""Verify the private worker protocol without exporting source metadata."""

import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from universal_downloader.desktop_worker import _worker_main
from universal_downloader.downloader import DownloadResult
from universal_downloader.inspection import MediaInspection


class Connection:
    def __init__(self, payload):
        self.payload, self.messages, self.closed = payload, [], False

    def send(self, message):
        self.messages.append(message)

    def recv(self):
        return self.payload

    def close(self):
        self.closed = True


class DesktopWorkerProtocolTests(unittest.TestCase):
    def test_download_uses_shared_converter_and_forwards_measured_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inspected = MediaInspection("https://example.com/audio", {"title": "Recording"}, ("audio",))
            connection = Connection({"action": "download", "inspected": inspected, "kind": "audio", "format": "flac",
                                     "settings": {"output": str(root), "max_height": None, "max_size": None}})

            def download(request, *, progress_hook, **kwargs):
                self.assertTrue(request.desktop_conversion)
                self.assertEqual(request.convert_to, "flac")
                progress_hook({"status": "downloading", "downloaded_bytes": 30, "total_bytes": 100})
                progress_hook({"status": "preparing", "progress": math.nan, "source": "private-path"})
                progress_hook({"status": "converting", "operation": "copying", "progress": 45, "eta": 3})
                progress_hook({"status": "converting", "operation": "copying", "progress": 100, "eta": math.inf})
                return DownloadResult("Recording", root / "Recording.flac")

            with patch("universal_downloader.desktop_worker.download", side_effect=download), patch("universal_downloader.desktop_worker._can_convert", return_value=False), patch("universal_downloader.desktop_worker.os.setsid", create=True), patch("universal_downloader.desktop_worker.time.monotonic", side_effect=range(1, 11)):
                _worker_main(connection)
            progress = [value for event, value in connection.messages if event == "progress"]
            self.assertEqual(progress[0]["status"], "downloading")
            self.assertEqual(progress[1]["operation"], "preparing")
            self.assertIsNone(progress[1]["progress"])
            self.assertEqual(progress[2]["progress"], 45)
            self.assertEqual(progress[2]["operation"], "copying")
            self.assertEqual(progress[-1]["progress"], 99.9)
            self.assertIsNone(progress[-1]["eta"])
            self.assertTrue(all("source" not in value for value in progress))
            self.assertEqual(connection.messages[-1][0], "result")
            self.assertTrue(connection.closed)

    def test_conversion_forwards_measured_progress_without_nonfinite_or_private_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            connection = Connection({"action": "convert", "image": {"name": "Clip.mp4"}, "format": "mkv",
                                     "settings": {"output": str(root)}, "background": "#FFFFFF", "work_dir": str(root)})

            def convert(*args, progress_hook, **kwargs):
                progress_hook({"status": "converting", "operation": "preparing", "progress": math.nan, "eta": math.inf})
                progress_hook({"status": "converting", "operation": "copying", "progress": 30, "eta": 5,
                               "source": "private-path", "speed": 2.0})
                progress_hook({"status": "converting", "operation": "copying", "progress": 100,
                               "eta": -1, "speed": math.nan})
                return root / "Clip.mkv"

            with patch("universal_downloader.local_media.convert_local_file", side_effect=convert), patch("universal_downloader.desktop_worker.os.setsid", create=True), patch("universal_downloader.desktop_worker.time.monotonic", side_effect=range(10)):
                _worker_main(connection)
            progress = [value for event, value in connection.messages if event == "progress"]
            self.assertEqual(progress[0]["operation"], "preparing")
            self.assertEqual(progress[1]["operation"], "preparing")
            measured = next(event for event in progress if event["progress"] == 30)
            self.assertEqual(measured["operation"], "copying")
            self.assertEqual(measured["eta"], 5)
            self.assertEqual(measured["speed"], 2.0)
            self.assertNotIn("source", measured)
            self.assertEqual(progress[-1]["progress"], 99.9)
            self.assertIsNone(progress[-1]["eta"])
            self.assertIsNone(progress[-1]["speed"])
            self.assertEqual(connection.messages[-1][0], "result")
            self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
