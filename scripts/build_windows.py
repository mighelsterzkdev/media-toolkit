"""Build a portable Python-free Windows release with notices and SHA-256 checksums."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = (
    "yt-dlp", "yt-dlp-ejs", "Pillow", "pi-heif", "brotli", "certifi", "charset-normalizer", "idna",
    "pycryptodomex", "requests", "urllib3", "websockets",
)


def heif_pyinstaller_options() -> list[str]:
    """Collect the read-only HEIF wheel, including delvewheel's sibling DLLs."""
    distribution = metadata.distribution("pi-heif")
    if distribution.version != "1.4.0":
        raise RuntimeError("Build with the pinned pi-heif 1.4.0 wheel.")
    options = ["--collect-all", "pi_heif", "--hidden-import", "_pi_heif", "--exclude-module", "pillow_heif"]
    libraries = [entry for entry in distribution.files or () if str(entry).lower().endswith(".dll")]
    if not libraries or any("x265" in str(entry).lower() for entry in libraries):
        raise RuntimeError("The HEIF wheel must contain the read-only libheif/libde265 binaries.")
    for entry in libraries:
        source = Path(distribution.locate_file(entry))
        if not source.is_file():
            raise RuntimeError(f"The HEIF binary is unavailable: {source.name}")
        # pi_heif's delvewheel initializer adds its package parent to the DLL
        # search path. Keep these independent, user-replaceable DLLs there.
        options += ["--add-binary", f"{source};."]
    return options


def verify_heif_binaries(directory: Path) -> None:
    distribution = metadata.distribution("pi-heif")
    expected = [Path(entry).name for entry in distribution.files or () if str(entry).lower().endswith(".dll")]
    missing = [name for name in expected if not (directory / name).is_file()]
    if not expected or missing:
        raise RuntimeError("The portable bundle is missing HEIF dependencies: " + ", ".join(missing))
    if any("x265" in path.name.lower() for path in directory.glob("*.dll")):
        raise RuntimeError("The portable bundle unexpectedly includes an HEVC encoder.")


def write_heif_sources(destination: Path) -> None:
    """Ship the decoder sources and notices with the independent shared DLLs."""
    import certifi

    notices = ROOT / "third_party" / "pi-heif"
    records = json.loads((notices / "SOURCES.json").read_text(encoding="utf-8"))
    package = destination / "pi-heif"
    package.mkdir(parents=True, exist_ok=True)
    for notice in notices.iterdir():
        if notice.is_file():
            shutil.copyfile(notice, package / notice.name)
    cache = ROOT / ".tools" / "third-party-sources"
    cache.mkdir(parents=True, exist_ok=True)
    target = package / "sources"
    target.mkdir(exist_ok=True)
    for record in records:
        cached = cache / record["filename"]
        legacy = ROOT / ".tools" / record["filename"]
        if not cached.is_file() and legacy.is_file() and _checksum(legacy) == record["sha256"]:
            shutil.copyfile(legacy, cached)
        if not cached.is_file() or _checksum(cached) != record["sha256"]:
            partial = cached.with_suffix(cached.suffix + ".partial")
            try:
                request = Request(record["url"], headers={"User-Agent": "Media-Toolkit-release-builder"})
                with urlopen(request, timeout=60, context=ssl.create_default_context(cafile=certifi.where())) as response, partial.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if _checksum(partial) != record["sha256"]:
                    raise RuntimeError(f"The HEIF source archive checksum did not match: {record['filename']}")
                partial.replace(cached)
            except (OSError, URLError) as error:
                raise RuntimeError(f"Could not retrieve the pinned HEIF source archive: {record['filename']}") from error
            finally:
                partial.unlink(missing_ok=True)
        shutil.copyfile(cached, target / cached.name)


