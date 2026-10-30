import unittest
from unittest.mock import patch

from yt_dlp.extractor.twitter import TwitterIE

from universal_downloader.sources import _TwitterMediaIE


class TwitterShareLinkTests(unittest.TestCase):
    def extracted_url(self, url):
        seen = []
        with patch.object(TwitterIE, "_real_extract", lambda _self, value: seen.append(value) or {"id": "1"}):
            _TwitterMediaIE()._real_extract(url)
        return seen[0]

    def test_photo_share_links_inspect_the_whole_post(self):
        post = "https://x.com/someone/status/2115900123766985170"
        for url, expected in (
            (post + "/photo/1", post),
            (post + "/photo/3/", post),
            (post + "/photo/2?s=20", post + "?s=20"),
            (post + "/photo/1#top", post + "#top"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.extracted_url(url), expected)

    def test_video_selection_and_plain_posts_are_unchanged(self):
        for url in (
            "https://x.com/someone/status/2115900123766985170",
            "https://x.com/someone/status/2115900123766985170/video/2",
            "https://x.com/photo/status/2115900123766985170",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.extracted_url(url), url)


if __name__ == "__main__":
    unittest.main()
