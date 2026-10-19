import contextlib
import functools
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image

from universal_downloader.errors import DownloadError
from universal_downloader.queue import load_batch, pasted_urls
from universal_downloader.cli import main
from universal_downloader.preferences import save_preferences


class QueueInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.file = Path(temporary.name) / "links.txt"

    def test_bom_comments_blank_lines_and_stable_deduplication(self):
        self.file.write_text("# links\n\nhttps://example.com/a#fragment\n  https://example.com/b  \nhttps://example.com/a#fragment\n", encoding="utf-8-sig")
        self.assertEqual(load_batch(self.file), ("https://example.com/a#fragment", "https://example.com/b"))

    def test_later_invalid_url_rejects_the_entire_queue(self):
        self.file.write_text("https://example.com/a\nnot-a-link\nhttps://example.com/b", encoding="utf-8")
        with self.assertRaisesRegex(DownloadError, "line 2"):
            load_batch(self.file)

    def test_url_count_and_file_size_are_bounded(self):
        self.file.write_text("https://example.com/a\nhttps://example.com/b", encoding="utf-8")
        with patch("universal_downloader.queue.MAX_QUEUE_URLS", 1), self.assertRaisesRegex(DownloadError, "up to 1"):
            load_batch(self.file)
        with patch("universal_downloader.queue.MAX_BATCH_BYTES", 10), self.assertRaisesRegex(DownloadError, "1 MiB"):
            load_batch(self.file)

    def test_empty_non_utf8_missing_and_directories_fail_cleanly(self):
        for data in (b"# no links\n\n", b"\xff\xfe\x80"):
            with self.subTest(data=data):
                self.file.write_bytes(data)
                with self.assertRaises(DownloadError):
                    load_batch(self.file)
        for path in (self.file.parent / "missing.txt", self.file.parent):
            with self.subTest(path=path), self.assertRaises(DownloadError):
                load_batch(path)

    def test_pasted_urls_are_validated_and_deduplicated(self):
        self.assertEqual(pasted_urls(" https://example.com/a  https://example.com/b https://example.com/a "), ("https://example.com/a", "https://example.com/b"))
        self.assertEqual(pasted_urls(' " https://example.com/quoted " '), ("https://example.com/quoted",))
        with self.assertRaises(DownloadError):
            pasted_urls("https://example.com/a file:///secret")

    def test_long_url_is_rejected_before_any_request(self):
        with patch("universal_downloader.queue.MAX_URL_LENGTH", 10), self.assertRaisesRegex(DownloadError, "character limit"):
            pasted_urls("https://example.com/long")


class _Handler(SimpleHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append(self.path)
        return super().do_GET()

    def log_message(self, *args):
        pass


class QueueDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name).resolve()
        with Image.new("RGBA", (20, 20), (255, 0, 0, 120)) as image:
            image.save(cls.root / "first.png")
        with Image.new("RGB", (20, 20), "blue") as image:
            image.save(cls.root / "second.jpg")
        first = Image.new("RGB", (20, 20), "red")
        first.save(cls.root / "animation.gif", save_all=True, append_images=[Image.new("RGB", (20, 20), "blue")], duration=100)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(cls.root)))
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name).resolve()
        self.output = self.work / "downloads"
        environment = patch.dict(os.environ, {"UNIDOWN_DATA_DIR": str(self.work / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        for redirect in (contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr)):
            redirect.__enter__()
            self.addCleanup(redirect.__exit__, None, None, None)
        _Handler.requests.clear()

    def batch(self, names):
        file = self.work / "links.txt"
        file.write_text("\n".join(self.base + "/" + name for name in names), encoding="utf-8-sig")
        return str(file)

    def test_real_batch_continues_after_failure_and_incompatible_animation_then_reuses_outputs(self):
        filename = self.batch(("first.png", "missing.png", "animation.gif", "second.jpg", "first.png"))
        arguments = ["--batch", filename, "--type", "image", "--convert", "jpg", "--output", str(self.output), "--no-preferences"]
        self.assertEqual(main(arguments), 1)
        files = list(self.output.iterdir())
        self.assertEqual(len(files), 2)
        for path in files:
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
        self.assertIn("/second.jpg", _Handler.requests)
        self.assertIn("completed 2 | already 0 | failed 1 | skipped 1", self.stdout.getvalue())
        self.assertEqual(main(arguments), 1)
        self.assertIn("completed 0 | already 2 | failed 1 | skipped 1", self.stdout.getvalue())
        self.assertEqual(set(self.output.iterdir()), set(files))

    def test_real_interactive_queue_confirms_once_and_preserves_each_original(self):
        paste = self.base + "/first.png " + self.base + "/second.jpg"
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[paste, "", "yes", ""]) as answers:
            self.assertEqual(main(["--output", str(self.output), "--no-preferences"]), 0)
        self.assertEqual(answers.call_count, 4)
        self.assertEqual({path.read_bytes() for path in self.output.iterdir()}, {(self.root / "first.png").read_bytes(), (self.root / "second.jpg").read_bytes()})

    def test_real_custom_queue_selects_policy_after_first_failure_without_reinspection(self):
        paste = " ".join(self.base + "/" + name for name in ("missing.png", "first.png", "second.jpg"))
        with patch("universal_downloader.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[paste, "2", "2", "yes", ""]) as answers:
            self.assertEqual(main(["--output", str(self.output), "--no-preferences"]), 1)
        self.assertEqual(answers.call_count, 5)
        self.assertEqual(_Handler.requests, ["/missing.png", "/first.png", "/second.jpg"])
        self.assertEqual(len(list(self.output.iterdir())), 2)
        for path in self.output.iterdir():
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
        self.assertIn("completed 2 | already 0 | failed 1 | skipped 0", self.stdout.getvalue())

    def test_invalid_file_never_contacts_even_the_first_valid_endpoint(self):
        file = self.work / "invalid.txt"
        file.write_text(self.base + "/first.png\ninvalid URL\n", encoding="utf-8")
        self.assertEqual(main(["--batch", str(file), "--output", str(self.output), "--no-preferences"]), 1)
        self.assertEqual(_Handler.requests, [])
        self.assertFalse(self.output.exists())

    def test_saved_height_is_inapplicable_to_real_stills_but_saved_size_remains_strict(self):
        filename = self.batch(("first.png", "second.jpg"))
        save_preferences({"max_height": 720, "max_size": 1_048_576})
        self.assertEqual(main(["--batch", filename, "--output", str(self.output)]), 0)
        self.assertEqual(len(list(self.output.iterdir())), 2)
        self.assertIn("Saved height limit does not apply", self.stdout.getvalue())
        save_preferences({"max_height": 720, "max_size": 1})
        smaller = self.work / "limited"
        self.assertEqual(main(["--batch", filename, "--output", str(smaller)]), 1)
        self.assertFalse(smaller.exists())
        self.assertIn("failed 2", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
