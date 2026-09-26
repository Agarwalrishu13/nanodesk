"""Where nanoDesk keeps the list of folders you asked it to watch.

One file, in one folder in your home directory:

```
~/.nanodesk/
└── settings.json     the folders you added, remembered between runs
```

Delete that folder and the app forgets which folders you added — which also
means there is nowhere else anything is stored. No account, no server, and
nothing about your computer is ever sent anywhere.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

APP_DIR_NAME = ".nanodesk"

_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "folders": [],      # folders the person added themselves, in the order they added them
    "last_scan": 0.0,
}


def data_dir() -> Path:
    """The app's folder in the user's home directory."""
    override = os.environ.get("NANODESK_HOME")
    path = Path(override) if override else Path.home() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return data_dir() / "settings.json"


def _read_json(path: Path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, type(fallback)) else fallback
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, value) -> None:
    """Write JSON atomically so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(value, handle, indent=2)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def load_settings() -> dict:
    with _lock:
        stored = _read_json(settings_path(), {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in stored.items() if key in DEFAULT_SETTINGS})
    if not isinstance(merged["folders"], list):
        merged["folders"] = []
    merged["folders"] = [str(item) for item in merged["folders"] if isinstance(item, str) and item.strip()]
    return merged


def save_settings(patch: dict) -> dict:
    with _lock:
        current = dict(DEFAULT_SETTINGS)
        current.update({k: v for k, v in _read_json(settings_path(), {}).items() if k in DEFAULT_SETTINGS})
        for key, value in (patch or {}).items():
            if key in DEFAULT_SETTINGS:
                current[key] = value
        _write_json(settings_path(), current)
    return current


# ------------------------------------------------------------------ folders
def load_folders() -> list:
    """The folders to look in, in the order they were added."""
    return list(load_settings()["folders"])


def add_folder(path) -> list:
    """Remember a folder. Returns the new list, with the most recent first-unique."""
    folder = str(Path(path).expanduser())
    folders = load_folders()
    if folder not in folders:
        folders.append(folder)
    save_settings({"folders": folders})
    return folders


def forget_folder(path) -> list:
    folder = str(Path(path).expanduser())
    folders = [item for item in load_folders() if item != folder]
    save_settings({"folders": folders})
    return folders


def note_scan(when: float) -> float:
    save_settings({"last_scan": float(when)})
    return float(when)
