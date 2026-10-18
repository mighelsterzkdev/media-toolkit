"""Exercise protected publication, including abrupt process termination."""

from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from threading import Barrier
import unittest
from unittest.mock import patch

from universal_downloader import storage
from universal_downloader.errors import DownloadError


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="unidown-storage-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "saved.bin"

    def test_complete_file_and_image_bytes_are_preserved(self):
        source = self.root / "source.bin"
        data = b"original bytes" * 10000
        source.write_bytes(data)
        self.assertTrue(storage.publish_file(source, self.destination))
        image_path = self.root / "image.bin"
        self.assertTrue(storage.publish_bytes(data, image_path))
        self.assertEqual(source.read_bytes(), data)
        self.assertEqual(self.destination.read_bytes(), data)
        self.assertEqual(image_path.read_bytes(), data)
        self.assertEqual(set(self.root.iterdir()), {source, self.destination, image_path})

    def test_existing_file_is_never_replaced_and_directory_is_rejected(self):
        self.destination.write_bytes(b"keep this")
        self.assertFalse(storage.publish_bytes(b"replacement", self.destination))
        self.assertEqual(self.destination.read_bytes(), b"keep this")
        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaisesRegex(DownloadError, "not a file"):
            storage.publish_bytes(b"image", directory)
        self.assertEqual(set(self.root.iterdir()), {self.destination, directory})

    def test_failure_or_cancellation_during_copy_leaves_no_final_or_work_file(self):
        for failure in (OSError("Disk full"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                def interrupted_copy(input_file, output):
                    output.write(input_file.read(3))
                    output.flush()
                    self.assertFalse(self.destination.exists())
                    raise failure

                with patch.object(storage.shutil, "copyfileobj", side_effect=interrupted_copy):
                    with self.assertRaises(type(failure)):
                        storage.publish_bytes(b"complete contents", self.destination)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_flush_never_publishes_or_leaves_work_files(self):
        with patch.object(storage.os, "fsync", side_effect=OSError("Flush failed")):
            with self.assertRaises(OSError):
                storage.publish_bytes(b"complete contents", self.destination)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_concurrent_publishers_keep_one_complete_winner_without_overwriting(self):
        barrier = Barrier(4)
        commit = storage._commit

        def simultaneous_commit(staged, destination):
            barrier.wait(timeout=10)
            return commit(staged, destination)

        contents = [bytes([index]) * 131072 for index in range(4)]
        with patch.object(storage, "_commit", side_effect=simultaneous_commit):
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(lambda data: storage.publish_bytes(data, self.destination), contents))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.destination.read_bytes(), contents[results.index(True)])
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_abrupt_exit_never_leaves_a_partial_file_at_the_final_path(self):
        script = textwrap.dedent("""
            import os
            import sys
            from pathlib import Path
            from unittest.mock import patch
            from universal_downloader import storage

            source, destination = map(Path, sys.argv[1:3])
            phase = sys.argv[3]
            commit = storage._commit

            def interrupted_copy(input_file, output):
                output.write(input_file.read(3))
                output.flush()
                os._exit(77)

            def interrupted_commit(staged, destination):
                commit(staged, destination)
                os._exit(77)

            target = 'shutil.copyfileobj' if phase == 'copy' else '_commit'
            interrupt = interrupted_copy if phase == 'copy' else interrupted_commit
            with patch('universal_downloader.storage.' + target, side_effect=interrupt):
                storage.publish_file(source, destination)
            raise AssertionError('The child should have exited abruptly')
        """)
        data = b"complete file contents" * 10000
        for phase in ("copy", "commit"):
            with self.subTest(phase=phase):
                folder = self.root / phase
                folder.mkdir()
                source, destination = folder / "source.bin", folder / "saved.bin"
                source.write_bytes(data)
                result = subprocess.run(
                    [sys.executable, "-B", "-c", script, str(source), str(destination), phase],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 77, result.stderr)
                if phase == "copy":
                    self.assertFalse(destination.exists())
                    self.assertTrue(storage.publish_file(source, destination))
                else:
                    self.assertFalse(storage.publish_file(source, destination))
                self.assertEqual(destination.read_bytes(), data)
                self.assertEqual(source.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
