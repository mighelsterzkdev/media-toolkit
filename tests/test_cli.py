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
        self.inspected = MediaInspection("https://example.com/video", {"title": "Example"}, ("video", "audio", "image"), image_data=b"image")
        self.inspect_media.return_value = self.inspected
        downloader = patch("universal_downloader.cli.download")
        self.download = downloader.start()
        self.addCleanup(downloader.stop)
        self.download.return_value = DownloadResult("Example", Path("example.mp4"))
        self.output, self.errors = io.StringIO(), io.StringIO()
        for redirect in (contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors)):
            redirect.__enter__()
            self.addCleanup(redirect.__exit__, None, None, None)

    def menu(self, answers, argv=None):
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=answers) as user_input:
            result = main(argv or [])
        return result, user_input

    def test_direct_command_defaults_to_original_detected_content(self):
        for kind in ("video", "audio", "image"):
            with self.subTest(kind=kind):
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                self.assertEqual(main([self.inspected.url]), 0)
                self.assertEqual(self.download.call_args.args[0].media_format, kind)

    def test_explicit_types_and_existing_type_alias(self):
        for flag, kind in (("--type", "audio"), ("--type", "thumbnail"), ("--format", "VIDEO")):
            with self.subTest(flag=flag, kind=kind):
                self.assertEqual(main([self.inspected.url, flag, kind, "--output", "my-downloads"]), 0)
                self.assertEqual(self.download.call_args.args[0].media_format, "image" if kind == "thumbnail" else kind.lower())
                self.assertEqual(self.download.call_args.args[0].output_dir, Path("my-downloads"))

    def test_conversion_and_manual_encoding_options_are_removed(self):
        for options in (
            ["--type", "png"], ["--format", "mp3"], ["--quality", "720"],
            ["--audio-bitrate", "192"], ["--image-quality", "80"],
            ["--source-format", "123"], ["--list-formats"],
        ):
            with self.subTest(options=options), self.assertRaises(SystemExit) as result:
                main([self.inspected.url, *options])
            self.assertEqual(result.exception.code, 2)
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()

    def test_each_type_needs_only_one_choice_without_format_or_quality_menus(self):
        for number, kind in (("1", "video"), ("2", "audio"), ("3", "image")):
            with self.subTest(kind=kind):
                status, user_input = self.menu([self.inspected.url, number, ""])
                self.assertEqual(status, 0)
                self.assertEqual(user_input.call_count, 3)
                self.assertEqual(self.download.call_args.args[0].media_format, kind)
                prompts = " ".join(call.args[0] for call in user_input.call_args_list)
                for removed in ("Format", "Quality", "bitrate", "PNG", "MP3"):
                    self.assertNotIn(removed, prompts)

    def test_single_type_downloads_without_any_choice(self):
        for kind in ("video", "audio", "image"):
            with self.subTest(kind=kind):
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                status, user_input = self.menu([self.inspected.url, ""])
                self.assertEqual(status, 0)
                self.assertEqual(user_input.call_count, 2)
                self.assertEqual(self.download.call_args.args[0].media_format, kind)

    def test_animated_images_need_no_extra_prompt(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Animation"}, ("image",), image_data=b"gif", animated=True)
        status, user_input = self.menu([self.inspected.url, ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 2)
        self.assertEqual(self.download.call_args.args[0].media_format, "image")
        self.assertIn("Available: Animation", self.output.getvalue())

    def test_inspection_precedes_choice_and_is_reused(self):
        answers = iter([self.inspected.url, "2", ""])

        def answer(prompt):
            if prompt.startswith("Download"):
                self.inspect_media.assert_called_once()
                self.assertIn("Title: Example", self.output.getvalue())
            return next(answers)

        status, _ = self.menu(answer)
        self.assertEqual(status, 0)
        self.assertIs(self.download.call_args.kwargs["inspected"], self.inspected)
        self.assertEqual(self.output.getvalue().count("Title: Example"), 1)

    def test_multiple_links_run_in_one_session(self):
        status, _ = self.menu([self.inspected.url, "2", self.inspected.url, "3", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.inspect_media.call_count, 2)
        self.assertEqual([call.args[0].media_format for call in self.download.call_args_list], ["audio", "image"])

    def test_invalid_choice_retries_without_rechecking_url(self):
        status, _ = self.menu([self.inspected.url, "wrong", "9", "2", ""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_called_once()
        self.assertEqual(self.download.call_args.args[0].media_format, "audio")

    def test_back_returns_to_url_without_downloading(self):
        status, _ = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.download.assert_not_called()

    def test_bad_url_returns_to_prompt_instead_of_closing(self):
        status, user_input = self.menu(["not-a-url", self.inspected.url, "3", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 4)
        self.inspect_media.assert_called_once()
        self.download.assert_called_once()
        self.assertIn("Invalid URL", self.errors.getvalue())

    def test_unsupported_url_fails_before_choices_and_accepts_another_link(self):
        self.inspect_media.side_effect = [DownloadError("No media found"), self.inspected]
        status, user_input = self.menu([self.inspected.url, self.inspected.url, "3", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 4)
        self.download.assert_called_once()
        self.assertIn("No media found", self.errors.getvalue())

    def test_failed_download_does_not_end_session(self):
        self.download.side_effect = [DownloadError("Website unavailable"), self.download.return_value]
        status, _ = self.menu([self.inspected.url, "1", self.inspected.url, "3", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.download.call_count, 2)
        self.assertIn("Website unavailable", self.errors.getvalue())

    def test_direct_failure_returns_nonzero_without_traceback(self):
        self.inspect_media.side_effect = DownloadError("Website unavailable")
        self.assertEqual(main([self.inspected.url]), 1)
        self.download.assert_not_called()
        self.assertIn("Website unavailable", self.errors.getvalue())
        self.assertNotIn("Traceback", self.errors.getvalue())

    def test_unavailable_type_is_rejected_before_download(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Silent"}, ("video",))
        self.assertEqual(main([self.inspected.url, "--type", "audio"]), 1)
        self.assertIn("No audio track", self.errors.getvalue())
        self.download.assert_not_called()

    def test_existing_download_is_reported(self):
        self.download.return_value = DownloadResult("Example", Path("example.mp4"), True)
        self.assertEqual(main([self.inspected.url]), 0)
        self.assertIn("Already downloaded:", self.output.getvalue())

    def test_empty_link_exits_without_inspection(self):
        status, _ = self.menu([""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()

    def test_eof_and_interrupt_exit_cleanly(self):
        self.assertEqual(self.menu(EOFError)[0], 0)
        self.assertEqual(self.menu(KeyboardInterrupt)[0], 130)
        self.assertNotIn("Traceback", self.errors.getvalue())

    @patch("universal_downloader.cli.sys.stdin.isatty", return_value=False)
    def test_noninteractive_missing_url_does_not_hang(self, isatty):
        with self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)

    @patch("universal_downloader.cli.find_ffmpeg", return_value=None)
    @patch("universal_downloader.cli.find_js_runtime", return_value=None)
    def test_doctor_reports_missing_tools(self, runtime, ffmpeg):
        self.assertEqual(main(["--doctor"]), 1)
        self.assertIn("install.cmd", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
