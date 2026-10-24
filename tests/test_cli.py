import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from universal_downloader.cli import _parse_size, _Progress, main
from universal_downloader.downloader import DownloadError, DownloadResult
from universal_downloader.inspection import MediaInspection
from universal_downloader.preferences import load_preferences, preferences_path, save_preferences


class CliTests(unittest.TestCase):
    def setUp(self):
        self.preferences_folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.preferences_folder.cleanup)
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": self.preferences_folder.name})
        environment.start()
        self.addCleanup(environment.stop)
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
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp4")
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

    def test_native_gif_has_only_original_without_ffmpeg(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Animation", "duration": 120}, ("image",),
            image_data=self.gif_data, animated=True, image_extension="gif",
        )
        with patch("universal_downloader.conversions.find_ffmpeg", return_value=None):
            status, _ = self.menu([self.inspected.url, "1", ""])
        self.assertEqual(status, 0)
        self.assertIn("1. Original GIF (recommended)", self.output.getvalue())
        self.assertNotIn("GIF animation", self.output.getvalue())
        self.assertNotIn("MP4 video", self.output.getvalue())
        self.assertIsNone(self.download.call_args.args[0].convert_to)

    def test_native_audio_copy_is_not_offered_as_a_conversion(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Sound", "ext": "mp3", "vcodec": "none", "acodec": "mp3"}, ("audio",),
        )
        status, _ = self.menu([self.inspected.url, "1", ""])
        self.assertEqual(status, 0)
        self.assertIn("1. Original MP3 (recommended)", self.output.getvalue())
        self.assertNotIn("2. MP3 audio", self.output.getvalue())
        self.assertIsNone(self.download.call_args.args[0].convert_to)

    def test_native_image_copy_is_hidden_but_other_format_remains(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Image"}, ("image",), image_extension="png",
        )
        status, _ = self.menu([self.inspected.url, "2", ""])
        self.assertEqual(status, 0)
        self.assertIn("1. Original PNG (recommended)", self.output.getvalue())
        self.assertNotIn("PNG image", self.output.getvalue())
        self.assertEqual(self.download.call_args.args[0].convert_to, "jpg")

    def test_explicit_native_copy_flag_remains_supported(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Sound", "ext": "mp3", "vcodec": "none", "acodec": "mp3"}, ("audio",),
        )
        self.assertEqual(main([self.inspected.url, "--convert", "mp3"]), 0)
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp3")

    def test_mp4_choice_explains_compatibility_conversion(self):
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Video", "ext": "mp4", "vcodec": "vp9", "acodec": "opus"}, ("video",),
        )
        status, _ = self.menu([self.inspected.url, "2", ""])
        self.assertEqual(status, 0)
        self.assertIn("Original MP4 (recommended)", self.output.getvalue())
        self.assertIn("MP4 video (H.264 / AAC)", self.output.getvalue())
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp4")

    def test_size_units_are_explicit_and_decimal_or_binary(self):
        for value, expected in (("100MB", 100_000_000), ("100MiB", 104_857_600), ("1.5 GB", 1_500_000_000), ("2KiB", 2048), ("1B", 1)):
            with self.subTest(value=value):
                self.assertEqual(_parse_size(value), expected)

    def test_optional_limits_reach_the_download_request(self):
        self.assertEqual(main([self.inspected.url, "--max-height", "720", "--max-size", "100MiB"]), 0)
        request = self.download.call_args.args[0]
        self.assertEqual(request.max_height, 720)
        self.assertEqual(request.max_size, 104_857_600)

    def test_invalid_limits_fail_before_network_access(self):
        for options in (("--max-height", "0"), ("--max-height", "-2"), ("--max-height", "720p"), ("--max-size", "0MB"), ("--max-size", "100"), ("--max-size", "NaNMB"), ("--max-size", "0.5B")):
            with self.subTest(options=options), self.assertRaises(SystemExit) as result:
                main([self.inspected.url, *options])
            self.assertEqual(result.exception.code, 2)
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()

    def test_height_limit_does_not_silently_resize_static_images_or_audio(self):
        for kind in ("audio", "image"):
            with self.subTest(kind=kind):
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                self.assertEqual(main([self.inspected.url, "--max-height", "720"]), 1)
                self.download.assert_not_called()

    def test_original_animation_exceeding_height_is_replaced_by_real_resize_choices(self):
        with io.BytesIO() as encoded:
            frames = [Image.new("RGB", (100, 100), color) for color in ("red", "blue")]
            frames[0].save(encoded, "GIF", save_all=True, append_images=frames[1:], duration=100)
            data = encoded.getvalue()
        self.inspect_media.return_value = MediaInspection(
            self.inspected.url, {"title": "Animation", "height": 100, "duration": 0.2}, ("image",),
            image_data=data, animated=True, image_extension="gif",
        )
        status, _ = self.menu([self.inspected.url, "1", ""], ["--max-height", "50"])
        self.assertEqual(status, 0)
        self.assertNotIn("Original GIF", self.output.getvalue())
        self.assertIn("1. GIF animation (recommended)", self.output.getvalue())
        self.assertEqual(self.download.call_args.args[0].convert_to, "gif")

    def test_raw_image_exceeding_source_cap_fails_before_choices(self):
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Image"}, ("image",), image_data=self.gif_data)
        self.assertEqual(main([self.inspected.url, "--max-size", "1B"]), 1)
        self.assertIn("source file exceeds --max-size", self.errors.getvalue())
        self.download.assert_not_called()

    def page_with_assets(self):
        assets = (
            MediaInspection("https://example.com/a.gif", {"title": "Actual animation"}, ("image",), image_data=self.gif_data, animated=True, image_extension="gif"),
            MediaInspection("https://example.com/b.jpg", {"title": "Second picture"}, ("image",), image_extension="jpg"),
        )
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Page"}, ("image",), alternatives=assets)
        return assets

    def test_multiple_page_assets_require_selection_before_format_menu(self):
        assets = self.page_with_assets()
        status, _ = self.menu([self.inspected.url, "wrong", "2", "1", ""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_called_once()
        request = self.download.call_args.args[0]
        self.assertEqual(request.url, assets[1].url)
        self.assertIs(self.download.call_args.kwargs["inspected"], assets[1])
        self.assertIn("1. Actual animation [Animation GIF, 2×2]", self.output.getvalue())
        self.assertIn("2. Second picture [Image JPG]", self.output.getvalue())
        self.assertIsNone(request.convert_to)

    def test_multiple_asset_menu_can_return_without_downloading(self):
        self.page_with_assets()
        status, _ = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.download.assert_not_called()

    def test_direct_multiple_asset_command_requires_explicit_item(self):
        self.page_with_assets()
        self.assertEqual(main([self.inspected.url]), 1)
        self.assertIn("--item NUMBER", self.errors.getvalue())
        self.assertIn("Actual animation", self.output.getvalue())
        self.download.assert_not_called()

    def test_direct_item_selects_and_converts_real_animation(self):
        assets = self.page_with_assets()
        self.assertEqual(main([self.inspected.url, "--item", "1", "--convert", "mp4"]), 0)
        request = self.download.call_args.args[0]
        self.assertEqual(request.url, assets[0].url)
        self.assertEqual(request.media_format, "image")
        self.assertEqual(request.convert_to, "mp4")

    def test_out_of_range_item_cannot_start_download(self):
        self.page_with_assets()
        self.assertEqual(main([self.inspected.url, "--item", "3"]), 1)
        self.assertIn("from 1 to 2", self.errors.getvalue())
        self.download.assert_not_called()

    def test_single_asset_item_one_is_supported_and_other_values_fail(self):
        self.assertEqual(main([self.inspected.url, "--item", "2"]), 1)
        self.download.assert_not_called()
        self.assertEqual(main([self.inspected.url, "--item", "1"]), 0)
        self.download.assert_called_once()

    def test_nonpositive_item_fails_before_inspection(self):
        for item in ("0", "-1", "1.5", "one"):
            with self.subTest(item=item), self.assertRaises(SystemExit) as error:
                main([self.inspected.url, "--item", item])
            self.assertEqual(error.exception.code, 2)
        self.inspect_media.assert_not_called()

    def test_similar_page_assets_show_dimensions_and_only_needed_filenames(self):
        assets = (
            MediaInspection(self.inspected.url, {"title": "Gallery", "image_url": "https://example.com/red.gif"}, ("image",), image_data=self.gif_data, animated=True, image_extension="gif"),
            MediaInspection(self.inspected.url, {"title": "Gallery", "image_url": "https://example.com/blue.gif"}, ("image",), image_data=self.gif_data, animated=True, image_extension="gif"),
            MediaInspection("https://example.com/video.mp4", {"title": "Gallery", "width": 480, "height": 270, "ext": "mp4", "vcodec": "h264", "acodec": "aac"}, ("video",)),
            MediaInspection("https://example.com/other.mp4", {"title": "Other video", "formats": [
                {"width": 320, "height": 180, "ext": "mp4", "vcodec": "h264", "acodec": "aac"},
                {"width": 640, "height": 360, "ext": "mp4", "vcodec": "h264", "acodec": "aac"},
            ]}, ("video",)),
        )
        self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Gallery"}, ("image", "video"), alternatives=assets)
        status, _ = self.menu([self.inspected.url, "0", ""])
        self.assertEqual(status, 0)
        self.assertIn("1. Gallery [Animation GIF, 2×2] - red.gif", self.output.getvalue())
        self.assertIn("2. Gallery [Animation GIF, 2×2] - blue.gif", self.output.getvalue())
        self.assertIn("3. Gallery [Video MP4, 480×270]", self.output.getvalue())
        self.assertIn("4. Other video [Video MP4, up to 640×360]", self.output.getvalue())
        self.assertNotIn(" - video.mp4", self.output.getvalue())
        self.download.assert_not_called()

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
        self.assertIn("scripts/install.cmd", self.output.getvalue())

    @patch("universal_downloader.cli.find_ffmpeg", return_value=Path("."))
    @patch("universal_downloader.cli.find_js_runtime", return_value=("node", Path("node.exe")))
    @patch("universal_downloader.cli.compatibility_issues", return_value=["yt-dlp version is untested."])
    def test_doctor_checks_dependency_adapter_compatibility(self, issues, runtime, ffmpeg):
        self.assertEqual(main(["--doctor"]), 1)
        self.assertIn("Dependency compatibility", self.output.getvalue())
        self.assertIn("yt-dlp version is untested", self.output.getvalue())

    @patch("universal_downloader.cli.sys.frozen", True, create=True)
    @patch("universal_downloader.cli.find_ffmpeg", return_value=None)
    @patch("universal_downloader.cli.find_js_runtime", return_value=None)
    def test_frozen_doctor_points_to_complete_release_instead_of_source_installer(self, runtime, ffmpeg):
        self.assertEqual(main(["--doctor"]), 1)
        self.assertIn("unidown.exe --install-tools", self.output.getvalue())
        self.assertNotIn("scripts/install.cmd", self.output.getvalue())

    def batch_file(self, contents):
        path = Path(self.preferences_folder.name) / "links.txt"
        path.write_text(contents, encoding="utf-8-sig")
        return str(path)

    def queue_inspections(self, *, kinds=("video",)):
        self.inspect_media.side_effect = lambda url, **kwargs: MediaInspection(url, {"title": "Queue item"}, kinds)

    def test_batch_validates_all_entries_before_network_and_rejects_positional_url(self):
        filename = self.batch_file("https://example.com/a\nnot a URL\n")
        self.assertEqual(main(["--batch", filename]), 1)
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()
        with self.assertRaises(SystemExit) as error:
            main([self.inspected.url, "--batch", filename])
        self.assertEqual(error.exception.code, 2)

    def test_automatic_batch_deduplicates_and_summarizes_saved_existing_and_failures(self):
        filename = self.batch_file("# queue\nhttps://example.com/a\nhttps://example.com/b\nhttps://example.com/a\nhttps://example.com/c\n")
        self.queue_inspections()
        self.download.side_effect = [DownloadResult("a", Path("a.mp4")), DownloadResult("b", Path("b.mp4"), True), DownloadError("Network unavailable")]
        self.assertEqual(main(["--batch", filename]), 1)
        self.assertEqual(self.inspect_media.call_count, 3)
        self.assertEqual([call.args[0].url for call in self.download.call_args_list], ["https://example.com/a", "https://example.com/b", "https://example.com/c"])
        self.assertIn("completed 1 | already 1 | failed 1 | skipped 0", self.output.getvalue())

    def test_queue_skips_incompatible_content_but_counts_network_failures(self):
        filename = self.batch_file("https://example.com/image\nhttps://example.com/audio\nhttps://example.com/unavailable")
        self.inspect_media.side_effect = [MediaInspection("https://example.com/image", {"title": "Image"}, ("image",)), MediaInspection("https://example.com/audio", {"title": "Audio"}, ("audio",)), DownloadError("Connection failed")]
        self.assertEqual(main(["--batch", filename, "--convert", "mp3"]), 1)
        self.download.assert_called_once()
        self.assertEqual(self.download.call_args.args[0].media_format, "audio")
        self.assertEqual(self.download.call_args.args[0].convert_to, "mp3")
        self.assertTrue(all(call.kwargs["media_format"] is None for call in self.inspect_media.call_args_list))
        self.assertIn("completed 1 | already 0 | failed 1 | skipped 1", self.output.getvalue())

    def test_interactive_pasted_queue_requires_confirmation_and_downloads_nothing_when_declined(self):
        status, _ = self.menu(["https://example.com/a https://example.com/b", "", "n", ""])
        self.assertEqual(status, 0)
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()
        self.assertIn("Queue: 2 links", self.output.getvalue())

    def test_interactive_batch_command_selects_original_once_for_the_queue(self):
        filename = self.batch_file("https://example.com/a\nhttps://example.com/b")
        self.queue_inspections()
        status, user_input = self.menu([f'batch "{filename}"', "", "y", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 4)
        self.assertEqual(self.download.call_count, 2)
        self.assertTrue(all(call.args[0].convert_to is None for call in self.download.call_args_list))

    def test_interactive_queue_custom_format_is_selected_once_and_inspection_is_reused(self):
        self.queue_inspections(kinds=("video", "audio"))
        status, user_input = self.menu(["https://example.com/a https://example.com/b", "2", "2", "2", "yes", ""])
        self.assertEqual(status, 0)
        self.assertEqual(user_input.call_count, 6)
        self.assertEqual(self.inspect_media.call_count, 2)
        self.assertEqual(self.download.call_count, 2)
        self.assertTrue(all(call.args[0].media_format == "audio" and call.args[0].convert_to == "mp3" for call in self.download.call_args_list))

    def test_custom_queue_uses_first_verifiable_item_and_counts_prior_failure_once(self):
        urls = ["https://example.com/unavailable", "https://example.com/good", "https://example.com/next"]
        self.inspect_media.side_effect = [DownloadError("First link is unavailable"), *[MediaInspection(url, {"title": "Good audio"}, ("video", "audio")) for url in urls[1:]]]
        status, answers = self.menu([" ".join(urls), "2", "2", "2", "yes", ""])
        self.assertEqual(status, 1)
        self.assertEqual(answers.call_count, 6)
        self.assertEqual([call.args[0] for call in self.inspect_media.call_args_list], urls)
        self.assertEqual([call.args[0].url for call in self.download.call_args_list], urls[1:])
        self.assertTrue(all(call.args[0].media_format == "audio" and call.args[0].convert_to == "mp3" for call in self.download.call_args_list))
        self.assertIn("completed 2 | already 0 | failed 1 | skipped 0", self.output.getvalue())
        self.assertEqual(self.errors.getvalue().count("First link is unavailable"), 1)

    def test_custom_queue_all_preflight_failures_report_summary_without_downloading(self):
        self.inspect_media.side_effect = [DownloadError("First unavailable"), DownloadError("Second unavailable")]
        status, answers = self.menu(["https://example.com/a https://example.com/b", "2", ""])
        self.assertEqual(status, 1)
        self.assertEqual(answers.call_count, 3)
        self.assertEqual(self.inspect_media.call_count, 2)
        self.download.assert_not_called()
        self.assertIn("completed 0 | already 0 | failed 2 | skipped 0", self.output.getvalue())

    def test_canceling_custom_queue_after_failed_preflight_never_downloads(self):
        self.inspect_media.side_effect = [DownloadError("First unavailable"), MediaInspection("https://example.com/good", {"title": "Good"}, ("video", "audio"))]
        status, _ = self.menu(["https://example.com/a https://example.com/good https://example.com/later", "2", "0", ""])
        self.assertEqual(status, 0)
        self.assertEqual(self.inspect_media.call_count, 2)
        self.download.assert_not_called()

    def test_automatic_queue_never_silently_selects_an_ambiguous_page(self):
        filename = self.batch_file(self.inspected.url)
        self.page_with_assets()
        self.assertEqual(main(["--batch", filename]), 1)
        self.download.assert_not_called()
        self.assertIn("--item NUMBER", self.errors.getvalue())

    def test_interrupting_queue_never_inspects_or_downloads_later_links(self):
        filename = self.batch_file("https://example.com/a\nhttps://example.com/b")
        self.queue_inspections()
        self.download.side_effect = KeyboardInterrupt
        self.assertEqual(main(["--batch", filename]), 130)
        self.download.assert_called_once()
        self.inspect_media.assert_called_once()
        self.assertIn("skipped 2", self.output.getvalue())

    def test_failed_queue_status_survives_end_of_interactive_session(self):
        self.queue_inspections()
        self.download.side_effect = DownloadError("Network failed")
        for ending in ("", EOFError):
            with self.subTest(ending=ending):
                status, _ = self.menu(["https://example.com/a https://example.com/b", "", "y", ending])
                self.assertEqual(status, 1)

    def test_explicit_output_and_limits_are_remembered_without_an_extra_prompt(self):
        path = Path(self.preferences_folder.name) / "media"
        self.assertEqual(main([self.inspected.url, "--output", str(path), "--max-height", "720", "--max-size", "1MB"]), 0)
        self.assertEqual(load_preferences(), {"output": str(path.resolve()), "max_height": 720, "max_size": 1_000_000})
        self.assertEqual(main([self.inspected.url]), 0)
        request = self.download.call_args.args[0]
        self.assertEqual(request.output_dir, path.resolve())
        self.assertEqual(request.max_height, 720)
        self.assertEqual(request.max_size, 1_000_000)

    def test_saved_height_is_not_applied_to_audio_or_still_images(self):
        save_preferences({"max_height": 720, "max_size": 1024})
        for kind in ("audio", "image"):
            with self.subTest(kind=kind):
                self.inspect_media.return_value = MediaInspection(self.inspected.url, {"title": "Example"}, (kind,))
                self.assertEqual(main([self.inspected.url]), 0)
                self.assertIsNone(self.download.call_args.args[0].max_height)
                self.assertEqual(self.download.call_args.args[0].max_size, 1024)
        self.assertIn("Saved height limit does not apply", self.output.getvalue())

    def test_no_preferences_skips_reading_and_does_not_modify_saved_settings(self):
        save_preferences({"max_height": 720, "max_size": 1024})
        original = preferences_path().read_bytes()
        with patch("universal_downloader.cli.load_preferences") as reader, patch("universal_downloader.cli.save_preferences") as writer:
            self.assertEqual(main([self.inspected.url, "--no-preferences", "--output", "other"]), 0)
            reader.assert_not_called()
            writer.assert_not_called()
        self.assertIsNone(self.download.call_args.args[0].max_height)
        self.assertIsNone(self.download.call_args.args[0].max_size)
        self.assertEqual(preferences_path().read_bytes(), original)

    def test_save_show_and_reset_preferences_are_standalone_commands(self):
        self.assertEqual(main(["--save-preferences", "--max-height", "720"]), 0)
        self.assertEqual(main(["--show-preferences"]), 0)
        self.assertIn("Max height: 720", self.output.getvalue())
        self.assertEqual(main(["--reset-preferences"]), 0)
        self.assertEqual(load_preferences(), {})
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()

    def test_automatic_preference_write_error_never_blocks_a_download(self):
        with patch("universal_downloader.cli.save_preferences", side_effect=DownloadError("Preferences unavailable. Use --no-preferences.")):
            self.assertEqual(main([self.inspected.url, "--output", "media"]), 0)
            self.download.assert_called_once()
            self.assertIn("--no-preferences", self.errors.getvalue())
            self.assertEqual(main(["--save-preferences"]), 1)

    def test_corrupt_preferences_fall_back_to_defaults_for_regular_downloads(self):
        preferences_path().write_text("not JSON", encoding="utf-8")
        self.assertEqual(main([self.inspected.url]), 0)
        self.assertEqual(self.download.call_args.args[0].output_dir, Path("downloads"))
        self.assertIn("--reset-preferences", self.errors.getvalue())

    def test_resume_events_do_not_expose_internal_partial_paths(self):
        progress = _Progress()
        progress({"status": "resuming", "filename": "private-internal-path"})
        progress({"status": "resume_saved", "filename": "private-internal-path"})
        self.assertIn("Resuming...", self.output.getvalue())
        self.assertIn("Paste the same link to continue", self.output.getvalue())
        self.assertNotIn("private-internal-path", self.output.getvalue())

    def test_clear_resume_is_isolated_and_calls_only_explicit_cleanup(self):
        cleanup = Mock(return_value=3)
        with patch.dict("sys.modules", {"universal_downloader.resume": SimpleNamespace(clear_resume=cleanup)}):
            self.assertEqual(main(["--clear-resume"]), 0)
        cleanup.assert_called_once_with()
        self.assertIn("Cleared 3 inactive partial downloads. Active downloads were kept.", self.output.getvalue())
        self.inspect_media.assert_not_called()
        self.download.assert_not_called()
        self.assertFalse(preferences_path().exists())
        with self.assertRaises(SystemExit):
            main([self.inspected.url, "--clear-resume"])


if __name__ == "__main__":
    unittest.main()
