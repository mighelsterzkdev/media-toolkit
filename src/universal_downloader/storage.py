"""Publish completed files without overwriting an existing download."""

from pathlib import Path
import shutil

from .errors import DownloadError


def publish_file(source: Path, destination: Path) -> bool:
    try:
        output = destination.open("xb")
    except FileExistsError:
        if not destination.is_file():
            raise DownloadError(f"The output path is not a file: {destination}")
        return False
    try:
        with output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return True
