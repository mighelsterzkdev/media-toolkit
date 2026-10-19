"""Keep trusted interrupted work outside downloads, under operating-system locks."""

from __future__ import annotations

from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from yt_dlp.networking import HEADRequest, Request
from yt_dlp.networking.exceptions import HTTPError, RequestError

from .errors import DownloadError
from .identity import _fingerprint, _state_directory

CHECKPOINT_BLOCK = 65_536
CHECKPOINT_INTERVAL = 1_048_576


def _hash(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DownloadError("The resume lock path is not a regular file.")
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DownloadError("This download is already running in another process. Wait for it to finish.", code="resume_locked") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _response_state(headers) -> tuple[int | None, str | None, str | None]:
    try:
        length = int(headers.get("Content-Length", "0")) or None
        if headers.get("Content-Range"):
            length = int(headers["Content-Range"].rsplit("/", 1)[1])
        if length is not None and length <= 0:
            length = None
    except (ValueError, TypeError):
        length = None
    if headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        return None, None, None
    etag = headers.get("ETag", "").strip()
    if length and etag.startswith('"') and etag.endswith('"') and not etag.startswith("W/"):
        return length, "ETag", etag
    modified, date = headers.get("Last-Modified"), headers.get("Date")
    try:
        # HTTP dates have one-second precision. The 60-second rule avoids
        # treating a recently modified Last-Modified value as a strong validator.
        reliable_date = date and modified and (parsedate_to_datetime(date) - parsedate_to_datetime(modified)).total_seconds() >= 60
    except (ValueError, TypeError, OverflowError):
        reliable_date = False
    return (length, "Last-Modified", modified) if length and reliable_date else (length, None, None)


def _probe(downloader, source, headers, *, logger=None):
    try:
        return _probe_response(downloader, source, headers)
    except (RequestError, OSError) as error:
        if isinstance(error, HTTPError):
            error.close()
        if logger:
            try:
                logger.debug(f"Resume validation unavailable: {error}")
            except Exception:
                pass
        # Preflight is an optimization. A server may reject HEAD and a tiny
        # range probe while allowing the normal, full GET download.
        return None, None, None, None


def _probe_response(downloader, source, headers):
    try:
        response = downloader.urlopen(HEADRequest(source["url"], headers=headers, extensions={"timeout": 8}))
    except HTTPError as error:
        status = error.status
        error.close()
        # Some sites route HEAD through a different handler and return a false
        # 403/404 even though the same authenticated GET is available.
        if status not in (400, 401, 403, 404, 405, 501):
            raise
        response = downloader.urlopen(Request(source["url"], headers={**headers, "Range": "bytes=0-0"}, extensions={"timeout": 8}))
    with response:
        return (*_response_state(response.headers), _hash(response.url))


class ResumeWork:
    """One identity and output folder own one durable, exclusively locked work area."""

    def __init__(self, output_dir, identity, *, logger=None, progress_hook=None):
        self.output_dir = Path(output_dir).resolve()
        self.identity = identity.key
        self.root_hash = _hash(os.path.normcase(str(Path(output_dir).resolve())))
        self.key = _hash([self.root_hash, identity.key])
        self.base = _state_directory().resolve() / "resume"
        self.path = self.base / self.key
        self.data = self.path / "data"
        self.logger = logger
        self.progress_hook = progress_hook
        self.metadata = {}
        self.validators = {}
        self._success = False
        self._guard_active = False
        self._temporary = None

    def fallback(self, error=None):
        if self._guard_active:
            self._guard.__exit__(None, None, None)
            self._guard_active = False
        self._temporary = tempfile.TemporaryDirectory(prefix=".unidown-", dir=self.output_dir)
        self.path = Path(self._temporary.name)
        self.data = self.path / "data"
        self.data.mkdir()
        self.metadata = {}
        self.validators = {}
        if self.logger:
            try:
                self.logger.warning("Resume storage is unavailable. Downloading safely without continuation.")
                self.logger.debug(f"Resume storage: {error}")
            except Exception:
                pass

    def __enter__(self):
        self._guard = _lock(_state_directory().resolve() / "resume-locks" / f"{self.key}.lock")
        try:
            self._guard.__enter__()
            self._guard_active = True
            if self.path.is_symlink():
                raise DownloadError("The resume work path is not a regular directory.")
            self.path.mkdir(parents=True, exist_ok=True)
            if self.path.resolve().parent != self.base.resolve():
                raise DownloadError("The resume work path is outside its private storage.")
            self.metadata = _read_state(self.path / "state.json", key=self.key, root=self.root_hash) or {}
            if self.data.is_symlink() or self.data.exists() and self.data.resolve().parent != self.path.resolve():
                raise DownloadError("The resume data path is not a regular private directory.")
            self.data.mkdir(exist_ok=True)
            return self
        except OSError as error:
            self.fallback(error)
            return self
        except BaseException:
            if self._guard_active:
                self._guard.__exit__(*__import__("sys").exc_info())
            raise

    def _write(self):
        if self._temporary:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="state-", suffix=".tmp", dir=self.path, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.metadata, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path / "state.json")
        except OSError as error:
            raise DownloadError("Resume storage became unavailable.", code="resume_unavailable") from error
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def reset(self):
        if self.data.exists():
            if self.data.is_symlink() or self.data.resolve().parent != self.path.resolve():
                raise DownloadError("The resume work path is not a regular directory.")
            shutil.rmtree(self.data)
        self.data.mkdir()
        self.metadata = {"version": 1, "root": self.root_hash, "identity": self.identity, "sources": [], "files": {}, "transfers": {}, "partials": {}, "trusted": False}

    def prepare(self, downloader, sources, information, *, image_data=None, maximum=None):
        try:
            return self._prepare(downloader, sources, information, image_data=image_data, maximum=maximum)
        except OSError as error:
            raise DownloadError("Resume storage became unavailable.", code="resume_unavailable") from error

    def _prepare(self, downloader, sources, information, *, image_data=None, maximum=None):
        if self.data.is_symlink() or self.data.exists() and (self.data.resolve().parent != self.path.resolve() or any(path.is_symlink() for path in self.data.rglob("*"))):
            raise DownloadError("The resume data path contains a symbolic link and cannot be trusted.")
        states = []
        self.validators = {}
        trusted = True
        if image_data is not None:
            states = [{"image": sha256(image_data).hexdigest(), "length": len(image_data)}]
        else:
            for source in sources:
                protocol = source.get("protocol") or source.get("url", "").split(":", 1)[0]
                if protocol not in ("http", "https") or source.get("fragments") or source.get("request_data"):
                    trusted = False
                    break
                headers = {**(information.get("http_headers") or {}), **(source.get("http_headers") or {}), "Accept-Encoding": "identity"}
                length, header, value, final_url = _probe(downloader, source, headers, logger=self.logger)
                signature = _hash({**{key: source.get(key) for key in ("url", "format_id", "ext", "vcodec", "acodec", "width", "height", "fps", "abr", "tbr", "language")}, "resolved_url": final_url})
                states.append({"signature": signature, "validator": _hash([header, value]) if header else None, "length": length})
                trusted = trusted and bool(header and length)
                self.validators[source["url"]] = (length, header, value, str(source.get("format_id") or ""), final_url)
                if header:
                    source["http_headers"] = {**headers, "If-Range": value}
        if maximum and states and all(state.get("length") for state in states) and sum(state["length"] for state in states) > maximum:
            raise DownloadError("The source download exceeds --max-size. Increase the limit or select a smaller source.")
        if not trusted or not self.metadata.get("trusted") or self.metadata.get("sources") != states:
            self.reset()
        elif not self._verify_partials():
            self.reset()
        self.metadata.update(sources=states, trusted=trusted)
        self.data.mkdir(exist_ok=True)
        # A file renamed immediately before a process was killed may not have
        # a completed fingerprint. Do not let yt-dlp trust its name alone.
        for path in self.data.rglob("*"):
            if path.is_file() and not path.name.endswith(".part"):
                relative = path.relative_to(self.data).as_posix()
                expected = self.metadata.get("files", {}).get(relative)
                if not expected or _fingerprint(path) != tuple(expected):
                    path.unlink()
        self._write()
        existing = sum(path.stat().st_size for path in self.data.rglob("*.part") if path.is_file())
        if existing and self.progress_hook:
            self.progress_hook({"status": "resuming", "downloaded_bytes": existing, "total_bytes": sum(state.get("length") or 0 for state in states) or None})

    def _verify_partials(self):
        for path in self.data.glob("*.part"):
            record = self.metadata.get("partials", {}).get(path.name)
            if not record:
                path.unlink()
                continue
            size = record["size"]
            if path.stat().st_size < size:
                return False
            chain = bytes(32)
            with path.open("rb") as stream:
                for _ in range(size // CHECKPOINT_BLOCK):
                    chain = sha256(chain + stream.read(CHECKPOINT_BLOCK)).digest()
                tail = stream.read(size % CHECKPOINT_BLOCK)
            if chain.hex() != record["chain"] or (sha256(tail).hexdigest() if tail else None) != record["tail"]:
                return False
            if path.stat().st_size > size:
                with path.open("r+b") as stream:
                    stream.truncate(size)
        return True

    def checkpoint(self, *, final=False):
        if not self.metadata.get("trusted") or self._temporary:
            return
        changed = False
        for path in self.data.glob("*.part"):
            if path.is_symlink():
                continue
            actual = path.stat().st_size
            previous = self.metadata.setdefault("partials", {}).get(path.name)
            old_size = previous["size"] if previous else 0
            size = actual if final else actual // CHECKPOINT_BLOCK * CHECKPOINT_BLOCK
            if not size or not final and previous and size - old_size < CHECKPOINT_INTERVAL:
                continue
            old_full = old_size // CHECKPOINT_BLOCK * CHECKPOINT_BLOCK
            chain = bytes.fromhex(previous["chain"]) if previous and size >= old_size else bytes(32)
            if size < old_size:
                old_full = 0
            with path.open("rb") as stream:
                stream.seek(old_full)
                for _ in range((size - old_full) // CHECKPOINT_BLOCK):
                    chain = sha256(chain + stream.read(CHECKPOINT_BLOCK)).digest()
                tail = stream.read(size % CHECKPOINT_BLOCK)
            self.metadata["partials"][path.name] = {"size": size, "chain": chain.hex(), "tail": sha256(tail).hexdigest() if tail else None}
            changed = True
        if changed:
            self._write()

    def completed(self, role):
        relative = self.metadata.get(role)
        if isinstance(relative, str):
            path = self.data / relative
            expected = self.metadata.get("files", {}).get(relative)
            if path.parent == self.data and expected and _fingerprint(path) == tuple(expected):
                return path
        return None

    def remember(self, role, path):
        relative = Path(path).relative_to(self.data).as_posix()
        self.metadata.setdefault("files", {})[relative] = _fingerprint(path)
        if role:
            self.metadata[role] = relative
        self._write()

    def progress(self, status):
        path = Path(status.get("filename") or "")
        if path.is_absolute() and path.parent == self.data:
            relative = path.relative_to(self.data).as_posix()
            first = relative not in self.metadata.setdefault("transfers", {})
            self.metadata["transfers"][relative] = status.get("total_bytes")
            if status.get("status") == "finished":
                source_url = (status.get("info_dict") or {}).get("url")
                expected = self.validators.get(source_url)
                if expected and expected[0] and path.stat().st_size != expected[0]:
                    raise DownloadError("The source length changed. Restarting its partial download.", code="resume_changed")
                self.remember(None, path)
            elif first:
                self._write()
            if status.get("status") == "downloading":
                self.checkpoint()

    @contextmanager
    def guard_requests(self, downloader):
        original = downloader.urlopen

        def checked(request):
            url = request if isinstance(request, str) else request.url
            expected = self.validators.get(url)
            try:
                response = original(request)
            except HTTPError as error:
                if error.status == 416 and expected:
                    error.close()
                    raise DownloadError("The partial source must be restarted.", code="resume_changed") from error
                raise
            if expected:
                length, header, value, _, final_url = expected
                actual_length, actual_header, actual_value = _response_state(response.headers)
                ranged = not isinstance(request, str) and bool(request.headers.get("Range"))
                changed = final_url is not None and final_url != _hash(response.url) or header and (actual_header != header or actual_value != value or actual_length != length)
                if changed:
                    if ranged:
                        response.close()
                        raise DownloadError("The media source changed. Restarting its partial download.", code="resume_changed")
                    # A fresh full GET cannot mix old and new bytes. CDNs may
                    # generate a new redirect token on every request; save the
                    # full response normally and turn off continuation for it.
                    self.metadata["trusted"] = False
                    self.validators[url] = (None, None, None, expected[3], None)
                    self._write()
            return response

        downloader.urlopen = checked
        try:
            yield
        finally:
            downloader.urlopen = original

    def complete(self):
        self._success = True

    def __exit__(self, exception_type, error, traceback):
        preserve = isinstance(error, (KeyboardInterrupt, SystemExit)) or isinstance(error, DownloadError) and error.code in ("network", "server", "rate_limited", "access_denied", "authentication")
        try:
            if self._temporary:
                self._temporary.cleanup()
                return
            if self._success or not preserve or not self.metadata.get("trusted"):
                if self.path.exists():
                    shutil.rmtree(self.path)
            elif self.metadata.get("trusted") and any(self.data.iterdir()):
                self.checkpoint(final=True)
                if self.progress_hook:
                    self.progress_hook({"status": "resume_saved"})
        finally:
            if self._guard_active:
                self._guard.__exit__(exception_type, error, traceback)


def _read_state(path, *, key=None, root=None):
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as stream:
            data = stream.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            return None
        value = json.loads(data)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("trusted"), bool):
        return None
    for field in ("root", "identity"):
        if not isinstance(value.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
            return None
    if key is not None and _hash([value["root"], value["identity"]]) != key or root is not None and value["root"] != root:
        return None
    for field in ("files", "transfers", "partials"):
        records = value.get(field)
        if not isinstance(records, dict) or len(records) > 64:
            return None
        for filename, record in records.items():
            if not isinstance(filename, str) or Path(filename).name != filename or "/" in filename or "\\" in filename:
                return None
            if field == "files":
                if not isinstance(record, list) or len(record) != 2 or not isinstance(record[0], int) or record[0] <= 0 or not isinstance(record[1], str) or not re.fullmatch(r"[a-f0-9]{64}", record[1]):
                    return None
            elif field == "partials":
                if not isinstance(record, dict) or not isinstance(record.get("size"), int) or record["size"] <= 0 or not isinstance(record.get("chain"), str) or not re.fullmatch(r"[a-f0-9]{64}", record["chain"]) or record.get("tail") is not None and (not isinstance(record["tail"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["tail"])):
                    return None
            elif record is not None and (not isinstance(record, (int, float)) or record < 0):
                return None
    sources = value.get("sources")
    if not isinstance(sources, list) or len(sources) > 16 or any(not isinstance(source, dict) for source in sources):
        return None
    for source in sources:
        identity = source.get("signature") or source.get("image")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            return None
        length = source.get("length")
        if length is not None and (not isinstance(length, int) or isinstance(length, bool) or length <= 0):
            return None
        validator = source.get("validator")
        if validator is not None and (not isinstance(validator, str) or not re.fullmatch(r"[a-f0-9]{64}", validator)):
            return None
    for field in ("original", "converted"):
        if field in value and (not isinstance(value[field], str) or value[field] not in value["files"]):
            return None
    return value


def pending_resumes(output_dir=None):
    root_hash = _hash(os.path.normcase(str(Path(output_dir).resolve()))) if output_dir is not None else None
    count = 0
    base = _state_directory().resolve() / "resume"
    for path in base.glob("*") if base.exists() else ():
        if not path.is_dir() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", path.name):
            continue
        try:
            state = _read_state(path / "state.json", key=path.name)
            if state and state.get("trusted") and (root_hash is None or state.get("root") == root_hash):
                count += 1
        except (OSError, ValueError):
            continue
    return count


def clear_resume(output_dir=None):
    root_hash = _hash(os.path.normcase(str(Path(output_dir).resolve()))) if output_dir is not None else None
    count = 0
    base = _state_directory().resolve() / "resume"
    for path in base.glob("*") if base.exists() else ():
        if not path.is_dir() or path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", path.name):
            continue
        try:
            with _lock(_state_directory().resolve() / "resume-locks" / f"{path.name}.lock"):
                state = _read_state(path / "state.json", key=path.name)
                if state and (root_hash is None or state.get("root") == root_hash):
                    shutil.rmtree(path)
                    count += 1
        except (DownloadError, OSError, ValueError):
            continue
    return count
