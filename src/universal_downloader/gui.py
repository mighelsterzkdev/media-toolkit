"""Local desktop host. The renderer only receives a narrow, validated bridge."""

from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import multiprocessing
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit

from . import APP_NAME, __version__
from .dependencies import PROJECT_ROOT, find_ffmpeg, find_js_runtime
from .errors import DownloadError
from .preferences import preferences_path
from .resume import clear_resume as discard_resumes


ASSETS = Path(__file__).resolve().parent / "desktop_assets"
CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


def _safe_error(error: BaseException) -> str:
    if isinstance(error, DownloadError):
        # Service errors are deliberately written for display. Unexpected errors
        # never expose traceback paths, environment variables, or request URLs.
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(error))
        message = " ".join("".join(character for character in text if ord(character) >= 32 and ord(character) != 127).split())
        return message[:600] or "The request could not be completed."
    return "The request could not be completed. Please try again."


def _open_directory(directory: Path) -> None:
    directory = directory.resolve()
    if not directory.is_dir():
        raise DownloadError("The download folder is no longer available.")
    if os.name == "nt":
        os.startfile(str(directory))
    else:
        command = ["open" if platform.system() == "Darwin" else "xdg-open", str(directory)]
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DesktopApi:
    """Only these public methods are exported through window.pywebview.api."""

    def __init__(self, service):
        self._service = service
        self._window = None
        self._webview = None
        self._tools_lock = threading.Lock()
        self._tools = {"status": "missing", "ffmpeg": False, "javascript": False, "error": None, "message": None}
        self._installer = None
        self._closed = False
        self._refresh_tools()

    def _refresh_tools(self):
        ffmpeg = find_ffmpeg() is not None
        javascript = find_js_runtime() is not None
        with self._tools_lock:
            self._tools = {"status": "ready" if ffmpeg and javascript else "missing", "ffmpeg": ffmpeg, "javascript": javascript, "error": None, "message": None}

    def _snapshot(self, state=None):
        state = dict(self._service.get_state() if state is None else state)
        with self._tools_lock:
            state["tools"] = dict(self._tools)
        state["version"] = __version__
        return state

    def _call(self, action, *args):
        try:
            if self._closed:
                raise DownloadError("The application is closing.")
            result = action(*args)
            return {"ok": True, "state": self._snapshot(result if isinstance(result, dict) else None)}
        except Exception as error:
            return {"ok": False, "error": _safe_error(error)}

    def get_state(self):
        return self._call(self._service.get_state)

    def _media_call(self, action, *args):
        def ready():
            self._require_download_tools()
            return action(*args)
        return self._call(ready)

    def _require_download_tools(self):
        with self._tools_lock:
            if self._tools["status"] != "ready":
                raise DownloadError("The download tools are not ready. Click Install tools, then try again.")

    def check_url(self, url):
        return self._media_call(self._service.check_url, url)

    def cancel_check(self):
        return self._call(self._service.cancel_check)

    def enqueue(self, inspection_id, asset_id, kind, output_format):
        return self._media_call(self._service.enqueue, inspection_id, asset_id, kind, output_format)

    def enqueue_links(self, text):
        return self._media_call(self._service.enqueue_links, text)

    def cancel_task(self, task_id):
        return self._call(self._service.cancel_task, task_id)

    def retry_task(self, task_id):
        # Local conversion validates its own codec requirements in the worker.
        def retry():
            tasks = self._service.get_state().get("tasks", ())
            if not any(task.get("id") == task_id and task.get("action") == "convert" for task in tasks):
                self._require_download_tools()
            return self._service.retry_task(task_id)
        return self._call(retry)

    def remove_finished(self):
        return self._call(self._service.remove_finished)

    def save_settings(self, settings):
        return self._call(self._service.save_settings, settings)

    def browse_folder(self):
        def browse():
            if self._window is None or self._webview is None:
                raise DownloadError("The folder picker is not available yet.")
            paths = self._window.create_file_dialog(
                self._webview.FileDialog.FOLDER, directory=str(self._service.output_directory()), allow_multiple=False,
            )
            if paths:
                return self._service.save_settings({"output": str(Path(paths[0]).resolve())})
            return self._service.get_state()
        return self._call(browse)

    def select_conversion_files(self):
        def select():
            if self._window is None or self._webview is None:
                raise DownloadError("The file picker is not available yet.")
            directory = self._service.output_directory()
            paths = self._window.create_file_dialog(
                self._webview.FileDialog.OPEN,
                directory=str(directory if directory.is_dir() else Path.home()),
                allow_multiple=True,
                file_types=(
                    "Media files (*.png;*.jpg;*.jpeg;*.webp;*.avif;*.heic;*.heif;*.bmp;*.tif;*.tiff;*.gif;*.apng;*.ico;*.tga;*.jp2;*.psd;*.pcx;*.pnm;*.ppm;*.pgm;*.pbm;*.dds;*.qoi;*.sgi;*.xbm;*.mp3;*.wav;*.flac;*.m4a;*.aac;*.opus;*.ogg;*.aif;*.aiff;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.mpeg;*.mpg;*.wmv;*.flv;*.ts;*.mts;*.m2ts)",
                    "All files (*.*)",
                ),
            )
            return self._service.add_conversion_files(list(paths)) if paths else self._service.get_state()
        return self._call(select)

    def remove_conversion_file(self, file_id):
        return self._call(self._service.remove_conversion_file, file_id)

    def clear_conversion_files(self):
        return self._call(self._service.clear_conversion_files)

    def enqueue_conversions(self, file_ids, target, background="#FFFFFF"):
        return self._call(self._service.enqueue_conversions, file_ids, target, background)

    def prepare_conversion(self, task_id):
        def prepare():
            if not isinstance(task_id, str) or not task_id or len(task_id) > 100:
                raise DownloadError("Choose a completed file to convert.")
            source = self._service.completed_path(task_id)
            # This shortcut starts a conversion for that file, rather than
            # mixing it with an unrelated previous selection.
            self._service.clear_conversion_files()
            return self._service.add_conversion_files([str(source)])
        return self._call(prepare)

    def open_folder(self, task_id=None):
        def open_recorded_folder():
            # Javascript can select a task identifier, never an arbitrary path.
            if task_id is None:
                directory = self._service.output_directory()
                directory.mkdir(parents=True, exist_ok=True)
            else:
                if not isinstance(task_id, str) or not task_id or len(task_id) > 100:
                    raise DownloadError("Choose a completed download to open its folder.")
                directory = self._service.completed_path(task_id).parent
            _open_directory(directory)
        return self._call(open_recorded_folder)

    def clear_resume(self):
        def clear():
            count = discard_resumes(self._service.output_directory())
            state = self._service.get_state()
            state["notice"] = f"Cleared {count} inactive interrupted download(s). Active downloads were kept."
            return state
        return self._call(clear)

    def install_tools(self):
        def install():
            with self._tools_lock:
                if self._tools["status"] in ("ready", "installing"):
                    return
                self._tools["status"] = "installing"
                self._tools["error"] = None
                self._tools["message"] = "Preparing media tools…"
            self._installer = threading.Thread(target=self._install, name="unidown-tool-setup", daemon=True)
            self._installer.start()
        return self._call(install)

    def _tool_progress(self, event):
        with self._tools_lock:
            if not self._closed:
                self._tools.update(message=event["message"], downloaded_bytes=event.get("downloaded_bytes"), total_bytes=event.get("total_bytes"))

    def _install(self):
        try:
            from .tool_setup import prepare_tools
            prepare_tools(progress=self._tool_progress)
            self._refresh_tools()
            with self._tools_lock:
                if self._tools["status"] != "ready":
                    raise DownloadError("Some download tools are still missing. Try preparing them again.")
        except Exception as error:
            # FFmpeg may have installed successfully even if Deno failed. Local
            # audio/video conversion can use it independently of download setup.
            self._refresh_tools()
            with self._tools_lock:
                self._tools["status"] = "failed"
                self._tools["error"] = _safe_error(error)
                self._tools["message"] = None
        finally:
            if not self._closed:
                self._service.refresh_conversion_files()


