"""Verify and save original image bytes without modifying the source image."""

from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
import warnings

from PIL import Image, UnidentifiedImageError
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError

from .errors import DownloadError

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_EXTENSIONS = {
    "JPEG": "jpg", "PNG": "png", "WEBP": "webp", "AVIF": "avif", "GIF": "gif",
    "BMP": "bmp", "TIFF": "tiff", "ICO": "ico", "MPO": "jpg",
}
class _PageImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []
        self.title = ""

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").lower()
            value = attributes.get("content", "")
            if name in ("og:image", "og:image:url", "twitter:image", "twitter:image:src") and value:
                self.urls.append(value)
            elif name in ("og:title", "twitter:title") and value and not self.title:
                self.title = value
        elif tag == "video" and attributes.get("poster"):
            self.urls.append(attributes["poster"])
        elif tag == "link" and attributes.get("rel") == "image_src" and attributes.get("href"):
            self.urls.append(attributes["href"])


def looks_like_image(url: str) -> bool:
    return Path(urlsplit(url).path).suffix.lower() in {
        ".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp", ".tif", ".tiff", ".ico",
    }


def _image_url(base: str, value: str) -> str | None:
    try:
        url = urljoin(base, value)
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    return url


def _open_response(downloader, request):
    try:
        return downloader.urlopen(request)
    except HTTPError as error:
        error.close()
        raise


def _read_response(response, limit: int, progress_hook=None) -> bytes:
    try:
        total = int(response.headers.get("Content-Length", "0")) or None
    except ValueError:
        total = None
    if total and total > limit:
        raise DownloadError(f"The image exceeds the {limit // 1_048_576} MB download limit.")
    output = BytesIO()
    while chunk := response.read(65_536):
        output.write(chunk)
        if output.tell() > limit:
            raise DownloadError(f"The image exceeds the {limit // 1_048_576} MB download limit.")
        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": output.tell(), "total_bytes": total})
    return output.getvalue()


def check_image(data: bytes) -> tuple[str, bool]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise DownloadError("This image exceeds the 40 million pixel limit.")
                extension = IMAGE_EXTENSIONS.get(image.format)
                if extension is None:
                    raise DownloadError("This image type is not supported by the installed image engine.")
                animated = getattr(image, "is_animated", False)
                image.verify()
                return extension, animated
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise DownloadError("The URL did not return a readable image.") from error
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise DownloadError("This image is too large to process.") from error


def page_or_image(downloader, url: str, progress_hook=None) -> tuple[dict, bytes | None]:
    """Fallback for a direct image or a page exposing an Open Graph image."""
    with _open_response(downloader, Request(url)) as response:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type.startswith(("video/", "audio/")):
            raise DownloadError("This direct media URL has no thumbnail. Use the video's webpage URL or a direct image URL.")
        page = content_type in ("text/html", "application/xhtml+xml")
        data = _read_response(response, 2 * 1024 * 1024 if page else MAX_IMAGE_BYTES, progress_hook if not page else None)
    identifier = sha256(url.encode()).hexdigest()[:12]
    title = Path(unquote(urlsplit(url).path)).stem or "image"
    info = {"id": identifier, "title": title, "webpage_url": url, "http_headers": {"Referer": url}}
    if page:
        parser = _PageImages()
        parser.feed(data.decode("utf-8", errors="replace"))
        if not parser.urls:
            raise DownloadError("No thumbnail or image found on this page. Try a direct image URL.")
        info["title"] = parser.title or title
        info["thumbnails"] = [{"url": candidate} for value in parser.urls if (candidate := _image_url(url, value))]
        return info, None
    check_image(data)
    info["image_url"] = url
    return info, data


def save_image(downloader, information: dict, direct_data: bytes | None = None) -> tuple[Path, bool]:
    """Save the verified source bytes, keeping format, metadata, and animation."""
    data = direct_data if direct_data is not None else fetch_image(downloader, information)
    extension, _ = check_image(data)
    info = {**information, "title": information.get("title") or "image", "id": information.get("id") or "image", "ext": extension}
    destination = Path(downloader.prepare_filename(info)).resolve()
    try:
        output = destination.open("xb")
    except FileExistsError:
        if not destination.is_file():
            raise DownloadError(f"The output path is not a file: {destination}")
        return destination, True
    try:
        with output:
            output.write(data)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination, False


def fetch_image(downloader, information: dict, progress_hook=None) -> bytes:
    """Fetch and verify the best reachable thumbnail, without saving a file."""
    thumbnails = list(information.get("thumbnails") or [])
    if information.get("image_url"):
        thumbnails.append({"url": information["image_url"]})
    if information.get("thumbnail"):
        thumbnails.append({"url": information["thumbnail"]})
    thumbnails.sort(key=lambda item: (
        item.get("preference") or 0,
        (item.get("width") or 0) * (item.get("height") or 0),
        item.get("width") or 0,
    ), reverse=True)
    tried = set()
    last_error = None
    for thumbnail in thumbnails:
        raw_url = thumbnail.get("url")
        if not raw_url:
            continue
        url = _image_url(information.get("webpage_url") or "", raw_url)
        if url is None:
            continue
        if url in tried:
            continue
        tried.add(url)
        headers = {**(information.get("http_headers") or {}), **(thumbnail.get("http_headers") or {})}
        if information.get("webpage_url"):
            headers.setdefault("Referer", information["webpage_url"])
        try:
            with _open_response(downloader, Request(url, headers=headers)) as response:
                data = _read_response(response, MAX_IMAGE_BYTES, progress_hook)
            check_image(data)
            return data
        except (DownloadError, RequestError, OSError, ValueError) as error:
            last_error = error
    if last_error:
        raise DownloadError(f"No readable thumbnail could be downloaded: {last_error}") from last_error
    raise DownloadError("This URL does not expose a thumbnail. Try a direct image URL or another video.")
