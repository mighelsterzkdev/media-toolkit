"""Bounded discovery of original assets from ordinary HTML and shared links."""

from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import DownloadError as YtDlpError

from .errors import DownloadError, extraction_error
from .dependencies import find_ffmpeg
from .images import MAX_IMAGE_BYTES, check_image
from .inspection import inspect_streams
from .urls import google_target, validate_url

MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 12
MAX_DISCOVERY_BYTES = 64 * 1024 * 1024
_SPACE = " \t\r\n\f"


def srcset_candidates(value: str) -> list[tuple[str, float, str]]:
    """Read HTML srcset tokens without splitting commas inside asset URLs.

    Widths are intrinsic pixels; densities are relative variants. Downloading
    prefers the largest declaration rather than emulating a browser viewport.
    """
    found = []
    position = 0
    while position < len(value) and len(found) < 64:
        while position < len(value) and value[position] in _SPACE + ",":
            position += 1
        start = position
        while position < len(value) and value[position] not in _SPACE:
            position += 1
        url = value[start:position]
        descriptors = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            start = position
            while position < len(value) and value[position] != ",":
                position += 1
            descriptors = value[start:position].strip()
        position += 1
        if not url or url.lower().startswith(("data:", "javascript:", "file:", "blob:")):
            continue
        if not descriptors:
            found.append((url, 1.0, "x"))
        elif re.fullmatch(r"[0-9]+w", descriptors):
            width = float(descriptors[:-1])
            if math.isfinite(width) and width > 0:
                found.append((url, width, "w"))
        elif re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?x", descriptors):
            density = float(descriptors[:-1])
            if math.isfinite(density) and density > 0:
                found.append((url, density, "x"))
    return found


def _usable_image_url(url) -> bool:
    if not isinstance(url, str) or not url or any(character.isspace() for character in url):
        return False
    if url.lower().startswith(("data:", "javascript:", "file:", "blob:")):
        return False
    try:
        basename = Path(urlsplit(url).path).name.lower()
    except ValueError:
        return False
    return not re.fullmatch(r"(?:transparent|spacer|blank|loading|placeholder)(?:[-_.][^/]*)?\.(?:gif|png|jpe?g|webp)", basename)


def image_variants(attrs: dict, sources=()) -> tuple[str, ...]:
    """One DOM image is one asset, with declared resolution/format fallbacks."""
    width = attrs.get("width") or ""
    base = float(width) if re.fullmatch(r"[1-9][0-9]{0,8}", width) else 1.0
    ranked = []
    width_descriptors = False
    original = attrs.get("data-original")
    if _usable_image_url(original):
        ranked.append((original, math.inf))
    for source in (*sources, attrs):
        media_type = (source.get("type") or "").split(";", 1)[0].lower()
        if media_type and media_type not in ("image/gif", "image/jpeg", "image/jpg", "image/png", "image/webp", "image/avif", "image/bmp", "image/tiff", "image/x-icon"):
            continue
        value = source.get("data-srcset") or source.get("data-lazy-srcset") or source.get("data-original-srcset") or source.get("srcset") or ""
        for url, size, unit in srcset_candidates(value):
            if _usable_image_url(url):
                width_descriptors |= unit == "w"
                ranked.append((url, size if unit == "w" else size * base))
    for key in ("data-src", "data-lazy-src", "src"):
        if _usable_image_url(attrs.get(key)):
            # With w descriptors, src is a fallback rather than a candidate
            # whose CSS display width could outrank the declared source set.
            ranked.append((attrs[key], 0 if width_descriptors else base))
    return tuple(dict.fromkeys(url for url, _ in sorted(ranked, key=lambda item: item[1], reverse=True)))[:6]


def _image_signature(data: bytes) -> bool:
    return data.startswith((b"GIF87a", b"GIF89a", b"\x89PNG\r\n", b"\xff\xd8\xff", b"BM", b"II*\0", b"MM\0*", b"\0\0\1\0")) or (
        data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    ) or (data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"heic", b"mif1"))