class _AssetHandler(BaseHTTPRequestHandler):
    """Serve only bundled static assets; there is no HTTP command endpoint."""

    server_version = "MediaToolkit"
    sys_version = ""

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self._serve(False)

    def do_GET(self):
        self._serve(True)

    def _serve(self, body):
        if self.headers.get("Host") != self.server.expected_host:
            self.send_error(403)
            return
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc or parts.query or not parts.path.startswith(self.server.prefix):
            self.send_error(404)
            return
        relative = unquote(parts.path[len(self.server.prefix):]) or "index.html"
        path = (self.server.assets / relative).resolve()
        if not path.is_relative_to(self.server.assets) or not path.is_file() or path.suffix.lower() not in (".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2", ".txt"):
            self.send_error(404)
            return
        if path.suffix.lower() == ".html" and path != self.server.assets / "index.html":
            self.send_error(404)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if body:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


class AssetServer:
    def __init__(self, assets=ASSETS):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _AssetHandler)
        self._server.daemon_threads = True
        self._server.assets = Path(assets).resolve()
        self._server.expected_host = f"127.0.0.1:{self._server.server_port}"
        self._server.prefix = f"/{secrets.token_urlsafe(24)}/"
        self.url = f"http://{self._server.expected_host}{self._server.prefix}index.html"
        self._thread = threading.Thread(target=self._server.serve_forever, name="unidown-assets", daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _allowed_navigation(url, expected):
    # A single document is allowed; hash links do not load a different page.
    parts = urlsplit(str(url))
    target = urlsplit(expected)
    return (parts.scheme, parts.netloc, parts.path, parts.query) == (target.scheme, target.netloc, target.path, target.query)


def _guard_navigation(window, expected):
    """Attach WebView2 guards before its first navigation (pywebview 6.2.1)."""
    control = window.native.webview

    def navigation(_sender, args):
        if not _allowed_navigation(args.Uri, expected):
            args.Cancel = True

    def initialized(sender, args):
        if not args.IsSuccess:
            return

        def new_window(_sender, request):
            request.Handled = True

        sender.CoreWebView2.NewWindowRequested += new_window
        # Keep native delegates alive for the full window lifetime.
        window._unidown_popup_guard = new_window

    control.NavigationStarting += navigation
    control.CoreWebView2InitializationCompleted += initialized
    window._unidown_navigation_guard = (navigation, initialized)


def _dark_titlebar(window):
    """Use Windows' standard dark caption; preserve native window controls."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        enabled = wintypes.BOOL(1)
        handle = wintypes.HWND(window.native.Handle.ToInt64())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError):
        pass


def _enable_per_monitor_dpi():
    """Set source launches to PMv2 before pywebview creates its WinForms thread.

    Frozen launches declare the same setting in their executable manifest.
    pywebview's later legacy SetProcessDPIAware call cannot downgrade a process
    whose awareness has already been set.
    """
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        target = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = wintypes.BOOL
        if setter(target):
            return True
        # A manifest (or the embedding host) can have set the process default
        # already. Check the context instead of trying a weaker legacy mode.
        current = user32.GetThreadDpiAwarenessContext
        current.argtypes = []
        current.restype = ctypes.c_void_p
        equal = user32.AreDpiAwarenessContextsEqual
        equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        equal.restype = wintypes.BOOL
        return bool(equal(current(), target))
    except (AttributeError, OSError):
        return False


def _fatal(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main() -> int:
    multiprocessing.freeze_support()
    _enable_per_monitor_dpi()
    try:
        import webview
        from .desktop_service import DesktopService
    except ImportError:
        _fatal("The desktop dependencies are missing. Run scripts/install.cmd or scripts/update.cmd, then open start.cmd again.")
        return 1
    if not (ASSETS / "index.html").is_file():
        _fatal("The desktop interface is missing. Extract the complete release or update the source checkout.")
        return 1
    # Windowed executables do not have console streams. Libraries may still log
    # ordinary status text; keep those writes local and invisible.
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w", encoding="utf-8"))
    service = None
    server = None
    try:
        service = DesktopService(default_output=PROJECT_ROOT / "downloads", queue_scope="desktop")
        api = DesktopApi(service)
        server = AssetServer()
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        window = webview.create_window(
            APP_NAME, server.url, js_api=api, width=1000, height=760,
            min_size=(760, 600), background_color="#09090b", text_select=True,
        )
        api._window, api._webview = window, webview
        if os.name == "nt":
            window.events.before_show += partial(_guard_navigation, window, server.url)
            window.events.before_show += partial(_dark_titlebar, window)

        def closing():
            with api._tools_lock:
                preparing = api._tools["status"] == "installing"
            active = preparing or service.active_jobs()
            if active and not window.create_confirmation_dialog(
                f"Close {APP_NAME}?", "There is work in progress. Close the app and stop the current work? Your queue will be kept. Use Retry after reopening to continue.",
            ):
                return False
            api._closed = True
            service.shutdown()
            return True

        window.events.closing += closing
        storage = preferences_path().parent / "webview2"
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(gui="edgechromium" if os.name == "nt" else None, debug=False, private_mode=True, storage_path=str(storage))
        return 0
    except Exception:
        _fatal("The desktop window could not start. On Windows, install Microsoft Edge WebView2 Runtime from https://developer.microsoft.com/microsoft-edge/webview2/ and run scripts/update.cmd. See the README for terminal commands.")
        return 1
    finally:
        if service is not None:
            service.shutdown()
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(main())
