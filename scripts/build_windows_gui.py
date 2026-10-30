"""Build one Windows ZIP containing the desktop app and the terminal executable."""

from __future__ import annotations

import argparse
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_windows
from scripts.frontend_assets import verify_frontend_assets

DESKTOP_PACKAGES = ("pywebview", "pythonnet", "clr-loader", "cffi", "pycparser", "bottle", "proxy-tools", "typing-extensions")


def verify_gui_manifest(executable: Path) -> None:
    """Reject a GUI executable that Windows would scale as a legacy bitmap."""
    import pefile
    with pefile.PE(str(executable)) as image:
        for resource in image.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id != 24:
                continue
            for named in resource.directory.entries:
                for language in named.directory.entries:
                    entry = language.data.struct
                    root = ElementTree.fromstring(image.get_data(entry.OffsetToData, entry.Size))
                    awareness = root.find(".//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}dpiAwareness")
                    if awareness is not None and awareness.text and awareness.text.split(",")[0].strip() == "PerMonitorV2":
                        return
    raise RuntimeError("The desktop executable is missing its PerMonitorV2 DPI manifest.")


def write_desktop_notices(destination: Path) -> None:
    inventory = destination / "INVENTORY.txt"
    with inventory.open("a", encoding="utf-8") as listing:
        for name in DESKTOP_PACKAGES:
            distribution = metadata.distribution(name)
            directory = destination / name
            directory.mkdir(exist_ok=True)
            for entry in distribution.files or ():
                parts = Path(entry).parts
                if not any(part.endswith(".dist-info") for part in parts) or not any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                    continue
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    start = next(index for index, part in enumerate(parts) if part.endswith(".dist-info"))
                    target = directory.joinpath(*parts[start:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            (directory / "SOURCE.txt").write_text(f"{distribution.metadata['Name']} {distribution.version}\n" + "\n".join(distribution.metadata.get_all("Project-URL") or ()) + "\n", encoding="utf-8")
            listing.write(f"{distribution.metadata['Name']} {distribution.version}\n")
    frontend = ROOT / "src/universal_downloader/desktop_assets/THIRD-PARTY-NOTICES.txt"
    if not frontend.is_file():
        raise RuntimeError("Build the desktop frontend and its third-party notices before packaging.")
    shutil.copyfile(frontend, destination / "FRONTEND-NOTICES.txt")
    for notice in (ROOT / "third_party").glob("*"):
        if notice.is_file():
            shutil.copyfile(notice, destination / notice.name)


def build(output: Path) -> Path:
    from universal_downloader import __version__
    assets = ROOT / "src/universal_downloader/desktop_assets"
    verify_frontend_assets(ROOT)
    # Keep separate library directories: Python's base_library.zip is derived
    # from each entry point and cannot safely be overwritten by another build.
    archive = build_windows.build(output)
    bundle = archive.with_suffix("")
    with tempfile.TemporaryDirectory(prefix="unidown-desktop-build-") as temporary:
        working = Path(temporary)
        command = [
            sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "media-toolkit", "--contents-directory", "_desktop",
            "--manifest", str(ROOT / "scripts/windows_gui.manifest"), "--icon", str(ROOT / "assets/media-toolkit.ico"),
            "--distpath", str(working / "dist"), "--workpath", str(working / "work"), "--specpath", str(working),
            "--paths", str(ROOT), "--collect-all", "yt_dlp", "--collect-all", "yt_dlp_ejs", "--collect-all", "PIL",
            "--collect-data", "webview", "--collect-binaries", "webview",
            "--hidden-import", "webview.platforms.edgechromium", "--hidden-import", "webview.platforms.winforms",
            "--add-data", f"{assets};universal_downloader/desktop_assets",
        ]
        command += build_windows.heif_pyinstaller_options() + build_windows.ssl_pyinstaller_options()
        for module in ("yt_dlp.__pyinstaller", "PyInstaller", "mutagen", "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa", "webview.platforms.cef", "webview.platforms.android", "webview.platforms.mshtml", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter"):
            command += ["--exclude-module", module]
        for package in (*build_windows.RUNTIME_PACKAGES, *DESKTOP_PACKAGES):
            command += ["--copy-metadata", package]
        command.append(str(ROOT / "scripts/gui_entry.py"))
        subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), check=True)
        executable = working / "dist/media-toolkit/media-toolkit.exe"
        build_windows.verify_exclusions(executable)
        build_windows.verify_heif_binaries(executable.parent / "_desktop")
        verify_gui_manifest(executable)
        shutil.copyfile(executable, bundle / executable.name)
        shutil.copytree(executable.parent / "_desktop", bundle / "_desktop")
    write_desktop_notices(bundle / "licenses")
    (bundle / "start.cmd").write_text(
        '@echo off\nsetlocal\ncd /d "%~dp0"\n'
        'if "%~1"=="" (\n    start "" "%~dp0media-toolkit.exe"\n    exit /b 0\n)\n'
        '"%~dp0unidown.exe" %*\nexit /b %errorlevel%\n', encoding="utf-8",
    )
    (bundle / "READ-ME.txt").write_text(
        f"Media Toolkit {__version__} — Windows x64\n\n"
        "Extract the complete ZIP into a writable folder. Keep _desktop and _internal beside both EXEs.\n"
        "Open start.cmd or media-toolkit.exe for the desktop app. No Python or Node.js is required.\n"
        "The desktop app uses the installed Microsoft Edge WebView2 Runtime (normally present on Windows 11).\n"
        "Runtime installer if needed: https://developer.microsoft.com/microsoft-edge/webview2/\n"
        "Download tab: paste a link, click Check, choose an output, then click Download.\n"
        "Convert tab: add images, audio, video or animations, choose a compatible format, then click Convert.\n"
        "Images: PNG, JPG, WebP, AVIF, BMP, TIFF, ICO, TGA, GIF, JP2. HEIC/HEIF input is supported.\n"
        "Audio: MP3, WAV, FLAC, M4A, AAC, Opus, Ogg, AIFF. Video: MP4, MKV, MOV, WebM.\n"
        "Animations: GIF, animated WebP, APNG, MP4. Original files are preserved.\n"
        "Videos can also become looping GIF, animated WebP or APNG without audio.\n"
        "Images work offline. Audio/video conversion and MP4 animation output need FFmpeg/ffprobe.\n"
        "Conversions show measured progress when available; compatible streams are copied without re-encoding.\n"
        "The queue is saved locally. After reopening, use Retry to continue unfinished tasks. Nothing restarts automatically.\n"
        "Use the queue for several links. Nothing starts until you explicitly add downloads.\n"
        "Click Install tools if needed to prepare checksum-pinned FFmpeg/Deno archives from their publishers.\n"
        "Downloads default to downloads/ beside this program. Settings persist in local application data.\n\n"
        "Run unidown.exe --help for the optional terminal interface.\n"
        "CLI examples: unidown.exe --doctor | unidown.exe --batch links.txt | unidown.exe --version\n"
        "Prepare tools from a terminal: unidown.exe --install-tools\n"
        "To update, extract a newer complete release. Do not replace only an EXE.\n"
        "Source and instructions: https://github.com/mighelsterzkdev/media-toolkit\n"
        "Third-party notices are in licenses/. This release is unsigned; verify SHA256SUMS.txt.\n",
        encoding="utf-8",
    )
    build_windows.write_archive(bundle, archive)
    digest = build_windows._checksum(archive)
    (archive.parent / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"\nDesktop and terminal release: {archive}\nSHA-256: {digest}", flush=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        build(args.output)
    except (OSError, RuntimeError, subprocess.CalledProcessError, metadata.PackageNotFoundError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
