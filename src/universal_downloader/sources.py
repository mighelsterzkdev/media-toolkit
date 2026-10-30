"""Provider adapters preserve source metadata before generic media inspection."""

import re
from urllib.parse import urlsplit

from yt_dlp.extractor.twitter import TwitterIE

from .tiktok import TikTokAudioIE

_PHOTO_SUFFIX = re.compile(r"/photo/\d+/?(?=$|[?#])")


class _TwitterMediaIE(TwitterIE):
    """Retain the API's media type, which the standard extractor omits."""

    @classmethod
    def ie_key(cls):
        return TwitterIE.ie_key()

    def _extract_status(self, *args, **kwargs):
        status = super()._extract_status(*args, **kwargs)
        for tweet in (status, status.get("quoted_status") or {}):
            for media in (tweet.get("extended_entities") or {}).get("media") or []:
                identifier = media.get("id_str") or media.get("id")
                if identifier is not None and media.get("type"):
                    self._media_types[str(identifier)] = media["type"]
                if (identifier is not None and media.get("type") == "photo" and (source := media.get("media_url_https"))
                        and str(identifier) not in {photo[0] for photo in self._photos}):
                    self._photos.append((str(identifier), source, media.get("original_info") or {}))
        return status

    def _real_extract(self, url):
        self._media_types = {}
        self._photos = []
        # Share links name one photo (/photo/1). yt-dlp reads that suffix as a
        # video selection and rejects photos, so inspect the whole post instead.
        url = _PHOTO_SUFFIX.sub("", url, count=1)
        result = super()._real_extract(url)
        entries = result.get("entries") if isinstance(result.get("entries"), list) else [result]
        for entry in entries:
            if media_type := self._media_types.get(str(entry.get("id"))):
                entry["media_type"] = media_type
        if not self._photos or self._match_valid_url(url).group("index"):
            return result
        # Without attached videos yt-dlp follows the first link in the text;
        # the post's own photos are what the link refers to.
        return self._with_photos(result, [] if result.get("_type") == "url" else entries)

    def _with_photos(self, result, entries):
        """yt-dlp skips photos; offer each one at original size beside any videos."""
        videos = [entry for entry in entries if entry.get("formats")]
        base = {key: value for key, value in result.items() if key not in ("_type", "url", "ie_key", "entries", "formats", "thumbnails", "thumbnail", "duration", "subtitles")}
        title = re.sub(r" #\d+$", "", base.get("title") or "")
        count = len(videos) + len(self._photos)
        photos = []
        for index, (identifier, source, size) in enumerate(self._photos, len(videos) + 1):
            stem, dot, extension = source.rpartition(".")
            original = f"{stem}?format={extension}&name=orig" if dot and "/" not in extension else source
            photos.append({
                **base, "id": identifier, "title": f"{title} #{index}" if count > 1 else title,
                "media_type": "photo", "image_url": original,
                "thumbnails": [{"url": original, "width": size.get("width"), "height": size.get("height")}],
            })
        if count == 1:
            return photos[0]
        return self.playlist_result([*videos, *photos], base.get("id"), title, base.get("description"))


def configure_sources(downloader) -> None:
    downloader.add_info_extractor(_TwitterMediaIE())
    downloader.add_info_extractor(TikTokAudioIE())


def is_video_animation(information: dict) -> bool:
    sources = information.get("formats") or [information]
    sources = [source for source in sources if source.get("url") and not source.get("has_drm")]
    if not sources:
        return False
    media_type = information.get("media_type")
    if media_type in ("animated_gif", "gif"):
        return True
    if media_type in ("video", "photo"):
        return False
    # Direct CDN links have no post metadata. Keep their known animation route
    # as a fallback; ordinary silent clips are not assumed to be GIFs.
    for source in sources:
        try:
            url = urlsplit(source["url"])
        except ValueError:
            continue
        if url.hostname == "video.twimg.com" and url.path.startswith("/tweet_video/") and url.path.lower().endswith(".mp4"):
            return True
    return False
