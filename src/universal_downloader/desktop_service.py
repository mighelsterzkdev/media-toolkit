"""Desktop state and queue, independent of the browser and terminal interface."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from uuid import uuid4

from PIL import Image, ImageOps

from . import __version__
from .desktop_download_formats import available_download_conversions
from .dependencies import PROJECT_ROOT, find_ffmpeg, find_js_runtime
from .desktop_queue_store import DesktopQueueStore, RESTORED_MESSAGE, conversion_background, selection_descriptor, selection_identity
from .desktop_worker import ProcessWorker
from .errors import DownloadError
from .limits import validate_download_source_limits, validate_limits
from .preferences import THEMES, load_preferences, load_theme, save_preferences, save_theme
from .queue import MAX_BATCH_BYTES, MAX_QUEUE_URLS, MAX_URL_LENGTH, validated_urls
from .resume import pending_resumes
from .urls import validate_url

TERMINAL = frozenset(("completed", "existing", "failed", "cancelled"))
ACTIVE = frozenset(("checking", "downloading", "preparing", "converting", "finalizing", "cancelling"))
MAX_QUEUED_IMAGE_BYTES = 128 * 1024 * 1024
MAX_CONVERSION_FILES = 100
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _text(value, limit=500):
    return "".join(character for character in _ANSI.sub("", str(value)) if ord(character) >= 32 and ord(character) != 127)[:limit]


def safe_error(error):
    if not isinstance(error, DownloadError):
        return "This operation could not be completed. Try again."
    message = _text(error, 2000)
    replacements = (
        ("Try again later, or use --type image to request only its thumbnail.", "Try again later or check a direct image link."),
        ("Select one in the menu or with --item NUMBER.", "Use Check and choose the item to download."),
        ("Select one with --item NUMBER.", "Use Check and choose the item to download."),
        ("Run scripts/install.cmd on Windows, or install FFmpeg and add both programs to PATH.", "Install the media tools from this app."),
        ("Run scripts/install.cmd.", "Install the media tools from this app."),
        ("Use --reset-preferences or --no-preferences.", "Open Settings and save your preferences again."),
        ("Downloads can continue with --no-preferences.", "You can continue downloading and try saving the settings again later."),
        (" Use --verbose for details.", ""),
        (" or use --verbose for technical details", ""),
        ("Try updating yt-dlp.", "Update the app and check the link again."),
        ("--max-height", "the maximum video height in Settings"),
        ("--max-size", "the maximum file size in Settings"),
    )
    for original, translated in replacements:
        message = message.replace(original, translated)
    return _ERROR_URL.sub("the media link", message)[:1000]


def _thumbnail(inspected):
    if not inspected.image_data:
        return None
    try:
        with Image.open(BytesIO(inspected.image_data)) as source:
            if source.width * source.height > 40_000_000:
                return None
            source.seek(0)
            with ImageOps.exif_transpose(source) as oriented:
                image = oriented.convert("RGB")
        try:
            image.thumbnail((320, 200))
            buffer = BytesIO()
            image.save(buffer, "PNG", optimize=True)
            data = buffer.getvalue()
            if len(data) > 102_400:
                image.thumbnail((224, 140))
                buffer = BytesIO()
                image.save(buffer, "PNG", optimize=True)
                data = buffer.getvalue()
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii") if len(data) <= 102_400 else None
        finally:
            image.close()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _height(settings, inspected, kind):
    return None if kind == "audio" or (kind == "image" and not inspected.animated) else settings["max_height"]


def _asset_description(inspected):
    descriptions = []
    if "video" in inspected.kinds or inspected.animation_video:
        if inspected.heights:
            descriptions.append(f"Up to {max(inspected.heights)}p")
        else:
            height = inspected.information.get("height")
            if isinstance(height, (int, float)) and not isinstance(height, bool) and math.isfinite(height) and height > 0:
                descriptions.append(f"{int(height)}p")
    elif "audio" not in inspected.kinds and (dimensions := inspected.image_dimensions):
        descriptions.append(f"{dimensions[0]} × {dimensions[1]}")
    duration = inspected.information.get("duration")
    if isinstance(duration, (float, int)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
        seconds = int(duration)
        descriptions.append(f"{seconds // 60}:{seconds % 60:02d}")
    return " · ".join(descriptions) or ("Animation" if inspected.animated else "Verified media")


def _asset_options(inspected, settings):
    kinds = []
    names = {"jpg": "JPEG", "jp2": "JPEG 2000", "webp": "WebP", "webm": "WebM"}
    audio = frozenset(("mp3", "wav", "flac", "m4a", "aac", "opus", "ogg", "aiff"))
    video = frozenset(("mp4", "mkv", "mov", "webm"))
    for kind in inspected.kinds:
        height = _height(settings, inspected, kind)
        options = []
        try:
            validate_download_source_limits(inspected, kind, target=None, max_height=height, max_size=settings["max_size"])
        except DownloadError:
            pass
        else:
            extension = inspected.original_extension(kind)
            options.append({"id": "original", "label": "Original", "description": f"Keep the source format{f' ({extension.upper()})' if extension else ''}.", "recommended": True})
        for target, description in available_download_conversions(inspected, kind, max_height=height).items():
            try:
                validate_download_source_limits(inspected, kind, target=target, max_height=height, max_size=settings["max_size"])
            except DownloadError:
                continue
            category = ("audio" if target in audio else "video" if target in video else
                        "animation" if (inspected.animated or kind == "video") and target in ("gif", "webp", "apng") else "image")
            native_av = target == inspected.original_extension(kind) and (kind in ("video", "audio") or inspected.animation_video)
            label = f"{names.get(target, target.upper())} {'compatibility' if native_av else category}"
            options.append({"id": target, "label": label, "description": description, "recommended": False})
        if options:
            kinds.append({"id": kind, "label": inspected.labels[kind], "options": options})
    return kinds


class DesktopService:
    """Own private checked media and a persisted queue; expose a bounded UI view."""

    def __init__(self, default_output: Path | None = None, *, queue_scope=None):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = False
        self._generation = 0
        self._conversion_generation = 0
        self._inspection_worker = None
        self._conversion_worker = None
        self._download_worker = None
        self._workers = set()
        self._assets = {}
        self._local_sources = {}
        self._local_images = {}
        self._tasks = []
        self._notice = None
        try:
            saved = load_preferences()
        except DownloadError as error:
            saved = {}
            self._notice = safe_error(error)
        self._settings = {"output": str(Path(saved.get("output") or default_output or PROJECT_ROOT / "downloads").expanduser().resolve()), "max_height": saved.get("max_height"), "max_size": saved.get("max_size")}
        self._theme = load_theme()
        self._queue_store = DesktopQueueStore(default_output, queue_scope=queue_scope)
        self._tasks = self._queue_store.load()
        if self._queue_store.notice:
            self._notice = self._queue_store.notice
        self._inspection = self._idle_inspection()
        self._conversion = self._idle_conversion()
        self._resume_time = 0
        self._resume_count = 0
        self._queue_thread = threading.Thread(target=self._run_queue, name="UniversalDownloader-queue", daemon=True)
        self._queue_thread.start()

    @staticmethod
    def _idle_inspection():
        return {"status": "idle", "id": None, "url": "", "error": None, "assets": []}

    @staticmethod
    def _idle_conversion():
        return {"status": "idle", "id": None, "files": [], "error": None}

    def _ensure_open(self):
        if self._closed:
            raise DownloadError("The application is closing.")

    def _persist_queue(self):
        """Called under the service lock at durable mutations, never per poll."""
        if not self._queue_store.save(self._tasks) and self._queue_store.notice:
            self._notice = self._queue_store.notice

    def get_state(self):
        with self._lock:
            if time.monotonic() - self._resume_time > 2:
                try:
                    self._resume_count = pending_resumes(self._settings["output"])
                except (OSError, ValueError):
                    self._resume_count = 0
                self._resume_time = time.monotonic()
            tasks = [{key: value for key, value in task.items() if not key.startswith("_")} for task in self._tasks]
            return deepcopy({"version": __version__, "settings": self._settings, "inspection": self._inspection, "conversion": self._conversion, "tasks": tasks, "notice": self._notice, "resume_count": self._resume_count, "theme": self._theme})

    def check_url(self, url):
        if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
            raise DownloadError("Paste a valid media URL of up to 16,384 characters.")
        url = validate_url(url)
        with self._lock:
            self._ensure_open()
            self._generation += 1
            generation = self._generation
            old = self._inspection_worker
            self._assets = {}
            self._inspection = {"status": "checking", "id": None, "url": url, "error": None, "assets": []}
        if old:
            old.close()
        threading.Thread(target=self._inspect, args=(url, generation), name="UniversalDownloader-check", daemon=True).start()
        return self.get_state()

    def _create_worker(self, *, generation=None, conversion_generation=None, task=None, attempt=None):
        worker = ProcessWorker()
        with self._lock:
            if (self._closed or (generation is not None and generation != self._generation)
                    or (conversion_generation is not None and conversion_generation != self._conversion_generation)
                    or (task is not None and (task["status"] in ("cancelled", "cancelling") or task["_attempt"] != attempt))):
                worker.close()
                return None
            self._workers.add(worker)
            if generation is not None:
                self._inspection_worker = worker
            elif conversion_generation is not None:
                self._conversion_worker = worker
            else:
                self._download_worker = worker
        return worker

    def _forget_worker(self, worker):
        if worker:
            worker.close()
            with self._lock:
                self._workers.discard(worker)
                if self._inspection_worker is worker:
                    self._inspection_worker = None
                if self._conversion_worker is worker:
                    self._conversion_worker = None
                if self._download_worker is worker:
                    self._download_worker = None

    def _inspect(self, url, generation):
        worker = None
        try:
            worker = self._create_worker(generation=generation)
            if worker is None:
                return
            worker.begin({"action": "inspect", "url": url})
            result = None
            for event, payload in worker.events():
                if event == "inspection":
                    result = payload
                elif event == "error":
                    raise DownloadError(payload["message"], code=payload["code"])
            if result is None:
                raise DownloadError("The media check stopped before returning a result. Try again.")
            with self._lock:
                if self._closed or generation != self._generation:
                    return
                inspection_id = uuid4().hex
                assets, private = [], {}
                for inspected in result.alternatives or (result,):
                    inspected = replace(inspected, alternatives=())
                    identifier = uuid4().hex
                    kinds = _asset_options(inspected, self._settings)
                    description = _asset_description(inspected) if kinds else "No available format fits the current limits. Change settings and check again."
                    assets.append({"id": identifier, "title": _text(inspected.information.get("title") or "Download", 300), "description": description, "thumbnail": _thumbnail(inspected), "kinds": kinds, "default_kind": kinds[0]["id"] if kinds else None})
                    private[identifier] = inspected
                self._assets = private
                self._inspection = {"status": "ready", "id": inspection_id, "url": url, "error": None, "assets": assets}
        except BaseException as error:
            with self._lock:
                if not self._closed and generation == self._generation:
                    self._inspection = {"status": "failed", "id": None, "url": url, "error": safe_error(error), "assets": []}
        finally:
            self._forget_worker(worker)

    def cancel_check(self):
        with self._lock:
            self._generation += 1
            worker = self._inspection_worker
            self._inspection = self._idle_inspection()
            self._assets = {}
        if worker:
            worker.close()
        return self.get_state()

    def _start_local_check(self):
        """Called with the lock; every restart invalidates the previous result."""
        self._conversion_generation += 1
        generation = self._conversion_generation
        previous = self._conversion_worker
        sources = list(self._local_sources.items())
        self._local_images = {}
        self._conversion.update(status="checking" if sources else "idle", id=None, error=None)
        return previous, generation, sources

    def _launch_local_check(self, previous, generation, sources):
        if previous:
            previous.close()
        if sources:
            threading.Thread(target=self._inspect_local, args=(sources, generation), name="MediaToolkit-local-check", daemon=True).start()

    def add_conversion_files(self, paths):
        if not isinstance(paths, (list, tuple)) or not paths or len(paths) > MAX_CONVERSION_FILES:
            raise DownloadError("Choose between 1 and 100 media files.")
        normalized = []
        for value in paths:
            if not isinstance(value, (str, Path)):
                raise DownloadError("Choose valid local media files.")
            value = str(value)
            if not value or len(value) > 8192 or any(ord(character) < 32 for character in value) or not Path(value).is_absolute():
                raise DownloadError("Choose valid local media files with absolute paths.")
            try:
                normalized.append(str(Path(value).resolve()))
            except (OSError, ValueError) as error:
                raise DownloadError("This file path could not be opened.") from error
        with self._lock:
            self._ensure_open()
            known = {os.path.normcase(source) for source in self._local_sources.values()}
            additions = []
            for source in normalized:
                identity = os.path.normcase(source)
                if identity not in known:
                    additions.append(source)
                    known.add(identity)
            if len(self._local_sources) + len(additions) > MAX_CONVERSION_FILES:
                raise DownloadError("The converter supports up to 100 selected files. Remove some files first.")
            for source in additions:
                identifier = uuid4().hex
                self._local_sources[identifier] = source
                self._conversion["files"].append({"id": identifier, "name": _text(Path(source).name, 300), "format": None,
                                                  "width": None, "height": None, "size": None, "transparent": False, "targets": [], "error": None})
            launch = self._start_local_check()
        self._launch_local_check(*launch)
        return self.get_state()

    def _inspect_local(self, sources, generation):
        worker = None
        try:
            worker = self._create_worker(conversion_generation=generation)
            if worker is None:
                return
            worker.begin({"action": "inspect_local", "paths": [source for _, source in sources]})
            result = None
            for event, payload in worker.events():
                if event == "local_inspection":
                    result = payload
                elif event == "error":
                    raise DownloadError(payload["message"], code=payload["code"])
            if not isinstance(result, list) or len(result) != len(sources):
                raise DownloadError("The file check stopped before returning its result. Add the files again.")
            with self._lock:
                if self._closed or generation != self._conversion_generation:
                    return
                files, images = [], {}
                for (identifier, source), image in zip(sources, result):
                    # Only a fixed metadata schema crosses the native bridge.
                    public = {key: image.get(key) for key in ("format", "width", "height", "size", "transparent", "targets", "error",
                                                             "media_type", "duration", "animated", "pages", "has_audio", "target_notes", "requires_tools", "background_targets")}
                    public.update(id=identifier, name=_text(Path(source).name, 300))
                    files.append(public)
                    if not image.get("error"):
                        images[identifier] = deepcopy(image)
                self._local_images = images
                self._conversion = {"status": "ready", "id": uuid4().hex, "files": files, "error": None}
        except BaseException as error:
            with self._lock:
                if not self._closed and generation == self._conversion_generation:
                    self._local_images = {}
                    self._conversion.update(status="failed", id=None, error=safe_error(error))
        finally:
            self._forget_worker(worker)

    def remove_conversion_file(self, identifier):
        with self._lock:
            self._ensure_open()
            if not isinstance(identifier, str) or identifier not in self._local_sources:
                raise DownloadError("This selected file no longer exists.")
            self._local_sources.pop(identifier)
            self._conversion["files"] = [image for image in self._conversion["files"] if image["id"] != identifier]
            launch = self._start_local_check()
        self._launch_local_check(*launch)
        return self.get_state()

    def clear_conversion_files(self):
        with self._lock:
            self._ensure_open()
            self._local_sources = {}
            self._conversion = self._idle_conversion()
            launch = self._start_local_check()
        self._launch_local_check(*launch)
        return self.get_state()

    def refresh_conversion_files(self):
        """Recheck selected files when newly installed codecs become available."""
        with self._lock:
            if self._closed or not self._local_sources:
                return self.get_state()
            launch = self._start_local_check()
        self._launch_local_check(*launch)
        return self.get_state()

    def enqueue_conversions(self, identifiers, target, background="#FFFFFF"):
        if (not isinstance(identifiers, list) or not identifiers or len(identifiers) > MAX_CONVERSION_FILES
                or any(not isinstance(identifier, str) for identifier in identifiers) or len(set(identifiers)) != len(identifiers)):
            raise DownloadError("Select between 1 and 100 checked media files.")
        if not isinstance(target, str) or not re.fullmatch(r"[a-z0-9]{2,8}", target) or background not in ("#FFFFFF", "#000000"):
            raise DownloadError("Choose an available output format and a white or black background.")
        with self._lock:
            self._ensure_open()
            if self._conversion["status"] != "ready":
                raise DownloadError("Wait for the selected files to be checked before converting.")
            records = []
            for identifier in identifiers:
                image = self._local_images.get(identifier)
                if image is None or target not in image["targets"]:
                    raise DownloadError("Choose a compatible format for every submitted file.")
                records.append(deepcopy(image))
            settings = deepcopy(self._settings)
            # Background only affects outputs with reduced alpha. Irrelevant
            # settings must not make two identical conversions distinct.
            identities = [("convert", image["source"], image["signature"], target,
                           conversion_background(image, target, background), settings["output"]) for image in records]
            active = [task["_identity"] for task in self._tasks if task["status"] not in TERMINAL]
            additions = [(image, identity) for image, identity in zip(records, identities) if identity not in active]
            if len(self._tasks) + len(additions) > MAX_QUEUE_URLS:
                raise DownloadError("The queue supports up to 1,000 items. Remove finished items first.")
            for image, identity in additions:
                self._tasks.append({"id": uuid4().hex, "url": "", "title": _text(image["name"], 300), "action": "convert",
                                    "source_format": image["format"], "can_convert": False, "kind": image.get("media_type", "image"), "format": target,
                                    "status": "queued", "progress": None, "downloaded_bytes": 0, "total_bytes": None,
                                    "speed": None, "eta": None, "path": None, "error": None, "operation": None,
                                    "_identity": identity, "_settings": deepcopy(settings), "_image": image,
                                    "_background": background, "_inspected": None, "_attempt": None})
            self._persist_queue()
            self._wake.set()
        return self.get_state()

    def _add_task(self, url, inspected=None, kind=None, output_format="original", *, retry_url=None):
        settings = deepcopy(self._settings)
        policy = "default" if kind is None or (inspected and kind == inspected.kinds[0]) else kind
        selection = selection_descriptor(inspected) if inspected is not None else None
        asset_url = url
        url = retry_url or url
        identity = (url, policy, output_format, settings["output"], settings["max_height"], settings["max_size"])
        if inspected is not None and asset_url != url:
            # A page may expose several signed asset links. Keep the stable
            # page as the retry URL and distinguish its explicitly chosen asset.
            identity += (selection_identity(selection),)
        for task in self._tasks:
            if task["_identity"] == identity and task["status"] not in TERMINAL:
                return task
        if len(self._tasks) >= MAX_QUEUE_URLS:
            raise DownloadError("The queue supports up to 1,000 items. Remove finished items first.")
        if inspected and inspected.image_data:
            buffers = {id(item["_inspected"].image_data): len(item["_inspected"].image_data)
                       for item in self._tasks if item["_inspected"] is not None and item["_inspected"].image_data}
            buffers[id(inspected.image_data)] = len(inspected.image_data)
            if sum(buffers.values()) > MAX_QUEUED_IMAGE_BYTES:
                raise DownloadError("The queue is holding too much image data. Wait for active downloads or clear finished items before adding more.")
        task = {"id": uuid4().hex, "url": url, "title": _text(inspected.information.get("title") or "Download", 300) if inspected else _text(url, 300),
                "action": "download", "source_format": None, "can_convert": False,
                "kind": kind, "format": output_format, "status": "queued", "progress": None,
                "downloaded_bytes": 0, "total_bytes": None, "speed": None, "eta": None, "path": None, "error": None, "operation": None,
                "_identity": identity, "_settings": settings, "_inspected": inspected,
                "_selection": selection, "_attempt": None}
        self._tasks.append(task)
        return task

    @staticmethod
    def _compact_task(task):
        inspected = task["_inspected"]
        if task["status"] in TERMINAL and inspected and inspected.image_data:
            # Cookies and extractor metadata permit an explicit retry. Most
            # image bytes can be fetched again if the finished-file cache misses.
            # Original animation-height validation still needs verified bytes.
            preserve = inspected.animated and _height(task["_settings"], inspected, task["kind"]) is not None
            if not preserve:
                task["_inspected"] = replace(inspected, image_data=None)

    def enqueue(self, inspection_id, asset_id, kind, output_format):
        with self._lock:
            self._ensure_open()
            if self._inspection["status"] != "ready" or inspection_id != self._inspection["id"] or asset_id not in self._assets:
                raise DownloadError("This media check is out of date. Check the URL again before downloading.")
            asset = next(asset for asset in self._inspection["assets"] if asset["id"] == asset_id)
            compatible = next((item for item in asset["kinds"] if item["id"] == kind), None)
            if not compatible or output_format not in {option["id"] for option in compatible["options"]}:
                raise DownloadError("Choose an available media type and format from this media check.")
            inspected = self._assets[asset_id]
            self._add_task(inspected.url, inspected, kind, output_format, retry_url=self._inspection["url"])
            self._persist_queue()
            self._wake.set()
        return self.get_state()

    def enqueue_links(self, text):
        if not isinstance(text, str) or len(text) > MAX_BATCH_BYTES or len(text.encode("utf-8")) > MAX_BATCH_BYTES:
            raise DownloadError("Paste up to 1 MiB of URLs, one per line or separated by spaces.")
        entries = [entry for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#") for entry in line.split()]
        urls = validated_urls((f"item {index}", value) for index, value in enumerate(entries, 1))
        with self._lock:
            self._ensure_open()
            if len(self._tasks) + len(urls) > MAX_QUEUE_URLS:
                raise DownloadError("The queue supports up to 1,000 items. Remove finished items first.")
            for url in urls:
                self._add_task(url)
            self._persist_queue()
            self._wake.set()
        return self.get_state()

    def _run_queue(self):
        while True:
            self._wake.wait(0.5)
            self._wake.clear()
            with self._lock:
                if self._closed:
                    return
                task = next((item for item in self._tasks if item["status"] == "queued"), None)
                if task:
                    task["status"] = "converting" if task["action"] == "convert" else ("checking" if task["_inspected"] is None else "downloading")
                    task["_attempt"] = uuid4().hex
                    attempt = task["_attempt"]
                    if task["action"] == "convert":
                        task["operation"] = "preparing"
                    self._persist_queue()
            if task:
                self._run_task(task, attempt)
                self._wake.set()

    def _run_task(self, task, attempt):
        worker = None
        result = None
        work_dir = None

        def stop_worker():
            nonlocal worker, work_dir
            self._forget_worker(worker)
            worker = None
            if work_dir is not None:
                # This exact private directory was created by this attempt.
                # Remove it only after containment has stopped the worker.
                root = Path(task["_settings"]["output"]).resolve()
                if work_dir.parent == root and work_dir.name.startswith(".unidown-convert-") and not work_dir.is_symlink():
                    shutil.rmtree(work_dir, ignore_errors=True)
                work_dir = None

        try:
            worker = self._create_worker(task=task, attempt=attempt)
            if worker is None:
                return
            payload = {"action": task["action"], "url": task["url"], "inspected": task["_inspected"], "kind": task["kind"], "format": task["format"], "settings": task["_settings"]}
            if task["_inspected"] is None and task.get("_selection") is not None:
                payload["selection"] = task["_selection"]
            if task["action"] == "convert":
                output = Path(task["_settings"]["output"]).resolve()
                try:
                    output.mkdir(parents=True, exist_ok=True)
                    work_dir = Path(tempfile.mkdtemp(prefix=".unidown-convert-", dir=output)).resolve()
                except OSError as error:
                    raise DownloadError("The output folder could not be prepared. Choose another folder and retry.") from error
                payload.update(image=task["_image"], background=task["_background"], work_dir=str(work_dir))
            worker.begin(payload)
            for event, payload in worker.events():
                with self._lock:
                    if task["_attempt"] != attempt or task["status"] in ("cancelled", "cancelling") or self._closed:
                        break
                    if event == "checked":
                        task.update(title=_text(payload["title"], 300), kind=payload["kind"], status="downloading")
                    elif event == "progress":
                        status = payload["status"]
                        if status in ("preparing", "converting"):
                            progress, eta = payload.get("progress"), payload.get("eta")
                            progress = min(99.9, max(0, progress)) if isinstance(progress, (int, float)) and not isinstance(progress, bool) and math.isfinite(progress) else None
                            eta = eta if isinstance(eta, (int, float)) and not isinstance(eta, bool) and math.isfinite(eta) and eta >= 0 else None
                            operation = payload.get("operation")
                            if operation not in ("preparing", "encoding", "copying"):
                                operation = "preparing" if status == "preparing" else "encoding"
                            task.update(status="converting", progress=progress, speed=None, eta=eta, operation=operation)
                        elif status == "finished":
                            task.update(status="finalizing", progress=None, speed=None, eta=None, operation=None)
                        elif status in ("downloading", "resuming"):
                            total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
                            received = payload.get("downloaded_bytes") or 0
                            task.update(status="downloading", progress=min(99.9, received * 100 / total) if total else None,
                                        downloaded_bytes=received, total_bytes=total, speed=payload.get("speed"), eta=payload.get("eta"), operation=None)
                    elif event == "result":
                        result = payload
                    elif event == "error":
                        raise DownloadError(payload["message"], code=payload["code"])
            stop_worker()
            with self._lock:
                if task["_attempt"] != attempt:
                    return
                if task["status"] in ("cancelled", "cancelling") or self._closed:
                    task["status"] = "cancelled"
                elif result:
                    path = Path(result["path"]).resolve()
                    root = Path(task["_settings"]["output"]).resolve()
                    if path.parent != root or not path.is_file():
                        raise DownloadError("The media worker did not produce a completed output file.")
                    task.update(status="existing" if result["already_exists"] else "completed", progress=100,
                                path=str(path), title=_text(result["title"], 300), error=None, speed=None, eta=None,
                                can_convert=bool(result.get("can_convert")), operation=None)
                else:
                    message = "The file conversion stopped before completing. Retry to convert it again." if task["action"] == "convert" else "The media worker stopped before completing the download. Retry to continue a supported partial download."
                    raise DownloadError(message)
                self._compact_task(task)
        except BaseException as error:
            stop_worker()
            with self._lock:
                if task["_attempt"] != attempt:
                    return
                if task["status"] in ("cancelled", "cancelling") or self._closed:
                    task["status"] = "cancelled"
                else:
                    task.update(status="failed", error=safe_error(error), progress=None, speed=None, eta=None, operation=None)
                self._compact_task(task)
        finally:
            stop_worker()
            with self._lock:
                self._resume_time = 0
                self._persist_queue()

    def _find_task(self, identifier):
        task = next((task for task in self._tasks if task["id"] == identifier), None)
        if task is None:
            raise DownloadError("This queue item no longer exists.")
        return task

    def cancel_task(self, task_id):
        with self._lock:
            self._ensure_open()
            task = self._find_task(task_id)
            worker = None
            if task["status"] == "queued":
                task["status"] = "cancelled"
            elif task["status"] in ACTIVE:
                task["status"] = "cancelling"
                worker = self._download_worker
            else:
                return self.get_state()
        if worker:
            worker.close()
        with self._lock:
            task["_attempt"] = None
            task.update(status="cancelled", speed=None, eta=None, error=None, operation=None)
            self._compact_task(task)
            self._resume_time = 0
            self._persist_queue()
            self._wake.set()
        return self.get_state()

    def retry_task(self, task_id):
        with self._lock:
            self._ensure_open()
            task = self._find_task(task_id)
            if task["status"] not in TERMINAL:
                raise DownloadError("Wait for this item to stop before retrying it.")
            for other in self._tasks:
                if other is not task and other["_identity"] == task["_identity"] and other["status"] not in TERMINAL:
                    raise DownloadError("This item is already in the queue.")
            task.update(status="queued", progress=None, downloaded_bytes=0, total_bytes=None, speed=None, eta=None, path=None, error=None, can_convert=False, operation=None)
            self._persist_queue()
            self._wake.set()
        return self.get_state()

    def remove_finished(self):
        with self._lock:
            self._tasks = [task for task in self._tasks if task["status"] not in TERMINAL]
            self._persist_queue()
        return self.get_state()

    def save_settings(self, settings):
        if not isinstance(settings, dict) or set(settings) - {"output", "max_height", "max_size"}:
            raise DownloadError("Invalid download settings.")
        with self._lock:
            self._ensure_open()
            merged = {**self._settings, **settings}
            output = merged["output"]
            if not isinstance(output, str) or not output or len(output) > 8192 or any(ord(character) < 32 for character in output) or not Path(output).is_absolute():
                raise DownloadError("Choose an absolute output folder path.")
            validate_limits(merged["max_height"], merged["max_size"])
            merged["output"] = str(Path(output).expanduser().resolve())
            save_preferences(merged)
            changed = any(merged[field] != self._settings[field] for field in ("max_height", "max_size"))
            self._settings = merged
            self._notice = None
            self._resume_time = 0
        if changed:
            self.cancel_check()
        return self.get_state()

    def set_theme(self, theme):
        if theme not in THEMES:
            raise DownloadError("Choose a supported theme.")
        with self._lock:
            self._ensure_open()
            self._theme = theme
        save_theme(theme)
        return self.get_state()

    def theme(self):
        with self._lock:
            return self._theme

    def output_directory(self):
        with self._lock:
            return Path(self._settings["output"])

    def completed_path(self, task_id):
        with self._lock:
            task = self._find_task(task_id)
            if task["status"] not in ("completed", "existing") or not task["path"]:
                raise DownloadError("This item has no completed file yet.")
            path = Path(task["path"]).resolve()
            if path.parent != Path(task["_settings"]["output"]).resolve() or not path.is_file():
                raise DownloadError("The completed file could not be found.")
            return path

    def tool_status(self):
        return {"ffmpeg": find_ffmpeg() is not None, "javascript": find_js_runtime() is not None}

    def active_jobs(self):
        with self._lock:
            return self._inspection["status"] == "checking" or self._conversion["status"] == "checking" or any(task["status"] not in TERMINAL for task in self._tasks)

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._conversion_generation += 1
            workers = list(self._workers)
            self._assets = {}
            self._local_sources = {}
            self._local_images = {}
            for task in self._tasks:
                if task["status"] not in TERMINAL:
                    task.update(status="cancelled", error=RESTORED_MESSAGE, speed=None, eta=None, operation=None)
            self._persist_queue()
            self._wake.set()
        for worker in workers:
            worker.close()
        self._queue_thread.join(timeout=3)
        with self._lock:
            self._queue_store.close()
