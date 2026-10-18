import unittest
from unittest.mock import patch

from yt_dlp.utils import ExtractorError

from universal_downloader.tiktok import TikTokAudioIE, is_tiktok_sound_url


class TikTokUrlTests(unittest.TestCase):
    def test_sound_routes_accept_real_slugs_and_identifiers(self):
        for url in (
            "https://www.tiktok.com/music/som-original-7697963541225163528",
            "https://tiktok.com/music/7697963541225163528?lang=en",
            "https://www.tiktok.com/music/%E9%9F%B3%E6%A5%BD-7697963541225163528",
            "https://m.tiktok.com/music/sound-name-7697963541225163528/",
            "https://www.tiktok.com/embed/music/7697963541225163528",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_tiktok_sound_url(url))

    def test_sound_adapter_does_not_capture_other_sites_or_video_routes(self):
        for url in (
            "https://tiktok.com.example.com/music/sound-7697963541225163528",
            "https://example.com/music/sound-7697963541225163528",
            "https://www.tiktok.com/@person/video/7697963541225163528",
            "https://www.tiktok.com/music/sound-without-id",
            "https://www.tiktok.com/music/sound-123garbage",
            "https://www.tiktok.com/tag/music",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_tiktok_sound_url(url))

    def test_original_audio_extension_is_read_from_path_query_or_content_type(self):
        extractor = TikTokAudioIE()
        for url, mime_type, expected, header_calls in (
            ("https://cdn.example.com/original.mp3", None, "mp3", 0),
            ("https://cdn.example.com/original?mime_type=audio_mp4", None, "m4a", 0),
            ("https://cdn.example.com/original", "audio/mpeg; charset=utf-8", "mp3", 1),
        ):
            with self.subTest(url=url), patch.object(extractor, "_request_webpage") as request:
                request.return_value.__enter__.return_value.headers = {"Content-Type": mime_type}
                result = extractor._audio_result({"playUrl": url}, "123", "https://www.tiktok.com/music/123")
                self.assertEqual(result["formats"][0]["ext"], expected)
                self.assertEqual(request.call_count, header_calls)

    def test_unknown_or_invalid_audio_is_rejected_without_guessing_a_format(self):
        extractor = TikTokAudioIE()
        with patch.object(extractor, "_request_webpage") as request:
            request.return_value.__enter__.return_value.headers = {"Content-Type": "text/html"}
            with self.assertRaisesRegex(ExtractorError, "supported audio format"):
                extractor._audio_result({"playUrl": "https://cdn.example.com/original"}, "123", "https://www.tiktok.com/music/123")
        for url in ("file:///original.mp3", "https://user:secret@example.com/audio.mp3", "https://[invalid"):
            with self.subTest(url=url), self.assertRaisesRegex(ExtractorError, "usable audio URL"):
                extractor._audio_result({"playUrl": url}, "123", "https://www.tiktok.com/music/123")


if __name__ == "__main__":
    unittest.main()
