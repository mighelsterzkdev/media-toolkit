import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from universal_downloader.cli import main
from universal_downloader.downloader import DownloadError, DownloadResult
from universal_downloader.inspection import MediaInspection


class CliTests(unittest.TestCase):
    def setUp(self):
        encoder = patch("universal_downloader.conversions.find_ffmpeg", return_value=Path("."))
        encoder.start()
        self.addCleanup(encoder.stop)
        with io.BytesIO() as encoded:
            Image.new("RGB", (2, 2), "red").save(encoded, "GIF")
            self.gif_data = encoded.getvalue()
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

    def test_formats_are_only_available_through_explicit_conversion(self):
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

    def test_explicit_conversion_selects_compatible_content(self):
        for target, kind in (("MP3", "audio"), ("mp4", "video"), ("png", "image"), ("jpg", "image"), ("JPEG", "image")):
            with self.subTest(target=target):
                self.assertEqual(main([self.inspected.url, "--convert", target]), 0)
                request = self.download.call_args.args[0]
                self.assertEqual(request.media_format, kind)
                self.assertEqual(request.convert_to, "jpg" if target == "JPEG" else target.lower())

    def test_pasted_url_offers_compatible_formats_after_content_choice(self):
        status, _ = self.menu([self.inspected.url, "2", "2", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.download.call_args.args[0].media_format, "audio")
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp3")
        self.assertIn("Original (recommended)", self.output.getvalue())
        self.assertIn("MP3 audio", self.output.getvalue())
        self.assertNotIn("PNG image", self.output.getvalue())

    def test_original_is_default_in_output_menu(self):
        status, _ = self.menu([self.inspected.url, "2", "", ""])
        self.assertEqual(status, 0)
        self.assertIsNone(self.download.call_args.args[0].convert_to)

    def test_back_from_output_menu_accepts_another_url(self):
        status, _ = self.menu([self.inspected.url, "3", "0", self.inspected.url, "1", "1", ""])
        self.assertEqual(status, 0)
        self.download.assert_called_once()
        self.assertIsNone(self.download.call_args.args[0].convert_to)

    def test_canceling_output_choice_never_starts_download(self):
        for interruption, expected_status in ((EOFError, 0), (KeyboardInterrupt, 130)):
            with self.subTest(interruption=interruption):
                status, _ = self.menu([self.inspected.url, "1", interruption])
                self.assertEqual(status, expected_status)
                self.download.assert_not_called()

    def test_animations_do_not_offer_static_image_conversions(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Animation"}, ("image",), image_data=self.gif_data, animated=True, image_extension="gif")
        status, _ = self.menu([self.inspected.url, "2", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.download.call_args.args[0].convert_to, "gif")
        self.assertNotIn("PNG", self.output.getvalue())
        self.assertNotIn("JPG", self.output.getvalue())

    def test_bad_conversion_does_not_download_or_ask_for_more_options(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Picture"}, ("image",), image_data=b"image")
        self.assertEqual(main([self.inspected.url, "--convert", "mp3"]), 1)
        self.download.assert_not_called()

    def test_each_content_type_offers_original_and_compatible_conversion(self):
        for number, kind in (("1", "video"), ("2", "audio"), ("3", "image")):
            with self.subTest(kind=kind):
                status, user_input = self.menu([self.inspected.url, number, "1", ""])
                self.assertEqual(status, 0)
                self.assertEqual(user_input.call_count, 4)
                self.assertEqual(self.download.call_args.args[0].media_format, kind)
                self.assertIsNone(self.download.call_args.args[0].convert_to)

    def test_single_type_waits_for_output_choice_before_downloading(self):
        for kind in ("video", "audio", "image"):
            with self.subTest(kind=kind):
                self.inspect_media.reset_mock()
                self.download.reset_mock()
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                answers = iter([self.inspected.url, "1", ""])

                def answer(prompt):
                    if prompt.startswith("Save as"):
                        self.inspect_media.assert_called_once()
                        self.download.assert_not_called()
                        self.assertIn("1. Original (recommended)", self.output.getvalue())
                    return next(answers)

                status, user_input = self.menu(answer)
                self.assertEqual(status, 0)
                self.assertEqual(user_input.call_count, 3)
                self.assertEqual(self.download.call_args.args[0].media_format, kind)

    def test_twitter_animation_offers_gif_without_separate_conversion_mode(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Animation", "media_type": "animated_gif", "ext": "mp4"},
            ("image",), animated=True, animation_video=True,
        )
        status, user_input = self.menu([self.inspected.url, "2", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 3)
        self.assertEqual(self.download.call_args.args[0].media_format, "image")
        self.assertEqual(self.download.call_args.args[0].convert_to, "gif")
        self.assertIn("Available: Animation", self.output.getvalue())
        self.assertIn("1. Original MP4 (recommended)", self.output.getvalue())
        self.assertIn("2. GIF animation", self.output.getvalue())
        self.assertIn("3. MP4 video", self.output.getvalue())

    def test_known_unusable_gif_durations_are_not_offered(self):
        for duration in (31, 0, -1, float("inf"), float("nan")):
            with self.subTest(duration=duration):
                self.output.seek(0)
                self.output.truncate()
                self.inspect_media.return_value = MediaInspection(
                    self.inspected.url, {"title": "Animation", "ext": "mp4", "duration": duration},
                    ("image",), animated=True, animation_video=True,
                )
                status, _ = self.menu([self.inspected.url, "2", ""])
                self.assertEqual(status, 0)
                self.assertNotIn("GIF animation", self.output.getvalue())
                self.assertIn("2. MP4 video", self.output.getvalue())
                self.assertEqual(self.download.call_args.args[0].convert_to, "mp4")

    def test_native_gif_remains_available_without_ffmpeg_or_duration_limit(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Animation", "duration": 120}, ("image",),
            image_data=self.gif_data, animated=True, image_extension="gif",
        )
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=None):
            status, _ = self.menu([self.inspected.url, "2", ""])
        self.assertEqual(status, 0)
        self.assertIn("1. Original GIF (recommended)", self.output.getvalue())
        self.assertIn("2. GIF animation", self.output.getvalue())
        self.assertNotIn("MP4 video", self.output.getvalue())
        self.assertEqual(self.download.call_args.args[0].convert_to, "gif")

    def test_missing_encoder_hides_conversion_but_still_requires_original_choice(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Animation", "ext": "mp4"},
            ("image",), animated=True, animation_video=True,
        )
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=None):
            status, user_input = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 3)
        self.assertIn("1. Original MP4 (recommended)", self.output.getvalue())
        self.assertNotIn("2.", self.output.getvalue())
        self.download.assert_not_called()

    def test_single_type_can_return_to_url_without_downloading(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, ("image",))
        status, _ = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.download.assert_not_called()

    def test_invalid_output_retries_without_inspecting_or_downloading_again(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, ("audio",))
        status, _ = self.menu([self.inspected.url, "wrong", "9", "2", ""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_called_once()
        self.download.assert_called_once()
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp3")

    def test_explicit_conversion_flag_does_not_ask_for_redundant_choice(self):
        status, user_input = self.menu([self.inspected.url, ""], ["--convert", "mp3"])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 2)
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp3")

    def test_inspection_precedes_choice_and_is_reused(self):
        answers = iter([self.inspected.url, "2", "1", ""])

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
        status, _ = self.menu([self.inspected.url, "2", "1", self.inspected.url, "3", "1", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.inspect_media.call_count, 2)
        self.assertEqual([call.args[0].media_format for call in self.download.call_args_list], ["audio", "image"])

    def test_invalid_choice_retries_without_rechecking_url(self):
        status, _ = self.menu([self.inspected.url, "wrong", "9", "2", "1", ""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_called_once()
        self.assertEqual(self.download.call_args.args[0].media_format, "audio")

    def test_back_returns_to_url_without_downloading(self):
        status, _ = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.download.assert_not_called()

    def test_bad_url_returns_to_prompt_instead_of_closing(self):
        status, user_input = self.menu(["not-a-url", self.inspected.url, "3", "1", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 5)
        self.inspect_media.assert_called_once()
        self.download.assert_called_once()
        self.assertIn("Invalid URL", self.errors.getvalue())

    def test_unsupported_url_fails_before_choices_and_accepts_another_link(self):
        self.inspect_media.side_effect = [DownloadError("No media found"), self.inspected]
        status, user_input = self.menu([self.inspected.url, self.inspected.url, "3", "1", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 5)
        self.download.assert_called_once()
        self.assertIn("No media found", self.errors.getvalue())

    def test_failed_download_does_not_end_session(self):
        self.download.side_effect = [DownloadError("Website unavailable"), self.download.return_value]
        status, _ = self.menu([self.inspected.url, "1", "1", self.inspected.url, "3", "1", ""])
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
