"""Live checks must verify readable media rather than accepting HTTP/file success alone."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from scripts.live_smoke import check_case, isolated_history, validate_media
from universal_downloader.downloader import DownloadResult
from universal_downloader.inspection import MediaInspection


class LiveMediaValidationTests(unittest.TestCase):
    def test_smoke_history_never_replaces_the_users_configured_history(self):
        with patch.dict(os.environ, {"UNIDOWN_DATA_DIR": "user-history"}):
            with tempfile.TemporaryDirectory(prefix="unidown-smoke-test-") as temporary:
                with isolated_history(Path(temporary)):
                    self.assertEqual(os.environ["UNIDOWN_DATA_DIR"], str(Path(temporary) / "history"))
                self.assertEqual(os.environ["UNIDOWN_DATA_DIR"], "user-history")

    def test_animation_validation_counts_real_image_frames(self):
        with tempfile.TemporaryDirectory(prefix="unidown-smoke-test-") as temporary:
            image = Path(temporary) / "animation.gif"
            Image.new("RGB", (8, 8), "red").save(image, save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
            result = validate_media(image, kind="image", animated=True)
            self.assertEqual(result["frames"], 2)
            self.assertEqual(result["format"], "GIF")

    def test_one_frame_does_not_pass_as_animation(self):
        with tempfile.TemporaryDirectory(prefix="unidown-smoke-test-") as temporary:
            image = Path(temporary) / "static.gif"
            Image.new("RGB", (8, 8), "red").save(image)
            with self.assertRaisesRegex(ValueError, "only one frame"):
                validate_media(image, kind="image", animated=True)
            with self.assertRaisesRegex(ValueError, "Expected audio"):
                validate_media(image, kind="audio", animated=False)

    def test_video_with_thumbnail_is_validated_against_the_video_not_thumbnail_bytes(self):
        with tempfile.TemporaryDirectory(prefix="unidown-smoke-test-") as temporary:
            video = Path(temporary) / "fixture.mp4"
            video.write_bytes(b"encoded-video-fixture")
            inspected = MediaInspection("https://example.com/video", {"title": "Video"}, ("video", "image"), image_data=b"thumbnail-fixture")
            with patch("universal_downloader.downloader.inspect_media", return_value=inspected), patch("universal_downloader.downloader.download", return_value=DownloadResult("Video", video)), patch("scripts.live_smoke.validate_media", return_value={"video_codecs": ["h264"]}):
                result = check_case({"name": "fixture", "url": inspected.url, "kind": "video"}, save=True)
            self.assertEqual(result["status"], "passed")

    def test_replaced_original_image_fails_the_live_check(self):
        with tempfile.TemporaryDirectory(prefix="unidown-smoke-test-") as temporary:
            image = Path(temporary) / "fixture.png"
            Image.new("RGB", (8, 8), "red").save(image)
            inspected = MediaInspection("https://example.com/image", {"title": "Image"}, ("image",), image_data=b"expected-original")
            with patch("universal_downloader.downloader.inspect_media", return_value=inspected), patch("universal_downloader.downloader.download", return_value=DownloadResult("Image", image)):
                result = check_case({"name": "fixture", "url": inspected.url, "kind": "image"}, save=True)
            self.assertEqual(result["status"], "failed")
            self.assertIn("original image bytes changed", result["error"])


if __name__ == "__main__":
    unittest.main()
