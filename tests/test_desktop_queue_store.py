"""Validate saved desktop tasks without trusting credentials or malformed JSON."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from universal_downloader.desktop_queue_store import (
    DesktopQueueStore, MAX_QUEUE_STATE_BYTES, RESTORED_MESSAGE,
    resolve_restored_selection, restore_task, selection_descriptor, task_record,
)
from universal_downloader.errors import DownloadError
from universal_downloader.inspection import MediaInspection


class DesktopQueueStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="media-queue-state-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        self.store = DesktopQueueStore(self.root / "downloads")
        self.addCleanup(self.store.close)

    def task(self, **changes):
        settings = {"output": str(self.root / "downloads"), "max_height": None, "max_size": None}
        task = {"id": uuid4().hex, "url": "https://example.com/media", "title": "Example", "action": "download",
                "source_format": None, "kind": "video", "format": "original", "status": "queued", "path": None,
                "error": None, "can_convert": False, "_settings": settings,
                "_identity": ("https://example.com/media", "default", "original", settings["output"], None, None),
                "_inspected": None, "_selection": None, "_attempt": None}
        task.update(changes)
        return task

    def test_active_restores_stopped_and_completed_preserves_valid_output_path(self):
        active = self.task(status="downloading")
        complete = self.task(status="completed", path=str(self.root / "downloads" / "movie.mp4"), can_convert=True)
        self.assertTrue(self.store.save([active, complete]))
        restored = self.store.load()
        self.assertEqual(restored[0]["status"], "cancelled")
        self.assertEqual(restored[0]["error"], RESTORED_MESSAGE)
        self.assertIsNone(restored[0]["_inspected"])
        self.assertEqual(restored[0]["_identity"], active["_identity"])
        self.assertEqual(restored[1]["status"], "completed")
        self.assertEqual(restored[1]["path"], complete["path"])
        self.assertEqual(restored[1]["progress"], 100)

    def test_only_whitelisted_metadata_and_hashes_are_saved(self):
        inspected = MediaInspection("https://example.com/media", {"id": "video-7", "url": "https://cdn.example.com/asset?token=SECRET",
                                    "extractor_key": "Example", "http_headers": {"Authorization": "SECRET"}}, ("video",), cookies=("SECRET",))
        task = self.task(_selection=selection_descriptor(inspected), _inspected=inspected)
        task["_unrelated"] = "SECRET"
        self.assertTrue(self.store.save([task]))
        raw = self.store.path.read_text(encoding="utf-8")
        for private in ("SECRET", "http_headers", "cookies", "cdn.example.com", "_unrelated"):
            self.assertNotIn(private, raw)
        self.assertIn("https://example.com/media", raw)

    def test_conversions_keep_source_fingerprint_but_not_extra_metadata(self):
        image = {"name": "source.png", "source": str(self.root / "source.png"), "signature": {"size": 40, "mtime_ns": -20, "ctime_ns": -10, "sha256": "a" * 64},
                 "format": "png", "media_type": "image", "transparent": True, "targets": ["jpg"], "http_headers": {"Authorization": "SECRET"}}
        task = self.task(url="", action="convert", kind="image", source_format="png", format="jpg", _image=image, _background="#000000")
        self.assertTrue(self.store.save([task]))
        restored = self.store.load()[0]
        self.assertEqual(restored["_image"]["signature"], image["signature"])
        self.assertEqual(restored["_background"], "#000000")
        self.assertNotIn("http_headers", restored["_image"])
        self.assertNotIn("SECRET", self.store.path.read_text(encoding="utf-8"))

    def test_video_background_choices_stay_distinct_after_restart_only_for_flattening_outputs(self):
        source = {"name": "source.mkv", "source": str(self.root / "source.mkv"),
                  "signature": {"size": 40, "mtime_ns": 20, "ctime_ns": 10, "sha256": "a" * 64},
                  "format": "mkv", "media_type": "video", "transparent": True,
                  "targets": ["mkv", "mov", "webm", "apng"], "background_targets": ["mkv", "mov", "webm"]}
        tasks = [self.task(url="", action="convert", kind="video", source_format="mkv", format=target,
                           _image=source, _background=background)
                 for target in source["targets"] for background in ("#FFFFFF", "#000000")]
        self.assertTrue(self.store.save(tasks))
        restored = self.store.load()
        for index, target in enumerate(source["targets"]):
            white, black = restored[index * 2:index * 2 + 2]
            with self.subTest(target=target):
                self.assertEqual(white["_image"]["background_targets"], source["background_targets"])
                if target in source["background_targets"]:
                    self.assertNotEqual(white["_identity"], black["_identity"])
                    self.assertEqual(white["_identity"][4], "#FFFFFF")
                    self.assertEqual(black["_identity"][4], "#000000")
                else:
                    self.assertEqual(white["_identity"], black["_identity"])
                    self.assertIsNone(white["_identity"][4])

    def test_expanded_download_outputs_survive_restart_with_exact_target(self):
        targets = {
            "image": ("png", "jpg", "webp", "avif", "bmp", "tiff", "ico", "tga", "gif", "jp2", "apng", "mp4", "mkv", "mov", "webm"),
            "audio": ("mp3", "wav", "flac", "m4a", "aac", "opus", "ogg", "aiff"),
            "video": ("mp4", "mkv", "mov", "webm", "gif", "webp", "apng"),
        }
        tasks = []
        for kind, formats in targets.items():
            for target in formats:
                task = self.task(kind=kind, format=target)
                task["_identity"] = (task["url"], kind, target, task["_settings"]["output"], None, None)
                tasks.append(task)
        self.assertTrue(self.store.save(tasks))
        restored = self.store.load()
        self.assertEqual([(task["kind"], task["format"]) for task in restored],
                         [(task["kind"], task["format"]) for task in tasks])
        self.assertTrue(all(task["status"] == "cancelled" and task["error"] == RESTORED_MESSAGE for task in restored))
        self.assertEqual([task["_identity"] for task in restored], [task["_identity"] for task in tasks])

    def test_invalid_download_category_cannot_replace_saved_queue(self):
        self.assertTrue(self.store.save([self.task()]))
        raw = self.store.path.read_bytes()
        for kind, target in (("audio", "png"), ("image", "mp3"), ("animation", "apng"), (None, "webp"), ("video", "png")):
            with self.subTest(kind=kind, target=target):
                self.assertFalse(self.store.save([self.task(kind=kind, format=target)]))
                self.assertEqual(self.store.path.read_bytes(), raw)

    def test_second_instance_cannot_overwrite_owner_queue_and_released_lock_is_reusable(self):
        owner_task = self.task()
        self.assertTrue(self.store.save([owner_task]))
        other = DesktopQueueStore(self.root / "downloads")
        self.addCleanup(other.close)
        self.assertIsNotNone(other.notice)
        self.assertEqual(other.load(), [])
        self.assertFalse(other.save([]))
        self.assertEqual(self.store.load()[0]["id"], owner_task["id"])
        self.store.close()
        replacement = DesktopQueueStore(self.root / "downloads")
        self.addCleanup(replacement.close)
        self.assertIsNone(replacement.notice)
        self.assertEqual(replacement.load()[0]["id"], owner_task["id"])

    def test_scope_isolates_outputs_but_explicit_desktop_scope_survives_release_paths(self):
        other = DesktopQueueStore(self.root / "other")
        stable = DesktopQueueStore(self.root / "release-1", queue_scope="desktop")
        self.addCleanup(other.close)
        self.addCleanup(stable.close)
        self.assertNotEqual(other.path, self.store.path)
        self.assertTrue(stable.save([self.task()]))
        stable.close()
        updated = DesktopQueueStore(self.root / "release-2", queue_scope="desktop")
        self.addCleanup(updated.close)
        self.assertEqual(len(updated.load()), 1)

    def test_atomic_failure_keeps_previous_queue_and_cleans_temporary_file(self):
        previous = self.task()
        self.assertTrue(self.store.save([previous]))
        with patch("universal_downloader.desktop_queue_store.os.replace", side_effect=OSError("disk unavailable")):
            self.assertFalse(self.store.save([]))
        self.assertEqual(self.store.load()[0]["id"], previous["id"])
        self.assertFalse(list(self.store.path.parent.glob(".desktop-queue-*.tmp")))

    def test_malformed_or_oversized_state_is_rejected_without_partial_restore(self):
        good = task_record(self.task())
        mutations = (
            lambda value: value.update(version=True),
            lambda value: value.update(scope="another"),
            lambda value: value.update(tasks=[good, good]),
            lambda value: value["tasks"][0].update(settings={"output": "relative", "max_height": None, "max_size": None}),
            lambda value: value["tasks"][0].update(status="completed", path=str(self.root / "outside.mp4")),
            lambda value: value["tasks"][0].update(url="https://user:secret@example.com/video"),
            lambda value: value["tasks"][0].update(kind=[]),
            lambda value: value["tasks"][0].update(format={}),
            lambda value: value["tasks"][0].update(settings={"output": str(self.root), "max_height": 10 ** 1000, "max_size": None}),
            lambda value: value["tasks"][0].update(selection={"source": "invalid"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload = {"version": 1, "scope": self.store.scope, "tasks": [deepcopy(good)]}
                mutate(payload)
                self.store.path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(self.store.load(), [])
                self.assertIn("could not be restored", self.store.notice)
        self.store.path.write_bytes(b" " * (MAX_QUEUE_STATE_BYTES + 1))
        self.assertEqual(self.store.load(), [])

    def test_save_bounds_count_and_preserves_last_good_state(self):
        self.assertTrue(self.store.save([self.task()]))
        raw = self.store.path.read_bytes()
        self.assertFalse(self.store.save([self.task()] * 1_001))
        self.assertEqual(self.store.path.read_bytes(), raw)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_saved_state_is_owner_only(self):
        self.assertTrue(self.store.save([self.task()]))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)


class SavedSelectionTests(unittest.TestCase):
    def test_image_content_selects_exact_gallery_item_without_persisting_extractor_id(self):
        first = MediaInspection("https://cdn.example.com/one.jpg", {"id": "gallery-first", "image_url": "https://cdn.example.com/one.jpg?token=old"}, ("image",), image_data=b"first")
        second = MediaInspection("https://cdn.example.com/two.jpg", {"id": "gallery-second", "image_url": "https://cdn.example.com/two.jpg?token=old"}, ("image",), image_data=b"second")
        descriptor = selection_descriptor(second)
        refreshed = replace(second, information={"id": "direct-image-new-id", "image_url": "https://cdn.example.com/two.jpg?token=new"})
        result = resolve_restored_selection(descriptor, replace(first, alternatives=(first, refreshed)))
        self.assertEqual(result.image_data, b"second")
        self.assertEqual(result.alternatives, ())
        self.assertNotIn("asset", descriptor)

    def test_video_selection_uses_stable_id_and_rejects_missing_or_ambiguous_asset(self):
        original = MediaInspection("https://example.com/post", {"id": "video-2", "extractor_key": "Example", "url": "https://cdn.example.com/v.mp4?expires=1"}, ("video", "audio"))
        descriptor = selection_descriptor(original)
        refreshed = replace(original, information={**original.information, "url": "https://cdn.example.com/v.mp4?expires=2"})
        self.assertEqual(resolve_restored_selection(descriptor, refreshed), refreshed)
        wrong = replace(refreshed, information={**refreshed.information, "id": "video-3"})
        with self.assertRaisesRegex(DownloadError, "Check the link"):
            resolve_restored_selection(descriptor, wrong)
        with self.assertRaisesRegex(DownloadError, "Check the link"):
            resolve_restored_selection(descriptor, replace(refreshed, alternatives=(refreshed, refreshed)))

    def test_changed_image_cannot_match_the_same_cdn_path(self):
        original = MediaInspection("https://example.com/image", {"image_url": "https://cdn.example.com/image"}, ("image",), image_data=b"original")
        with self.assertRaisesRegex(DownloadError, "Check the link"):
            resolve_restored_selection(selection_descriptor(original), replace(original, image_data=b"replacement"))

    def test_video_keeps_provider_identity_when_thumbnail_and_cdn_host_change(self):
        original = MediaInspection("https://example.com/post/7", {"id": "video-7", "extractor_key": "Example", "url": "https://old-cdn.example.com/v.mp4?signature=old"},
                                   ("video", "audio", "image"), image_data=b"old poster")
        refreshed = replace(original, information={**original.information, "url": "https://new-cdn.example.com/another.mp4?signature=new"}, image_data=b"new poster")
        descriptor = selection_descriptor(original)
        self.assertNotIn("image", descriptor)
        self.assertIn("asset", descriptor)
        self.assertEqual(resolve_restored_selection(descriptor, refreshed), refreshed)
        with self.assertRaisesRegex(DownloadError, "Check the link"):
            resolve_restored_selection(descriptor, replace(refreshed, information={**refreshed.information, "id": "video-8"}))


if __name__ == "__main__":
    unittest.main()
