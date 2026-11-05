"""Real codec checks for per-task resize and bounded compression."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from universal_downloader.conversion_options import DEFAULT_OPTIONS, validate_options
from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.desktop_queue_store import restore_task, task_record
from universal_downloader.errors import DownloadError
from universal_downloader.local_media import convert_local_file, inspect_local_files


class ConversionOptionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-options-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "output"

    def checked(self, source):
        result = inspect_local_files([source])[0]
        self.assertIsNone(result["error"], result)
        return result

    def image(self, size=(640, 360), *, native="PNG"):
        source = self.root / ("source.jpg" if native == "JPEG" else "source.png")
        with Image.frombytes("RGB", size, bytes((i * 73 + (i // 257) * 31) % 256 for i in range(size[0] * size[1] * 3))) as image:
            image.save(source, native)
        return source

    def tools(self):
        location = find_ffmpeg()
        if location is None:
            self.skipTest("FFmpeg and ffprobe are not installed")
        suffix = ".exe" if (location / "ffmpeg.exe").exists() else ""
        return location / f"ffmpeg{suffix}", location / f"ffprobe{suffix}"

    def generate(self, name, arguments):
        ffmpeg, _ = self.tools()
        source = self.root / name
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *arguments, "-threads", "1", str(source)],
                       capture_output=True, check=True, timeout=30, **hidden_process_options())
        return source

    def probe(self, source):
        _, ffprobe = self.tools()
        result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
                                capture_output=True, text=True, check=True, timeout=15, **hidden_process_options())
        return json.loads(result.stdout)

    def test_image_same_format_resize_keeps_proportions_and_never_enlarges_or_changes_source(self):
        source = self.image((80, 40), native="JPEG")
        original = source.read_bytes()
        checked = self.checked(source)
        self.assertNotIn("jpg", checked["targets"])
        for width, expected in ((40, (40, 20)), (160, (80, 40))):
            output = convert_local_file(checked, "jpg", self.output, options={"max_width": width})
            with Image.open(output) as encoded:
                self.assertEqual(encoded.size, expected)
            self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(self.output.glob(".unidown-convert-*")), [])

    def test_image_target_size_publishes_only_a_complete_file_that_fits(self):
        source = self.image()
        original = source.read_bytes()
        output = convert_local_file(self.checked(source), "jpg", self.output,
                                    options={"max_width": 320, "quality": "best", "max_size": 16_384})
        self.assertLessEqual(output.stat().st_size, 16_384)
        with Image.open(output) as encoded:
            self.assertEqual(encoded.size, (320, 180))
            encoded.load()
        self.assertEqual(source.read_bytes(), original)

    def test_impossible_image_limit_has_bounded_attempts_and_publishes_nothing(self):
        source = self.image((512, 512))
        original = source.read_bytes()
        from universal_downloader.local_conversion import _encode_static_image
        with patch("universal_downloader.local_conversion._encode_static_image", wraps=_encode_static_image) as encoder:
            with self.assertRaisesRegex(DownloadError, "could not be reached"):
                convert_local_file(self.checked(source), "jpg", self.output, options={"max_size": 1024})
            self.assertLessEqual(encoder.call_count, 6)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(source.read_bytes(), original)

    def test_lossless_target_rejects_size_and_invalid_options_before_publication(self):
        checked = self.checked(self.image(native="JPEG"))
        for options in ({"max_size": 20_000}, {"max_width": True}, {"max_width": 1}, {"quality": []}, {"max_size": 0}, {"unknown": 1}):
            with self.subTest(options=options), self.assertRaises(DownloadError):
                convert_local_file(checked, "png", self.output, options=options)
        self.assertFalse(self.output.exists())
        for value in ([], {"max_height": float("nan")}, {"max_width": 16_385}, {"max_size": 1_073_741_825}):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                validate_options(value)

    def test_real_video_resize_and_size_limit_keep_full_duration_and_original(self):
        source = self.generate("source.mkv", ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1.5", "-c:v", "ffv1"])
        original = source.read_bytes()
        checked = self.checked(source)
        output = convert_local_file(checked, "mp4", self.output, options={"max_height": 90, "quality": "smaller", "max_size": 32_768})
        result = self.probe(output)
        stream = next(item for item in result["streams"] if item["codec_type"] == "video")
        self.assertEqual((stream["width"], stream["height"]), (160, 90))
        self.assertAlmostEqual(float(result["format"]["duration"]), 1.5, delta=.15)
        self.assertLessEqual(output.stat().st_size, 32_768)
        self.assertEqual(source.read_bytes(), original)
        # A compatible MP4 remains available for an explicit same-format resize.
        repeated = convert_local_file(self.checked(output), "mp4", self.output, options={"max_height": 720})
        stream = next(item for item in self.probe(repeated)["streams"] if item["codec_type"] == "video")
        self.assertEqual((stream["width"], stream["height"]), (160, 90))

    def test_real_lossy_audio_limit_and_impossible_budget_do_not_truncate(self):
        source = self.generate("source.wav", ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3", "-c:a", "pcm_s16le"])
        checked = self.checked(source)
        output = convert_local_file(checked, "opus", self.output, options={"max_size": 24_576})
        self.assertLessEqual(output.stat().st_size, 24_576)
        self.assertAlmostEqual(float(self.probe(output)["format"]["duration"]), 3, delta=.15)
        before = set(self.output.iterdir())
        with self.assertRaisesRegex(DownloadError, "too small"):
            convert_local_file(checked, "mp3", self.output, options={"max_size": 1024})
        self.assertEqual(set(self.output.iterdir()), before)

    def test_queue_options_round_trip_and_legacy_defaults_keep_independent_snapshots(self):
        checked = self.checked(self.image())
        options = {**DEFAULT_OPTIONS, "max_width": 320, "quality": "smaller", "max_size": 32_768}
        task = {"id": uuid4().hex, "url": "", "title": "Image", "action": "convert", "source_format": "png", "kind": "image", "format": "jpg",
                "status": "queued", "path": None, "error": None, "can_convert": False, "_image": checked, "_background": "#FFFFFF",
                "_settings": {"output": str(self.output), "max_height": None, "max_size": None}, "_conversion_options": options}
        record = task_record(task)
        restored = restore_task(json.loads(json.dumps(record)))
        self.assertEqual(restored["_conversion_options"], options)
        self.assertEqual(restored["_identity"][-1], options)
        self.assertEqual(restored["status"], "cancelled")
        legacy = deepcopy(record)
        legacy.pop("conversion_options")
        self.assertEqual(restore_task(legacy)["_conversion_options"], DEFAULT_OPTIONS)
        options["max_width"] = 160
        self.assertEqual(restored["_conversion_options"]["max_width"], 320)


if __name__ == "__main__":
    unittest.main()
