"""Resolve TikTok sound pages to the exact tagged audio, rather than a playlist."""

from urllib.parse import urlsplit

from yt_dlp.extractor.tiktok import TikTokBaseIE, TikTokIE, TikTokSoundIE
from yt_dlp.networking import HEADRequest
from yt_dlp.utils import ExtractorError, determine_ext, int_or_none, mimetype2ext, parse_qs, url_or_none
from yt_dlp.utils.traversal import traverse_obj


class TikTokAudioIE(TikTokBaseIE):
    IE_NAME = "tiktok:audio"
    _VALID_URL = r"https?://(?:(?:www|m)\.)?tiktok\.com/(?:music/(?:[^/?#]+-)?|embed/music/)(?P<id>\d+)(?:[/?#]|$)"

    @classmethod
    def ie_key(cls):
        return TikTokSoundIE.ie_key()

    @staticmethod
    def _matches(music, sound_id):
        return bool(isinstance(music, dict) and str(music.get("id")) == sound_id and not music.get("private") and url_or_none(music.get("playUrl")))

    def _page_music(self, webpage, sound_id):
        data = self._get_universal_data(webpage, sound_id)
        for key in ("desktop.musicPage.musicDetail", "webapp.music-detail"):
            music = traverse_obj(data, (key, "musicInfo", "music", {dict}))
            if self._matches(music, sound_id):
                return music
        return None

    def _audio_result(self, music, sound_id, webpage_url):
        audio_url = music["playUrl"]
        try:
            parsed = urlsplit(audio_url)
        except ValueError as error:
            raise ExtractorError("TikTok did not provide a usable audio URL.", expected=True) from error
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ExtractorError("TikTok did not provide a usable audio URL.", expected=True)
        allowed = {"mp3", "m4a", "aac", "ogg", "opus", "flac", "wav", "webm", "mp4"}
        extension = determine_ext(audio_url, default_ext=None)
        if extension not in allowed:
            mime_type = (parse_qs(audio_url).get("mime_type") or [""])[-1].replace("_", "/")
            extension = mimetype2ext(mime_type)
        if extension not in allowed:
            with self._request_webpage(
                HEADRequest(audio_url, headers={"Referer": "https://www.tiktok.com/"}),
                sound_id, note="Checking audio format",
            ) as response:
                extension = mimetype2ext(response.headers.get("Content-Type", "").split(";", 1)[0])
        if extension not in allowed:
            raise ExtractorError("TikTok did not identify a supported audio format.", expected=True)
        if extension == "mp4":
            extension = "m4a"
        title = music.get("title") or f"TikTok sound {sound_id}"
        author = music.get("authorName")
        return {
            "id": sound_id,
            "title": f"{title} - {author}" if author else title,
            "track": title,
            "artists": [author] if author else None,
            "duration": int_or_none(music.get("duration")),
            "webpage_url": webpage_url,
            "http_headers": {"Referer": "https://www.tiktok.com/"},
            "formats": [{"format_id": "audio", "url": audio_url, "ext": extension, "vcodec": "none"}],
        }

    def _real_extract(self, url):
        sound_id = self._match_id(url)
        webpage_url = f"https://www.tiktok.com/music/{sound_id}" if "/embed/music/" in url else url
        webpage = self._download_webpage(webpage_url, sound_id, note="Checking sound page")
        if music := self._page_music(webpage, sound_id):
            return self._audio_result(music, sound_id, webpage_url)

        # The official music embed exposes associated posts when the desktop
        # page only contains an empty client-side shell. Resolve their tagged
        # sound metadata; never substitute the video's mixed audio track.
        embed_url = f"https://www.tiktok.com/embed/music/{sound_id}"
        webpage = self._download_webpage(
            embed_url, sound_id, note="Checking public sound metadata", headers={"Referer": webpage_url},
        )
        state = self._search_json(
            r'<script[^>]+\bid="__FRONTITY_CONNECT_STATE__"[^>]*>', webpage,
            "public sound data", sound_id, end_pattern=r"</script>", default={},
        )
        routes = traverse_obj(state, ("source", "data", {dict})) or {}
        entry = traverse_obj(routes, ((f"/embed/music/{sound_id}", f"/embed/music/{sound_id}/"), {dict}), get_all=False) or {}
        if entry.get("isError") or traverse_obj(entry, ("embedInfo", "statusCode")) not in (None, 0):
            raise ExtractorError("This TikTok sound is unavailable or restricted.", expected=True)
        video_extractor = TikTokIE(self._downloader)
        for video in (traverse_obj(entry, ("videoList", {list})) or [])[:3]:
            if not isinstance(video, dict):
                continue
            video_id = str(video.get("id") or "")
            if not video_id.isascii() or not video_id.isdecimal() or video.get("privateItem"):
                continue
            video_url = TikTokBaseIE._create_url("_", video_id)
            video_data, status = video_extractor._extract_web_data_and_status(video_url, video_id, fatal=False)
            music = traverse_obj(video_data, ("music", {dict}))
            if status == 0 and self._matches(music, sound_id):
                return self._audio_result(music, sound_id, webpage_url)
        raise ExtractorError(
            "TikTok did not expose downloadable audio for this sound. Try the URL of a public video that uses it.",
            expected=True,
        )


def is_tiktok_sound_url(url: str) -> bool:
    return TikTokAudioIE.suitable(url)
