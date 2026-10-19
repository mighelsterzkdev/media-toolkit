"""Translate extractor failures without disguising access or network problems."""

from __future__ import annotations

import socket
from urllib.error import HTTPError as UrlHTTPError, URLError

from yt_dlp.networking.exceptions import HTTPError, RequestError
from yt_dlp.utils import ExtractorError, GeoRestrictedError, UnsupportedError


class DownloadError(Exception):
    """An error that can be displayed directly to the user."""

    def __init__(self, message: str, *, code: str = "unknown"):
        super().__init__(message)
        self.code = code


def _exceptions(error: BaseException) -> tuple[BaseException, ...]:
    """yt-dlp wraps original exceptions in several different public fields."""
    pending = [error]
    found = []
    visited = set()
    while pending and len(found) < 100:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in visited:
            continue
        visited.add(id(current))
        found.append(current)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            pending.append(exc_info[1])
        pending.extend((getattr(current, "cause", None), current.__cause__, current.__context__))
    return tuple(found)


def _http_status(error: BaseException) -> int | None:
    if isinstance(error, HTTPError):
        return error.status
    if isinstance(error, UrlHTTPError):
        return error.code
    return None


def _network_error(error: BaseException) -> bool:
    return isinstance(error, (RequestError, URLError, TimeoutError, ConnectionError, socket.gaierror))


def is_unsupported(error: BaseException) -> bool:
    """Only a typed unsupported URL may fall back to generic page discovery."""
    chain = _exceptions(error)
    return any(isinstance(item, UnsupportedError) for item in chain) and not any(
        _http_status(item) is not None or _network_error(item) or isinstance(item, (GeoRestrictedError, DownloadError)) for item in chain
    )


def extraction_error(error: BaseException) -> DownloadError:
    """Give real extraction failures a useful message and stable reason code."""
    if isinstance(error, DownloadError):
        return error
    chain = _exceptions(error)
    status = next((status for item in chain if (status := _http_status(item)) is not None), None)
    if status is not None:
        if status == 401:
            return DownloadError("This content requires signing in (HTTP 401). Use a publicly accessible link.", code="authentication")
        if status == 403:
            return DownloadError("The site denied access to this content (HTTP 403). It may require sign-in, be restricted, or block automated requests.", code="access_denied")
        if status in (404, 410):
            return DownloadError(f"This content was not found or is no longer available (HTTP {status}).", code="not_found")
        if status == 429:
            return DownloadError("The site is limiting requests (HTTP 429). Wait a while and try again.", code="rate_limited")
        if status >= 500:
            return DownloadError(f"The site is temporarily unavailable (HTTP {status}). Try again later.", code="server")
        return DownloadError(f"The site could not serve this content (HTTP {status}). Check the link and try again.", code="http")
    if any(_network_error(item) for item in chain):
        if any(isinstance(item, (TimeoutError, socket.timeout)) for item in chain):
            return DownloadError("The connection to the site timed out. Check your connection and try again.", code="network")
        return DownloadError("Could not connect to the site. Check your connection and try again.", code="network")
    if is_unsupported(error):
        return DownloadError("This URL is not supported by the media extractor. Try a direct media URL or the page that contains it.", code="unsupported")
    if any(isinstance(item, GeoRestrictedError) for item in chain):
        return DownloadError("This content is restricted in your location.", code="restricted")
    for item in chain:
        if isinstance(item, ExtractorError) and item.expected:
            return DownloadError(item.orig_msg, code="unavailable")
    return DownloadError("Could not retrieve this media. Try again later or use --verbose for technical details.", code="extraction")
