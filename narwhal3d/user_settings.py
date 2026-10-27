"""Persistent user preferences. **No `bpy`, no rhino3dm.**

A small, typed, JSON-backed store for the application's user preferences. The
schema is the ``defaults`` dict passed to the constructor: each key's **type**
is inferred from its default value and validated by ``load``. For compatibility,
``set`` remains permissive about values (but refuses unknown keys).

The contract:

* ``get(key)`` / ``set(key, value)`` -- a key not in ``defaults`` is an
  **unknown key** and raises ``KeyError`` (the only refusal ``set`` makes);
* ``save`` / ``load`` -- JSON, written **atomically** (a temp file in the same
  directory, fsync'd, then ``os.replace``d over the store) so a crash mid-write
  never leaves a half-written store;
* a value of the **wrong type** in the file falls back to the default for that
  key (``"20"`` where the default is ``20`` is wrong, not ``20``);
* a **missing or corrupt** store file loads as all-defaults -- it never raises,
  so a bad settings file cannot stop the app at startup.

The default values below are application choices. In particular, the
``autosave_minutes`` interval is not documented in the reference CAD application's help.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile

#: The initial preference schema. A new preference is added as a new key here. Types are inferred from these values (int -> int,
#: str -> str), and load() enforces that contract.
DEFAULTS = {
    "autosave_minutes": 20,        # the reference CAD application's default interval is not documented
    "recent_limit": 10,
    "default_print_size": "Letter",
}


def settings_path():
    """Shared application settings path; resolve the environment at call time."""
    override = os.environ.get("NARWHAL3D_SETTINGS_FILE")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return base / "Narwhal3D" / "preferences.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Narwhal3D" / "preferences.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "narwhal3d" / "preferences.json"


def _read_json(path, *, for_write=False):
    """Read safely at startup; refuse to overwrite a file we cannot read."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        if for_write and not isinstance(exc, FileNotFoundError):
            raise
        return {}
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path, data):
    """Replace a JSON file only after serialization and fsync succeed.

    Unique sibling temps avoid collisions between writers; failures remove the temp
    and leave the original untouched. This is atomic replacement, not an interprocess
    read/modify/write lock: simultaneous writers to the same key remain last-wins.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=path.name + ".tmp.",
                                         delete=False) as fh:
            tmp = fh.name
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


class UserSettings:
    """A bounded, typed, newest-wins store of user preferences."""

    def __init__(self, path, defaults):
        self.path = path
        self._defaults = deepcopy(defaults)
        # start at the defaults; load() refreshes from the file
        self._values = deepcopy(self._defaults)

    # -- access ------------------------------------------------------------
    def _check_key(self, key):
        if key not in self._defaults:
            raise KeyError(key)

    def get(self, key):
        """The current value for ``key``; ``KeyError`` for an unknown key."""
        self._check_key(key)
        return self._values[key]

    def set(self, key, value):
        """Store ``value`` for ``key``. ``KeyError`` for an unknown key.

        A value of the wrong type is accepted here and self-heals on the next
        ``load`` (where a wrong-type value in the file falls back to the default
        for that key) -- the spec's type contract lives on load, not set.
        """
        self._check_key(key)
        self._values[key] = value

    # -- persistence -------------------------------------------------------
    def save(self):
        """Atomically save this schema, preserving other owners' fresh fields.

        As before, all schema keys are saved and set() accepts wrong types. Unknown
        disk keys stay private but survive saves, even when added after load().
        """
        data = _read_json(self.path, for_write=True)
        data.update(self._values)
        _atomic_write(self.path, data)

    def load(self):
        """Read the store; wrong-type values fall back to the default for that key.

        A missing file, a file that is not valid JSON, or a JSON value that is
        not an object, all load as **all defaults** and return -- never raise,
        so a bad settings file cannot stop the app at startup.
        """
        data = _read_json(self.path)
        values = {}
        for key, default in self._defaults.items():
            if key in data and type(data[key]) is type(default):
                values[key] = data[key]
            else:
                values[key] = deepcopy(default)
        self._values = values
