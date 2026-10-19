import io
import unittest

from PIL import Image

from universal_downloader.inspection import MediaInspection


class OriginalFormatTests(unittest.TestCase):
    def test_verified_image_dimensions_are_read_without_changing_bytes(self):
        with io.BytesIO() as encoded:
            Image.new("RGBA", (31, 17), "red").save(encoded, "PNG")
            data = encoded.getvalue()
        inspected = MediaInspection("https://example.com/image", {}, ("image",), image_data=data)
        self.assertEqual(inspected.image_dimensions, (31, 17))
        self.assertEqual(inspected.image_data, data)

    def test_deferred_or_unreadable_image_dimensions_are_unknown(self):
        for data in (None, b"not an image"):
            with self.subTest(data=data):
                inspected = MediaInspection("https://example.com/image", {}, ("image",), image_data=data)
                self.assertIsNone(inspected.image_dimensions)

    def test_single_asset_inspection_keeps_backward_compatible_default(self):
        inspected = MediaInspection("https://example.com/image", {}, ("image",))
        self.assertEqual(inspected.alternatives, ())

    def test_verified_image_format_takes_precedence_over_page_format(self):
        inspected = MediaInspection("https://example.com/page", {"ext": "mp4"}, ("image",), image_extension="png")
        self.assertEqual(inspected.original_extension("image"), "png")

    def test_single_original_containers_are_identified(self):
        for kind, extension, codecs in (
            ("video", "mp4", {"vcodec": "h264", "acodec": "aac"}),
            ("audio", "webm", {"vcodec": "none", "acodec": "opus"}),
            ("audio", "mp3", {"vcodec": "none", "acodec": "mp3"}),
        ):
            with self.subTest(kind=kind, extension=extension):
                information = {"formats": [{"ext": extension, **codecs}]}
                inspected = MediaInspection("https://example.com/media", information, (kind,))
                self.assertEqual(inspected.original_extension(kind), extension)

    def test_merged_and_extracted_containers_are_not_guessed(self):
        sources = [
            {"ext": "mp4", "vcodec": "h264", "acodec": "none"},
            {"ext": "webm", "vcodec": "none", "acodec": "opus"},
        ]
        inspected = MediaInspection("https://example.com/video", {"ext": "mp4", "formats": sources}, ("video", "audio"))
        self.assertIsNone(inspected.original_extension("video"))
        self.assertEqual(inspected.original_extension("audio"), "webm")
        inspected = MediaInspection("https://example.com/video", {"ext": "mp4", "vcodec": "h264", "acodec": "aac"}, ("video", "audio"))
        self.assertIsNone(inspected.original_extension("audio"))

    def test_mixed_or_unknown_containers_keep_the_generic_label(self):
        for sources in (
            [{"ext": "mp4", "vcodec": "h264"}, {"ext": "webm", "vcodec": "vp9"}],
            [{"vcodec": "h264"}],
            [{"ext": "bad extension", "vcodec": "h264"}],
        ):
            with self.subTest(sources=sources):
                inspected = MediaInspection("https://example.com/video", {"formats": sources}, ("video",))
                self.assertIsNone(inspected.original_extension("video"))


if __name__ == "__main__":
    unittest.main()
