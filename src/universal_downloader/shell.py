"""Taskbar progress and completion notifications for the desktop window.

Both go through the window's own WinForms thread: ITaskbarList3 shows
progress on the taskbar button, and a short-lived NotifyIcon balloon (shown
by Windows 10/11 as a toast) reports finished work while the app is in the
background.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

NO_PROGRESS, INDETERMINATE, NORMAL, ERROR = 0, 1, 2, 4
_CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11D0-958A-006097C9A090}"
_IID_TASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
_HR_INIT, _SET_PROGRESS_VALUE, _SET_PROGRESS_STATE = 3, 9, 10


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    value = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
    return value


def _system_language():
    try:
        return "pt" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x16 else "en"
    except (AttributeError, OSError):
        return "en"


def language(preference):
    return _system_language() if preference == "system" else preference


class Feedback:
    """Mirror the app's work on the taskbar; notify only while it is not in front."""

    def __init__(self, window, app_name):
        self._window = window
        self._app_name = app_name
        self._taskbar = None
        self._shown = None
        self._icons = []

    def _invoke(self, function):
        if os.name != "nt":
            return
        from System import Action  # pythonnet, available wherever pywebview uses WinForms
        try:
            # BeginInvoke never waits for the UI thread, so closing cannot deadlock.
            self._window.native.BeginInvoke(Action(function))
        except Exception:
            pass

    def _hwnd(self):
        return wintypes.HWND(self._window.native.Handle.ToInt64())

    def _method(self, index, *argtypes):
        vtable = ctypes.cast(self._taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])

    def progress(self, state, value=0.0):
        """Show ``state``; ``value`` (0..1) applies to NORMAL and ERROR."""
        shown = (state, round(max(0.0, min(1.0, value)) * 1000) if state in (NORMAL, ERROR) else 0)
        if shown == self._shown:
            return
        self._shown = shown

        def apply():
            try:
                if self._taskbar is None:
                    pointer = ctypes.c_void_p()
                    ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_TASKBAR_LIST)), None, 1,
                                                         ctypes.byref(_guid(_IID_TASKBAR_LIST3)), ctypes.byref(pointer))
                    self._taskbar = pointer
                    self._method(_HR_INIT)(self._taskbar)
                hwnd = self._hwnd()
                self._method(_SET_PROGRESS_STATE, wintypes.HWND, ctypes.c_int)(self._taskbar, hwnd, shown[0])
                if shown[0] in (NORMAL, ERROR):
                    self._method(_SET_PROGRESS_VALUE, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)(self._taskbar, hwnd, shown[1], 1000)
            except OSError:
                pass
        self._invoke(apply)

    def notify(self, title, message):
        def show():
            try:
                user32 = ctypes.windll.user32
                user32.GetForegroundWindow.restype = ctypes.c_void_p
                if user32.GetForegroundWindow() == self._window.native.Handle.ToInt64():
                    return
                from System.Windows.Forms import NotifyIcon, Timer, ToolTipIcon
                icon = NotifyIcon()
                icon.Icon = self._window.native.Icon  # the window's own icon, also when run from source
                icon.Text = self._app_name[:63]
                icon.Visible = True
                timer = Timer()
                timer.Interval = 10_000

                def restore(*_args):
                    self._window.restore()
                    ctypes.windll.user32.SetForegroundWindow(self._window.native.Handle.ToInt64())

                def dispose(*_args):
                    timer.Stop()
                    icon.Visible = False
                    icon.Dispose()
                    self._icons.remove(entry)

                entry = (icon, timer, restore, dispose)
                self._icons.append(entry)  # keep the delegates alive until disposed
                icon.BalloonTipClicked += restore
                timer.Tick += dispose
                icon.ShowBalloonTip(5000, title, message, getattr(ToolTipIcon, "None"))
                timer.Start()
            except Exception:
                pass
        self._invoke(show)
