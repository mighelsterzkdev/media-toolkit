"""Real media round trips, frame preservation, and local snapshot boundaries."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.errors import DownloadError
from universal_downloader.local_media import convert_local_file, inspect_local_files


class LocalMediaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-toolkit-local-media-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "output"

    def animation(self, native="PNG", name="animation.png", *, loop=0):
        source = self.root / name
        with Image.new("RGBA", (18, 14), (255, 0, 0, 255)) as first, Image.new("RGBA", (18, 14), (0, 0, 255, 255)) as second:
            first.save(source, native, save_all=True, append_images=[second], duration=[100, 200], loop=loop, lossless=True, exact=True)
        return source

    def checked(self, source):
        result = inspect_local_files([source])[0]
        self.assertIsNone(result["error"], result)
        return result

    def audio(self, name="source.audio"):
        source = self.root / name
        with wave.open(str(source), "wb") as encoded:
            encoded.setnchannels(1)
            encoded.setsampwidth(2)
            encoded.setframerate(44_100)
            encoded.writeframes(bytes(22_050 * 2))
        return source

    def tools(self):
        directory = find_ffmpeg()
        if directory is None:
            self.skipTest("FFmpeg and ffprobe are not installed")
        suffix = ".exe" if (directory / "ffmpeg.exe").is_file() else ""
        return directory / f"ffmpeg{suffix}", directory / f"ffprobe{suffix}"

    def run_tool(self, arguments):
        executable, _probe = self.tools()
        subprocess.run([str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", *map(str, arguments)],
                       capture_output=True, check=True, timeout=30, **hidden_process_options())

    def probe(self, source):
        _encoder, executable = self.tools()
        return json.loads(subprocess.run([str(executable), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
                                         capture_output=True, text=True, check=True, timeout=15, **hidden_process_options()).stdout)

    def video(self, *, audio=True, native="mkv", codec="ffv1", extra_audio=False):
        source = self.root / f"video.{native}"
        options = ["-f", "lavfi", "-i", "color=c=red:size=34x26:rate=10:duration=0.4"]
        if audio:
            options += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.4", "-map", "0:v:0", "-map", "1:a:0"]
            if extra_audio:
                options += ["-map", "1:a:0"]
        options += ["-c:v", codec, "-c:a", "aac", "-threads", "1", source]
        self.run_tool(options)
        return source

    def assert_clean(self):
        self.assertEqual(list(self.output.glob(".unidown-convert-*")), [])

    def test_static_images_keep_existing_independent_engine(self):
        source = self.root / "photo.fake"
        with Image.new("RGBA", (20, 16), (30, 70, 100, 128)) as image:
            image.save(source, "PNG")
        with patch("universal_downloader.local_media.find_ffmpeg", return_value=None):
            checked = self.checked(source)
            self.assertEqual(checked["media_type"], "image")
            self.assertFalse(checked["requires_tools"])
            self.assertIn("bmp", checked["targets"])
            converted = convert_local_file(checked, "bmp", self.output, "#000000")
        with Image.open(converted) as image:
            self.assertEqual(image.format, "BMP")
            self.assertEqual(image.size, (20, 16))

    def test_gif_apng_and_webp_preserve_frames_timing_and_loop_offline(self):
        for encoder, native in (("PNG", "apng"), ("GIF", "gif"), ("WEBP", "webp")):
            with self.subTest(source=encoder):
                source = self.animation(encoder, f"source-{native}.unknown")
                original = source.read_bytes()
                with patch("universal_downloader.local_media.find_ffmpeg", return_value=None):
                    checked = self.checked(source)
                    self.assertEqual(checked["format"], native)
                    self.assertEqual(checked["media_type"], "animation")
                    self.assertEqual(checked["pages"], 2)
                    self.assertAlmostEqual(checked["duration"], .3)
                    self.assertFalse(checked["requires_tools"])
                    self.assertEqual(set(checked["targets"]), {"gif", "apng", "webp"} - {native})
                    for target in checked["targets"]:
                        output = convert_local_file(checked, target, self.output)
                        actual = self.checked(output)
                        self.assertEqual(actual["media_type"], "animation")
                        self.assertEqual(actual["pages"], 2)
                        self.assertAlmostEqual(actual["duration"], .3)
                        with Image.open(output) as animation:
                            self.assertEqual(animation.info.get("loop"), 0)
                            animation.seek(0)
                            self.assertEqual(animation.convert("RGB").getpixel((4, 4)), (255, 0, 0))
                            animation.seek(1)
                            self.assertEqual(animation.convert("RGB").getpixel((4, 4)), (0, 0, 255))
                self.assertEqual(source.read_bytes(), original)
        self.assert_clean()

    def test_animation_alpha_kept_in_lossless_outputs_and_gif_keeps_opaque_black(self):
        source = self.root / "transparent.png"
        with Image.new("RGBA", (18, 14), (0, 0, 0, 255)) as first, Image.new("RGBA", (18, 14), "blue") as second:
            first.putpixel((0, 0), (200, 40, 60, 0))
            second.putpixel((0, 0), (200, 40, 60, 0))
            first.save(source, "PNG", save_all=True, append_images=[second], duration=100, loop=0)
        checked = self.checked(source)
        self.assertTrue(checked["transparent"])
        for target in ("gif", "webp"):
            output = convert_local_file(checked, target, self.output)
            with Image.open(output) as animation:
                for index in range(2):
                    animation.seek(index)
                    with animation.convert("RGBA") as frame:
                        self.assertEqual(frame.getpixel((0, 0))[3], 0)
                        self.assertEqual(frame.getpixel((1, 0))[3], 255)

    def test_animation_snapshot_change_and_limits_do_not_leave_outputs(self):
        source = self.animation()
        checked = self.checked(source)
        before = source.stat()
        source.write_bytes(source.read_bytes() + b"changed")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(DownloadError, "changed after"):
            convert_local_file(checked, "gif", self.output)
        with patch("universal_downloader.local_animation.MAX_ANIMATION_PIXELS", 100):
            invalid = inspect_local_files([source])[0]
        self.assertIn("decoded pixel limit", invalid["error"])
        self.assertFalse(self.output.exists())

    def test_short_frame_timing_and_finite_playback_survive_lossless_animation_conversion(self):
        source = self.root / "fast.apng"
        with Image.new("RGBA", (10, 10), "red") as first, Image.new("RGBA", (10, 10), "blue") as second:
            first.save(source, "PNG", save_all=True, append_images=[second], duration=[3, 7], loop=3)
        checked = self.checked(source)
        self.assertAlmostEqual(checked["duration"], .01)
        output = convert_local_file(checked, "webp", self.output)
        self.assertAlmostEqual(self.checked(output)["duration"], .01)
        with Image.open(output) as animation:
            self.assertEqual(animation.info["loop"], 3)
        gif = convert_local_file(checked, "gif", self.output)
        with Image.open(gif) as animation:
            self.assertEqual(animation.info["loop"], 2)
        self.assertAlmostEqual(self.checked(gif)["duration"], .02)

    def test_apng_static_poster_is_not_encoded_as_an_extra_animation_frame(self):
        source = self.root / "poster.apng"
        with Image.new("RGBA", (10, 10), "green") as poster, Image.new("RGBA", (10, 10), "red") as first, Image.new("RGBA", (10, 10), "blue") as second:
            poster.save(source, "PNG", save_all=True, append_images=[first, second], default_image=True, duration=[100, 200], loop=0)
        checked = self.checked(source)
        self.assertEqual(checked["pages"], 2)
        output = convert_local_file(checked, "webp", self.output)
        self.assertEqual(self.checked(output)["pages"], 2)
        with Image.open(output) as animation:
            self.assertEqual(animation.convert("RGB").getpixel((4, 4)), (255, 0, 0))

    def test_multipage_documents_and_playlists_are_not_flattened_or_followed(self):
        source = self.root / "pages.tiff"
        with Image.new("RGB", (10, 10), "red") as first, Image.new("RGB", (10, 10), "blue") as second:
            first.save(source, "TIFF", save_all=True, append_images=[second])
        self.assertIn("multipage", inspect_local_files([source])[0]["error"])
        playlist = self.root / "playlist.m3u8"
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:1,\nhttps://example.invalid/media.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        checked = inspect_local_files([playlist])[0]
        self.assertTrue(checked["error"])
        self.assertEqual(checked["targets"], [])

    def test_missing_tools_is_actionable_but_does_not_break_other_files(self):
        source = self.audio()
        with patch("universal_downloader.local_media.find_ffmpeg", return_value=None):
            failed, animation = inspect_local_files([source, self.animation()])
        self.assertIn("Install the media tools", failed["error"])
        self.assertEqual(failed["targets"], [])
        self.assertIsNone(animation["error"])

    def test_all_offered_audio_encoders_write_real_identifiable_audio(self):
        self.tools()
        source = self.audio()
        original = sha256(source.read_bytes()).hexdigest()
        checked = self.checked(source)
        self.assertEqual(checked["media_type"], "audio")
        self.assertEqual(checked["format"], "wav")
        self.assertIsNone(checked["width"])
        self.assertTrue(checked["has_audio"])
        self.assertTrue(checked["requires_tools"])
        self.assertEqual(set(checked["targets"]), {"mp3", "flac", "m4a", "aac", "opus", "ogg", "aiff"})
        expected = {"mp3": "mp3", "flac": "flac", "m4a": "aac", "aac": "aac", "opus": "opus", "ogg": "vorbis", "aiff": "pcm_s24be"}
        for target in checked["targets"]:
            with self.subTest(target=target):
                output = convert_local_file(checked, target, self.output)
                metadata = self.probe(output)
                self.assertEqual(len(metadata["streams"]), 1)
                self.assertEqual(metadata["streams"][0]["codec_name"], expected[target])
                actual = self.checked(output)
                self.assertEqual(actual["media_type"], "audio")
                self.assertEqual(actual["format"], target)
                if target == "flac":
                    pcm = convert_local_file(actual, "wav", self.output)
                    self.assertEqual(self.probe(pcm)["streams"][0]["codec_name"], "pcm_s24le")
        self.assertEqual(sha256(source.read_bytes()).hexdigest(), original)
        self.assert_clean()

    def test_video_transcodes_all_containers_and_extracts_only_existing_audio(self):
        source = self.video(extra_audio=True)
        original = source.read_bytes()
        checked = self.checked(source)
        self.assertEqual(checked["media_type"], "video")
        self.assertEqual(checked["format"], "mkv")
        self.assertIn("other tracks", checked["target_notes"]["mp4"])
        self.assertEqual(set(checked["targets"]), {"mp4", "mov", "webm", "mp3", "wav", "flac", "m4a", "aac", "opus", "ogg", "aiff"})
        for target in ("mp4", "mov", "webm", "mp3"):
            with self.subTest(target=target):
                output = convert_local_file(checked, target, self.output)
                actual = self.checked(output)
                self.assertEqual(actual["format"], target)
                metadata = self.probe(output)
                if target == "mp3":
                    self.assertEqual(actual["media_type"], "audio")
                    self.assertEqual(len(metadata["streams"]), 1)
                else:
                    self.assertEqual(actual["media_type"], "video")
                    self.assertEqual(len(metadata["streams"]), 2)
                    self.assertEqual((actual["width"], actual["height"]), (34, 26))
        self.assertEqual(source.read_bytes(), original)
        self.assert_clean()

    def test_silent_video_has_no_audio_extraction_and_mkv_output_works(self):
        source = self.video(audio=False, native="mp4", codec="mpeg4")
        checked = self.checked(source)
        self.assertFalse(checked["has_audio"])
        self.assertEqual(set(checked["targets"]), {"mp4", "mkv", "mov", "webm"})
        normalized = convert_local_file(checked, "mp4", self.output)
        actual = self.checked(normalized)
        self.assertNotIn("mp4", actual["targets"])
        self.assertEqual(self.probe(normalized)["streams"][0]["codec_name"], "h264")
        mkv = convert_local_file(actual, "mkv", self.output)
        self.assertEqual(self.checked(mkv)["format"], "mkv")
        with self.assertRaisesRegex(DownloadError, "compatible"):
            convert_local_file(checked, "mp3", self.output)

    def test_common_video_inputs_are_detected_by_content_without_requiring_extensions(self):
        cases = (("avi", "mpeg4", "avi"), ("asf", "wmv2", "wmv"), ("flv", "flv1", "flv"),
                 ("mpegts", "mpeg2video", "mpegts"), ("mpeg", "mpeg2video", "mpeg"))
        for muxer, encoder, native in cases:
            with self.subTest(format=muxer):
                source = self.root / f"input-{muxer}.data"
                self.run_tool(["-f", "lavfi", "-i", "color=c=red:size=64x48:rate=25:duration=0.4",
                               "-c:v", encoder, "-threads", "1", "-an", "-f", muxer, source])
                checked = self.checked(source)
                self.assertEqual(checked["format"], native)
                self.assertEqual(checked["media_type"], "video")
                output = convert_local_file(checked, "mp4", self.output)
                self.assertEqual(self.checked(output)["format"], "mp4")

    def test_audio_cover_art_does_not_turn_an_audio_file_into_a_video(self):
        audio = self.audio()
        cover = self.root / "cover.jpg"
        with Image.new("RGB", (32, 32), "red") as image:
            image.save(cover, "JPEG")
        source = self.root / "album.m4a"
        self.run_tool(["-i", audio, "-i", cover, "-map", "0:a:0", "-map", "1:v:0", "-c:a", "aac",
                       "-c:v", "copy", "-disposition:v:0", "attached_pic", "-f", "ipod", source])
        checked = self.checked(source)
        self.assertEqual(checked["media_type"], "audio")
        self.assertNotIn("mp4", checked["targets"])
        output = convert_local_file(checked, "mp3", self.output)
        self.assertEqual(len(self.probe(output)["streams"]), 1)

    def test_multichannel_lossy_outputs_explicitly_downmix_to_stereo(self):
        source = self.root / "surround.wav"
        self.run_tool(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=5.1", "-t", "0.2", "-c:a", "pcm_s16le", source])
        checked = self.checked(source)
        for target in ("mp3", "m4a", "opus", "ogg"):
            with self.subTest(target=target):
                self.assertIn("Downmixes multichannel audio to stereo", checked["target_notes"][target])
                output = convert_local_file(checked, target, self.output)
                self.assertEqual(self.probe(output)["streams"][0]["channels"], 2)
        output = convert_local_file(checked, "flac", self.output)
        self.assertEqual(self.probe(output)["streams"][0]["channels"], 6)
        self.assertNotIn("Downmixes", checked["target_notes"]["flac"])

    def test_animation_mp4_encodes_every_source_frame_without_looping_forever(self):
        self.tools()
        source = self.animation("WEBP", "animated.webp")
        checked = self.checked(source)
        self.assertIn("mp4", checked["targets"])
        output = convert_local_file(checked, "mp4", self.output)
        actual = self.checked(output)
        self.assertEqual(actual["media_type"], "video")
        self.assertFalse(actual["has_audio"])
        self.assertAlmostEqual(actual["duration"], .3, delta=.04)
        self.assertEqual(int(self.probe(output)["streams"][0]["nb_frames"]), 2)
        self.assert_clean()

    def test_audio_copy_snapshot_change_cleanup_and_original_output_name_collision(self):
        self.tools()
        source = self.audio()
        checked = self.checked(source)
        saved = source.stat()
        source.write_bytes(source.read_bytes()[:-2] + b"xx")
        os.utime(source, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        with self.assertRaisesRegex(DownloadError, "changed after"):
            convert_local_file(checked, "flac", self.output)
        self.assertEqual(list(self.output.iterdir()), [])
        checked = self.checked(source)
        first = convert_local_file(checked, "flac", self.output)
        second = convert_local_file(checked, "flac", self.output)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assert_clean()

    def test_capabilities_hide_unavailable_encoders(self):
        self.tools()
        with patch("universal_downloader.local_media._capabilities", return_value=({"flac"}, {"flac"})):
            checked = self.checked(self.audio())
        self.assertEqual(checked["targets"], ["flac"])

    def test_progress_cancellation_does_not_publish_any_audio(self):
        self.tools()
        source = self.audio()
        checked = self.checked(source)

        def cancel(_event):
            raise DownloadError("Cancelled")

        with self.assertRaisesRegex(DownloadError, "Cancelled"):
            convert_local_file(checked, "flac", self.output, progress_hook=cancel)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
