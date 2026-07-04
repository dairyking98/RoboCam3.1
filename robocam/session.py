"""
Session manager — persists UI state across restarts.

Saves to ~/.local/share/RoboCam3/session.json (XDG on Linux,
AppData on Windows, ~/Library/Application Support on macOS).
"""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from typing import Any, Dict


DEFAULT_SESSION: Dict[str, Any] = {
    "experiment": {
        "name": "my_experiment",
        "mode": "Image",
        "dwell": 1.0,
        "image_format": "jpg",
        "duration": 5.0,
        "use_laser": False,
        "laser_on": 1.0,
        "post": 2.0,
        "cal_file": "",
    },
    "calibration": {
        "cols": 12,
        "rows": 8,
        "pattern": "Raster",
        "cal_name": "calibration",
        "last_cal_path": "",
        "exp_ms": 20,
        "gain": 100,
        "hqi_enabled": False,
        "usb_bandwidth": 100,
        "offset": 0,
        "sensor_mode_index": 0,
        "step": "1.0",
        "qc_format": "jpg",
        "qc_duration": 5.0,
    },
}


def _data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    path = os.path.join(base, "RoboCam3")
    os.makedirs(path, exist_ok=True)
    return path


class SessionManager:
    def __init__(self):
        self._path = os.path.join(_data_dir(), "session.json")
        self._data: Dict[str, Any] = self._load()

    def get(self, section: str) -> Dict[str, Any]:
        """Return a copy of a session section, falling back to defaults."""
        defaults = DEFAULT_SESSION.get(section, {})
        stored = self._data.get(section, {})
        merged = deepcopy(defaults)
        merged.update(stored)
        return merged

    def update(self, section: str, values: Dict[str, Any]):
        """Merge values into a section (does not write to disk yet)."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section].update(values)

    def save(self):
        """Flush the in-memory session to disk."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            print(f"[Session] Could not save {self._path}: {e}")

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return deepcopy(DEFAULT_SESSION)


session_manager = SessionManager()
