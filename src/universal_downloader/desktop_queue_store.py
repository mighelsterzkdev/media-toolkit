"""Bounded, private desktop queue state; never persist extractor credentials."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .desktop_download_formats import DOWNLOAD_TARGETS_BY_KIND
from .errors import DownloadError
from .preferences import preferences_path
from .urls import validate_url

MAX_SAVED_TASKS = 1_000
MAX_QUEUE_STATE_BYTES = 8 * 1_048_576
RESTORED_MESSAGE = "Restored after restart. Retry to continue."
_STATUSES = frozenset(("queued", "checking", "downloading", "preparing", "converting", "finalizing", "cancelling", "completed", "existing", "failed", "cancelled"))
_TERMINAL = frozenset(("completed", "existing", "failed", "cancelled"))
_KINDS = frozenset(("image", "audio", "video", "animation"))
_FORMATS = frozenset(("original",)).union(*DOWNLOAD_TARGETS_BY_KIND.values())


def _string(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid saved text")
    return value


def _path(value):
    value = _string(value, 8_192)
    if not Path(value).is_absolute():
        raise ValueError("invalid saved path")
    return str(Path(value).resolve())


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) or value < 0 or value > 2 ** 63 - 1 or not math.isfinite(value):
        raise ValueError("invalid saved number")
    return value


def _settings(value):
    if not isinstance(value, dict) or set(value) != {"output", "max_height", "max_size"}:
        raise ValueError("invalid saved settings")
    result = {"output": _path(value["output"])}
    for field in ("max_height", "max_size"):
        number = value[field]
        if number is not None and (_number(number, integer=True) == 0 or number > 2 ** 63 - 1):
            raise ValueError("invalid saved limit")
        result[field] = number
    return result


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_locator(inspected):
    information = inspected.information
    media_asset = any(kind in inspected.kinds for kind in ("video", "audio")) or inspected.animation_video
    source = (information.get("webpage_url") or inspected.url) if media_asset else information.get("image_url") or information.get("url") or inspected.url
    if not isinstance(source, str):
        source = inspected.url
    try:
        parts = urlsplit(source)
        # Queries may contain expiring signatures. The extractor ID and image
        # fingerprint below disambiguate assets sharing this stable path.
        return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port, parts.path)
    except ValueError:
        return ("", "", None, inspected.url)


def selection_descriptor(inspected):
    """Keep stable, hashed asset identity rather than cookies or signed URLs."""
    information = inspected.information
    identifier = information.get("id")
    result = {"source": _digest(_source_locator(inspected))}
    content_image = bool(inspected.image_data) and not any(kind in inspected.kinds for kind in ("video", "audio")) and not inspected.animation_video
    if not content_image and isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
        result["asset"] = _digest([information.get("extractor_key") or information.get("extractor"), str(identifier)])
    if content_image:
        result["image"] = sha256(inspected.image_data).hexdigest()
    return result


def _selection(value):
    if not isinstance(value, dict) or not value or set(value) - {"source", "asset", "image"}:
        raise ValueError("invalid saved selection")
    if any(not isinstance(item, str) or re.fullmatch(r"[a-f0-9]{64}", item) is None for item in value.values()):
        raise ValueError("invalid saved selection fingerprint")
    return dict(value)


def selection_identity(descriptor):
    return _digest(_selection(descriptor))


def resolve_restored_selection(descriptor, inspected):
    """Recheck the link, then require exactly the previously selected asset."""
    expected = _selection(descriptor)
    candidates = []
    for candidate in inspected.alternatives or (inspected,):
        current = selection_descriptor(candidate)
        if all(current.get(field) == value for field, value in expected.items()):
            candidates.append(candidate)
    if len(candidates) != 1:
        raise DownloadError("This saved media selection is no longer available. Check the link and choose the item again.")
    return replace(candidates[0], alternatives=())


def _image(value):
    if not isinstance(value, dict):
        raise ValueError("invalid saved local source")
    source = _path(value.get("source"))
    signature = value.get("signature")
    if not isinstance(signature, dict) or set(signature) != {"size", "mtime_ns", "ctime_ns", "sha256"}:
        raise ValueError("invalid saved source fingerprint")
    fingerprint = {"size": _number(signature["size"], integer=True)}
    for field in ("mtime_ns", "ctime_ns"):
        stamp = signature[field]
        if type(stamp) is not int or not -(2 ** 63) <= stamp <= 2 ** 63 - 1:
            raise ValueError("invalid saved source timestamp")
        fingerprint[field] = stamp
    fingerprint["sha256"] = signature["sha256"]
    if not isinstance(fingerprint["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", fingerprint["sha256"]) is None:
        raise ValueError("invalid saved source digest")
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > len(_FORMATS) or any(not isinstance(target, str) or target not in _FORMATS - {"original"} for target in targets):
        raise ValueError("invalid saved conversion targets")
    kind = value.get("media_type", "image")
    if not isinstance(kind, str) or kind not in _KINDS or type(value.get("transparent")) is not bool:
        raise ValueError("invalid saved source kind")
    result = {"name": _string(value.get("name"), 300), "source": source, "signature": fingerprint,
              "format": _string(value.get("format"), 30), "media_type": kind, "targets": list(dict.fromkeys(targets)),
              "transparent": value["transparent"], "error": None}
    duration = value.get("duration")
    if duration is not None:
        result["duration"] = _number(duration)
    return result


def task_record(task):
    """Serialize only fields needed to display and explicitly retry a task."""
    result = {field: task.get(field) for field in ("id", "url", "title", "action", "source_format", "kind", "format", "status", "path", "error", "can_convert")}
    result["settings"] = task["_settings"]
    if task["action"] == "convert":
        result.update(image=task["_image"], background=task["_background"])
    else:
        result["selection"] = task.get("_selection")
        result["policy"] = task["_identity"][1]
        result["selection_key"] = task["_identity"][6] if len(task["_identity"]) == 7 else None
    # Applying the exact load validator also strips all unrelated metadata
    # from local inspection results, including future extractor fields.
    return _validate_record(result)


def _validate_record(value):
    if not isinstance(value, dict) or set(value) - {"id", "url", "title", "action", "source_format", "kind", "format", "status", "path", "error", "can_convert", "settings", "image", "background", "selection", "policy", "selection_key"}:
        raise ValueError("invalid saved task")
    identifier = value.get("id")
    if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
        raise ValueError("invalid saved task ID")
    action = value.get("action")
    kind, target, status = value.get("kind"), value.get("format"), value.get("status")
    if action not in ("download", "convert") or kind is not None and (not isinstance(kind, str) or kind not in _KINDS) or not isinstance(target, str) or target not in _FORMATS or not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("invalid saved operation")
    settings = _settings(value.get("settings"))
    path = _path(value["path"]) if value.get("path") is not None else None
    if path is not None and (status not in ("completed", "existing") or Path(path).parent != Path(settings["output"])):
        raise ValueError("invalid saved completed file")
    if status in ("completed", "existing") and path is None:
        raise ValueError("missing saved completed file")
    error = _string(value["error"], 1_000, empty=True) if value.get("error") is not None else None
    source_format = _string(value["source_format"], 30) if value.get("source_format") is not None else None
    result = {"id": identifier, "title": _string(value.get("title"), 300), "action": action,
              "source_format": source_format, "kind": kind, "format": target, "status": status,
              "path": path, "error": error, "can_convert": value.get("can_convert") is True, "settings": settings}
    if action == "download":
        result["url"] = validate_url(_string(value.get("url"), 16_384))
        if kind == "animation" or (target != "original" and target not in DOWNLOAD_TARGETS_BY_KIND.get(kind, ())):
            raise ValueError("invalid saved download format")
        selection = value.get("selection")
        result["selection"] = _selection(selection) if selection is not None else None
        if selection is not None and kind is None:
            raise ValueError("missing saved media selection")
        policy = value.get("policy", kind or "default")
        if policy not in ("default", "video", "audio", "image"):
            raise ValueError("invalid saved download policy")
        result["policy"] = policy
        selection_key = value.get("selection_key")
        if selection_key is not None and (selection is None or selection_key != selection_identity(selection)):
            raise ValueError("invalid saved asset identity")
        result["selection_key"] = selection_key
    else:
        image = _image(value.get("image"))
        if kind != image["media_type"] or target not in image["targets"] or value.get("background") not in ("#FFFFFF", "#000000"):
            raise ValueError("invalid saved conversion")
        result.update(url="", image=image, background=value["background"])
    return result


def restore_task(record):
    """Construct private runtime fields without performing network or file IO."""
    value = _validate_record(record)
    settings = value.pop("settings")
    image, background, selection = value.pop("image", None), value.pop("background", None), value.pop("selection", None)
    policy = value.pop("policy", None)
    selection_key = value.pop("selection_key", None)
    if value["status"] not in _TERMINAL:
        value.update(status="cancelled", error=RESTORED_MESSAGE, path=None, can_convert=False)
    if value["action"] == "convert":
        identity = ("convert", image["source"], image["signature"], value["format"],
                    background if value["format"] in ("jpg", "bmp", "gif", "mp4") and image["transparent"] else None, settings["output"])
    else:
        identity = (value["url"], policy, value["format"], settings["output"], settings["max_height"], settings["max_size"])
        if selection_key is not None:
            identity += (selection_key,)
    value.update(progress=100 if value["status"] in ("completed", "existing") else None,
                 downloaded_bytes=0, total_bytes=None, speed=None, eta=None, operation=None,
                 _identity=identity, _settings=settings, _inspected=None, _selection=selection, _attempt=None)
    if image is not None:
        value.update(_image=image, _background=background)
    return value


class DesktopQueueStore:
    """One writer per queue scope; another app instance keeps a volatile queue."""

    def __init__(self, default_output=None, *, queue_scope=None):
        scope = queue_scope if queue_scope is not None else "desktop" if default_output is None else os.path.normcase(str(Path(default_output).resolve()))
        self.scope = sha256(scope.encode("utf-8")).hexdigest()[:24]
        self.path = preferences_path().parent / f"desktop-queue-{self.scope}.json"
        self._lock = None
        self.notice = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_suffix(".lock")
            if lock_path.is_symlink():
                raise OSError("queue lock must be a regular file")
            stream = lock_path.open("a+b")
            self._lock = stream
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            self.notice = "The saved queue is open in another app instance or unavailable. This window's queue will not be saved."

    def load(self):
        if self._lock is None:
            return []
        try:
            if self.path.is_symlink():
                raise ValueError("saved queue must be a regular file")
            with self.path.open("rb") as source:
                raw = source.read(MAX_QUEUE_STATE_BYTES + 1)
            if len(raw) > MAX_QUEUE_STATE_BYTES:
                raise ValueError("saved queue is too large")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"version", "scope", "tasks"} or type(payload["version"]) is not int or payload["version"] != 1 or payload["scope"] != self.scope:
                raise ValueError("unsupported saved queue")
            records = payload["tasks"]
            if not isinstance(records, list) or len(records) > MAX_SAVED_TASKS:
                raise ValueError("too many saved tasks")
            result = [restore_task(record) for record in records]
            if len({task["id"] for task in result}) != len(result):
                raise ValueError("duplicate saved task IDs")
            return result
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, RecursionError, UnicodeError, DownloadError):
            self.notice = "The saved queue could not be restored. New tasks can still be added."
            return []

    def save(self, tasks):
        if self._lock is None:
            return False
        temporary = None
        try:
            if len(tasks) > MAX_SAVED_TASKS:
                raise ValueError("too many tasks")
            payload = {"version": 1, "scope": self.scope, "tasks": [task_record(task) for task in tasks]}
            raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            if len(raw) > MAX_QUEUE_STATE_BYTES:
                raise ValueError("queue state is too large")
            with tempfile.NamedTemporaryFile(prefix=".desktop-queue-", suffix=".tmp", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                if os.name != "nt":
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return True
        except (OSError, ValueError, TypeError, RecursionError, DownloadError):
            self.notice = "The queue could not be saved. Current tasks can continue, but may not be restored after closing."
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def close(self):
        # Closing releases the OS lock even after an interrupted writer. The
        # marker file alone is never interpreted as a running process.
        if self._lock is not None:
            self._lock.close()
            self._lock = None
