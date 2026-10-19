"""Publish complete files without exposing partial data or replacing downloads."""

from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedFile:
    """A complete private copy, ready for a short atomic publication."""

    path: Path

    def publish(self, destination: Path) -> bool:
        if _exists(destination):
            return False
        return _commit(self.path, destination)


@contextmanager
def _prepared_file(input_file: BinaryIO, directory: Path):
    with tempfile.TemporaryDirectory(prefix=".unidown-save-", dir=directory) as temporary:
        staged = Path(temporary) / "complete"
        with staged.open("xb") as output:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        yield PreparedFile(staged)


@contextmanager
def prepare_file(source: Path, directory: Path):
    """Copy and flush on the destination filesystem before taking shared locks."""
    with source.open("rb") as input_file, _prepared_file(input_file, directory) as prepared:
        yield prepared


def _publish(input_file: BinaryIO, destination: Path) -> bool:
    if _exists(destination):
        return False
    with _prepared_file(input_file, destination.parent) as prepared:
        return prepared.publish(destination)


def publish_file(source: Path, destination: Path) -> bool:
    """Stage a file on the destination filesystem before publishing it."""
    with source.open("rb") as input_file:
        return _publish(input_file, destination)


def publish_bytes(data: bytes, destination: Path) -> bool:
    """Publish verified image bytes through the same protected save path."""
    with BytesIO(data) as input_file:
        return _publish(input_file, destination)
