"""Check the actual Node-generated release manifest against Python packaging."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_windows_gui
from scripts.frontend_assets import verify_frontend_assets


class FrontendAssetTests(unittest.TestCase):
    def setUp(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the frontend build integration check")
        temporary = tempfile.TemporaryDirectory(prefix="media-toolkit-frontend-build-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ui = self.root / "desktop-ui"
        self.assets = self.root / "src/universal_downloader/desktop_assets"
        (self.ui / "src").mkdir(parents=True)
        (self.assets / "assets").mkdir(parents=True)
        (self.ui / "build-inputs.json").write_text(json.dumps({
            "files": ["build-inputs.json", "index.html", "pnpm-lock.yaml"], "directories": ["src"],
        }), encoding="utf-8")
        (self.ui / "src/App.tsx").write_bytes(b"export const title = 'Current interface'\r\n")
        (self.ui / "index.html").write_text("<div id='root'></div>\n", encoding="utf-8")
        (self.ui / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        (self.assets / "index.html").write_text("<script src='./assets/current.js'></script>\n", encoding="utf-8")
        (self.assets / "assets/current.js").write_text("console.log('Current interface')\n", encoding="utf-8")
        generator = Path(__file__).resolve().parents[1] / "desktop-ui/scripts/build-manifest.mjs"
        subprocess.run([node, str(generator), str(self.ui), str(self.assets)],
                       check=True, capture_output=True, timeout=30)

    def test_current_assets_pass_and_text_newlines_are_portable(self):
        verify_frontend_assets(self.root)
        # Git normalizes source and generated text when a checkout changes OS.
        (self.ui / "src/App.tsx").write_bytes(b"export const title = 'Current interface'\n")
        (self.assets / "assets/current.js").write_bytes(b"console.log('Current interface')\r\n")
        verify_frontend_assets(self.root)

    def test_source_or_dependency_changes_require_a_frontend_rebuild(self):
        for relative in ("src/App.tsx", "pnpm-lock.yaml"):
            with self.subTest(input=relative):
                source = self.ui / relative
                original = source.read_bytes()
                source.write_bytes(original + b"// changed\n")
                with self.assertRaisesRegex(RuntimeError, "source files changed.*pnpm build"):
                    verify_frontend_assets(self.root)
                source.write_bytes(original)

    def test_new_source_files_require_a_frontend_rebuild(self):
        (self.ui / "src/new-feature.tsx").write_text("export const feature = true\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "source files changed"):
            verify_frontend_assets(self.root)

    def test_missing_or_unexpected_generated_assets_cannot_be_packaged(self):
        for changed in ("missing", "unexpected"):
            with self.subTest(change=changed):
                asset = self.assets / "assets/current.js"
                original = asset.read_bytes()
                extra = self.assets / "assets/obsolete.js"
                if changed == "missing":
                    asset.unlink()
                else:
                    extra.write_text("old interface", encoding="utf-8")
                with patch.object(build_windows_gui, "ROOT", self.root), patch.object(build_windows_gui.build_windows, "build") as package:
                    with self.assertRaisesRegex(RuntimeError, "generated frontend files are missing or changed"):
                        build_windows_gui.build(self.root / "dist")
                    package.assert_not_called()
                asset.write_bytes(original)
                extra.unlink(missing_ok=True)

    def test_valid_manifest_allows_packaging_to_start(self):
        with patch.object(build_windows_gui, "ROOT", self.root), patch.object(build_windows_gui.build_windows, "build", side_effect=RuntimeError("packager reached")) as package:
            with self.assertRaisesRegex(RuntimeError, "packager reached"):
                build_windows_gui.build(self.root / "dist")
            package.assert_called_once()


if __name__ == "__main__":
    unittest.main()
