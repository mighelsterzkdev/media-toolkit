import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from universal_downloader.downloader import DownloadError, DownloadRequest, download, validate_url


class InputTests(unittest.TestCase):
    def test_urls_preserve_query_and_trim_clipboard_whitespace(self):
        self.assertEqual(
            validate_url("  https://example.com/watch?v=abc&list=123\n"),
            "https://example.com/watch?v=abc&list=123",
        )

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
    def test_invalid_request_does_not_start_network_access(self, downloader):
        with self.assertRaises(DownloadError):
            download(DownloadRequest("not a url"))
        downloader.assert_not_called()

    @patch("universal_downloader.downloader.find_ffmpeg", return_value=None)
    @patch("universal_downloader.downloader.YoutubeDL")
    def test_missing_ffmpeg_fails_before_network_access(self, downloader, ffmpeg):
        with self.assertRaisesRegex(DownloadError, "FFmpeg"):
            download(DownloadRequest("https://example.com/video"))
        downloader.assert_not_called()

    def test_invalid_format_and_quality_fail_early(self):
        for media_format, quality in (("wav", "best"), ("video", "999"), ("mp3", "720")):
            with self.subTest(format=media_format, quality=quality), self.assertRaises(DownloadError):
                download(DownloadRequest("https://example.com/video", media_format, quality))

    @patch("universal_downloader.downloader.find_ffmpeg", return_value=Path("."))
    @patch("universal_downloader.downloader.find_js_runtime", return_value=None)
    @patch("universal_downloader.downloader.YoutubeDL")
    def test_playlists_and_live_streams_are_rejected_before_downloading(self, factory, runtime, ffmpeg):
        downloader = factory.return_value.__enter__.return_value
        for information in (
            {"_type": "playlist", "entries": []},
            {"_type": "multi_video", "entries": []},
            {"title": "Live", "is_live": True},
            {"title": "Premiere", "live_status": "is_upcoming"},
        ):
            with self.subTest(information=information), tempfile.TemporaryDirectory() as temporary:
                downloader.extract_info.return_value = information
                with self.assertRaises(DownloadError):
                    download(DownloadRequest("https://example.com/video", output_dir=Path(temporary)))
                downloader.process_ie_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
