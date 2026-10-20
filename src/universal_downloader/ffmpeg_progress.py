"""Bounded, cross-platform FFmpeg progress collection for local conversions."""

from __future__ import annotations

from collections import deque
import math
import queue
import subprocess
import threading
import time


def _number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


class Progress:
    """Use encoded timestamps, keeping duration-based percentages monotonic."""

    def __init__(self, duration, operation):
        duration = _number(duration)
        self.duration = duration if duration is not None and duration > 0 else None
        self.operation = operation
        self.last = 0.0
        self.fields = {}

    def event(self, *, finished=False):
        timestamp = _number(self.fields.get("out_time_us", self.fields.get("out_time_ms")))
        seconds = max(0.0, timestamp / 1_000_000) if timestamp is not None else None
        ratio = _number(str(self.fields.get("speed", "")).removesuffix("x"))
        ratio = ratio if ratio is not None and ratio > 0 else None
        percent = None
        eta = None
        if self.duration is not None:
            if seconds is not None:
                self.last = max(self.last, min(99.9, seconds / self.duration * 100))
            if finished:
                self.last = 99.9
            percent = round(self.last, 2)
            if seconds is not None and ratio is not None:
                eta = math.ceil(max(0, self.duration - seconds) / ratio)
            if finished:
                eta = 0
        return {"status": "converting", "operation": self.operation,
                "progress": percent, "speed": ratio, "eta": eta}

    def line(self, line):
        key, separator, value = line.strip().partition("=")
        if not separator:
            return None
        if key == "progress":
            # FFmpeg also prints progress=end after a failed encode. Only the
            # caller's successful exit check can complete this phase.
            event = self.event()
            self.fields = {}
            return event
        if key in ("out_time_us", "out_time_ms", "speed"):
            self.fields[key] = value
        return None


def run(command, *, duration=None, operation="encoding", progress_hook=None, timeout=1_800, process_options=None):
    """Drain both pipes while delivering progress on the caller's thread.

    Windows pipes do not support selectors, so dedicated bounded readers keep
    a noisy codec from deadlocking either pipe. Callback exceptions stop and
    reap this process before the caller removes its private output directory.
    """
    parsed = Progress(duration, operation)
    if progress_hook:
        progress_hook(parsed.event())
    events = queue.Queue(maxsize=16)
    errors = deque(maxlen=16)  # At most 64 KiB of stderr, even on corrupt input.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **(process_options or {}))

    def enqueue(event):
        try:
            events.put_nowait(event)
        except queue.Full:
            # The newest timestamp is more useful than an obsolete update.
            try:
                events.get_nowait()
            except queue.Empty:
                pass
            try:
                events.put_nowait(event)
            except queue.Full:
                pass

    def read_progress():
        assert process.stdout is not None
        discard = False
        while chunk := process.stdout.readline(4_096):
            complete = chunk.endswith(b"\n")
            if not discard and complete:
                event = parsed.line(chunk.decode("ascii", errors="replace"))
                if event is not None:
                    enqueue(event)
            discard = not complete

    def read_errors():
        assert process.stderr is not None
        while chunk := process.stderr.read(4_096):
            errors.append(chunk)

    readers = [threading.Thread(target=read_progress, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                event = events.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                event = None
            if event is not None and progress_hook:
                progress_hook(event)
            if process.poll() is not None and not readers[0].is_alive() and events.empty():
                break
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command, stderr=b"".join(errors))
        if progress_hook:
            progress_hook(parsed.event(finished=True))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
