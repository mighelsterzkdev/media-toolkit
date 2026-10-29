"""Persist only explicit output and limit preferences, never media or credentials."""

import json
import os
from pathlib import Path
import tempfile

from .errors import DownloadError

MAX_PREFERENCES_BYTES = 16_384
FIELDS = frozenset(("output", "max_height", "max_size"))


def preferences_path() -> Path:
    if configured := os.environ.get("UNIDOWN_DATA_DIR"):
        directory = Path(configured).expanduser()
    elif os.name == "nt":
        directory = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "UniversalDownloader"
    else:
        directory = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "universal-downloader"
    return directory / "preferences.json"


def _validated(values: dict) -> dict:
    if not isinstance(values, dict) or set(values) - FIELDS:
        raise ValueError("unknown preference fields")
    output = values.get("output")
    if output is not None and (not isinstance(output, str) or not output or len(output) > 8192 or any(ord(character) < 32 for character in output) or not Path(output).is_absolute()):
        raise ValueError("invalid output directory")
    for name in ("max_height", "max_size"):
        value = values.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise ValueError("invalid download limit")
    return {name: values[name] for name in FIELDS if name in values}


def load_preferences() -> dict:
    try:
        with preferences_path().open("rb") as source:
            data = source.read(MAX_PREFERENCES_BYTES + 1)
        if len(data) > MAX_PREFERENCES_BYTES:
            raise ValueError("preference file is too large")
        payload = json.loads(data.decode("utf-8"))
        version = payload.pop("version", None) if isinstance(payload, dict) else None
        if type(version) is not int or version != 1:
            raise ValueError("unsupported preference version")
        return _validated(payload)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as error:
        raise DownloadError("Saved preferences could not be read. Use --reset-preferences or --no-preferences.") from error


def save_preferences(values: dict) -> Path:
    temporary = None
    try:
        payload = {"version": 1, **_validated(values)}
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(data) > MAX_PREFERENCES_BYTES:
            raise ValueError("preference file is too large")
        destination = preferences_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".preferences-", suffix=".tmp", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        return destination
    except (OSError, ValueError) as error:
        raise DownloadError("Could not save preferences. Downloads can continue with --no-preferences.") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


THEMES = ("system", "dark", "light")


def _appearance_path() -> Path:
    # Kept apart from preferences.json so CLI preference saves never drop it.
    return preferences_path().with_name("appearance.json")


def load_theme() -> str:
    try:
        with _appearance_path().open("rb") as source:
            data = source.read(4097)
        value = json.loads(data.decode("utf-8")) if len(data) <= 4096 else None
        theme = value.get("theme") if isinstance(value, dict) else None
        return theme if theme in THEMES else "system"
    except (OSError, ValueError, UnicodeError):
        return "system"


def save_theme(theme: str) -> None:
    if theme not in THEMES:
        raise DownloadError("Choose a supported theme.")
    destination = _appearance_path()
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".appearance-", suffix=".tmp", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write((json.dumps({"version": 1, "theme": theme}) + "\n").encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except OSError as error:
        raise DownloadError("The theme changed, but it could not be saved.") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def reset_preferences() -> Path:
    path = preferences_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise DownloadError("Could not reset saved preferences.") from error
    return path
