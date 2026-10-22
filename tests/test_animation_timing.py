"""Animation timing and format-boundary regressions using actual image files."""

from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from PIL import Image

from universal_downloader.animation_timing import quantize
from universal_downloader.local_animation import _frames, _save_sequence, animation_targets
from universal_downloader.local_media import convert_local_file, inspect_local_files


class QuantizedTimingTests(unittest.TestCase):
    def test_gif_timing_keeps_a_40fps_animation_at_its_original_speed(self):
        self.assertEqual(quantize([25] * 4, 10), [30, 20, 30, 20])

    def test_short_frames_reserve_their_minimum_without_unnecessarily_extending_the_total(self):
        self.assertEqual(quantize([1000, 1], 10), [990, 10])
        self.assertEqual(quantize([1, 1000], 10), [10, 990])
        self.assertEqual(quantize([1, 1, 1], 10), [10, 10, 10])

    def test_fractional_60fps_boundaries_stay_close_and_do_not_accumulate_rounding_error(self):
        durations = [1000 / 60] * 60
        converted = quantize(durations, 1)
        self.assertEqual(sum(converted), 1000)
        for count in range(1, 61):
            self.assertLessEqual(abs(sum(converted[:count]) - count * 1000 / 60), .501)


class RealAnimationTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mt-animation-timing-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def animation(self, *, count=4, duration=25, width=16, height=8, loop=0, native="PNG"):
        source = self.root / f"source-{count}-{width}-{height}-{loop}.{native.lower()}"
        frames = [Image.new("RGBA", (width, height), ((index * 43) % 256, (index * 79) % 256, (index * 127) % 256, 255)) for index in range(count)]
        try:
            frames[0].save(source, native, save_all=True, append_images=frames[1:], duration=duration, loop=loop)
        finally:
            for frame in frames:
                frame.close()
        return source

    def checked(self, source):
        result = inspect_local_files([source])[0]
        self.assertIsNone(result["error"], result)
        return result

    def durations(self, path):
        with Image.open(path) as image:
            delays = []
            for index in range(image.n_frames):
                image.seek(index)
                image.load()
                delays.append(image.info["duration"])
        return delays

    def set_delays(self, source, numerator, denominator):
        # Build standards-valid fractional or long-delay fixtures independently
        # of Pillow's millisecond-only save API.
        data = bytearray(source.read_bytes())
        offset = 8
        while offset < len(data):
            length, kind = struct.unpack(">I4s", data[offset:offset + 8])
            if kind == b"fcTL":
                payload = offset + 8
                data[payload + 20:payload + 24] = struct.pack(">HH", numerator, denominator)
                chunk = bytes(data[payload:payload + length])
                data[payload + length:payload + length + 4] = struct.pack(">I", zlib.crc32(kind + chunk))
            offset += length + 12
        source.write_bytes(data)
        return source

    def fractional_apng(self):
        return self.set_delays(self.animation(count=6, duration=17), 1, 60)

    def test_real_apng_to_gif_keeps_frames_and_total_playback_time(self):
        source = self.animation()
        original = source.read_bytes()
        output = convert_local_file(self.checked(source), "gif", self.root / "output")
        self.assertEqual(self.durations(output), [30, 20, 30, 20])
        self.assertEqual(source.read_bytes(), original)

    def test_fractional_source_delays_are_only_quantized_at_output_and_have_honest_precision(self):
        source = self.fractional_apng()
        checked = self.checked(source)
        self.assertAlmostEqual(checked["duration"], .1)
        self.assertIn("1 ms", checked["target_notes"]["webp"])
        with _frames(source.read_bytes()) as (_native, frames, durations, plays, _transparent):
            self.assertAlmostEqual(durations[0], 1000 / 60)
            for target in ("webp", "apng"):
                with self.subTest(target=target):
                    output = self.root / f"fractional.{target}"
                    with output.open("wb") as stream:
                        _save_sequence(frames, durations, plays, target, stream, "#FFFFFF")
                    actual = self.durations(output)
                    self.assertEqual(len(actual), 6)
                    self.assertAlmostEqual(sum(actual), 100)
                    for count in range(1, 7):
                        self.assertLessEqual(abs(sum(actual[:count]) - count * 1000 / 60), .501)

    def test_limits_are_applied_to_each_output_format_instead_of_rejecting_wide_sources(self):
        checked = self.checked(self.animation(width=16_384, height=1))
        self.assertIn("gif", checked["targets"])
        self.assertNotIn("webp", checked["targets"])
        checked = self.checked(self.animation(width=65_536, height=1))
        self.assertNotIn("gif", checked["targets"])
        self.assertNotIn("webp", checked["targets"])
        self.assertNotIn("mp4", checked["targets"])
        self.assertIn("mp4", animation_targets("gif", width=16_383, height=2, mp4_available=True))
        self.assertNotIn("mp4", animation_targets("gif", width=16_385, height=2, mp4_available=True))

    def test_loop_count_limits_are_checked_before_offering_webp(self):
        source = self.animation(native="GIF", loop=65_535)
        checked = self.checked(source)
        self.assertNotIn("webp", checked["targets"])
        output = convert_local_file(checked, "apng", self.root / "output")
        with Image.open(output) as image:
            self.assertEqual(image.info["loop"], 65_536)
        restored = self.checked(output)
        self.assertIn("gif", restored["targets"])
        self.assertNotIn("webp", restored["targets"])

    def test_long_valid_source_delays_only_offer_outputs_their_encoders_can_represent(self):
        source = self.set_delays(self.animation(count=2), 900, 1)
        checked = self.checked(source)
        self.assertAlmostEqual(checked["duration"], 1800)
        self.assertEqual(checked["targets"], ["webp"])
        output = convert_local_file(checked, "webp", self.root / "output")
        self.assertEqual(self.durations(output), [900_000, 900_000])
        # Pillow's APNG output and the MP4 bridge both use a 16-bit millisecond
        # delay field, even though APNG itself supports longer rational delays.
        restored = self.checked(output)
        self.assertEqual(restored["targets"], [])
        self.assertIn("gif", animation_targets("apng", durations=[655_350, 10]))
        self.assertNotIn("gif", animation_targets("apng", durations=[655_360, 10]))
        self.assertIn("apng", animation_targets("gif", durations=[65_535, 10]))
        self.assertNotIn("apng", animation_targets("gif", durations=[65_536, 10]))


if __name__ == "__main__":
    unittest.main()
