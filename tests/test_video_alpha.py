"""Decode real alpha video and verify the app's transparency/background policy."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.errors import DownloadError
from universal_downloader.local_media import convert_local_file, inspect_local_files
from universal_downloader.media_alpha import decoder_options, has_alpha


class VideoAlphaTests(unittest.TestCase):
    def setUp(self):
        directory = find_ffmpeg()
        if directory is None:
            self.skipTest("FFmpeg is required for alpha video integration tests")
        suffix = ".exe" if (directory / "ffmpeg.exe").is_file() else ""
        self.ffmpeg = directory / f"ffmpeg{suffix}"
        self.ffprobe = directory / f"ffprobe{suffix}"
        temporary = tempfile.TemporaryDirectory(prefix="media-toolkit-video-alpha-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for index, color in enumerate(((240, 0, 0), (0, 0, 240))):
            with Image.new("RGBA", (48, 24), (*color, 255)) as frame:
                for x in range(32):
                    for y in range(24):
                        frame.putpixel((x, y), (*color, 0 if x < 16 else 128))
                frame.save(self.root / f"frame-{index}.png")

    def run_ffmpeg(self, arguments):
        result = subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *map(str, arguments)],
                                capture_output=True, timeout=30, **hidden_process_options())
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def source(self, codec, extension):
        source = self.root / f"source-{codec}.{extension}"
        options = ["-framerate", "5", "-i", self.root / "frame-%d.png", "-c:v", codec, "-threads", "1"]
        if codec in ("libvpx", "libvpx-vp9"):
            encoders = subprocess.run([str(self.ffmpeg), "-hide_banner", "-encoders"], capture_output=True, text=True,
                                      check=True, timeout=15, **hidden_process_options()).stdout
            if codec not in encoders:
                self.skipTest(f"The installed FFmpeg does not include {codec}")
            options += ["-pix_fmt", "yuva420p", "-auto-alt-ref", "0"]
            if codec == "libvpx-vp9":
                options += ["-lossless", "1"]
        else:
            options += ["-pix_fmt", "argb"]
        self.run_ffmpeg([*options, source])
        return source

    def checked(self, source):
        checked = inspect_local_files([source], ffmpeg_location=self.ffmpeg.parent)[0]
        self.assertIsNone(checked["error"], checked)
        return checked

    def test_alpha_detection_and_decoder_are_specific_to_the_selected_stream(self):
        self.assertTrue(has_alpha({"pix_fmt": "yuva444p10le"}))
        self.assertTrue(has_alpha({"pix_fmt": "argb"}))
        self.assertFalse(has_alpha({"pix_fmt": "yuv420p"}))
        for codec, decoder in (("vp8", "libvpx"), ("vp9", "libvpx-vp9")):
            stream = {"index": 2, "codec_name": codec, "pix_fmt": "yuv420p", "tags": {"ALPHA_MODE": "1"}}
            self.assertTrue(has_alpha(stream))
            self.assertEqual(decoder_options(stream), ["-c:2", decoder])
            self.assertEqual(decoder_options({**stream, "tags": {}}), [])

    def test_vp8_and_vp9_alpha_is_preserved_in_lossless_animations(self):
        for codec in ("libvpx", "libvpx-vp9"):
            with self.subTest(codec=codec):
                source = self.source(codec, "webm")
                checked = self.checked(source)
                self.assertTrue(checked["transparent"], checked)
                for target in ("webp", "apng"):
                    with self.subTest(target=target):
                        output = convert_local_file(checked, target, self.root / "output", ffmpeg_location=self.ffmpeg.parent)
                        with Image.open(output) as animation:
                            self.assertEqual(animation.n_frames, 2)
                            for frame in range(2):
                                with self.subTest(frame=frame):
                                    animation.seek(frame)
                                    rgba = animation.convert("RGBA")
                                    self.assertEqual(rgba.getpixel((8, 8))[3], 0)
                                    self.assertAlmostEqual(rgba.getpixel((24, 8))[3], 128, delta=2)
                                    self.assertEqual(rgba.getpixel((40, 8))[3], 255)

    def test_gif_keeps_zero_alpha_and_composites_partial_alpha_on_selected_background(self):
        checked = self.checked(self.source("qtrle", "mov"))
        for background, expected in (("#FFFFFF", (247, 127, 127)), ("#000000", (120, 0, 0))):
            with self.subTest(background=background):
                output = convert_local_file(checked, "gif", self.root / "output", background,
                                            ffmpeg_location=self.ffmpeg.parent)
                with Image.open(output) as animation:
                    rgba = animation.convert("RGBA")
                    self.assertEqual(rgba.getpixel((8, 8))[3], 0)
                    partial = rgba.getpixel((24, 8))
                    self.assertEqual(partial[3], 255)
                    for actual, wanted in zip(partial[:3], expected):
                        self.assertAlmostEqual(actual, wanted, delta=3)

    def test_every_opaque_video_profile_uses_selected_background(self):
        checked = self.checked(self.source("qtrle", "mov"))
        self.assertTrue(checked["transparent"], checked)
        for target in ("mp4", "mkv", "mov", "webm"):
            for background, expected in (("#FFFFFF", 255), ("#000000", 0)):
                with self.subTest(target=target, background=background):
                    output = convert_local_file(checked, target, self.root / "output", background,
                                                ffmpeg_location=self.ffmpeg.parent)
                    decoded = self.root / f"decoded-{target}-{expected}.png"
                    self.run_ffmpeg(["-i", output, "-frames:v", "1", "-update", "1", decoded])
                    with Image.open(decoded) as image:
                        pixel = image.convert("RGB").getpixel((8, 8))
                        for value in pixel:
                            self.assertAlmostEqual(value, expected, delta=8)

    def test_transparent_webp_adapter_rejects_resource_limits_without_publishing_partial_output(self):
        # WebM does not provide a trustworthy frame count, so the bounded
        # decoder must still enforce its limits after preparing the snapshot.
        source = self.source("libvpx-vp9", "webm")
        original = source.read_bytes()
        checked = self.checked(source)
        output = self.root / "output"
        with patch("universal_downloader.local_animation.MAX_ANIMATION_PIXELS", 100):
            with self.assertRaisesRegex(DownloadError, "decoded pixel limit"):
                convert_local_file(checked, "webp", output, ffmpeg_location=self.ffmpeg.parent)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(output.rglob("*")), [])

    def test_vp9_stream_copy_keeps_the_alpha_plane_discoverable_after_remux(self):
        source = self.source("libvpx-vp9", "webm")
        matroska = self.root / "source.mkv"
        self.run_ffmpeg(["-i", source, "-c", "copy", matroska])
        checked = self.checked(matroska)
        self.assertTrue(checked["transparent"], checked)
        self.assertTrue(checked["_plans"]["webm"]["video_copy"], checked)
        remuxed = convert_local_file(checked, "webm", self.root / "output", ffmpeg_location=self.ffmpeg.parent)
        checked_again = self.checked(remuxed)
        self.assertTrue(checked_again["transparent"], checked_again)
        animation = convert_local_file(checked_again, "apng", self.root / "output", ffmpeg_location=self.ffmpeg.parent)
        with Image.open(animation) as image:
            self.assertEqual(image.convert("RGBA").getpixel((8, 8))[3], 0)


if __name__ == "__main__":
    unittest.main()
