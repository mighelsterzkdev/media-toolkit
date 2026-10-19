"""DPI bootstrap is checked without creating or controlling any native window."""

import ctypes
import unittest
from unittest.mock import Mock, patch

from universal_downloader import gui


class DpiBootstrapTests(unittest.TestCase):
    def test_source_launch_requests_per_monitor_v2_before_any_window(self):
        user32 = Mock()
        user32.SetProcessDpiAwarenessContext.return_value = True
        with patch.object(gui.platform, "system", return_value="Windows"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            self.assertTrue(gui._enable_per_monitor_dpi())
        self.assertEqual(user32.SetProcessDpiAwarenessContext.call_args.args[0].value, ctypes.c_void_p(-4).value)
        user32.GetThreadDpiAwarenessContext.assert_not_called()

    def test_manifest_awareness_is_verified_without_downgrading_to_legacy(self):
        user32 = Mock()
        user32.SetProcessDpiAwarenessContext.return_value = False
        user32.GetThreadDpiAwarenessContext.return_value = -4
        user32.AreDpiAwarenessContextsEqual.return_value = True
        with patch.object(gui.platform, "system", return_value="Windows"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            self.assertTrue(gui._enable_per_monitor_dpi())
        user32.SetProcessDPIAware.assert_not_called()
        user32.AreDpiAwarenessContextsEqual.assert_called_once()


if __name__ == "__main__":
    unittest.main()
