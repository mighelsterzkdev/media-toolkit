import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from universal_downloader.cli import main
from universal_downloader.downloader import DownloadError, DownloadResult


class CliTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stderr = contextlib.redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.addCleanup(self.stderr.__exit__, None, None, None)

    @patch("universal_downloader.cli.download")
    def test_direct_mp3_command_reports_final_path(self, download):
        download.return_value = DownloadResult("Example", Path("example.mp3"))
        self.assertEqual(main(["https://example.com/video", "--format", "mp3"]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "mp3")
        self.assertIn("example.mp3", self.output.getvalue())

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "invalid", "2"])
    @patch("universal_downloader.cli.download")
    def test_menu_reads_url_once_and_retries_invalid_choice(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.mp3"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].url, "https://example.com/video")
        self.assertEqual(download.call_args.args[0].media_format, "mp3")
        self.assertEqual(user_input.call_count, 3)

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "", "3"])
    @patch("universal_downloader.cli.download")
    def test_video_menu_selects_resolution(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.mp4"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].quality, "720")

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="")
    @patch("universal_downloader.cli.download")
    def test_empty_link_exits_without_download(self, download, user_input, isatty):
        self.assertEqual(main([]), 0)
        download.assert_not_called()

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=False)
    def test_noninteractive_missing_url_fails_without_hanging(self, isatty):
        with self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)

    def test_mp3_with_resolution_is_rejected(self):
        with self.assertRaises(SystemExit) as result:
            main(["https://example.com/video", "--format", "mp3", "--quality", "720"])
        self.assertEqual(result.exception.code, 2)

    @patch("universal_downloader.cli.download", side_effect=DownloadError("Website unavailable"))
    def test_download_failure_has_nonzero_exit_without_traceback(self, download):
        self.assertEqual(main(["https://example.com/video"]), 1)
        self.assertIn("Website unavailable", self.output.getvalue())
        self.assertNotIn("Traceback", self.output.getvalue())

    @patch("universal_downloader.cli.download", side_effect=KeyboardInterrupt)
    def test_interrupt_returns_cancelled_exit_code(self, download):
        self.assertEqual(main(["https://example.com/video"]), 130)

    @patch("universal_downloader.cli.find_ffmpeg", return_value=None)
    @patch("universal_downloader.cli.find_js_runtime", return_value=None)
    def test_doctor_reports_missing_tools(self, runtime, ffmpeg):
        self.assertEqual(main(["--doctor"]), 1)
        self.assertIn("install.cmd", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
