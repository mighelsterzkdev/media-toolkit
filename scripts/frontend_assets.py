"""Reject stale or incomplete desktop assets before creating a release."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "BUILD-MANIFEST.json"
_TEXT_EXTENSIONS = {".css", ".html", ".js", ".json", ".mjs", ".ts", ".tsx", ".txt", ".yaml", ".yml"}


def _input_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("invalid build input path")
    result = root / relative
    if result.is_symlink() or not result.resolve().is_relative_to(root):
        raise ValueError("build inputs must be regular files inside the frontend")
    return result


def _files_under(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("missing build input directory")
    result = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("build inputs must be regular files")
        if path.is_file():
            result.append(path)
    return result


def _inventory(root, files):
    result = {}
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or invalid build input file")
        content = path.read_bytes()
        if path.suffix in _TEXT_EXTENSIONS:
            content = content.replace(b"\r\n", b"\n")
        result[path.relative_to(root).as_posix()] = sha256(content).hexdigest()
    return result


def verify_frontend_assets(root=ROOT):
    """Check both the source inventory and every generated asset without Node."""
    root = Path(root).resolve()
    ui = root / "desktop-ui"
    assets = root / "src/universal_downloader/desktop_assets"
    try:
        definition = json.loads((ui / "build-inputs.json").read_text(encoding="utf-8"))
        if (not isinstance(definition, dict) or set(definition) != {"files", "directories"}
                or not isinstance(definition["files"], list) or not isinstance(definition["directories"], list)):
            raise ValueError("invalid frontend build input definition")
        sources = [_input_path(ui, relative) for relative in definition["files"]]
        for relative in definition["directories"]:
            sources.extend(_files_under(_input_path(ui, relative)))
        manifest = json.loads((assets / MANIFEST_NAME).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {"version", "sources", "outputs"} or type(manifest["version"]) is not int or manifest["version"] != 1:
            raise ValueError("invalid frontend build manifest")
        if manifest["sources"] != _inventory(ui, sources):
            raise ValueError("frontend source files changed after the last build")
        outputs = [path for path in _files_under(assets) if path.relative_to(assets).as_posix() != MANIFEST_NAME]
        if manifest["outputs"] != _inventory(assets, outputs) or "index.html" not in manifest["outputs"]:
            raise ValueError("generated frontend files are missing or changed")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        raise RuntimeError(f"The desktop interface is missing or out of date ({error}). Run pnpm build in desktop-ui before packaging.") from error


if __name__ == "__main__":
    verify_frontend_assets()
    print("Desktop source and generated asset fingerprints match.")
