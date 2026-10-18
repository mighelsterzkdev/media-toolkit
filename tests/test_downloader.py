import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from universal_downloader.downloader import DownloadError, DownloadRequest, download, inspect_media, validate_url
from universal_downloader.inspection import MediaInspection, is_video_animation


class InputTests(unittest.TestCase):
    def test_twitter_animation_assets_are_distinct_from_silent_videos_and_posters(self):
        for url, animated in (
            ("https://video.twimg.com/tweet_video/clip.mp4?tag=12", True),
            ("https://video.twimg.com/ext_tw_video/123/pu/vid/clip.mp4", False),
            ("https://video.twimg.com/amplify_video/123/vid/clip.mp4", False),
            ("https://pbs.twimg.com/tweet_video_thumb/clip.jpg", False),
            ("https://video.twimg.com.example.com/tweet_video/clip.mp4", False),
            ("http://[invalid", False),
        ):
            with self.subTest(url=url):
                self.assertEqual(is_video_animation({"formats": [{"url": url}]}), animated)

    def test_clipboard_urls_trim_whitespace_and_surrounding_quotes(self):
        for value in (
            "  https://example.com/watch?v=abc&list=123\n",
            '"https://example.com/watch?v=abc&list=123"',
            "'https://example.com/watch?v=abc&list=123'",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_url(value), "https://example.com/watch?v=abc&list=123")

    def test_invalid_urls_are_rejected(self):
        for url in (
            "", "example.com/video", "file:///C:/secret", "ftp://example.com/file",
            "https://", "https://example.com:bad/file", "https://[invalid",
            "https://user:secret@example.com/video", "https://example.com/a b",
            "https://example.com/a\nb", "javascript:alert(1)",
        ):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)

    @patch("universal_downloader.downloader.YoutubeDL")
    def test_invalid_requests_fail_before_network_access(self, downloader):
        for request in (
            DownloadRequest("not a url"), DownloadRequest("https://example.com/video", "zip"),
            DownloadRequest("https://example.com/video", "mp3"), DownloadRequest("https://example.com/video", "png"),
        ):
            with self.subTest(request=request), self.assertRaises(DownloadError):
                download(request)
        with self.assertRaises(DownloadError):
            inspect_media("https://example.com/video", media_format="png")
        downloader.assert_not_called()

    @patch("universal_downloader.downloader.find_ffmpeg", return_value=None)
    @patch("universal_downloader.downloader.YoutubeDL")
    def test_missing_ffmpeg_fails_before_network_access(self, downloader, ffmpeg):
        with self.assertRaisesRegex(DownloadError, "FFmpeg"):
            download(DownloadRequest("https://example.com/video"))
        downloader.assert_not_called()

    @patch("universal_downloader.downloader.YoutubeDL")
    def test_inspection_cannot_be_reused_for_another_url(self, downloader):
        inspected = MediaInspection("https://example.com/one", {"title": "One"}, ("video",))
        with self.assertRaisesRegex(DownloadError, "does not match"):
            download(DownloadRequest("https://example.com/two"), inspected=inspected)
        downloader.assert_not_called()

    @patch("universal_downloader.downloader.find_ffmpeg", return_value=Path("."))
    @patch("universal_downloader.downloader.find_js_runtime", return_value=None)
    @patch("universal_downloader.downloader.YoutubeDL")
    def test_playlists_and_live_streams_are_rejected_before_downloading(self, factory, runtime, ffmpeg):
        downloader = factory.return_value.__enter__.return_value
        for information in (
            {"_type": "playlist", "entries": []},
            {"_type": "multi_video", "entries": []},
            {"title": "Live", "is_live": True, "url": "https://example.com/video"},
            {"title": "Premiere", "live_status": "is_upcoming", "url": "https://example.com/video"},
        ):
            with self.subTest(information=information), tempfile.TemporaryDirectory() as temporary:
                downloader.extract_info.return_value = information
                with self.assertRaises(DownloadError):
                    download(DownloadRequest("https://example.com/video", output_dir=Path(temporary) / "new"))
                self.assertFalse((Path(temporary) / "new").exists())
                downloader.process_ie_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
