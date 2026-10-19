"""Verify real desktop image workers without opening or controlling a window."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import multiprocessing
from multiprocessing import spawn
import os
from pathlib import Path
import tempfile
import time

from PIL import Image, ImageDraw

from universal_downloader.desktop_service import DesktopService
from universal_downloader.gui import DesktopApi


@contextmanager
def worker_executable(executable: Path | None):
    if executable is None:
        yield
        return
    executable = executable.resolve(strict=True)
    previous_executable = spawn.get_executable()
    previous_command, previous_preparation = spawn.get_command_line, spawn.get_preparation_data
    multiprocessing.set_executable(str(executable))
    # The windowed bootloader accepts the frozen multiprocessing protocol. Do
    # not replace its embedded module paths with this source parent's sys.path.
    spawn.get_command_line = lambda **values: [str(executable), "--multiprocessing-fork", *[f"{key}={value!r}" for key, value in values.items()]]

    def preparation(name):
        data = previous_preparation(name)
        for field in ("sys_path", "init_main_from_path", "init_main_from_name"):
            data.pop(field, None)
        return data

    spawn.get_preparation_data = preparation
    try:
        yield
    finally:
        spawn.get_command_line, spawn.get_preparation_data = previous_command, previous_preparation
        multiprocessing.set_executable(os.fsdecode(previous_executable))


def wait_for(service, predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = service.get_state()
        if predicate(state):
            return state
        time.sleep(0.05)
    raise AssertionError(service.get_state())


def require(response):
    assert response["ok"], response
    return response["state"]


def verify(executable: Path | None = None):
    previous_state = os.environ.get("UNIDOWN_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="media-toolkit-desktop-check-") as temporary, worker_executable(executable):
        root = Path(temporary)
        os.environ["UNIDOWN_DATA_DIR"] = str(root / "state")
        sources, output = root / "source", root / "output"
        sources.mkdir()
        transparent, photo, webp, animated, broken = [sources / name for name in ("Transparent.png", "Photo.jpg", "Graphic.webp", "Animation.png", "Broken.png")]
        with Image.new("RGBA", (64, 48), (0, 0, 0, 0)) as image:
            ImageDraw.Draw(image).rectangle((16, 12, 47, 35), fill=(30, 120, 210, 255))
            image.save(transparent)
            image.save(webp, "WEBP", lossless=True, exact=True)
        with Image.new("RGB", (48, 64), "red") as image:
            image.save(photo)
            with Image.new("RGB", image.size, "blue") as second:
                image.save(animated, save_all=True, append_images=[second], duration=100, loop=0)
        broken.write_bytes(b"not an image")
        originals = {file: sha256(file.read_bytes()).hexdigest() for file in sources.iterdir()}
        service = DesktopService(output)
        api = DesktopApi(service)
        api._tools["status"] = "missing"
        try:
            service.add_conversion_files([str(file) for file in sources.iterdir()])
            state = wait_for(service, lambda value: value["conversion"]["status"] != "checking")
            assert state["conversion"]["status"] == "ready", state
            files = {file["name"]: file for file in state["conversion"]["files"]}
            assert files[animated.name]["error"] and files[broken.name]["error"]
            assert files[transparent.name]["transparent"]
            assert all("source" not in file and "signature" not in file for file in files.values())
            assert not api.enqueue_conversions([files[transparent.name]["id"], files[broken.name]["id"]], "jpg")["ok"]
            assert not service.get_state()["tasks"]
            ids = [files[file.name]["id"] for file in (transparent, webp)]
            require(api.enqueue_conversions(ids, "jpg", "#000000"))
            state = wait_for(service, lambda value: len(value["tasks"]) == 2 and all(task["status"] in ("completed", "failed") for task in value["tasks"]))
            assert all(task["status"] == "completed" for task in state["tasks"]), state
            for task in state["tasks"]:
                with Image.open(task["path"]) as image:
                    assert image.format == "JPEG" and image.size == (64, 48)
                    assert max(image.convert("RGB").getpixel((0, 0))) < 5
            require(api.enqueue_conversions([ids[0]], "webp"))
            task = service.get_state()["tasks"][-1]
            require(api.cancel_task(task["id"]))
            assert service.get_state()["tasks"][-1]["status"] == "cancelled"
            require(api.retry_task(task["id"]))
            state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
            converted = state["tasks"][-1]
            assert converted["status"] == "completed", converted
            with Image.open(converted["path"]) as actual, Image.open(transparent) as original:
                assert actual.format == "WEBP"
                assert actual.convert("RGBA").tobytes() == original.convert("RGBA").tobytes()
            # The recorded-file shortcut replaces the previous selection and
            # uses its native format to offer the next compatible conversion.
            require(api.prepare_conversion(state["tasks"][0]["id"]))
            state = wait_for(service, lambda value: value["conversion"]["status"] != "checking")
            assert len(state["conversion"]["files"]) == 1
            selected = state["conversion"]["files"][0]
            assert selected["format"] == "jpg" and "png" in selected["targets"]
            require(api.enqueue_conversions([selected["id"]], "png"))
            state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
            assert state["tasks"][-1]["status"] == "completed", state
            with Image.open(state["tasks"][-1]["path"]) as image:
                assert image.format == "PNG"
            assert all(sha256(file.read_bytes()).hexdigest() == digest for file, digest in originals.items())
            deadline = time.monotonic() + 10
            while list(output.glob(".unidown-convert-*")) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not list(output.glob(".unidown-convert-*"))
            completed = [Path(task["path"]) for task in state["tasks"]]
            require(api.clear_conversion_files())
            require(api.remove_finished())
            assert all(path.is_file() for path in completed)
            print("Desktop workers passed: local inspection, compatible formats, atomic batch validation, JPEG background, lossless WebP, cancel/retry, completed-file shortcut, source preservation and private workspace cleanup.", flush=True)
        finally:
            service.shutdown()
            if previous_state is None:
                os.environ.pop("UNIDOWN_DATA_DIR", None)
            else:
                os.environ["UNIDOWN_DATA_DIR"] = previous_state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", nargs="?", type=Path, help="built media-toolkit.exe; omit to verify source workers")
    verify(parser.parse_args().executable)
