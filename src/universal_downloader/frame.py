"""Windows 11 style custom title bar.

The WinForms form is borderless, so its own size arithmetic never counts a
caption. The native window styles that bring snapping, minimize/maximize
animations, the taskbar-aware maximized size and the system menu are added
back, and WM_NCCALCSIZE gives the whole window to the web view. The web
header then acts as the caption: dragging, edge resizing and the window
buttons are forwarded to Windows as the same native messages a real frame
would produce.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os

WM_SIZE, SIZE_MINIMIZED = 0x0005, 1
WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE, SC_CLOSE = 0xF020, 0xF030, 0xF120, 0xF060
GWL_STYLE = -16
FRAME_STYLES = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000  # caption, thick frame, system menu, min/max boxes
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010  # also NOSIZE, NOMOVE, NOZORDER, NOACTIVATE
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
HIT_TESTS = {
    "drag": 2, "resize-left": 10, "resize-right": 11, "resize-top": 12, "resize-top-left": 13,
    "resize-top-right": 14, "resize-bottom": 15, "resize-bottom-left": 16, "resize-bottom-right": 17,
}
COMMANDS = {"minimize": SC_MINIMIZE, "close": SC_CLOSE}
ACTIONS = frozenset((*HIT_TESTS, *COMMANDS, "maximize"))

LRESULT = ctypes.c_ssize_t
# WINFUNCTYPE exists only on Windows; the module must still import elsewhere.
SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t) if os.name == "nt" else None


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


@lru_cache(maxsize=1)
def _apis():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = LRESULT
    return user32, comctl32


def _handle(window):
    return wintypes.HWND(window.native.Handle.ToInt64())


def _on_ui_thread(window, function):
    from System import Action  # pythonnet, available wherever pywebview uses WinForms
    window.native.Invoke(Action(function))


def _wake_web_view(window):
    control = getattr(window.native, "webview", None)
    if control is not None:
        control.Visible = False
        control.Visible = True


def use_custom_frame(window):
    """Restore native frame behaviour on a borderless window, without a caption."""
    if os.name != "nt":
        return
    from System import Action
    user32, comctl32 = _apis()

    def procedure(hwnd, message, wparam, lparam, _id, _data):
        if message == WM_NCCALCSIZE and wparam:
            # The whole window is client area. A maximized window overhangs the
            # monitor by its frame, so inset it to keep the content on screen.
            if user32.IsZoomed(hwnd):
                dpi = user32.GetDpiForWindow(hwnd) or 96
                padding = user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
                x = user32.GetSystemMetricsForDpi(SM_CXFRAME, dpi) + padding
                y = user32.GetSystemMetricsForDpi(SM_CYFRAME, dpi) + padding
                rect = _NCCALCSIZE_PARAMS.from_address(lparam).rgrc[0]
                rect.left, rect.top, rect.right, rect.bottom = rect.left + x, rect.top + y, rect.right - x, rect.bottom - y
            return 0
        if message == WM_SIZE:
            if wparam == SIZE_MINIMIZED:
                window._custom_frame_minimized = True
            elif getattr(window, "_custom_frame_minimized", False):
                # WebView2 pauses while minimized. A restore that bypasses
                # SC_RESTORE (for example ShowWindow) does not wake it, so show
                # the control again once the window is back.
                window._custom_frame_minimized = False
                window.native.BeginInvoke(Action(lambda: _wake_web_view(window)))
        return comctl32.DefSubclassProc(hwnd, message, wparam, lparam)

    callback = SUBCLASSPROC(procedure)
    window._custom_frame_procedure = callback  # keep the callback alive with the window

    def install():
        hwnd = _handle(window)
        comctl32.SetWindowSubclass(hwnd, callback, 1, 0)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, user32.GetWindowLongPtrW(hwnd, GWL_STYLE) | FRAME_STYLES)
        corner = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED)

    _on_ui_thread(window, install)


def window_action(window, action):
    """Forward a title bar action from the web header as the native frame would."""
    if action not in ACTIONS:
        raise ValueError("Unsupported window action.")
    user32, _ = _apis()
    hwnd = _handle(window)
    if action == "maximize":
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_RESTORE if user32.IsZoomed(hwnd) else SC_MAXIMIZE, 0)
    elif action in COMMANDS:
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, COMMANDS[action], 0)
    else:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))

        def begin():
            user32.ReleaseCapture()
            user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, HIT_TESTS[action], (point.y & 0xFFFF) << 16 | point.x & 0xFFFF)
        _on_ui_thread(window, begin)
