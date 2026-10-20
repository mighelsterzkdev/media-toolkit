"""Private, contained desktop workers; never import the browser or start a UI."""

from __future__ import annotations

import math
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time

from .downloader import DownloadRequest, download, inspect_media
from .errors import DownloadError


class _QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _can_convert(path):
    from .local_media import inspect_local_files
    try:
        inspected = inspect_local_files([path])
        return len(inspected) == 1 and not inspected[0].get("error") and bool(inspected[0].get("targets"))
    except (DownloadError, OSError, ValueError, TypeError, RuntimeError, MemoryError):
        # A completed download remains successful even if it is not eligible
        # for the optional conversion shortcut.
        return False


def _worker_main(connection):
    """Wait for the parent's containment gate before touching any media URL."""
    try:
        if os.name != "nt":
            os.setsid()
        connection.send(("ready", None))
        payload = connection.recv()
        if payload["action"] == "inspect":
            result = inspect_media(payload["url"], logger=_QuietLogger())
            connection.send(("inspection", result))
            return
        if payload["action"] == "inspect_local":
            from .local_media import inspect_local_files

            connection.send(("local_inspection", inspect_local_files(payload["paths"])))
            return
        if payload["action"] == "convert":
            from .local_media import convert_local_file

            connection.send(("progress", {"status": "converting"}))
            path = convert_local_file(payload["image"], payload["format"], Path(payload["settings"]["output"]),
                                       background=payload["background"], work_dir=Path(payload["work_dir"]))
            connection.send(("result", {"title": payload["image"]["name"], "path": str(path), "already_exists": False, "can_convert": True}))
            return
        if payload["action"] != "download":
            raise DownloadError("This media operation is not supported.")
        inspected = payload.get("inspected")
        kind = payload.get("kind")
        if inspected is None:
            inspected = inspect_media(payload["url"], logger=_QuietLogger())
            if inspected.alternatives:
                raise DownloadError("This page contains several media items. Use Check and select the item to download.")
            kind = inspected.kinds[0]
        connection.send(("checked", {"title": inspected.information.get("title") or "Download", "kind": kind}))
        height = payload["settings"]["max_height"]
        if kind == "audio" or (kind == "image" and not inspected.animated):
            height = None
        request = DownloadRequest(
            inspected.url, kind, Path(payload["settings"]["output"]),
            None if payload.get("format", "original") == "original" else payload["format"],
            height, payload["settings"]["max_size"],
        )
        previous = 0.0

        def progress(event):
            nonlocal previous
            status = event.get("status")
            now = time.monotonic()
            if status == "downloading" and now - previous < 0.10:
                return
            previous = now
            connection.send(("progress", {
                "status": status,
                **{field: _number(event.get(field)) for field in ("downloaded_bytes", "total_bytes", "total_bytes_estimate", "speed", "eta")},
            }))

        result = download(request, inspected=inspected, logger=_QuietLogger(), progress_hook=progress)
        connection.send(("result", {"title": result.title, "path": str(result.path), "already_exists": result.already_exists,
                                    "can_convert": _can_convert(result.path)}))
    except DownloadError as error:
        try:
            connection.send(("error", {"message": str(error), "code": error.code}))
        except (OSError, EOFError):
            pass
    except BaseException:
        try:
            connection.send(("error", {"message": "The media worker could not finish this operation. Try again.", "code": "worker"}))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class _WindowsJob:
    """The parent owns a non-inheritable job handle; closing it kills descendants."""

    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        api.CloseHandle.restype = wintypes.BOOL
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Could not create the desktop worker job")
        try:
            information = EXTENDED()
            information.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
                raise OSError(ctypes.get_last_error(), "Could not configure the desktop worker job")
            if not api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process.sentinel)):
                raise OSError(ctypes.get_last_error(), "Could not contain the desktop worker")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProcessWorker:
    """One private pipe and one OS process tree, never a shared executor pool."""

    def __init__(self):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe(duplex=True)
        self.process = context.Process(target=_worker_main, args=(child,), daemon=True, name="UniversalDownloader-media")
        self.job = None
        self._lock = threading.RLock()
        self._closed = False
        self._group_ready = False
        try:
            self.process.start()
            child.close()
            if os.name == "nt":
                self.job = _WindowsJob(self.process)
        except BaseException:
            child.close()
            self.close()
            raise

    def begin(self, payload):
        if not self.connection.poll(30):
            raise DownloadError("The media worker did not start. Restart the application and try again.")
        event, _ = self.connection.recv()
        if event != "ready":
            raise DownloadError("The media worker could not be initialized.")
        self._group_ready = True
        with self._lock:
            if self._closed:
                raise DownloadError("This operation was cancelled.")
            self.connection.send(payload)

    def events(self):
        while True:
            if self._closed:
                return
            try:
                if self.connection.poll(0.10):
                    event = self.connection.recv()
                    yield event
                    if event[0] in ("inspection", "local_inspection", "result", "error"):
                        return
                elif not self.process.is_alive():
                    # Drain the final message, including a large inspection,
                    # before interpreting a clean exit as missing output.
                    if self.connection.poll():
                        continue
                    return
            except (OSError, EOFError, ValueError):
                return

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.job:
                self.job.close()
            elif os.name != "nt" and self._group_ready:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if self.process.pid and self.process.is_alive():
                self.process.terminate()
            if self.process.pid:
                self.process.join(timeout=2)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=2)
                if not self.process.is_alive():
                    self.process.close()
            self.connection.close()
