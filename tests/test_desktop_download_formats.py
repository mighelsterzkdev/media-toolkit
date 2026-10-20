"""Desktop URL conversion uses the same real outputs as local files."""

from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from universal_downloader.desktop_download_formats import available_download_conversions, validate_download_conversion
from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.download_conversion import convert_downloaded_file
from universal_downloader.downloader import DownloadRequest, DownloadResult, download
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection


def picture(*, animated=False, size=(32, 16)):
    stream = BytesIO()
    with Image.new("RGBA", size, (20, 80, 140, 100)) as first:
        if animated:
            with Image.new("RGBA", size, (220, 30, 60, 255)) as second:
                first.save(stream, "GIF", save_all=True, append_images=[second], duration=[100, 200], loop=2)
        else:
            first.save(stream, "PNG")
    return MediaInspection("https://example.com/image", {"id": "image", "title": "Example", "image_url": "https://example.com/image"},
                           ("image",), image_data=stream.getvalue(), animated=animated, image_extension="gif" if animated else "png")


class DesktopDownloadFormatTests(unittest.TestCase):
    def test_static_image_uses_real_encoders_and_only_fitting_icon_sizes(self):
        options = available_download_conversions(picture(), "image")
        self.assertTrue({"jpg", "webp", "avif", "bmp", "tiff", "tga", "gif", "jp2"}.issubset(options), options)
        self.assertNotIn("png", options)
        self.assertNotIn("ico", options)
        self.assertNotIn("mp3", options)
        self.assertIn("white background", options["jpg"])
        self.assertIn("ico", available_download_conversions(picture(size=(32, 32)), "image"))
        self.assertNotIn("ico", available_download_conversions(picture(size=(300, 300)), "image"))

    def test_real_animations_never_offer_still_image_outputs(self):
        options = available_download_conversions(picture(animated=True), "image")
        self.assertTrue({"apng", "webp"}.issubset(options), options)
        self.assertFalse({"png", "jpg", "gif", "tiff"} & options.keys())
        with self.assertRaises(DownloadError):
            validate_download_conversion(picture(animated=True), "image", "jpg")

    def test_video_and_audio_choices_match_installed_encoders_and_height_policy(self):
        encoders = frozenset(("libx264", "aac", "libvpx-vp9", "libopus", "libmp3lame", "pcm_s24le", "flac", "libvorbis", "pcm_s24be"))
        muxers = frozenset(("mp4", "matroska", "mov", "webm", "mp3", "wav", "flac", "ipod", "adts", "opus", "ogg", "aiff"))
        inspected = MediaInspection("https://example.com/v", {"ext": "mov", "height": 720, "vcodec": "h264", "acodec": "aac"}, ("video", "audio"), heights=(720,))
        with patch("universal_downloader.local_media._tools", return_value=(Path("ffmpeg"), Path("ffprobe"))), patch("universal_downloader.local_media._capabilities", return_value=(encoders, muxers)):
            self.assertEqual(set(available_download_conversions(inspected, "video")), {"mp4", "mkv", "webm"})
            self.assertEqual(set(available_download_conversions(inspected, "video", max_height=480)), {"mp4"})
            audio = available_download_conversions(inspected, "audio")
            self.assertEqual(set(audio), {"mp3", "wav", "flac", "m4a", "aac", "opus", "ogg", "aiff"})
            pseudo = replace(inspected, kinds=("image",), animated=True, animation_video=True)
            with patch("universal_downloader.desktop_download_formats.available_conversions", return_value=("gif", "mp4")):
                options = available_download_conversions(pseudo, "image")
            self.assertEqual(set(options), {"mp4", "mkv", "webm", "gif"})
            self.assertFalse({"jpg", "png", "apng", "webp"} & options.keys())

    def test_missing_tools_hide_only_tool_dependent_outputs(self):
        with patch("universal_downloader.local_media._tools", side_effect=DownloadError("missing")):
            self.assertIn("webp", available_download_conversions(picture(), "image"))
            media = MediaInspection("https://example.com/v", {}, ("video",))
            self.assertEqual(available_download_conversions(media, "video"), {})

    def test_height_options_ignore_unplayable_drm_and_storyboard_sources(self):
        inspected = MediaInspection("https://example.com/video", {"formats": [
            {"height": 480, "vcodec": "h264", "acodec": "aac", "has_drm": True},
            {"height": 240, "vcodec": "images", "ext": "mhtml", "protocol": "mhtml"},
            {"height": 1080, "vcodec": "h264", "acodec": "aac", "ext": "mp4"},
        ]}, ("video", "audio"), heights=(1080,))
        encoders = frozenset(("libx264", "aac", "libvpx-vp9", "libopus"))
        muxers = frozenset(("mp4", "matroska", "mov", "webm"))
        with patch("universal_downloader.local_media._tools", return_value=(Path("ffmpeg"), Path("ffprobe"))), patch("universal_downloader.local_media._capabilities", return_value=(encoders, muxers)):
            self.assertEqual(set(available_download_conversions(inspected, "video", max_height=480)), {"mp4"})


class DownloadConversionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_desktop_download_produces_actual_new_formats_and_reuses_completed_cache(self):
        inspected = picture()
        original = self.root / "source.png"
        original.write_bytes(inspected.image_data)

        def retrieve(url, kind, output_dir, *args, **kwargs):
            path = output_dir / "source.png"
            shutil.copyfile(original, path)
            return DownloadResult("Example", path)

        with patch("universal_downloader.downloader._download_original", side_effect=retrieve) as retrieve_mock:
            for target, expected in (("webp", "WEBP"), ("tiff", "TIFF"), ("avif", "AVIF"), ("jp2", "JPEG2000")):
                request = DownloadRequest(inspected.url, "image", self.root / "downloads", target, desktop_conversion=True)
                result = download(request, inspected=inspected)
                self.assertFalse(result.already_exists)
                with Image.open(result.path) as image:
                    image.load()
                    self.assertEqual(image.format, expected)
                    self.assertEqual(image.size, (32, 16))
                cached = download(request, inspected=inspected)
                self.assertTrue(cached.already_exists)
                self.assertEqual(cached.path, result.path)
                compacted = download(request, inspected=replace(inspected, image_data=None))
                self.assertTrue(compacted.already_exists)
                self.assertEqual(compacted.path, result.path)
            self.assertEqual(retrieve_mock.call_count, 4)
        self.assertEqual(original.read_bytes(), inspected.image_data)
        self.assertFalse(any(self.root.joinpath("downloads").glob(".unidown-*")))

    def test_legacy_cli_keeps_its_existing_conversion_contract(self):
        inspected = picture()
        with patch("universal_downloader.downloader._download_original") as retrieve:
            with self.assertRaisesRegex(DownloadError, "Unsupported conversion"):
                download(DownloadRequest(inspected.url, "image", self.root / "downloads", "webp"), inspected=inspected)
            retrieve.assert_not_called()

    def test_animation_output_preserves_frames_timing_and_loop(self):
        inspected = picture(animated=True)
        source = self.root / "source.gif"
        source.write_bytes(inspected.image_data)
        output = self.root / "converted.apng"
        self.assertTrue(convert_downloaded_file(source, "apng", output, inspected=inspected, kind="image"))
        with Image.open(output) as result:
            self.assertTrue(result.is_animated)
            self.assertEqual(result.n_frames, 2)
            self.assertEqual(result.info["loop"], 3)
            durations = []
            for index in range(result.n_frames):
                result.seek(index)
                durations.append(result.info["duration"])
            self.assertEqual(durations, [100, 200])

    def test_failed_output_limit_does_not_publish_or_change_source(self):
        inspected = picture()
        source = self.root / "source.png"
        source.write_bytes(inspected.image_data)
        output = self.root / "converted.tiff"
        with self.assertRaisesRegex(DownloadError, "exceeds"):
            convert_downloaded_file(source, "tiff", output, inspected=inspected, kind="image", max_size=1)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), inspected.image_data)
        self.assertFalse(list(self.root.glob(".desktop-convert-*")))

    def test_resized_webp_keeps_frames_timing_and_loop_in_gif_and_mp4(self):
        source = self.root / "source.webp"
        with Image.new("RGBA", (32, 16), (10, 80, 170, 255)) as first, Image.new("RGBA", (32, 16), (230, 40, 20, 255)) as second:
            first.save(source, "WEBP", save_all=True, append_images=[second], duration=[100, 200], loop=3, lossless=True)
        original = source.read_bytes()
        inspected = replace(picture(animated=True), image_data=original, image_extension="webp")
        options = available_download_conversions(inspected, "image", max_height=8)
        self.assertIn("gif", options)
        self.assertNotIn("15 frames", options["gif"])
        output = self.root / "resized.gif"
        self.assertTrue(convert_downloaded_file(source, "gif", output, inspected=inspected, kind="image", max_height=8))
        with Image.open(output) as actual:
            self.assertEqual(actual.size, (16, 8))
            self.assertEqual(actual.n_frames, 2)
            self.assertEqual(actual.info["loop"], 2)
            durations = []
            colors = []
            for index in range(actual.n_frames):
                actual.seek(index)
                durations.append(actual.info["duration"])
                colors.append(actual.convert("RGB").getpixel((8, 4)))
            self.assertEqual(durations, [100, 200])
            self.assertNotEqual(colors[0], colors[1])
        tools = find_ffmpeg()
        if tools is not None:
            video = self.root / "resized.mp4"
            self.assertTrue(convert_downloaded_file(source, "mp4", video, inspected=inspected, kind="image", max_height=8))
            ffprobe = tools / ("ffprobe.exe" if (tools / "ffprobe.exe").is_file() else "ffprobe")
            probed = subprocess.run([str(ffprobe), "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(video)],
                                    check=True, capture_output=True, text=True, timeout=15, **hidden_process_options())
            metadata = json.loads(probed.stdout)
            stream = metadata["streams"][0]
            self.assertEqual((stream["width"], stream["height"]), (16, 8))
            self.assertEqual(int(stream["nb_read_frames"]), 2)
            self.assertAlmostEqual(float(metadata["format"]["duration"]), .3, places=2)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".desktop-convert-*")))

    def test_animated_thumbnail_does_not_reclassify_selected_audio_or_video(self):
        thumbnail = picture(animated=True)
        source = self.root / "source.media"
        source.write_bytes(b"private checked media")
        for kind, target in (("video", "mkv"), ("audio", "flac")):
            inspected = replace(thumbnail, kinds=("video", "audio", "image"))
            checked = {"media_type": kind, "source": str(source), "signature": {}, "targets": [target], "error": None}

            def convert(media, requested, directory, **kwargs):
                output = directory / f"output.{requested}"
                output.write_bytes(b"finished conversion")
                return output

            with self.subTest(kind=kind), patch("universal_downloader.local_media.inspect_local_files", return_value=[checked]), patch("universal_downloader.local_media.convert_local_file", side_effect=convert):
                output = self.root / f"completed.{target}"
                self.assertTrue(convert_downloaded_file(source, target, output, inspected=inspected, kind=kind))
                self.assertEqual(output.read_bytes(), b"finished conversion")

    def test_native_gif_resize_works_without_ffmpeg(self):
        inspected = picture(animated=True)
        source = self.root / "source.gif"
        source.write_bytes(inspected.image_data)
        with patch("universal_downloader.local_media._tools", side_effect=DownloadError("no tools")):
            options = available_download_conversions(inspected, "image", max_height=8)
            self.assertEqual(set(options), {"gif"})
            output = self.root / "resized.gif"
            self.assertTrue(convert_downloaded_file(source, "gif", output, inspected=inspected, kind="image", max_height=8))
        with Image.open(output) as result:
            self.assertEqual(result.size, (16, 8))
            self.assertEqual(result.n_frames, 2)

    def test_changed_media_category_is_rejected_before_conversion(self):
        inspected = picture(animated=True)
        source = self.root / "replacement.png"
        source.write_bytes(picture().image_data)
        with self.assertRaisesRegex(DownloadError, "does not match"):
            convert_downloaded_file(source, "webp", self.root / "output.webp", inspected=inspected, kind="image")


if __name__ == "__main__":
    unittest.main()
