"""Timestamp progress, bounded pipe handling and subprocess cancellation."""

import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from universal_downloader.ffmpeg_progress import Progress, run


class ProgressTests(unittest.TestCase):
    def test_encoded_time_is_monotonic_and_estimates_remaining_seconds(self):
        progress = Progress(10, "encoding")
        self.assertEqual(progress.event()["progress"], 0)
        progress.line("out_time_us=2500000")
        progress.line("speed=2.0x")
        event = progress.line("progress=continue")
        self.assertEqual(event, {"status": "converting", "operation": "encoding", "progress": 25.0, "speed": 2.0, "eta": 4})
        progress.line("out_time_ms=2000000")
        self.assertEqual(progress.line("progress=continue")["progress"], 25.0)
        progress.line("out_time_us=12000000")
        self.assertEqual(progress.line("progress=continue")["progress"], 99.9)
        event = progress.event(finished=True)
        self.assertEqual((event["progress"], event["eta"]), (99.9, 0))

    def test_missing_duration_and_invalid_statistics_do_not_invent_progress(self):
        for duration in (None, 0, -1, float("nan"), "invalid"):
            progress = Progress(duration, "copying")
            progress.line("out_time_us=2500000")
            progress.line("speed=3x")
            event = progress.line("progress=continue")
            self.assertIsNone(event["progress"])
            self.assertIsNone(event["eta"])
            self.assertEqual(event["operation"], "copying")
        progress = Progress(10, "encoding")
        progress.line("out_time_us=N/A")
        progress.line("speed=NaNx")
        event = progress.line("progress=continue")
        self.assertEqual(event["progress"], 0)
        self.assertIsNone(event["eta"])
        self.assertIsNone(event["speed"])

    def command(self, source):
        return [sys.executable, "-u", "-c", source]

    def test_two_full_pipes_are_drained_and_stderr_is_bounded(self):
        command = self.command("import sys; sys.stderr.write('x'*200000); sys.stderr.flush(); "
                               "print('out_time_us=1000000\\nprogress=end', flush=True); "
                               "sys.stderr.write('y'*200000); sys.stderr.flush(); sys.exit(3)")
        events = []
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run(command, duration=2, progress_hook=events.append, timeout=5)
        self.assertEqual(raised.exception.returncode, 3)
        self.assertLessEqual(len(raised.exception.stderr), 65_536)
        self.assertTrue(any(event["progress"] == 50 for event in events))
        self.assertNotIn(99.9, [event["progress"] for event in events])

    def test_callback_exception_stops_running_process_and_reraises(self):
        command = self.command("import time; print('out_time_us=1000000\\nprogress=continue', flush=True); time.sleep(30)")
        processes = []
        original = subprocess.Popen

        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        def cancel(event):
            if event["progress"]:
                raise RuntimeError("cancelled by callback")

        with patch("universal_downloader.ffmpeg_progress.subprocess.Popen", side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, "cancelled by callback"):
                run(command, duration=2, progress_hook=cancel, timeout=5)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertTrue(processes[0].stderr.closed)

    def test_timeout_stops_process_instead_of_waiting_for_full_conversion(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run(self.command("import time; time.sleep(30)"), timeout=.1)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
