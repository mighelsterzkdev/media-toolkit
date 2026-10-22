"""Display-geometry regressions with real orientation and anamorphic video."""

from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
from universal_downloader.errors import DownloadError
from universal_downloader.media_geometry import analyze, input_options


def matrix(a, b, c, d, *, x=0, y=0, perspective=0):
    values = (a, b, perspective, c, d, 0, x, y, 1 << 30)
    return "\n".join(f"{index // 3:08d}: " + " ".join(str(value) for value in values[index:index + 3]) for index in range(0, 9, 3))


class GeometryTests(unittest.TestCase):
    def test_input_override_is_limited_to_the_selected_stream_when_normalizing(self):
        plain = {"index": 2, "width": 40, "height": 20}
        self.assertEqual(input_options(plain, analyze(plain)), [])
        transformed = {**plain, "tags": {"rotate": "90"}}
        self.assertEqual(input_options(transformed, analyze(transformed)), ["-display_rotation:2", "0"])
        for index in (None, True, -1, "2"):
            with self.subTest(index=index), self.assertRaisesRegex(DownloadError, "invalid metadata"):
                input_options({**transformed, "index": index}, analyze(transformed))

    def test_square_pixel_expansion_preserves_detail_before_rotating_anamorphic_video(self):
        landscape = analyze({"width": 40, "height": 20, "sample_aspect_ratio": "2:1"})
        portrait = analyze({"width": 40, "height": 20, "sample_aspect_ratio": "2:1",
                            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": 90}]})
        self.assertEqual(landscape.output_size(), (80, 20))
        self.assertEqual(portrait.output_size(), (20, 80))
        self.assertEqual(portrait.output_size(max_height=40), (10, 40))
        self.assertEqual(analyze({"width": 40, "height": 20, "sample_aspect_ratio": "1:2"}).output_size(), (40, 40))

    def test_height_limits_include_even_pixel_padding_and_never_upscale_normal_sources(self):
        geometry = analyze({"width": 35, "height": 27})
        self.assertEqual(geometry.output_size(even=True), (36, 28))
        self.assertEqual(geometry.output_size(max_height=27, even=True), (34, 26))
        self.assertEqual(geometry.output_size(max_height=100, even=False), (35, 27))
        self.assertEqual(geometry.output_size(max_height=1), (1, 1))
        with self.assertRaisesRegex(DownloadError, "at least 2"):
            geometry.output_size(max_height=1, even=True)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                geometry.output_size(max_height=value)

    def test_matrix_reflection_scale_and_translation_are_not_lost(self):
        stream = {"width": 40, "height": 20, "side_data_list": [{"side_data_type": "Display Matrix",
                  "displaymatrix": matrix(-131072, 0, 0, 65536, x=80 * 65536)}]}
        geometry = analyze(stream)
        self.assertEqual(geometry.sample_aspect_ratio, Fraction(2))
        self.assertTrue(geometry.mirrored)
        self.assertEqual(geometry.output_size(), (80, 20))
        self.assertTrue(geometry.normalization_required)

    def test_invalid_or_unsafe_display_metadata_is_not_silently_ignored(self):
        for ratio in ("1:0", "-1:2", "nan", "inf"):
            with self.subTest(ratio=ratio), self.assertRaises(DownloadError):
                analyze({"width": 40, "height": 20, "sample_aspect_ratio": ratio})
        for transform in (matrix(65536, 0, 0, 65536, perspective=1), matrix(65536, 10000, 0, 65536)):
            with self.subTest(transform=transform), self.assertRaises(DownloadError):
                analyze({"width": 40, "height": 20, "side_data_list": [{"side_data_type": "Display Matrix", "displaymatrix": transform}]})
        geometry = analyze({"width": 40, "height": 20, "sample_aspect_ratio": "2000000:1"})
        with self.assertRaisesRegex(DownloadError, "too large"):
            geometry.filters()


class RealGeometryTests(unittest.TestCase):
    def setUp(self):
        tools = find_ffmpeg()
        if tools is None:
            self.skipTest("FFmpeg and ffprobe are not installed")
        suffix = ".exe" if (tools / "ffmpeg.exe").is_file() else ""
        self.ffmpeg, self.ffprobe = tools / f"ffmpeg{suffix}", tools / f"ffprobe{suffix}"
        temporary = tempfile.TemporaryDirectory(prefix="mt-real-geometry-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.picture = self.root / "corners.png"
        with Image.new("RGB", (40, 20), "red") as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 0, 39, 9), fill=(0, 255, 0))
            draw.rectangle((0, 10, 19, 19), fill=(0, 0, 255))
            draw.rectangle((20, 10, 39, 19), fill=(255, 255, 0))
            image.save(self.picture)
        self.source = self.root / "source.mp4"
        self.run_tool(["-loop", "1", "-framerate", "10", "-i", self.picture, "-t", "0.2",
                       "-c:v", "libx264rgb", "-crf", "0", "-preset", "ultrafast", "-threads", "1", self.source])
        help_text = subprocess.run([str(self.ffmpeg), "-hide_banner", "-h", "full"], capture_output=True,
                                   text=True, timeout=20, **hidden_process_options()).stdout
        self.has_display_options = "-display_rotation" in help_text

    def run_tool(self, arguments):
        subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *map(str, arguments)],
                       check=True, capture_output=True, timeout=30, **hidden_process_options())

    def probe(self, path):
        result = subprocess.run([str(self.ffprobe), "-v", "error", "-show_streams", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True, timeout=15, **hidden_process_options())
        return json.loads(result.stdout)["streams"][0]

    def rotated(self, angle, *, mirror=False):
        source = self.root / f"rotated-{angle}-{mirror}.mp4"
        if self.has_display_options:
            options = ["-display_rotation:v:0", str(angle)]
            if mirror:
                options += ["-display_hflip:v:0"]
            self.run_tool([*options, "-i", self.source, "-c", "copy", source])
        elif not mirror:
            self.run_tool(["-i", self.source, "-c", "copy", "-metadata:s:v:0", f"rotate={angle}", source])
        else:
            self.skipTest("This FFmpeg does not provide display-matrix fixture options")
        return source

    def test_explicit_cardinal_orientation_matches_ffmpeg_autorotation_corner_for_corner(self):
        for angle in (90, 180, 270):
            with self.subTest(angle=angle):
                source = self.rotated(angle)
                geometry = analyze(self.probe(source))
                auto, explicit = self.root / f"auto-{angle}.png", self.root / f"explicit-{angle}.png"
                self.run_tool(["-i", source, "-frames:v", "1", auto])
                self.run_tool(["-noautorotate", "-i", source, "-vf", geometry.filters(), "-frames:v", "1", explicit])
                with Image.open(auto) as expected, Image.open(explicit) as actual:
                    self.assertEqual(actual.size, geometry.output_size())
                    self.assertEqual(actual.size, expected.size)
                    self.assertEqual(actual.convert("RGB").tobytes(), expected.convert("RGB").tobytes())

    def test_mirrored_cardinal_matrices_match_ffmpeg_autorotation(self):
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                source = self.rotated(angle, mirror=True)
                geometry = analyze(self.probe(source))
                auto, explicit = self.root / f"auto-m-{angle}.png", self.root / f"explicit-m-{angle}.png"
                self.run_tool(["-i", source, "-frames:v", "1", auto])
                self.run_tool(["-noautorotate", "-i", source, "-vf", geometry.filters(), "-frames:v", "1", explicit])
                with Image.open(auto) as expected, Image.open(explicit) as actual:
                    self.assertEqual(actual.size, expected.size)
                    self.assertEqual(actual.convert("RGB").tobytes(), expected.convert("RGB").tobytes())

    def test_input_override_prevents_encoded_outputs_from_inheriting_an_applied_matrix(self):
        if not self.has_display_options:
            self.skipTest("Display-transform encoding requires FFmpeg 6 or newer")
        for angle, mirror in ((90, False), (90, True), (180, True), (270, True)):
            with self.subTest(angle=angle, mirror=mirror):
                source = self.rotated(angle, mirror=mirror)
                stream = self.probe(source)
                geometry = analyze(stream)
                reference = self.root / f"reference-encoded-{angle}-{mirror}.png"
                encoded = self.root / f"normalized-{angle}-{mirror}.mp4"
                decoded = self.root / f"decoded-normalized-{angle}-{mirror}.png"
                self.run_tool(["-i", source, "-frames:v", "1", reference])
                self.run_tool(["-noautorotate", *input_options(stream, geometry), "-i", source,
                               "-vf", geometry.filters() + ",sidedata=mode=delete:type=DISPLAYMATRIX",
                               "-c:v", "libx264rgb", "-crf", "0", "-bf", "0", "-threads", "1", encoded])
                self.run_tool(["-i", encoded, "-frames:v", "1", decoded])
                metadata = self.probe(encoded)
                self.assertFalse(any(item.get("rotation") for item in metadata.get("side_data_list", [])))
                with Image.open(reference) as expected, Image.open(decoded) as actual:
                    self.assertEqual(actual.size, geometry.output_size())
                    self.assertEqual(actual.convert("RGB").tobytes(), expected.convert("RGB").tobytes())

    def test_square_pixel_normalization_keeps_anamorphic_shape_and_maximum_height(self):
        source = self.root / "anamorphic.mp4"
        self.run_tool(["-i", self.source, "-vf", "setsar=2", "-c:v", "libx264rgb", "-crf", "0", "-threads", "1", source])
        geometry = analyze(self.probe(source))
        self.assertEqual((geometry.display_width, geometry.display_height), (80, 20))
        output = self.root / "square.png"
        self.run_tool(["-noautorotate", "-i", source, "-vf", geometry.filters(max_height=10), "-frames:v", "1", output])
        with Image.open(output) as image:
            self.assertEqual(image.size, (40, 10))
            self.assertLessEqual(max(abs(actual - expected) for actual, expected in zip(image.getpixel((5, 2)), (255, 0, 0))), 3)

    def test_arbitrary_angles_expand_the_canvas_and_resizing_remains_within_the_limit(self):
        for angle in (30, -30, 45):
            source = self.rotated(angle)
            geometry = analyze(self.probe(source))
            self.assertGreater(geometry.display_height, 20)
            for ceiling in (None, 17):
                with self.subTest(angle=angle, ceiling=ceiling):
                    output = self.root / f"expanded-{angle}-{ceiling}.png"
                    self.run_tool(["-noautorotate", "-i", source, "-vf", geometry.filters(max_height=ceiling, even=True), "-frames:v", "1", output])
                    with Image.open(output) as image:
                        self.assertEqual(image.size, geometry.output_size(max_height=ceiling, even=True))
                        self.assertEqual(image.width % 2, 0)
                        self.assertEqual(image.height % 2, 0)
                        if ceiling is not None:
                            self.assertLessEqual(image.height, ceiling)


if __name__ == "__main__":
    unittest.main()
