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


class TwitterPhotoTests(unittest.TestCase):
    post = "https://x.com/someone/status/2115900123766985170"

    def extract(self, photos, result, url=None):
        status = {"extended_entities": {"media": [
            {"id_str": str(index), "type": "photo", "media_url_https": f"https://pbs.twimg.com/media/P{index}.jpg",
             "original_info": {"width": 4000, "height": 3000}} for index in photos]}}

        def extract(extractor, _url):
            extractor._extract_status("2115900123766985170")
            return dict(result)

        with patch.object(TwitterIE, "_extract_status", lambda _self, _id: status), patch.object(TwitterIE, "_real_extract", extract):
            return _TwitterMediaIE()._real_extract(url or self.post)

    def test_single_photo_uses_the_original_size(self):
        info = self.extract([1], {"id": "2115900123766985170", "title": "Someone - hello"})
        self.assertEqual(info["id"], "1")
        self.assertEqual(info["title"], "Someone - hello")
        self.assertEqual(info["image_url"], "https://pbs.twimg.com/media/P1?format=jpg&name=orig")
        self.assertEqual(info["media_type"], "photo")

    def test_every_photo_becomes_a_choice(self):
        info = self.extract([1, 2, 3], {"id": "2115900123766985170", "title": "Someone - hello"}, self.post + "/photo/2")
        self.assertEqual(info["_type"], "playlist")
        self.assertEqual([entry["id"] for entry in info["entries"]], ["1", "2", "3"])
        self.assertEqual([entry["title"] for entry in info["entries"]], [f"Someone - hello #{index}" for index in (1, 2, 3)])

    def test_videos_stay_first_beside_photos(self):
        video = {"id": "9", "title": "Someone - hello", "formats": [{"url": "https://video.twimg.com/v.mp4"}]}
        info = self.extract([1], video)
        self.assertEqual([entry["id"] for entry in info["entries"]], ["9", "1"])
        self.assertEqual(info["entries"][1]["title"], "Someone - hello #2")
        self.assertNotIn("formats", info["entries"][1])

    def test_linked_page_does_not_replace_the_posts_photos(self):
        info = self.extract([1, 2], {"_type": "url", "url": "https://youtube.com/watch?v=x", "id": "2115900123766985170", "title": "Someone - hello"})
        self.assertEqual([entry["id"] for entry in info["entries"]], ["1", "2"])
        self.assertNotIn("url", info["entries"][0])

    def test_video_selection_ignores_photos(self):
        video = {"id": "9", "title": "Someone - hello", "formats": [{"url": "https://video.twimg.com/v.mp4"}]}
        self.assertEqual(self.extract([1], video, self.post + "/video/1")["id"], "9")


if __name__ == "__main__":
    unittest.main()
