"""Publish complete files without exposing partial data or replacing downloads."""

from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
from typing import BinaryIO

from .errors import DownloadError


def _exists(destination: Path) -> bool:
    if destination.exists() or destination.is_symlink():
        if not destination.is_file():
            raise DownloadError(f"The output path is not a file: {destination}")
        return True
    return False


def _commit(staged: Path, destination: Path) -> bool:
    try:
        # Windows rename refuses an existing destination. POSIX rename would
        # replace it, so publish with an exclusive hard link on that platform.
        if os.name == "nt":
            os.rename(staged, destination)
        else:
            os.link(staged, destination)
    except FileExistsError:
        if not destination.is_file():
            raise DownloadError(f"The output path is not a file: {destination}")
        return False
    return True


def _publish(input_file: BinaryIO, destination: Path) -> bool:
    if _exists(destination):
        return False
    with tempfile.TemporaryDirectory(prefix=".unidown-save-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "complete"
        with staged.open("xb") as output:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        return _commit(staged, destination)


def publish_file(source: Path, destination: Path) -> bool:
    """Stage a file on the destination filesystem before publishing it."""
    with source.open("rb") as input_file:
        return _publish(input_file, destination)


def publish_bytes(data: bytes, destination: Path) -> bool:
    """Publish verified image bytes through the same protected save path."""
    with BytesIO(data) as input_file:
        return _publish(input_file, destination)
