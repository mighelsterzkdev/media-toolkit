"""Provider adapters preserve source metadata before generic media inspection."""

from urllib.parse import urlsplit

from yt_dlp.extractor.twitter import TwitterIE


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
        return status

    def _real_extract(self, url):
        self._media_types = {}
        result = super()._real_extract(url)
        entries = result.get("entries") if isinstance(result.get("entries"), list) else [result]
        for entry in entries:
            if media_type := self._media_types.get(str(entry.get("id"))):
                entry["media_type"] = media_type
        return result


def configure_sources(downloader) -> None:
    downloader.add_info_extractor(_TwitterMediaIE())


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
