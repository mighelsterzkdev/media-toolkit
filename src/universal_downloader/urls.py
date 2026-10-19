"""Validate URLs and unwrap explicit Google result targets without changing assets."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import DownloadError


def validate_url(value: str) -> str:
    url = value.strip()
    if len(url) >= 2 and url[0] == url[-1] and url[0] in ("'", '"'):
        url = url[1:-1].strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
        valid = valid and not parts.username and not parts.password
        parts.port
    except ValueError:
        valid = False
    if not valid or any(character.isspace() or ord(character) < 32 for character in url):
        raise DownloadError("Invalid URL. Paste a complete URL starting with http:// or https://.")
    return url


def is_google(host: str) -> bool:
    return bool(re.fullmatch(r"(?:www\.)?google\.(?:com|com\.[a-z]{2}|co\.[a-z]{2}|[a-z]{2,3})", host.lower()))


def google_target(url: str) -> str:
    """Only unwrap a selected target; a search query is never a media selection."""
    seen = set()
    for _ in range(8):
        if url in seen:
            raise DownloadError("This shared link contains a redirect loop.")
        seen.add(url)
        parts = urlsplit(url)
        if not is_google(parts.hostname or ""):
            break
        query = parse_qs(parts.query)
        keys = ("imgurl", "mediaurl") if parts.path == "/imgres" else ("url", "q") if parts.path == "/url" else ()
        target = next((query[key][0] for key in keys if query.get(key)), None)
        if not target:
            if parts.path in ("/search", "/imgres"):
                raise DownloadError("This Google link does not select a media file. Open a result and copy its image or source-page link.")
            break
        # parse_qs already decodes one layer. Decode further only when even
        # the scheme is encoded, preserving percent escapes in signed URLs.
        for _ in range(2):
            if target.startswith(("http://", "https://")):
                break
            target = unquote(target)
        url = validate_url(target)
    else:
        raise DownloadError("This shared link has too many nested redirects.")
    return url
