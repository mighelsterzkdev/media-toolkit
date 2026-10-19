"""Read bounded link queues without performing any network requests."""

from pathlib import Path

from .errors import DownloadError
from .urls import validate_url

MAX_BATCH_BYTES = 1_048_576
MAX_QUEUE_URLS = 1000
MAX_URL_LENGTH = 16_384


class QueueIncompatible(DownloadError):
    """A verified item does not provide the explicitly selected media policy."""


def validated_urls(values) -> tuple[str, ...]:
    urls = []
    seen = set()
    entries = 0
    for position, value in values:
        entries += 1
        if entries > MAX_QUEUE_URLS:
            raise DownloadError(f"A queue supports up to {MAX_QUEUE_URLS} URL entries.")
        if len(value) > MAX_URL_LENGTH:
            raise DownloadError(f"URL {position} exceeds the {MAX_URL_LENGTH} character limit.")
        try:
            url = validate_url(value)
        except DownloadError as error:
            raise DownloadError(f"Invalid URL at {position}: {error}") from error
        if url not in seen:
            urls.append(url)
            seen.add(url)
    if not urls:
        raise DownloadError("The queue contains no URLs.")
    return tuple(urls)


def load_batch(path: Path) -> tuple[str, ...]:
    try:
        with Path(path).expanduser().open("rb") as source:
            data = source.read(MAX_BATCH_BYTES + 1)
        if len(data) > MAX_BATCH_BYTES:
            raise DownloadError("The batch file exceeds the 1 MiB limit.")
        contents = data.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise DownloadError("Could not read the batch file. Use a UTF-8 text file with one URL per line.") from error
    return validated_urls((f"line {index}", line.strip()) for index, line in enumerate(contents.splitlines(), 1) if line.strip() and not line.lstrip().startswith("#"))


def pasted_urls(value: str) -> tuple[str, ...]:
    if len(value) > MAX_BATCH_BYTES or len(value.encode("utf-8")) > MAX_BATCH_BYTES:
        raise DownloadError("The pasted queue exceeds the 1 MiB limit.")
    try:
        single = validate_url(value)
    except DownloadError:
        pass
    else:
        return validated_urls((("item 1", single),))
    return validated_urls((f"item {index}", item) for index, item in enumerate(value.split(), 1))
