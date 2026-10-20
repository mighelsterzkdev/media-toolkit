"""Verify real desktop image workers without opening or controlling a window."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import multiprocessing
from multiprocessing import spawn
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image, ImageDraw

from universal_downloader.desktop_service import DesktopService
from universal_downloader.dependencies import find_ffmpeg, hidden_process_options
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
        transparent, photo, webp, animated, animated_gif, icon, phone, broken = [sources / name for name in (
            "Transparent.png", "Photo.jpg", "Graphic.webp", "Animation.png", "Animation.gif", "Icon.png", "Phone.heic", "Broken.png")]
        with Image.new("RGBA", (64, 48), (0, 0, 0, 0)) as image:
            ImageDraw.Draw(image).rectangle((16, 12, 47, 35), fill=(30, 120, 210, 255))
            image.save(transparent)
            image.save(webp, "WEBP", lossless=True, exact=True)
        with Image.new("RGBA", (32, 32), (30, 120, 210, 255)) as image:
            ImageDraw.Draw(image).rectangle((8, 8, 23, 23), fill=(220, 80, 20, 255))
            image.save(icon)
        with Image.new("RGB", (48, 64), "red") as image:
            image.save(photo)
            with Image.new("RGB", image.size, "blue") as second:
                image.save(animated, save_all=True, append_images=[second], duration=100, loop=0)
                image.save(animated_gif, save_all=True, append_images=[second], duration=100, loop=0)
        phone.write_bytes((Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "local-sample.heic").read_bytes())
        tools = find_ffmpeg()
        video = None
        if tools is not None:
            suffix = ".exe" if (tools / "ffmpeg.exe").is_file() else ""
            ffmpeg, ffprobe = tools / f"ffmpeg{suffix}", tools / f"ffprobe{suffix}"
            video = sources / "Clip.bin"
            subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                            "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=10:d=0.3", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-f", "mp4", str(video)],
                           capture_output=True, timeout=30, check=True, **hidden_process_options())
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
            assert files[broken.name]["error"]
            for source in (animated, animated_gif):
                assert files[source.name]["error"] is None and files[source.name]["media_type"] == "animation", files[source.name]
                assert files[source.name]["animated"] and files[source.name]["pages"] == 2, files[source.name]
            assert files[phone.name]["error"] is None and files[phone.name]["format"] == "heic", files[phone.name]
            if video is not None:
                assert files[video.name]["error"] is None and files[video.name]["media_type"] == "video", files[video.name]
                assert files[video.name]["format"] == "mp4", files[video.name]
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
            # Exercise each expanded static encoder and decoder inside the
            # worker, including native codecs collected into the frozen build.
            expected_formats = {"avif": "AVIF", "bmp": "BMP", "tiff": "TIFF", "ico": "ICO", "gif": "GIF", "jp2": "JPEG2000", "tga": "TGA"}
            icon_id = files[icon.name]["id"]
            assert set(expected_formats).issubset(files[icon.name]["targets"]), files[icon.name]
            expanded = []
            for target, expected_format in expected_formats.items():
                require(api.enqueue_conversions([icon_id], target))
                state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
                task = state["tasks"][-1]
                assert task["status"] == "completed", task
                with Image.open(task["path"]) as image:
                    image.load()
                    assert image.format == expected_format and image.size == (32, 32), (target, image.format, image.size)
                expanded.append(Path(task["path"]))
            require(api.enqueue_conversions([files[phone.name]["id"]], "png"))
            state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
            phone_task = state["tasks"][-1]
            assert phone_task["status"] == "completed", phone_task
            with Image.open(phone_task["path"]) as image:
                image.load()
                assert image.format == "PNG" and image.size == (files[phone.name]["width"], files[phone.name]["height"])
            require(api.enqueue_conversions([files[animated.name]["id"]], "gif"))
            state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
            animation_task = state["tasks"][-1]
            assert animation_task["status"] == "completed", animation_task
            with Image.open(animation_task["path"]) as image:
                assert image.format == "GIF" and image.n_frames == 2, animation_task
                for frame in range(image.n_frames):
                    image.seek(frame)
                    assert image.info["duration"] == 100
                    assert image.convert("RGB").getpixel((0, 0)) == ((255, 0, 0) if frame == 0 else (0, 0, 255))
            require(api.clear_conversion_files())
            service.add_conversion_files([str(path) for path in expanded])
            state = wait_for(service, lambda value: value["conversion"]["status"] != "checking")
            rechecked = state["conversion"]["files"]
            assert len(rechecked) == len(expanded)
            assert all(file["error"] is None and file["media_type"] == "image" and "png" in file["targets"] for file in rechecked), rechecked
            require(api.enqueue_conversions([file["id"] for file in rechecked], "png"))
            state = wait_for(service, lambda value: all(task["status"] in ("completed", "failed") for task in value["tasks"]))
            assert all(task["status"] == "completed" for task in state["tasks"]), state
            for checked, task in zip(rechecked, state["tasks"][-len(rechecked):]):
                with Image.open(task["path"]) as image, Image.open(icon) as original:
                    image.load()
                    assert image.format == "PNG" and image.size == (32, 32)
                    actual, expected = image.convert("RGB").tobytes(), original.convert("RGB").tobytes()
                    if checked["format"] == "avif":
                        assert sum(abs(left - right) for left, right in zip(actual, expected)) / len(expected) < 10
                    else:
                        assert actual == expected, checked["format"]
            if video is not None:
                require(api.clear_conversion_files())
                service.add_conversion_files([str(video)])
                state = wait_for(service, lambda value: value["conversion"]["status"] != "checking")
                selected_video = state["conversion"]["files"][0]
                assert selected_video["error"] is None and {"mp3", "webm"}.issubset(selected_video["targets"]), selected_video
                api._tools["status"] = "ready"
                for target in ("mp3", "webm"):
                    require(api.enqueue_conversions([selected_video["id"]], target))
                    state = wait_for(service, lambda value: value["tasks"][-1]["status"] in ("completed", "failed"))
                    task = state["tasks"][-1]
                    assert task["status"] == "completed", task
                    result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", task["path"]],
                                            capture_output=True, text=True, timeout=20, check=True, **hidden_process_options())
                    streams = json.loads(result.stdout)["streams"]
                    codecs = {stream["codec_type"]: stream["codec_name"] for stream in streams}
                    assert codecs == ({"audio": "mp3"} if target == "mp3" else {"video": "vp9", "audio": "opus"}), codecs
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
            av_result = "MP4 audio extraction and WebM conversion, " if video is not None else ""
            print("Desktop workers passed: content inspection, AVIF/BMP/TIFF/ICO/GIF/JP2/TGA roundtrips, HEIC decoding, animation preservation, " + av_result + "atomic batch validation, JPEG background, lossless WebP, cancel/retry, completed-file shortcut, source preservation and private workspace cleanup.", flush=True)
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
