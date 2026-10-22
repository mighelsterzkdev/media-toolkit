"""Actual encoder regressions for display geometry, timestamps and codec profiles."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.local_media import convert_local_file, inspect_local_files


class MediaFidelityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mt-media-fidelity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        directory = find_ffmpeg()
        if directory is None:
            self.skipTest("FFmpeg and ffprobe are not installed")
        suffix = ".exe" if (directory / "ffmpeg.exe").is_file() else ""
        self.ffmpeg, self.ffprobe = directory / f"ffmpeg{suffix}", directory / f"ffprobe{suffix}"

    def encode(self, *arguments):
        return subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *map(str, arguments)],
                              capture_output=True, check=True, timeout=30, **hidden_process_options()).stdout

    def probe(self, source):
        result = subprocess.run([str(self.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
                                capture_output=True, text=True, check=True, timeout=15, **hidden_process_options())
        return json.loads(result.stdout)

    def checked(self, source, **options):
        result = inspect_local_files([source], **options)[0]
        self.assertIsNone(result["error"], result)
        return result

    def clip(self, name, codec, *options):
        source = self.root / name
        self.encode("-f", "lavfi", "-i", "testsrc=size=40x20:rate=10:duration=0.3",
                    "-c:v", codec, "-threads", "1", "-an", *options, source)
        return source

    def animation(self, durations):
        source = self.root / "variable.apng"
        frames = [Image.new("RGBA", (20, 12), color) for color in ("red", "blue", "green", "yellow")[:len(durations)]]
        try:
            frames[0].save(source, "PNG", save_all=True, append_images=frames[1:], duration=durations, loop=0)
        finally:
            for frame in frames:
                frame.close()
        return source

    def delays(self, source):
        with Image.open(source) as image:
            result = []
            for index in range(image.n_frames):
                image.seek(index)
                image.load()
                result.append(image.info["duration"])
            return result

    def test_each_native_video_container_offers_its_compatibility_profile_when_needed(self):
        for native, codec, options, expected in (
            ("mp4", "mpeg4", (), "h264"), ("mkv", "ffv1", (), "h264"),
            ("mov", "mpeg4", (), "h264"), ("webm", "libvpx", ("-pix_fmt", "yuv420p"), "vp9"),
        ):
            with self.subTest(native=native):
                source = self.clip(f"native.{native}", codec, *options)
                checked = self.checked(source)
                original = sha256(source.read_bytes()).hexdigest()
                self.assertIn(native, checked["targets"])
                self.assertNotIn("without re-encoding", checked["target_notes"][native])
                output = convert_local_file(checked, native, self.output)
                self.assertEqual(self.probe(output)["streams"][0]["codec_name"], expected)
                self.assertNotIn(native, self.checked(output)["targets"])
                self.assertEqual(sha256(source.read_bytes()).hexdigest(), original)

    def test_native_alac_m4a_can_be_converted_to_aac_without_a_container_detour(self):
        source = self.root / "lossless.m4a"
        self.encode("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.2", "-c:a", "alac", source)
        checked = self.checked(source)
        self.assertIn("m4a", checked["targets"])
        converted = convert_local_file(checked, "m4a", self.output)
        stream = self.probe(converted)["streams"][0]
        self.assertEqual((stream["codec_name"], stream["profile"]), ("aac", "LC"))
        self.assertNotIn("m4a", self.checked(converted)["targets"])

    def test_pcm_extraction_preserves_existing_bit_depth_and_sample_payload(self):
        source = self.root / "pcm.mkv"
        self.encode("-f", "lavfi", "-i", "color=c=red:size=20x12:rate=10:duration=0.3",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.3",
                    "-c:v", "ffv1", "-c:a", "pcm_s16le", "-threads", "1", source)
        checked = self.checked(source)
        self.assertIn("existing 16-bit PCM", checked["target_notes"]["wav"])
        converted = convert_local_file(checked, "wav", self.output)
        self.assertEqual(self.probe(converted)["streams"][0]["codec_name"], "pcm_s16le")
        original_pcm = self.encode("-i", source, "-map", "0:a:0", "-f", "s16le", "pipe:1")
        converted_pcm = self.encode("-i", converted, "-map", "0:a:0", "-f", "s16le", "pipe:1")
        self.assertEqual(converted_pcm, original_pcm)

    def test_shared_maximum_height_is_enforced_for_all_video_containers(self):
        source = self.clip("source.mp4", "libx264", "-pix_fmt", "yuv420p")
        checked = self.checked(source, max_height=13)
        self.assertTrue({"mp4", "mkv", "mov", "webm"}.issubset(checked["targets"]))
        for target in ("mp4", "mkv", "mov", "webm"):
            with self.subTest(target=target):
                output = convert_local_file(checked, target, self.output, max_height=13)
                stream = self.probe(output)["streams"][0]
                self.assertLessEqual(stream["height"], 13)
                self.assertEqual(stream["height"] % 2, 0)
                self.assertEqual(stream["sample_aspect_ratio"], "1:1")
        tiny = self.checked(source, max_height=1)
        self.assertFalse({"mp4", "mkv", "mov", "webm"} & set(tiny["targets"]))
        self.assertTrue({"gif", "webp", "apng"}.issubset(tiny["targets"]))

    def test_encoded_rotation_and_anamorphic_pixels_are_normalized_only_once(self):
        source = self.clip("coded.mp4", "libx264", "-pix_fmt", "yuv444p", "-vf", "setsar=2")
        rotated = self.root / "rotated.mp4"
        help_text = subprocess.run([str(self.ffmpeg), "-hide_banner", "-h", "full"], capture_output=True,
                                   text=True, timeout=20, **hidden_process_options()).stdout
        if "-display_rotation" in help_text:
            self.encode("-display_rotation:v:0", "90", "-i", source, "-c", "copy", rotated)
        else:
            self.encode("-i", source, "-c", "copy", "-metadata:s:v:0", "rotate=90", rotated)
        checked = self.checked(rotated)
        self.assertEqual((checked["width"], checked["height"]), (20, 80))
        self.assertIn("mp4", checked["targets"])
        for target in ("mp4", "mov", "webm", "apng"):
            with self.subTest(target=target):
                output = convert_local_file(checked, target, self.output)
                actual = self.checked(output)
                self.assertEqual((actual["width"], actual["height"]), (20, 80))
                if target != "apng":
                    stream = self.probe(output)["streams"][0]
                    self.assertEqual((stream["width"], stream["height"]), (20, 80))
                    self.assertEqual(stream["sample_aspect_ratio"], "1:1")
                    self.assertFalse(any(item.get("rotation") for item in stream.get("side_data_list", [])))

    def test_older_tools_report_display_transform_requirements_before_conversion(self):
        source = self.clip("anamorphic.mp4", "libx264", "-pix_fmt", "yuv420p", "-vf", "setsar=2")
        plain = self.clip("plain.mp4", "libx264", "-pix_fmt", "yuv420p")
        with patch("universal_downloader.local_media._display_override_cached", return_value=False):
            checked = inspect_local_files([source], ffmpeg_location=self.ffmpeg.parent)[0]
            self.assertIn("FFmpeg 6 or newer", checked["error"])
            self.assertEqual(checked["targets"], [])
            self.assertIsNone(inspect_local_files([plain], ffmpeg_location=self.ffmpeg.parent)[0]["error"])

    def test_video_animation_roundtrip_retains_each_variable_frame_delay(self):
        for durations in ([100, 300], [40, 160, 80, 120]):
            with self.subTest(durations=durations):
                source = self.animation(durations)
                mp4 = convert_local_file(self.checked(source), "mp4", self.output)
                mkv = convert_local_file(self.checked(mp4), "mkv", self.output)
                for video in (mp4, mkv):
                    checked = self.checked(video)
                    for target in ("gif", "webp", "apng"):
                        with self.subTest(container=video.suffix, target=target):
                            converted = convert_local_file(checked, target, self.output)
                            actual = self.delays(converted)
                            self.assertEqual(len(actual), len(durations))
                            for received, expected in zip(actual, durations):
                                self.assertAlmostEqual(received, expected, places=5)
                source.unlink()


if __name__ == "__main__":
    unittest.main()
