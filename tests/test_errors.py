"""Classify actual extractor failures before considering generic discovery."""

import socket
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError as UrlHTTPError

from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError as YtDlpError, ExtractorError, GeoRestrictedError, UnsupportedError

from universal_downloader.errors import DownloadError, extraction_error, is_unsupported


def _http(status):
    return HTTPError(SimpleNamespace(status=status, reason="fixture", close=lambda: None))


def _wrapped(error):
    return YtDlpError("Extractor failed", exc_info=(type(error), error, None))


class ErrorTests(unittest.TestCase):
    def test_only_typed_unsupported_errors_allow_generic_fallback(self):
        for error in (UnsupportedError("https://example.com/page"), _wrapped(UnsupportedError("https://example.com/page"))):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(is_unsupported(error))
                self.assertEqual(extraction_error(error).code, "unsupported")
        for error in (YtDlpError("Unsupported URL: https://example.com/page"), ExtractorError("This site is unsupported", expected=True), ValueError("Unsupported URL")):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(is_unsupported(error))

    def test_nested_http_failures_keep_their_real_classification(self):
        cases = ((401, "authentication", "signing in"), (403, "access_denied", "denied access"), (404, "not_found", "not found"), (410, "not_found", "no longer available"), (429, "rate_limited", "limiting requests"), (500, "server", "temporarily unavailable"), (503, "server", "temporarily unavailable"), (400, "http", "could not serve"))
        for status, code, fragment in cases:
            with self.subTest(status=status):
                error = _wrapped(ExtractorError("Unable to extract metadata", cause=_http(status)))
                translated = extraction_error(error)
                self.assertEqual(translated.code, code)
                self.assertIn(fragment, str(translated))
                self.assertIn(str(status), str(translated))
                self.assertFalse(is_unsupported(error))

    def test_standard_library_http_error_is_classified_through_explicit_cause(self):
        error = YtDlpError("outer")
        error.__cause__ = UrlHTTPError("https://example.com/private", 403, "Forbidden", {}, None)
        self.assertEqual(extraction_error(error).code, "access_denied")
        self.assertFalse(is_unsupported(error))

    def test_network_and_timeout_failures_never_allow_image_fallback(self):
        for cause in (socket.gaierror("DNS failed"), ConnectionResetError("reset"), TimeoutError("timed out")):
            with self.subTest(cause=type(cause).__name__):
                error = _wrapped(ExtractorError("Unable to fetch media", cause=TransportError(cause=cause)))
                translated = extraction_error(error)
                self.assertEqual(translated.code, "network")
                self.assertFalse(is_unsupported(error))
                if isinstance(cause, TimeoutError):
                    self.assertIn("timed out", str(translated))

    def test_real_http_or_network_failure_overrides_unsupported_wrapper(self):
        for cause, code in ((_http(403), "access_denied"), (TransportError("Connection failed"), "network")):
            with self.subTest(code=code):
                error = UnsupportedError("https://example.com/page")
                error.__cause__ = cause
                self.assertFalse(is_unsupported(error))
                self.assertEqual(extraction_error(error).code, code)

    def test_context_and_cycle_wrappers_are_followed_without_looping(self):
        error = YtDlpError("outer")
        error.__context__ = _http(429)
        error.exc_info = (YtDlpError, error, None)
        self.assertEqual(extraction_error(error).code, "rate_limited")
        self.assertFalse(is_unsupported(error))

    def test_known_restrictions_and_expected_messages_remain_clear(self):
        restricted = _wrapped(GeoRestrictedError("Not available here"))
        self.assertEqual(extraction_error(restricted).code, "restricted")
        self.assertIn("location", str(extraction_error(restricted)))
        private = _wrapped(ExtractorError("This video is private", expected=True))
        self.assertEqual(str(extraction_error(private)), "This video is private")
        self.assertFalse(is_unsupported(private))
        restricted_wrapper = GeoRestrictedError("Not available here", cause=UnsupportedError("https://example.com/page"))
        self.assertFalse(is_unsupported(restricted_wrapper))
        self.assertEqual(extraction_error(restricted_wrapper).code, "restricted")

    def test_existing_user_error_is_preserved_and_unexpected_details_stay_out_of_normal_output(self):
        existing = DownloadError("Choose a valid URL", code="validation")
        self.assertIs(extraction_error(existing), existing)
        unexpected = extraction_error(_wrapped(ExtractorError("Internal traceback details")))
        self.assertEqual(unexpected.code, "extraction")
        self.assertIn("--verbose", str(unexpected))
        self.assertNotIn("Internal traceback", str(unexpected))


if __name__ == "__main__":
    unittest.main()