def _html(data: bytes, content_type: str) -> bool:
    return content_type in ("text/html", "application/xhtml+xml") or data.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


class PageMedia(HTMLParser):
    """Read explicit media declarations, never execute page JavaScript."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates = []
        self.images = []
        self.covers = []
        self.title = ""
        self.base = None
        self.redirect = None
        self.refresh = False
        self._title = False
        self._script = None
        self._json = []
        self._container = None
        self._picture = None
        self.variants = {}

    def add(self, url, kind, *, primary=True, cover=False):
        if isinstance(url, str) and url:
            collection = self.covers if cover else self.candidates if primary else self.images
            if len(collection) < MAX_CANDIDATES and (url, kind) not in collection:
                collection.append((url, kind))

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "title":
            self._title = True
        elif tag == "base" and not self.base:
            self.base = attrs.get("href")
        elif tag == "meta":
            name = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content") or ""
            if name in ("og:title", "twitter:title") and content:
                self.title = content
            elif name in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
                self.add(content, "video")
            elif name in ("og:audio", "og:audio:url", "og:audio:secure_url"):
                self.add(content, "audio")
            elif name in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
                self.add(content, "image", primary=False, cover=True)
            elif (attrs.get("http-equiv") or "").lower() == "refresh":
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*;\s*url\s*=\s*(.*?)\s*", content, re.IGNORECASE)
                if match and float(match[1]) <= 1:
                    self.redirect = match[2].strip(" '\"")
                    self.refresh = True
        elif tag in ("video", "audio"):
            self._container = tag
            self.add(attrs.get("src"), tag)
            if tag == "video":
                self.add(attrs.get("poster"), "image", primary=False, cover=True)
        elif tag == "source" and self._container:
            self.add(attrs.get("src"), self._container)
        elif tag == "picture":
            self._picture = []
        elif tag == "source" and self._picture is not None:
            if len(self._picture) < MAX_CANDIDATES:
                self._picture.append(attrs)
        elif tag == "img":
            try:
                tiny = int(attrs.get("width", "999")) <= 64 and int(attrs.get("height", "999")) <= 64
            except (TypeError, ValueError):
                tiny = False
            sources = self._picture or ()
            conditional = {}
            unconditional = []
            for source in sources:
                media = (source.get("media") or "").strip()
                if media and media.lower() != "all":
                    conditional.setdefault(media, []).append(source)
                else:
                    unconditional.append(source)
            groups = [image_variants(attrs, unconditional)]
            # Media conditions can change the crop/content (art direction),
            # not just resolution. Without a viewport, expose those branches
            # rather than silently replacing the fallback image with a crop.
            groups.extend(image_variants({"width": attrs.get("width")}, branch) for branch in conditional.values())
            responsive = any(attrs.get(key) for key in ("srcset", "data-srcset", "data-lazy-srcset", "data-original-srcset", "data-original", "data-src", "data-lazy-src")) or bool(self._picture)
            if (not tiny or responsive) and "logo" not in ((attrs.get("alt") or "") + " " + (attrs.get("class") or "")).lower():
                for variants in groups:
                    if variants:
                        self.add(variants[0], "image", primary=False)
                        if (variants[0], "image") in self.images:
                            self.variants[variants[0]] = variants
        elif tag == "link" and (attrs.get("rel") or "").lower() == "image_src":
            self.add(attrs.get("href"), "image", primary=False)
        elif tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._script = True
            self._json = []
        elif tag == "a":
            # Google's short image links sometimes end at an HTML redirect.
            href = attrs.get("href") or ""
            if "/imgres?" in href and "imgurl=" in href and not self.redirect:
                self.redirect = href

    def handle_data(self, data):
        if self._title and not self.title:
            self.title = data.strip()
        if self._script:
            self._json.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag in ("video", "audio"):
            self._container = None
        elif tag == "picture":
            self._picture = None
        elif tag == "script" and self._script:
            self._script = None
            try:
                self._structured(json.loads("".join(self._json)))
            except (ValueError, RecursionError):
                pass

    def _structured(self, value, depth=0):
        if depth > 12:
            return
        if isinstance(value, list):
            for item in value[:MAX_CANDIDATES]:
                self._structured(item, depth + 1)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = types if isinstance(types, list) else [types]
            for name, kind in (("VideoObject", "video"), ("AudioObject", "audio"), ("ImageObject", "image")):
                if name in types:
                    self.add(value.get("contentUrl"), kind, primary=kind != "image")
            for key in ("@graph", "mainEntity", "associatedMedia", "image", "video", "audio"):
                if key in value:
                    self._structured(value[key], depth + 1)


def _asset_url(base, value):
    try:
        return google_target(validate_url(urljoin(base, value)))
    except (DownloadError, ValueError):
        return None


def discover(downloader, url: str, *, image_only=False, _seen=None, _budget=None):
    """Return verified image/declared media assets, or None for a direct stream.

    Video/audio candidates still pass stream probing in MediaInspection. Images
    are verified from their bytes, including files with no usable extension.
    """
    seen = set() if _seen is None else _seen
    budget = [MAX_DISCOVERY_BYTES, 0, time.monotonic() + 40] if _budget is None else _budget
    url = google_target(url)
    if url in seen or len(seen) >= 5:
        raise DownloadError("This page contains a redirect loop or too many nested pages.")
    budget[1] += 1
    if budget[0] <= 0 or budget[1] > 24 or time.monotonic() >= budget[2]:
        raise DownloadError("This page exceeded the media discovery limit. Try a direct media link.")
    seen.add(url)
    with downloader.urlopen(Request(url, extensions={"timeout": min(8, max(1, budget[2] - time.monotonic()))})) as response:
        final = validate_url(response.url)
        resolved = google_target(final)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
        try:
            declared = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            declared = 0
        if content_type.startswith("image/") and declared > MAX_IMAGE_BYTES:
            raise DownloadError("The image exceeds the 32 MB download limit.")
        if content_type in ("text/html", "application/xhtml+xml") and declared > MAX_PAGE_BYTES:
            raise DownloadError("This page exceeds the 4 MB discovery limit.")
        prefix = response.read(4096)
        budget[0] -= len(prefix)
        if resolved != final:
            return discover(downloader, resolved, image_only=image_only, _seen=seen, _budget=budget)
        page = _html(prefix, content_type)
        image = _image_signature(prefix) or content_type.startswith("image/")
        if not page and not image:
            return None
        limit = min(MAX_PAGE_BYTES if page else MAX_IMAGE_BYTES, budget[0] + len(prefix))
        try:
            length = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > limit:
            raise DownloadError("This page or image exceeds the discovery size limit.")
        data = bytearray(prefix)
        while chunk := response.read(min(65536, max(1, limit + 1 - len(data)))):
            data.extend(chunk)
            budget[0] -= len(chunk)
            if len(data) > limit or time.monotonic() >= budget[2]:
                raise DownloadError("This page or image exceeds the discovery size limit.")
    identifier = sha256(final.encode()).hexdigest()[:12]
    title = Path(unquote(urlsplit(final).path)).stem or "media"
    info = {"id": identifier, "title": title, "webpage_url": final, "original_url": url,
            "extractor_key": "Generic", "http_headers": {"Referer": final}}
    if not page:
        extension, animated = check_image(bytes(data))
        info.update(image_url=final, ext=extension)
        return [(info, bytes(data))]
    parser = PageMedia()
    parser.feed(bytes(data).decode("utf-8", errors="replace"))
    base = urljoin(final, parser.base) if parser.base else final
    # An immediate refresh on a redirect shell is navigation. Never let it
    # replace declared primary media or an independent image on a real page.
    if parser.redirect and (urlsplit(url).hostname in ("images.app.goo.gl", "share.google") or parser.refresh and not (parser.candidates or parser.images)):
        redirect = _asset_url(base, parser.redirect)
        if redirect:
            return discover(downloader, redirect, image_only=image_only, _seen=seen, _budget=budget)
    candidates = []
    primary = parser.candidates
    # A metadata cover can be a static preview of an animated image in the
    # body. Preserve both as explicit choices when no video/audio is declared.
    images = [*parser.covers, *parser.images] if not primary else parser.covers
    candidate_urls = set()
    for value, kind in [*primary, *images]:
        if image_only and kind != "image":
            continue
        candidate = _asset_url(base, value)
        if candidate and candidate not in candidate_urls and candidate != final:
            variants = tuple(dict.fromkeys(filter(None, (_asset_url(base, variant) for variant in parser.variants.get(value, (value,))))))
            candidates.append((candidate, kind, variants))
            candidate_urls.add(candidate)
            if len(candidates) >= MAX_CANDIDATES:
                break
    if not candidates:
        if image_only:
            raise DownloadError("No readable image or thumbnail was declared on this page. Try a direct image URL.")
        raise DownloadError("No downloadable video, audio, or image was declared on this page. Try the media file or its original source-page link.")
    assets = []
    failures = []
    has_primary = bool(parser.candidates) and not image_only
    for candidate, kind, variants in candidates[:MAX_CANDIDATES]:
        if budget[0] <= 0 or budget[1] >= 24 or time.monotonic() >= budget[2]:
            break
        try:
            if kind == "image":
                found = None
                for variant in variants:
                    if budget[0] <= 0 or budget[1] >= 24 or time.monotonic() >= budget[2]:
                        break
                    try:
                        found = discover(downloader, variant, image_only=image_only, _seen=set(seen), _budget=budget)
                    except (DownloadError, RequestError, YtDlpError, OSError) as error:
                        failures.append(error if isinstance(error, DownloadError) else extraction_error(error))
                        continue
                    if found:
                        break
                if not found:
                    continue
            else:
                found = discover(downloader, candidate, image_only=image_only, _seen=set(seen), _budget=budget)
            if found is None:
                if kind == "image":
                    continue
                metadata = downloader.extract_info(candidate, download=False)
                if not metadata or metadata.get("_type") in ("playlist", "multi_video"):
                    continue
                metadata.update(webpage_url=candidate, original_url=final, title=parser.title or metadata.get("title") or title)
                video, audio, _ = inspect_streams(metadata, find_ffmpeg(), downloader.cookiejar)
                if not video and not audio:
                    raise DownloadError("The declared media stream could not be verified.")
                covers = parser.covers or parser.images
                if covers:
                    thumbnail = _asset_url(base, covers[0][0])
                    if thumbnail:
                        metadata.setdefault("thumbnail", thumbnail)
                assets.append((metadata, None))
            else:
                for metadata, image_data in found:
                    metadata.update(title=parser.title or metadata.get("title") or title, original_url=final)
                    # A cover is kept as a thumbnail on the primary video. It
                    # must never replace an inaccessible stream on this page.
                    if has_primary and image_data:
                        if not check_image(image_data)[1] or not any(data is None for _, data in assets):
                            continue
                    assets.append((metadata, image_data))
        except (DownloadError, RequestError, YtDlpError, OSError) as error:
            failures.append(error if isinstance(error, DownloadError) else extraction_error(error))
    animated = [(metadata, data) for metadata, data in assets if data and check_image(data)[1]]
    if animated:
        assets = animated + [(metadata, data) for metadata, data in assets if not data or not check_image(data)[1]]
    if not assets:
        if failures:
            raise DownloadError("The page's media could not be accessed. " + str(failures[0])) from failures[0]
        raise DownloadError("No readable media could be verified on this page.")
    # The same asset can be declared by several standard tags.
    unique = {}
    for metadata, image_data in assets:
        unique.setdefault(metadata.get("image_url") or metadata.get("url") or metadata["webpage_url"], (metadata, image_data))
    return list(unique.values())
