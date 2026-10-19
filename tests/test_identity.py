"""Regression tests for source identity and verified completed-file reuse."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from universal_downloader.downloader import DownloadRequest, download
from universal_downloader.identity import DownloadHistory, media_identity
from universal_downloader.inspection import MediaInspection


class _SilentLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


class IdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "downloads"
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)

    def inspected(self, url="https://first.example/item/123", *, title="Same title", color="red", extractor="Example", page_url=None):
        data = BytesIO()
        with Image.new("RGB", (8, 8), color) as image:
            image.save(data, "PNG")
        information = {
            "id": "123", "title": title, "extractor_key": extractor,
            "webpage_url": page_url or url, "image_url": "https://cdn.example/image.png",
        }
        return MediaInspection(url, information, ("image",), image_data=data.getvalue(), image_extension="png")

    def save(self, inspected, target=None):
        return download(DownloadRequest(inspected.url, "image", self.output, target), inspected=inspected, logger=_SilentLogger())

    def test_same_id_and_title_from_different_sources_never_collide(self):
        first_info = self.inspected()
        second_info = self.inspected("https://second.example/item/123", color="blue")
        first = self.save(first_info)
        second = self.save(second_info)
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(second.already_exists)
        self.assertEqual(first.path.read_bytes(), first_info.image_data)
        self.assertEqual(second.path.read_bytes(), second_info.image_data)
        self.assertIn("first.example", first.path.name)
        self.assertIn("second.example", second.path.name)

    def test_title_change_reuses_verified_download_without_renaming_it(self):
        original = self.save(self.inspected(title="Old title"))
        renamed = self.save(self.inspected(title="A completely new title"))
        self.assertEqual(original.path, renamed.path)
        self.assertTrue(renamed.already_exists)
        self.assertEqual(renamed.title, "A completely new title")
        self.assertEqual(list(self.output.iterdir()), [original.path])

    def test_same_generic_filename_in_different_url_paths_is_not_the_same_asset(self):
        first = self.save(self.inspected("https://same.example/one/123.png", extractor="Generic"))
        second = self.save(self.inspected("https://same.example/two/123.png", color="blue", extractor="Generic"))
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(second.already_exists)

    def test_generic_query_selected_assets_remain_distinct(self):
        first = self.save(self.inspected("https://same.example/asset?file=one", extractor="Generic"))
        second = self.save(self.inspected("https://same.example/asset?file=two", color="blue", extractor="Generic"))
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(second.already_exists)

    def test_tracking_and_fragments_do_not_duplicate_a_generic_asset(self):
        original = self.save(self.inspected("https://same.example/asset.png?size=large", extractor="Generic"))
        repeated = self.save(self.inspected("https://same.example/asset.png?size=large&utm_source=share#preview", extractor="Generic"))
        self.assertEqual(original.path, repeated.path)
        self.assertTrue(repeated.already_exists)

    def test_unknown_www_subdomain_is_not_assumed_to_host_the_same_asset(self):
        original = self.save(self.inspected("https://same.example/asset.png", extractor="Generic"))
        separate = self.save(self.inspected("https://www.same.example/asset.png", extractor="Generic", color="blue"))
        self.assertNotEqual(original.path, separate.path)
        self.assertFalse(separate.already_exists)

    def test_twitter_domain_aliases_share_provider_identity(self):
        twitter = self.inspected("https://twitter.com/account/status/123", extractor="Twitter")
        renamed = self.inspected("https://x.com/account/status/123", extractor="Twitter")
        original = self.save(twitter)
        repeated = self.save(renamed)
        self.assertEqual(original.path, repeated.path)
        self.assertTrue(repeated.already_exists)

    def test_changed_file_with_same_size_and_timestamp_is_preserved_and_not_reused(self):
        inspected = self.inspected()
        first = self.save(inspected)
        timestamp = first.path.stat().st_mtime_ns
        changed = b"x" * first.path.stat().st_size
        first.path.write_bytes(changed)
        os.utime(first.path, ns=(timestamp, timestamp))
        recovered = self.save(inspected)
        self.assertFalse(recovered.already_exists)
        self.assertNotEqual(recovered.path, first.path)
        self.assertEqual(first.path.read_bytes(), changed)
        self.assertEqual(recovered.path.read_bytes(), inspected.image_data)
        repeated = self.save(inspected)
        self.assertTrue(repeated.already_exists)
        self.assertEqual(repeated.path, recovered.path)

    def test_deleted_completed_file_is_downloaded_again(self):
        inspected = self.inspected()
        first = self.save(inspected)
        first.path.unlink()
        recovered = self.save(inspected)
        self.assertFalse(recovered.already_exists)
        self.assertEqual(recovered.path, first.path)
        self.assertEqual(recovered.path.read_bytes(), inspected.image_data)

    def test_unindexed_conflicting_file_is_preserved_even_when_its_bytes_match(self):
        inspected = self.inspected()
        request = DownloadRequest(inspected.url, "image", self.output)
        identity = media_identity(request, inspected, "image", None)
        self.output.mkdir()
        unverified = self.output / identity.filename(inspected.information["title"], "png")
        unverified.write_bytes(inspected.image_data)
        result = self.save(inspected)
        self.assertFalse(result.already_exists)
        self.assertNotEqual(result.path, unverified)
        self.assertEqual(unverified.read_bytes(), inspected.image_data)

    def test_original_and_each_conversion_have_separate_persistent_identity(self):
        inspected = self.inspected()
        original = self.save(inspected)
        converted = self.save(inspected, "jpg")
        self.assertNotEqual(original.path, converted.path)
        with Image.open(converted.path) as image:
            self.assertEqual(image.format, "JPEG")
        repeated = self.save(self.inspected(title="Renamed"), "jpg")
        self.assertTrue(repeated.already_exists)
        self.assertEqual(repeated.path, converted.path)
        self.assertEqual(original.path.read_bytes(), inspected.image_data)

    def test_resolved_page_identity_is_shared_with_a_google_wrapper(self):
        direct = self.inspected()
        wrapper = self.inspected("https://www.google.com/imgres?imgurl=anything", page_url=direct.url)
        original = self.save(direct)
        repeated = self.save(wrapper)
        self.assertTrue(repeated.already_exists)
        self.assertEqual(original.path, repeated.path)

    def test_generic_and_image_identity_use_resolved_asset_not_original_wrapper(self):
        for extractor in ("Generic", "Image"):
            with self.subTest(extractor=extractor):
                direct = self.inspected("https://media.example/animation.gif?asset=123", extractor=extractor)
                wrapper = self.inspected("https://www.google.com/url?q=anything", page_url=direct.url, extractor=extractor)
                wrapper.information["id"] = "different-wrapper-derived-id"
                wrapper.information["original_url"] = wrapper.url
                direct_key = media_identity(DownloadRequest(direct.url, "image", self.output), direct, "image", "mp4")
                wrapper_key = media_identity(DownloadRequest(wrapper.url, "image", self.output), wrapper, "image", "mp4")
                self.assertEqual(direct_key.key, wrapper_key.key)
                self.assertEqual(wrapper_key.source, "media.example")

    def test_quality_and_size_constraints_are_part_of_video_identity(self):
        inspected = MediaInspection("https://example.com/watch/123", {"id": "123", "title": "Video", "webpage_url": "https://example.com/watch/123"}, ("video",))
        unconstrained = media_identity(SimpleNamespace(max_height=None, max_size=None), inspected, "video", None)
        height_limited = media_identity(SimpleNamespace(max_height=720, max_size=None), inspected, "video", None)
        size_limited = media_identity(SimpleNamespace(max_height=None, max_size=1_000_000), inspected, "video", None)
        converted = media_identity(SimpleNamespace(max_height=None, max_size=None), inspected, "video", "mp4")
        self.assertEqual(len({item.key for item in (unconstrained, height_limited, size_limited, converted)}), 4)

    def test_animation_image_and_video_aliases_use_the_same_identity(self):
        inspected = self.inspected()
        inspected = MediaInspection(inspected.url, inspected.information, ("image",), animated=True, animation_video=True)
        request = DownloadRequest(inspected.url, "image", self.output)
        self.assertEqual(media_identity(request, inspected, "image", "gif"), media_identity(request, inspected, "video", "gif"))

    def test_animation_resolution_limits_have_distinct_identity_and_names(self):
        inspected = self.inspected()
        inspected = MediaInspection(inspected.url, inspected.information, ("image",), animated=True, animation_video=True)
        original = media_identity(SimpleNamespace(max_height=None, max_size=None), inspected, "image", "mp4")
        limited = media_identity(SimpleNamespace(max_height=360, max_size=None), inspected, "image", "mp4")
        alias = media_identity(SimpleNamespace(max_height=360, max_size=None), inspected, "video", "mp4")
        self.assertNotEqual(original.key, limited.key)
        self.assertEqual(limited.key, alias.key)
        self.assertIn("360p", limited.filename("Animation", "mp4"))

    def test_history_stays_outside_download_folder_and_survives_new_instances(self):
        inspected = self.inspected()
        result = self.save(inspected)
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.assertEqual(DownloadHistory(self.output).cached(identity), result.path)
        self.assertTrue((self.root / "state" / "downloads.sqlite3").is_file())
        self.assertEqual(list(self.output.iterdir()), [result.path])

    def test_concurrent_publications_of_same_identity_reuse_one_verified_file(self):
        inspected = self.inspected()
        self.output.mkdir()
        staged = self.root / "staged.png"
        staged.write_bytes(inspected.image_data)
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(DownloadHistory(self.output).publish, staged, identity, "Same title") for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(existing for _, existing in results), [False, True])
        self.assertEqual(list(self.output.iterdir()), [results[0][0]])

    def test_corrupt_history_never_blocks_download_or_reuses_an_unverified_file(self):
        database = self.root / "state" / "downloads.sqlite3"
        database.parent.mkdir()
        corrupt = b"this is not a SQLite database"
        database.write_bytes(corrupt)
        inspected = self.inspected()
        first = self.save(inspected)
        second = self.save(inspected)
        self.assertFalse(first.already_exists)
        self.assertFalse(second.already_exists)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), inspected.image_data)
        self.assertEqual(second.path.read_bytes(), inspected.image_data)
        self.assertEqual(database.read_bytes(), corrupt)

    def test_permission_failure_disables_cache_but_preserves_wrong_existing_media(self):
        inspected = self.inspected()
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.output.mkdir()
        conflict = self.output / identity.filename(inspected.information["title"], "png")
        wrong_media = b"unrelated existing file"
        conflict.write_bytes(wrong_media)
        logger = Mock()
        history = DownloadHistory(self.output, logger=logger)
        staged = self.root / "complete.png"
        staged.write_bytes(inspected.image_data)
        with patch("universal_downloader.identity.sqlite3.connect", side_effect=PermissionError("Access denied")) as connection:
            self.assertIsNone(history.cached(identity))
            result, exists = history.publish(staged, identity, inspected.information["title"])
            self.assertIsNone(history.cached(identity))
        self.assertFalse(exists)
        self.assertNotEqual(result, conflict)
        self.assertEqual(result.read_bytes(), inspected.image_data)
        self.assertEqual(conflict.read_bytes(), wrong_media)
        self.assertEqual(connection.call_count, 1)
        logger.warning.assert_called_once()

    def test_locked_history_falls_back_without_a_long_wait_or_wrong_cache_hit(self):
        inspected = self.inspected()
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.output.mkdir()
        history = DownloadHistory(self.output)
        self.assertIsNone(history.cached(identity))
        connection = sqlite3.connect(history.database)
        self.addCleanup(connection.close)
        connection.execute("BEGIN EXCLUSIVE")
        staged = self.root / "complete.png"
        staged.write_bytes(inspected.image_data)
        try:
            self.assertIsNone(history.cached(identity))
            result, exists = history.publish(staged, identity, inspected.information["title"])
        finally:
            connection.rollback()
        self.assertFalse(exists)
        self.assertEqual(result.read_bytes(), inspected.image_data)
        self.assertEqual(list(self.output.iterdir()), [result])

    def test_commit_failure_after_publish_does_not_duplicate_or_discard_the_completed_file(self):
        inspected = self.inspected()
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.output.mkdir()
        staged = self.root / "complete.png"
        staged.write_bytes(inspected.image_data)
        real_connect = sqlite3.connect

        class CommitFailure:
            def __init__(self, *args, **kwargs):
                self.connection = real_connect(*args, **kwargs)

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def commit(self):
                raise sqlite3.OperationalError("Database became unavailable")

            def rollback(self):
                self.connection.rollback()

            def close(self):
                self.connection.close()

        with patch("universal_downloader.identity.sqlite3.connect", new=CommitFailure):
            result, exists = DownloadHistory(self.output).publish(staged, identity, inspected.information["title"])
        self.assertFalse(exists)
        self.assertEqual(result.read_bytes(), inspected.image_data)
        self.assertEqual(list(self.output.iterdir()), [result])

    def test_file_publication_failure_is_not_mistaken_for_a_history_failure(self):
        inspected = self.inspected()
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.output.mkdir()
        staged = self.root / "complete.png"
        staged.write_bytes(inspected.image_data)
        logger = Mock()
        with patch("universal_downloader.identity.publish_file", side_effect=PermissionError("Output folder denied")), self.assertRaises(PermissionError):
            DownloadHistory(self.output, logger=logger).publish(staged, identity, inspected.information["title"])
        logger.warning.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_record_insertion_failure_after_publish_keeps_exactly_one_completed_file(self):
        inspected = self.inspected()
        identity = media_identity(DownloadRequest(inspected.url, "image", self.output), inspected, "image", None)
        self.output.mkdir()
        staged = self.root / "complete.png"
        staged.write_bytes(inspected.image_data)
        real_connect = sqlite3.connect

        class InsertFailure:
            def __init__(self, *args, **kwargs):
                self.connection = real_connect(*args, **kwargs)

            def execute(self, statement, *args):
                if statement.startswith("INSERT"):
                    raise sqlite3.OperationalError("Database became read-only")
                return self.connection.execute(statement, *args)

            def commit(self):
                self.connection.commit()

            def rollback(self):
                self.connection.rollback()

            def close(self):
                self.connection.close()

        with patch("universal_downloader.identity.sqlite3.connect", new=InsertFailure):
            result, exists = DownloadHistory(self.output).publish(staged, identity, inspected.information["title"])
        self.assertFalse(exists)
        self.assertEqual(result.read_bytes(), inspected.image_data)
        self.assertEqual(list(self.output.iterdir()), [result])

    def test_malformed_history_row_is_ignored_instead_of_becoming_a_cache_hit(self):
        inspected = self.inspected()
        first = self.save(inspected)
        connection = sqlite3.connect(self.root / "state" / "downloads.sqlite3")
        try:
            connection.execute("UPDATE downloads SET filename = ?, size = ?, sha256 = ?", (b"invalid binary filename", "not a file size", "invalid checksum"))
            connection.commit()
        finally:
            connection.close()
        recovered = self.save(inspected)
        self.assertFalse(recovered.already_exists)
        self.assertNotEqual(recovered.path, first.path)
        self.assertEqual(first.path.read_bytes(), inspected.image_data)
        self.assertEqual(recovered.path.read_bytes(), inspected.image_data)


if __name__ == "__main__":
    unittest.main()
