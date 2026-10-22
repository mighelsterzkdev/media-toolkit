"""Quantize animation timestamps without accumulating per-frame rounding error."""

from fractions import Fraction


def quantize(durations, quantum_ms, *, min_ticks=1):
    """Keep frames and the nearest representable total, with bounded delays.

    Reserve the minimum delay for the remaining frames before choosing each
    boundary. This also handles a long delay followed by very short frames.
    """
    if not durations:
        return []
    if quantum_ms <= 0 or min_ticks < 1:
        raise ValueError("Animation timing requires a positive quantum and minimum delay.")
    ticks = [Fraction(str(value)) / quantum_ms for value in durations]
    if any(value <= 0 for value in ticks):
        raise ValueError("Animation frame durations must be positive.")

    def nearest(value):
        return (value.numerator * 2 + value.denominator) // (value.denominator * 2)

    total = max(len(ticks) * min_ticks, nearest(sum(ticks)))
    requested, emitted = Fraction(0), 0
    result = []
    for index, value in enumerate(ticks):
        requested += value
        remaining = (len(ticks) - index - 1) * min_ticks
        boundary = min(total - remaining, max(emitted + min_ticks, nearest(requested)))
        result.append((boundary - emitted) * quantum_ms)
        emitted = boundary
    return result