def write_notices(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for name in (*RUNTIME_PACKAGES, "packaging", "setuptools", "pyinstaller"):
        distribution = metadata.distribution(name)
        package = destination / name
        package.mkdir(exist_ok=True)
        for entry in distribution.files or ():
            if ".dist-info/" in str(entry).replace("\\", "/") and any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    # Preserve separate vendored notices with identical basenames.
                    parts = Path(entry).parts
                    start = next(index for index, part in enumerate(parts) if part.endswith(".dist-info"))
                    target = package.joinpath(*parts[start:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        details = {key: distribution.metadata.get_all(key) for key in ("Name", "Version", "License", "License-Expression", "Home-page", "Project-URL")}
        (package / "METADATA.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
        inventory.append(f"{distribution.metadata['Name']} {distribution.version}")
        if name == "certifi":
            # MPL source remains available beside the compiled application.
            # Include the installed, unmodified Python/data files verbatim.
            for entry in distribution.files or ():
                relative = Path(entry)
                if relative.parts[0] == "certifi" and relative.suffix in (".py", ".pem"):
                    target = package / "source" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(distribution.locate_file(entry), target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, destination / "PYTHON-LICENSE.txt")
    (destination / "INVENTORY.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")
    write_heif_sources(destination)
    (destination / "EXTERNAL-TOOLS.txt").write_text(
        "External tools are not redistributed inside this application archive.\n"
        "On first use the application downloads checksum-pinned official publisher archives directly.\n"
        "FFmpeg 9.0.2 (Gyan GPLv3 build): https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2\n"
        "FFmpeg licenses and build information: https://www.gyan.dev/ffmpeg/builds/\n"
        "FFmpeg license details: https://ffmpeg.org/legal.html\n"
        "Deno 2.9.7 (MIT): https://github.com/denoland/deno/releases/tag/v2.9.7\n"
        "Deno license: https://github.com/denoland/deno/blob/v2.9.7/LICENSE.md\n"
        "Downloads and license files supplied in those archives are stored under .tools beside unidown.exe.\n",
        encoding="utf-8",
    )


def build(output: Path) -> Path:
    from universal_downloader import __version__
    from universal_downloader.compatibility import compatibility_issues
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("Build this Windows x64 release on Windows x64.")
    issues = compatibility_issues()
    if issues:
        raise RuntimeError("Dependencies are not the tested release: " + "; ".join(issues))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f"media-toolkit-{__version__}-windows-x64"
    bundle = output / name
    archive = output / f"{name}.zip"
    if bundle.exists() or archive.exists():
        raise RuntimeError(f"Use a new output directory; a release already exists in {output}.")
    with tempfile.TemporaryDirectory(prefix="unidown-build-") as temporary:
        working = Path(temporary)
        command = [
            sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
            "--name", "unidown", "--distpath", str(working / "dist"), "--workpath", str(working / "work"),
            "--specpath", str(working), "--paths", str(ROOT), "--collect-all", "yt_dlp",
            "--exclude-module", "yt_dlp.__pyinstaller", "--exclude-module", "PyInstaller",
            "--exclude-module", "mutagen",
            "--collect-all", "yt_dlp_ejs", "--collect-all", "PIL",
        ]
        command += heif_pyinstaller_options()
        for package in RUNTIME_PACKAGES:
            command += ["--copy-metadata", package]
        command.append(str(ROOT / "scripts/portable_entry.py"))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        verify_exclusions(working / "dist/unidown/unidown.exe")
        verify_heif_binaries(working / "dist/unidown/_internal")
        shutil.copytree(working / "dist/unidown", bundle)
    shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE")
    write_notices(bundle / "licenses")
    (bundle / "start.cmd").write_text(
        '@echo off\nsetlocal\ncd /d "%~dp0"\n"%~dp0unidown.exe" %*\nset "result=%errorlevel%"\n'
        'if not "%result%"=="0" if not "%result%"=="130" if "%~1"=="" set /p "_unidownExit=Press Enter to close..."\n'
        'exit /b %result%\n', encoding="utf-8",
    )
    (bundle / "READ-ME.txt").write_text(
        f"Media Toolkit {__version__} — Windows x64\n\n"
        "1. Extract the entire ZIP into a writable folder. Keep _internal beside unidown.exe.\n"
        "2. Open start.cmd. No Python installation or administrator access is required.\n"
        "3. On first use, FFmpeg and Deno are downloaded directly from their publishers. Internet is required.\n"
        "   The pinned archives are verified with SHA-256 before installation beside the program.\n"
        "4. Paste a link and choose the original or a compatible conversion. Files go into downloads.\n\n"
        "From a terminal: unidown.exe --doctor | unidown.exe --version | unidown.exe --help\n"
        "Prepare tools without opening the menu: unidown.exe --install-tools\n"
        "To update, download and extract a newer complete release. Do not replace only the EXE.\n"
        "Source, instructions and releases: https://github.com/mighelsterzkdev/media-toolkit\n"
        "Third-party licenses and external tool source links are in licenses/.\n"
        "This release is unsigned; the SHA256SUMS.txt file verifies download integrity.\n",
        encoding="utf-8",
    )
    write_archive(bundle, archive)
    digest = _checksum(archive)
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"\nPortable release: {archive}\nSHA-256: {digest}", flush=True)
    return archive


def write_archive(bundle: Path, archive: Path) -> None:
    """Never redistribute tools or user media created while verifying a bundle."""
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as package:
        for file in sorted(bundle.rglob("*")):
            relative = file.relative_to(bundle)
            if file.is_file() and relative.parts[0] not in (".tools", "downloads"):
                package.write(file, str(Path(bundle.name) / relative))


def verify_exclusions(executable: Path) -> None:
    """Do not redistribute optional GPL metadata editing or build-time modules."""
    from PyInstaller.archive.readers import CArchiveReader
    python_archive = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz")
    unexpected = [name for name in python_archive.toc if name.split(".", 1)[0] in ("mutagen", "PyInstaller")]
    if unexpected:
        raise RuntimeError("The portable bundle unexpectedly contains excluded modules: " + ", ".join(unexpected))


def _checksum(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="new directory for the bundle and ZIP")
    args = parser.parse_args()
    try:
        build(args.output)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
