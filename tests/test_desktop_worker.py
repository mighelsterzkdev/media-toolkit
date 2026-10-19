"""Verify process-tree ownership with actual OS child processes."""

import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

from universal_downloader.desktop_worker import ProcessWorker, _WindowsJob
from universal_downloader.dependencies import hidden_process_options


def _child_tree(connection):
    if os.name != "nt":
        os.setsid()
    connection.send("ready")
    connection.recv()
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"], **hidden_process_options())
    connection.send(child.pid)
    connection.recv()


def _alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        api.GetExitCodeProcess.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            status = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        from pathlib import Path
        status = Path(f"/proc/{pid}/stat")
        if status.exists() and status.read_text().rsplit(")", 1)[1].strip().startswith("Z"):
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class DesktopWorkerTests(unittest.TestCase):
    def test_worker_does_not_run_before_parent_gate_and_closes_os_handles(self):
        worker = ProcessWorker()
        process = worker.process
        pid = process.pid
        self.assertTrue(worker.connection.poll(15))
        self.assertEqual(worker.connection.recv()[0], "ready")
        self.assertTrue(_alive(pid))
        self.assertFalse(worker.connection.poll(0.15))
        worker.close()
        worker.close()
        self.assertFalse(_alive(pid))
        with self.assertRaises(ValueError):
            process.is_alive()

    def test_contained_tree_cleanup_does_not_terminate_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"], **hidden_process_options())
        self.addCleanup(unrelated.wait, timeout=5)
        self.addCleanup(unrelated.terminate)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_child_tree, args=(child,))
        process.start()
        child.close()
        job = _WindowsJob(process) if os.name == "nt" else None
        try:
            self.assertTrue(parent.poll(15))
            self.assertEqual(parent.recv(), "ready")
            parent.send("start")
            self.assertTrue(parent.poll(15))
            descendant = parent.recv()
            self.assertTrue(_alive(descendant))
            wrapper = ProcessWorker.__new__(ProcessWorker)
            wrapper.process, wrapper.connection, wrapper.job = process, parent, job
            wrapper._lock = threading.RLock()
            wrapper._closed = False
            wrapper._group_ready = True
            wrapper.close()
            deadline = time.monotonic() + 5
            while _alive(descendant) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(_alive(descendant))
            self.assertIsNone(unrelated.poll())
        finally:
            if job:
                job.close()
            try:
                if process.is_alive():
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    process.terminate()
                    process.join(timeout=5)
                process.close()
            except ValueError:
                pass
            parent.close()


if __name__ == "__main__":
    unittest.main()
