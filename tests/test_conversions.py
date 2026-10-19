import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from universal_downloader.conversions import available_conversions, convert_file, validate_conversion
from universal_downloader.dependencies import find_ffmpeg
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection


class ConversionChoicesTests(unittest.TestCase):
    def image(self, extension="GIF", *, size=(100, 100), animated=True):
        with io.BytesIO() as encoded:
            frames = [Image.new("RGB", size, color) for color in ("red", "blue")]
            options = dict(save_all=True, append_images=frames[1:], duration=200) if animated else {}
            frames[0].save(encoded, extension, **options)
            return encoded.getvalue()

    def test_actual_native_bytes_remove_duplicate_even_without_extension_metadata(self):
        data = self.image()
        inspected = MediaInspection("https://example.com/image", {"title": "Example"}, ("image",), image_data=data, animated=True)
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=Path(".")):
            self.assertEqual(available_conversions(inspected, "image"), ("mp4",))

    def test_gif_resize_is_meaningful_and_requires_encoder(self):
        inspected = MediaInspection("https://example.com/a.gif", {"duration": 0.4}, ("image",), image_data=self.image(), animated=True, image_extension="gif")
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=Path(".")):
            self.assertEqual(available_conversions(inspected, "image", max_height=50), ("gif", "mp4"))
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=None):
            self.assertEqual(available_conversions(inspected, "image", max_height=50), ())
            with self.assertRaisesRegex(DownloadError, "FFmpeg"):
                validate_conversion(inspected, "image", "gif", max_height=50)
            # An explicitly requested exact copy still works without tools.
            validate_conversion(inspected, "image", "gif")

    def test_resizing_long_native_gif_respects_reencoding_duration_cap(self):
        inspected = MediaInspection("https://example.com/a.gif", {"duration": 120}, ("image",), image_data=self.image(), animated=True, image_extension="gif")
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=Path(".")):
            self.assertEqual(available_conversions(inspected, "image", max_height=50), ("mp4",))

    def test_native_still_and_audio_targets_are_not_redundant(self):
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=Path(".")):
            image = MediaInspection("https://example.com/a.jpg", {}, ("image",), image_extension="jpg")
            audio = MediaInspection("https://example.com/a.mp3", {"ext": "mp3", "vcodec": "none", "acodec": "mp3"}, ("audio",))
            self.assertEqual(available_conversions(image, "image"), ("png",))
            self.assertEqual(available_conversions(audio, "audio"), ())

    def test_one_pixel_height_does_not_offer_unencodable_mp4(self):
        inspected = MediaInspection("https://example.com/a.gif", {"duration": 0.4}, ("image",), image_data=self.image(), animated=True, image_extension="gif")
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=Path(".")):
            self.assertEqual(available_conversions(inspected, "image", max_height=1), ("gif",))


class ConversionFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "animation.gif"
        frames = [Image.new("RGB", (100, 100), color) for color in ("red", "blue", "green")]
        frames[0].save(self.source, "GIF", save_all=True, append_images=frames[1:], duration=200)

    def require_ffmpeg(self):
        tools = find_ffmpeg()
        if tools is None:
            self.skipTest("FFmpeg is required for real conversion tests")
        return tools

    def test_size_limit_is_checked_before_publish_and_cleans_work(self):
        destination = self.root / "output.gif"
        with self.assertRaisesRegex(DownloadError, "exceeds --max-size"):
            convert_file(self.source, "gif", destination, max_size=1)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".unidown*")), [])
        self.assertTrue(self.source.is_file())

    def test_size_limit_never_replaces_an_existing_file(self):
        destination = self.root / "output.gif"
        destination.write_bytes(b"keep existing")
        with self.assertRaises(DownloadError):
            convert_file(self.source, "gif", destination, max_size=1)
        self.assertEqual(destination.read_bytes(), b"keep existing")

    def test_direct_conversion_rejects_invalid_limit_values(self):
        for name, value in (("max_size", 0), ("max_size", -1), ("max_size", True), ("max_height", 0), ("max_height", 1.5)):
            with self.subTest(name=name, value=value), self.assertRaises(DownloadError):
                convert_file(self.source, "gif", self.root / "output.gif", **{name: value})
        self.assertEqual(list(self.root.glob(".unidown*")), [])

    def test_native_gif_is_resized_when_requested_and_remains_animated(self):
        self.require_ffmpeg()
        destination = self.root / "small.gif"
        self.assertTrue(convert_file(self.source, "gif", destination, max_height=40))
        with Image.open(destination) as image:
            self.assertLessEqual(image.height, 40)
            self.assertTrue(image.is_animated)
        self.assertEqual(list(self.root.glob(".unidown*")), [])

    def test_gif_to_mp4_obeys_odd_height_cap_without_upscaling(self):
        tools = self.require_ffmpeg()
        executable = tools / ("ffprobe.exe" if (tools / "ffprobe.exe").is_file() else "ffprobe")
        for maximum, expected in ((49, 48), (720, 100)):
            with self.subTest(maximum=maximum):
                destination = self.root / f"converted-{maximum}.mp4"
                self.assertTrue(convert_file(self.source, "mp4", destination, max_height=maximum))
                result = subprocess.run([str(executable), "-v", "error", "-show_streams", "-of", "json", str(destination)], check=True, capture_output=True, text=True)
                video = json.loads(result.stdout)["streams"][0]
                self.assertEqual(video["codec_name"], "h264")
                self.assertEqual(video["height"], expected)
                self.assertLessEqual(video["width"], 100)


if __name__ == "__main__":
    unittest.main()
