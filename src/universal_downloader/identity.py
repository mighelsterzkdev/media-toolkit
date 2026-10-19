"""Identify source assets and reuse only verified, completed local downloads."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from yt_dlp.utils import sanitize_filename

from .errors import DownloadError
from .storage import PreparedFile, prepare_file


def _canonical_url(value: str) -> str:
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    aliases = {
        "twitter.com": "x.com", "www.twitter.com": "x.com", "mobile.twitter.com": "x.com", "www.x.com": "x.com",
        "www.youtube.com": "youtube.com", "m.youtube.com": "youtube.com", "youtu.be": "youtube.com",
        "www.tiktok.com": "tiktok.com", "m.tiktok.com": "tiktok.com",
    }
    hostname = aliases.get(hostname, hostname)
    port = parts.port
    authority = f"{hostname}:{port}" if port and port != {"https": 443, "http": 80}.get(parts.scheme) else hostname
    query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme.lower(), authority, parts.path or "/", urlencode(query), ""))


@dataclass(frozen=True)
class DownloadIdentity:
    key: str
    source: str
    media_id: str
    role: str
    target: str | None
    max_height: int | None = None

    def filename(self, title: str, extension: str) -> str:
        def short(value: str, length: int) -> str:
            cleaned = sanitize_filename(value, restricted=False).strip(" .")
            return cleaned.encode("utf-8")[:length].decode("utf-8", errors="ignore").strip(" .")

        title = short(title or "download", 64) or "download"
        source = re.sub(r"[^A-Za-z0-9._-]", "_", self.source)[:24] or "source"
        media_id = short(self.media_id, 20)
        role = self.role + (f"-{self.target}" if self.target else "")
        if self.max_height:
            role += f"-{self.max_height}p"
        identity = "-".join(part for part in (source, media_id, self.key[:16]) if part)
        return f"{title} [{identity}] [{role}].{extension}"


def media_identity(request, inspected, kind: str, target: str | None) -> DownloadIdentity:
    """Use provider IDs where meaningful and full URLs for generic direct assets."""
    info = inspected.information
    source_url = info.get("webpage_url") or info.get("original_url") or inspected.url
    canonical = _canonical_url(source_url)
    source = urlsplit(canonical).netloc
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    media_id = str(info.get("id") or "")
    role = inspected.image_role if kind == "image" or inspected.animation_video else kind
    # Generic extractor IDs often contain only a filename, so they do not
    # identify distinct directories or query-selected files on the same host.
    asset = canonical if not media_id or extractor in ("generic", "image") else media_id
    height = getattr(request, "max_height", None) if role in ("video", "animation") else None
    descriptor = {
        "version": 1, "source": source, "extractor": extractor,
        "asset": asset, "role": role, "target": target or "original",
        "max_height": height, "max_size": getattr(request, "max_size", None),
    }
    key = sha256(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DownloadIdentity(key, source, media_id, role, target, height)


def _state_directory() -> Path:
    if configured := os.environ.get("UNIDOWN_DATA_DIR"):
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "UniversalDownloader"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "universal-downloader"


def _fingerprint(path: Path) -> tuple[int, str] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        if before.st_size <= 0:
            return None
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            return None
        return after.st_size, digest.hexdigest()
    except OSError:
        return None


class DownloadHistory:
    """Keep the index outside media folders and verify bytes before any reuse."""

    def __init__(self, output_dir: Path, *, logger=None):
        self.output_dir = output_dir.resolve()
        self.root = os.path.normcase(str(self.output_dir))
        self.database = _state_directory() / "downloads.sqlite3"
        self.logger = logger
        self._disabled = False

    def _unavailable(self, error: BaseException) -> None:
        """History improves reuse, but its failure must not stop a download."""
        if self._disabled:
            return
        self._disabled = True
        if self.logger:
            try:
                self.logger.warning("Download history is unavailable. Saving without cache; existing files will be preserved.")
                self.logger.debug(f"Download history: {error}")
            except Exception:
                # A diagnostic logger must not become another save failure.
                pass

    @contextmanager
    def _connection(self):
        connection = None
        try:
            try:
                self.database.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.database, timeout=1)
                connection.execute("CREATE TABLE IF NOT EXISTS downloads (root TEXT NOT NULL, identity TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(root, identity))")
            except (OSError, sqlite3.Error) as error:
                raise _HistoryUnavailable() from error
            try:
                yield connection
                connection.commit()
            except sqlite3.Error as error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise _HistoryUnavailable() from error
        finally:
            if connection:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    def _record(self, connection, identity: DownloadIdentity):
        return connection.execute("SELECT filename, size, sha256 FROM downloads WHERE root = ? AND identity = ?", (self.root, identity.key)).fetchone()

    def _verified(self, row) -> Path | None:
        if row:
            filename, size, checksum = row
            if not isinstance(filename, str) or not isinstance(size, int) or size <= 0 or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
                return None
            path = self.output_dir / filename
            # Treat history as data: never follow a path outside this folder.
            if path.parent == self.output_dir and path.name == filename and _fingerprint(path) == (size, checksum):
                return path
        return None

    def _cached(self, connection, identity: DownloadIdentity) -> Path | None:
        return self._verified(self._record(connection, identity))

    def cached(self, identity: DownloadIdentity) -> Path | None:
        if self._disabled:
            return None
        try:
            with self._connection() as connection:
                return self._cached(connection, identity)
        except _HistoryUnavailable as error:
            self._unavailable(error.__cause__ or error)
            return None

    def _publish_unique(self, prepared: PreparedFile, filename: str) -> Path:
        initial = self.output_dir / filename
        destination = initial
        number = 1
        while destination.exists() or destination.is_symlink() or not prepared.publish(destination):
            number += 1
            destination = initial.with_name(f"{initial.stem} ({number}){initial.suffix}")
        return destination

    def publish(self, staged: Path, identity: DownloadIdentity, title: str) -> tuple[Path, bool]:
        """Publish a complete file, keeping every conflicting or modified file."""
        fingerprint = _fingerprint(staged)
        if fingerprint is None:
            raise DownloadError("The download did not produce a complete, non-empty file.")
        extension = staged.suffix.removeprefix(".").lower()
        if not re.fullmatch(r"[a-z0-9]{1,12}", extension):
            raise DownloadError("The downloaded file has no recognized file extension.")
        filename = identity.filename(title, extension)
        # Copying, flushing and verifying large files can take longer than
        # SQLite's busy timeout. Only the final rename and index update need
        # to hold the writer lock.
        with prepare_file(staged, self.output_dir) as prepared:
            if not self._disabled:
                published = None
                try:
                    with self._connection() as connection:
                        while True:
                            record = self._record(connection, identity)
                            if existing := self._verified(record):
                                return existing, True
                            connection.execute("BEGIN IMMEDIATE")
                            # Another publisher may have completed while this
                            # file was being verified. Recheck outside the lock.
                            if self._record(connection, identity) != record:
                                connection.rollback()
                                continue
                            published = self._publish_unique(prepared, filename)
                            connection.execute("INSERT OR REPLACE INTO downloads (root, identity, filename, size, sha256) VALUES (?, ?, ?, ?, ?)", (self.root, identity.key, published.name, *fingerprint))
                            break
                    return published, False
                except _HistoryUnavailable as error:
                    self._unavailable(error.__cause__ or error)
                    if published is not None:
                        # A commit failure must not publish the same complete
                        # download twice or delete the file already delivered.
                        return published, False
            return self._publish_unique(prepared, filename), False


class _HistoryUnavailable(Exception):
    """An optional SQLite operation failed, independently of file publication."""
