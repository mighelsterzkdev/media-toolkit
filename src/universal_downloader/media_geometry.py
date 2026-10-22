"""Normalize display geometry explicitly before encoding square-pixel media.

Input filters from this module require ``-noautorotate``. Stream copies should
keep their original display metadata and do not use these filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import re

from .errors import DownloadError

MAX_FRAME_DIMENSION = 65_535
MAX_FRAME_PIXELS = 80_000_000


def _positive_dimension(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise DownloadError("The video has invalid frame dimensions.")
    return value


def _rounded(value):
    value = Fraction(value)
    return max(1, (value.numerator * 2 + value.denominator) // (value.denominator * 2))


def _ratio(value):
    if value in (None, "N/A", "0:1", "0/1"):
        return Fraction(1)
    try:
        result = Fraction(str(value).replace(":", "/"))
    except (ValueError, ZeroDivisionError) as error:
        raise DownloadError("The video has an invalid sample aspect ratio.") from error
    if result <= 0 or result.numerator > 2_147_483_647 or result.denominator > 2_147_483_647:
        raise DownloadError("The video has an invalid sample aspect ratio.")
    return result


def _angle(value):
    try:
        angle = float(value)
    except (ValueError, TypeError) as error:
        raise DownloadError("The video has invalid rotation metadata.") from error
    if not math.isfinite(angle):
        raise DownloadError("The video has invalid rotation metadata.")
    angle = (angle + 180) % 360 - 180
    cardinal = round(angle / 90) * 90
    return float(cardinal) if abs(angle - cardinal) < .01 else angle


def _matrix(value):
    if not isinstance(value, str):
        raise DownloadError("The video has unreadable display matrix metadata.")
    values = []
    for line in value.splitlines():
        if ":" in line:
            values.extend(int(number) for number in re.findall(r"[+-]?\d+", line.split(":", 1)[1]))
    if len(values) != 9:
        raise DownloadError("The video has unreadable display matrix metadata.")
    # A translated track is re-anchored to its complete frame bounds when
    # exporting it. Perspective cannot be represented by scale/rotate/flip.
    if any(values[index] for index in (2, 5)) or values[8] != 1 << 30:
        raise DownloadError("This video uses a display transform that cannot be preserved by the converter.")
    a, b, c, d = [values[index] / 65_536 for index in (0, 1, 3, 4)]
    horizontal, vertical = math.hypot(a, b), math.hypot(c, d)
    if not horizontal or not vertical or abs(a * c + b * d) > horizontal * vertical * .0001:
        raise DownloadError("This video uses a skewed or invalid display transform.")
    # T = rotation * axis-scale * vertical-reflection. In image coordinates a
    # positive atan2 angle rotates clockwise, matching FFmpeg's rotate filter.
    clockwise = _angle(math.degrees(math.atan2(b, a)))
    mirrored = a * d - b * c < 0
    stretch = Fraction(horizontal / vertical).limit_denominator(1_000_000)
    return clockwise, mirrored, stretch


def _orientation(stream):
    for item in stream.get("side_data_list") or ():
        if isinstance(item, dict) and item.get("side_data_type") == "Display Matrix":
            if "displaymatrix" in item:
                return _matrix(item["displaymatrix"])
            if "rotation" in item:
                return -_angle(item["rotation"]), False, Fraction(1)
            raise DownloadError("The video has unreadable display matrix metadata.")
    rotation = (stream.get("tags") or {}).get("rotate", 0)
    return -_angle(rotation), False, Fraction(1)


def _bounds(width, height):
    if width > MAX_FRAME_DIMENSION or height > MAX_FRAME_DIMENSION or width * height > MAX_FRAME_PIXELS:
        raise DownloadError("This video's display dimensions are too large to convert safely.")


def _rotated_size(width, height, clockwise):
    quarter = round(clockwise / 90)
    if abs(clockwise - quarter * 90) < .01:
        return (height, width) if quarter % 2 else (width, height)
    radians = math.radians(clockwise)
    cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
    return math.ceil(width * cosine + height * sine), math.ceil(width * sine + height * cosine)


@dataclass(frozen=True)
class VideoGeometry:
    coded_width: int
    coded_height: int
    sample_aspect_ratio: Fraction
    clockwise_rotation: float
    mirrored: bool

    @property
    def square_width(self):
        return _rounded(self.coded_width * max(Fraction(1), self.sample_aspect_ratio))

    @property
    def square_height(self):
        return _rounded(self.coded_height * max(Fraction(1), 1 / self.sample_aspect_ratio))

    @property
    def display_width(self):
        return _rotated_size(self.square_width, self.square_height, self.clockwise_rotation)[0]

    @property
    def display_height(self):
        return _rotated_size(self.square_width, self.square_height, self.clockwise_rotation)[1]

    @property
    def normalization_required(self):
        return self.sample_aspect_ratio != 1 or self.clockwise_rotation != 0 or self.mirrored

    def _plan(self, max_height, even):
        if max_height is not None and (isinstance(max_height, bool) or not isinstance(max_height, int) or max_height < 1):
            raise DownloadError("Choose a positive maximum video height.")
        ceiling = max_height - max_height % 2 if max_height is not None and even else max_height
        if ceiling == 0:
            raise DownloadError("This video output needs a maximum height of at least 2 pixels.")
        _bounds(self.coded_width, self.coded_height)
        width, height = self.display_width, self.display_height
        scale = min(Fraction(1), Fraction(ceiling, height)) if ceiling is not None else Fraction(1)
        quarter = round(self.clockwise_rotation / 90)
        cardinal = abs(self.clockwise_rotation - quarter * 90) < .01
        if cardinal:
            content_width, content_height = _rounded(width * scale), _rounded(height * scale)
            before_width, before_height = ((content_height, content_width) if quarter % 2 else (content_width, content_height))
            oriented_width, oriented_height = content_width, content_height
        else:
            before_width, before_height = _rounded(self.square_width * scale), _rounded(self.square_height * scale)
            oriented_width, oriented_height = _rotated_size(before_width, before_height, self.clockwise_rotation)
            correction = min(Fraction(1), Fraction(ceiling, oriented_height)) if ceiling is not None else Fraction(1)
            content_width, content_height = _rounded(oriented_width * correction), _rounded(oriented_height * correction)
        output_width = content_width + content_width % 2 if even else content_width
        output_height = content_height + content_height % 2 if even else content_height
        for dimensions in ((before_width, before_height), (oriented_width, oriented_height), (output_width, output_height)):
            _bounds(*dimensions)
        return before_width, before_height, oriented_width, oriented_height, content_width, content_height, output_width, output_height

    def output_size(self, max_height=None, even=False):
        return self._plan(max_height, even)[-2:]

    def filters(self, max_height=None, even=False):
        before_width, before_height, oriented_width, oriented_height, content_width, content_height, output_width, output_height = self._plan(max_height, even)
        result = []
        if (before_width, before_height) != (self.coded_width, self.coded_height):
            result.append(f"scale={before_width}:{before_height}:flags=lanczos")
        result.append("setsar=1")
        if self.mirrored:
            result.append("vflip")
        quarter = round(self.clockwise_rotation / 90)
        if abs(self.clockwise_rotation - quarter * 90) < .01:
            if quarter % 4 == 1:
                result.append("transpose=clock")
            elif quarter % 4 == 2:
                result.extend(("hflip", "vflip"))
            elif quarter % 4 == 3:
                result.append("transpose=cclock")
        else:
            angle = format(self.clockwise_rotation, ".12g")
            result.append(f"rotate={angle}*PI/180:ow={oriented_width}:oh={oriented_height}:c=black")
        if (content_width, content_height) != (oriented_width, oriented_height):
            result.append(f"scale={content_width}:{content_height}:flags=lanczos")
        if (output_width, output_height) != (content_width, content_height):
            result.append(f"pad={output_width}:{output_height}:0:0")
        return ",".join(result)


def analyze(stream):
    """Return geometry derived from the selected stream's decoded metadata."""
    if not isinstance(stream, dict):
        raise DownloadError("The video has invalid display metadata.")
    width, height = _positive_dimension(stream.get("width")), _positive_dimension(stream.get("height"))
    clockwise, mirrored, stretch = _orientation(stream)
    return VideoGeometry(width, height, _ratio(stream.get("sample_aspect_ratio")) * stretch, clockwise, mirrored)


def input_options(stream, geometry):
    """Clear inherited stream transforms after capturing their geometry.

    FFmpeg 6+ can otherwise copy a stream-level display matrix to an encoded
    output even when its frame side data was removed by a filter. Setting an
    input rotation rebuilds the whole matrix, including any reflections, as
    identity. Apply the original transform explicitly through ``filters`` and
    pass these options before ``-i`` together with ``-noautorotate``. Packet
    copies must keep their original metadata and never use these options.
    """
    if not geometry.normalization_required:
        return []
    index = stream.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise DownloadError("The selected video stream has invalid metadata.")
    return [f"-display_rotation:{index}", "0"]
