import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from universal_downloader.cli import main
from universal_downloader.downloader import DownloadError, DownloadResult
from universal_downloader.inspection import MediaInspection


class CliTests(unittest.TestCase):
    def setUp(self):
        inspector = patch("universal_downloader.cli.inspect_media")
        self.inspect_media = inspector.start()
        self.addCleanup(inspector.stop)
        self.inspected = MediaInspection(
            "https://example.com/video", {"title": "Example"},
            ("video", "audio", "image"), (2160, 1440, 1080, 720, 480, 360), b"thumbnail",
        )
        self.inspect_media.return_value = self.inspected
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
    @patch("builtins.input", side_effect=["https://example.com/video", "invalid", "2", "", ""])
    @patch("universal_downloader.cli.download")
    def test_menu_reads_url_once_and_retries_invalid_choice(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.mp3"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].url, "https://example.com/video")
        self.assertEqual(download.call_args.args[0].media_format, "mp3")
        self.assertEqual(user_input.call_count, 5)

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "", "", "5"])
    @patch("universal_downloader.cli.download")
    def test_video_menu_selects_resolution(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.mp4"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].quality, "720")

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "3", "3"])
    @patch("universal_downloader.cli.download")
    def test_image_menu_selects_webp_without_video_quality_prompt(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.webp"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "webp")
        self.assertEqual(user_input.call_count, 3)

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "2", "6"])
    @patch("universal_downloader.cli.download")
    def test_lossless_audio_menu_does_not_ask_for_bitrate(self, download, user_input, isatty):
        download.return_value = DownloadResult("Example", Path("example.flac"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "flac")
        self.assertEqual(user_input.call_count, 3)

    @patch("universal_downloader.cli.download")
    def test_extended_command_options_and_aliases(self, download):
        download.return_value = DownloadResult("Example", Path("example.jpg"))
        self.assertEqual(main(["https://example.com/video", "--format", "JPEG", "--image-quality", "80"]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "jpg")
        self.assertEqual(download.call_args.args[0].image_quality, 80)
        self.assertEqual(main(["https://example.com/video", "--format", "opus", "--audio-bitrate", "128"]), 0)
        self.assertEqual(download.call_args.args[0].audio_bitrate, "128")
        self.assertEqual(main(["https://example.com/video", "--format", "webm", "--quality", "2160"]), 0)
        self.assertEqual(download.call_args.args[0].quality, "2160")

    def test_incompatible_options_are_rejected(self):
        for options in (
            ["--format", "png", "--quality", "720"],
            ["--format", "flac", "--audio-bitrate", "192"],
            ["--format", "video", "--image-quality", "80"],
            ["--format", "image", "--source-format", "123"],
            ["--source-format", "123", "--quality", "720"],
        ):
            with self.subTest(options=options), self.assertRaises(SystemExit) as result:
                main(["https://example.com/video", *options])
            self.assertEqual(result.exception.code, 2)

    @patch("universal_downloader.cli.inspect_url")
    @patch("universal_downloader.cli.download")
    def test_source_format_list_does_not_download(self, download, inspect_url):
        inspect_url.return_value = {
            "title": "Example", "thumbnail": "https://example.com/thumb.jpg",
            "formats": [{"format_id": "123", "ext": "mp4", "width": 1920, "height": 1080, "vcodec": "h264", "acodec": "aac"}],
        }
        self.assertEqual(main(["https://example.com/video", "--list-formats"]), 0)
        self.assertIn("123", self.output.getvalue())
        self.assertIn("1920x1080", self.output.getvalue())
        self.assertIn("thumbnail", self.output.getvalue())
        download.assert_not_called()

    @patch("universal_downloader.cli.download")
    def test_source_format_selector_is_forwarded(self, download):
        download.return_value = DownloadResult("Example", Path("example.mp4"))
        self.assertEqual(main(["https://example.com/video", "--source-format", "137+140"]), 0)
        self.assertEqual(download.call_args.args[0].source_format, "137+140")

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

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="https://example.com/video")
    @patch("universal_downloader.cli.download")
    def test_invalid_content_fails_before_any_format_prompt(self, download, user_input, isatty):
        self.inspect_media.side_effect = DownloadError("No media found")
        self.assertEqual(main([]), 1)
        self.assertEqual(user_input.call_count, 1)
        download.assert_not_called()
        self.assertIn("No media found", self.output.getvalue())

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("universal_downloader.cli.download")
    def test_inspection_finishes_before_choices_and_is_reused(self, download, isatty):
        download.return_value = DownloadResult("Example", Path("example.mp3"))
        answers = iter(["https://example.com/video", "2", "", ""])

        def answer(prompt):
            if not prompt.startswith("Paste"):
                self.inspect_media.assert_called_once()
                self.assertIn("Title: Example", self.output.getvalue())
            return next(answers)

        with patch("builtins.input", side_effect=answer):
            self.assertEqual(main([]), 0)
        self.assertIs(download.call_args.kwargs["inspected"], self.inspected)
        self.assertEqual(self.output.getvalue().count("Title: Example"), 1)

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "3"])
    @patch("universal_downloader.cli.download")
    def test_image_only_skips_type_and_resolution_menus(self, download, user_input, isatty):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Picture"}, ("image",), image_data=b"image")
        download.return_value = DownloadResult("Picture", Path("picture.webp"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "webp")
        self.assertEqual(user_input.call_count, 2)
        self.assertNotIn("Video", user_input.call_args.args[0])

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "6"])
    @patch("universal_downloader.cli.download")
    def test_audio_only_offers_audio_formats_directly(self, download, user_input, isatty):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Song"}, ("audio",))
        download.return_value = DownloadResult("Song", Path("song.flac"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "flac")
        self.assertEqual(user_input.call_count, 2)
        self.assertIn("Audio format", user_input.call_args.args[0])

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["https://example.com/video", "", "2"])
    @patch("universal_downloader.cli.download")
    def test_silent_720p_video_hides_audio_and_higher_resolutions(self, download, user_input, isatty):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Silent"}, ("video",), (720,))
        download.return_value = DownloadResult("Silent", Path("silent.mp4"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].quality, "720")
        prompts = "\n".join(call.args[0] for call in user_input.call_args_list)
        for unavailable in ("Audio", "2160", "1440", "1080", "480", "360"):
            self.assertNotIn(unavailable, prompts)

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="https://example.com/video")
    @patch("universal_downloader.cli.download")
    def test_animated_image_keeps_original_without_conversion_prompt(self, download, user_input, isatty):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Animation"}, ("image",), image_data=b"gif", animated=True)
        download.return_value = DownloadResult("Animation", Path("animation.gif"))
        self.assertEqual(main([]), 0)
        self.assertEqual(download.call_args.args[0].media_format, "image")
        self.assertEqual(user_input.call_count, 1)

    @patch("universal_downloader.cli.download")
    def test_incompatible_content_is_rejected_before_download(self, download):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Picture"}, ("image",), image_data=b"image")
        self.assertEqual(main([self.inspected.url, "--format", "mp3"]), 1)
        self.assertIn("No audio track", self.output.getvalue())
        download.assert_not_called()

    @patch("universal_downloader.cli.download")
    def test_direct_url_uses_detected_content_type_as_default(self, download):
        download.return_value = DownloadResult("Example", Path("example.png"))
        for kind in ("image", "audio"):
            with self.subTest(kind=kind):
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                self.assertEqual(main([self.inspected.url]), 0)
                self.assertEqual(download.call_args.args[0].media_format, kind)


if __name__ == "__main__":
    unittest.main()
