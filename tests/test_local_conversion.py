"""Real local-image inspection, preservation, and atomic publication checks."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps

from universal_downloader.errors import DownloadError
from universal_downloader.local_conversion import convert_local_image, inspect_local_files


class LocalConversionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="unidown-local-conversion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "converted"

    def image(self, name="source.png", *, format="PNG", mode="RGBA", color=(30, 70, 120, 255), size=(16, 12), **options):
        path = self.root / name
        with Image.new(mode, size, color) as image:
            image.save(path, format, **options)
        return path

    def inspected(self, path):
        image = inspect_local_files([path])[0]
        self.assertIsNone(image["error"], image)
        return image

    def assert_clean(self):
        self.assertEqual(list(self.output.glob(".unidown-convert-*")), [])

    def test_content_detection_per_file_errors_and_meaningful_targets(self):
        source = self.image("not-a-jpeg.jpg")
        unsupported = self.image("bitmap.png", format="BMP", mode="RGB", color="red")
        invalid = self.root / "invalid.png"
        invalid.write_text("This is not an image", encoding="utf-8")
        results = inspect_local_files([source, unsupported, invalid, self.root / "missing.webp", self.root])
        self.assertEqual(len(results), 5)
        valid = results[0]
        self.assertEqual(valid["format"], "png")
        self.assertEqual(valid["targets"], ["jpg", "webp"])
        self.assertEqual((valid["width"], valid["height"]), (16, 12))
        self.assertEqual(valid["size"], source.stat().st_size)
        self.assertEqual(valid["signature"]["sha256"], sha256(source.read_bytes()).hexdigest())
        self.assertFalse(valid["transparent"])
        self.assertEqual(valid["source"], str(source))
        for failed in results[1:]:
            self.assertTrue(failed["error"], failed)
            self.assertEqual(failed["targets"], [])

    def test_png_and_webp_animation_are_rejected_without_losing_frames(self):
        for format, extension in (("PNG", "png"), ("WEBP", "webp")):
            with self.subTest(format=format):
                source = self.root / f"animated.{extension}"
                with Image.new("RGBA", (12, 12), "red") as first, Image.new("RGBA", (12, 12), "blue") as second:
                    first.save(source, format, save_all=True, append_images=[second], duration=100, loop=0)
                original = source.read_bytes()
                result = inspect_local_files([source])[0]
                self.assertIn("Animated or multipage", result["error"])
                self.assertEqual(result["targets"], [])
                with self.assertRaisesRegex(DownloadError, "Check this image"):
                    convert_local_image(result, "jpg", self.output)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(self.output.exists())

    def test_corrupt_png_is_rejected_during_check(self):
        source = self.image()
        source.write_bytes(source.read_bytes()[:-20])
        result = inspect_local_files([source])[0]
        self.assertIn("not a readable", result["error"])
        self.assertEqual(result["targets"], [])

    def test_file_and_pixel_limits_are_checked_before_encoding(self):
        source = self.image()
        with patch("universal_downloader.local_conversion.MAX_LOCAL_IMAGE_BYTES", 10):
            self.assertIn("128 MB", inspect_local_files([source])[0]["error"])
        with patch("universal_downloader.local_conversion.MAX_LOCAL_IMAGE_PIXELS", 100):
            self.assertIn("40 million", inspect_local_files([source])[0]["error"])
        self.assertFalse(self.output.exists())

    def test_changed_source_is_rejected_even_with_original_size_and_modified_time(self):
        source = self.image()
        checked = self.inspected(source)
        recorded = source.stat()
        data = bytearray(source.read_bytes())
        data[len(data) // 2] ^= 1
        source.write_bytes(data)
        os.utime(source, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
        with self.assertRaisesRegex(DownloadError, "changed after it was checked"):
            convert_local_image(checked, "jpg", self.output)
        self.assertFalse(self.output.exists())

    def test_removed_source_is_a_readable_error(self):
        source = self.image()
        checked = self.inspected(source)
        source.unlink()
        with self.assertRaisesRegex(DownloadError, "no longer available"):
            convert_local_image(checked, "jpg", self.output)

    def test_jpeg_exif_orientation_changes_output_pixels_and_dimensions(self):
        exif = Image.Exif()
        exif[274] = 6
        source = self.image("oriented.jpg", format="JPEG", mode="RGB", color="red", size=(12, 8), exif=exif)
        checked = self.inspected(source)
        original = source.read_bytes()
        self.assertEqual((checked["width"], checked["height"]), (8, 12))
        output = convert_local_image(checked, "png", self.output)
        with Image.open(source) as opened, ImageOps.exif_transpose(opened) as expected, Image.open(output) as converted:
            self.assertEqual(converted.size, expected.size)
            self.assertEqual(converted.tobytes(), expected.convert("RGB").tobytes())
            self.assertNotIn(converted.getexif().get(274), (5, 6, 7, 8))
        self.assertEqual(source.read_bytes(), original)
        self.assert_clean()

    def test_lossless_webp_and_png_keep_alpha_and_exact_pixels(self):
        source = self.image(color=(20, 70, 180, 128))
        original = source.read_bytes()
        checked = self.inspected(source)
        self.assertTrue(checked["transparent"])
        events = []
        webp = convert_local_image(checked, "webp", self.output, progress_hook=events.append)
        self.assertEqual(events, [{"status": "converting"}])
        webp_checked = self.inspected(webp)
        self.assertTrue(webp_checked["transparent"])
        png = convert_local_image(webp_checked, "png", self.output)
        with Image.open(source) as expected, Image.open(webp) as actual_webp, Image.open(png) as actual_png:
            self.assertEqual(actual_webp.convert("RGBA").tobytes(), expected.tobytes())
            self.assertEqual(actual_png.convert("RGBA").tobytes(), expected.tobytes())
        self.assertEqual(source.read_bytes(), original)
        self.assert_clean()

    def test_palette_transparency_is_detected_and_kept(self):
        source = self.root / "palette.png"
        with Image.new("P", (10, 10), 0) as palette:
            palette.putpalette([220, 10, 30, 10, 100, 30] + [0] * 762)
            palette.putpixel((2, 2), 1)
            palette.save(source, "PNG", transparency=0)
        checked = self.inspected(source)
        self.assertTrue(checked["transparent"])
        output = convert_local_image(checked, "webp", self.output)
        with Image.open(output) as converted:
            self.assertEqual(converted.convert("RGBA").getpixel((0, 0))[3], 0)
            self.assertEqual(converted.convert("RGBA").getpixel((2, 2)), (10, 100, 30, 255))

    def test_explicit_white_and_black_jpeg_backgrounds(self):
        source = self.image(color=(200, 10, 80, 0))
        checked = self.inspected(source)
        for background, expected in (("#FFFFFF", (255, 255, 255)), ("#000000", (0, 0, 0))):
            with self.subTest(background=background):
                output = convert_local_image(checked, "jpg", self.output, background)
                with Image.open(output) as converted:
                    self.assertEqual(converted.format, "JPEG")
                    self.assertEqual(converted.mode, "RGB")
                    self.assertEqual(converted.getpixel((8, 6)), expected)
        self.assert_clean()

    def test_original_misleading_extension_and_existing_files_are_never_replaced(self):
        source = self.image("photo.jpg")
        original = source.read_bytes()
        existing = self.root / "photo (1).jpg"
        existing.write_bytes(b"keep this existing file")
        output = convert_local_image(self.inspected(source), "jpg", self.root)
        self.assertEqual(output.name, "photo (2).jpg")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(existing.read_bytes(), b"keep this existing file")
        with Image.open(output) as image:
            self.assertEqual(image.format, "JPEG")
        self.assertEqual(list(self.root.glob(".unidown-convert-*")), [])

    def test_concurrent_publication_uses_unique_complete_files(self):
        source = self.image()
        checked = self.inspected(source)
        with ThreadPoolExecutor(max_workers=4) as executor:
            outputs = list(executor.map(lambda _: convert_local_image(checked, "webp", self.output), range(4)))
        self.assertEqual(len(set(outputs)), 4)
        for output in outputs:
            with Image.open(output) as converted:
                converted.load()
                self.assertEqual(converted.format, "WEBP")
        self.assert_clean()

    def test_same_format_unsupported_target_and_background_are_rejected(self):
        checked = self.inspected(self.image())
        for target, background in (("png", "#FFFFFF"), ("gif", "#FFFFFF"), ([], "#FFFFFF"), ("jpg", "transparent"), ("jpg", [])):
            with self.subTest(target=target, background=background), self.assertRaises(DownloadError):
                convert_local_image(checked, target, self.output, background)
        self.assertFalse(self.output.exists())

    def test_encoder_failure_exposes_no_partial_output_and_cleans_owned_work(self):
        checked = self.inspected(self.image())
        with patch.object(Image.Image, "save", side_effect=OSError("encoder failed")):
            with self.assertRaisesRegex(DownloadError, "could not be converted"):
                convert_local_image(checked, "jpg", self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_hook_cleans_owned_work_and_leaves_original(self):
        source = self.image()
        checked = self.inspected(source)
        original = source.read_bytes()

        def cancel(_event):
            self.assertFalse((self.output / "source.jpg").exists())
            raise DownloadError("Cancelled")

        with self.assertRaisesRegex(DownloadError, "Cancelled"):
            convert_local_image(checked, "jpg", self.output, progress_hook=cancel)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(source.read_bytes(), original)

    def test_parent_owned_workspace_is_supported_but_never_guessed_or_removed(self):
        checked = self.inspected(self.image())
        self.output.mkdir()
        work = self.output / ".unidown-convert-attempt"
        work.mkdir()
        output = convert_local_image(checked, "webp", self.output, work_dir=work)
        self.assertTrue(output.is_file())
        self.assertTrue(work.is_dir())
        outside = self.root / ".unidown-convert-outside"
        outside.mkdir()
        with self.assertRaisesRegex(DownloadError, "workspace is invalid"):
            convert_local_image(checked, "jpg", self.output, work_dir=outside)
        self.assertEqual(list(outside.iterdir()), [])

    def test_no_conversion_depends_on_external_media_tools(self):
        checked = self.inspected(self.image())
        with patch("universal_downloader.dependencies.find_ffmpeg", return_value=None):
            output = convert_local_image(checked, "webp", self.output)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
