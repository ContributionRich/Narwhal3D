"""Persistent user preferences. **No `bpy`, no rhino3dm.**

A small, typed, JSON-backed store for the application's user preferences. The
schema is the ``defaults`` dict passed to the constructor: each key's **type**
is inferred from its default value, and that type is the contract for both
``set`` and ``load``.

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

import json
import os

#: The initial preference schema. A new preference is added as a new key here. Types are inferred from these values (int -> int,
#: str -> str), and that is the type contract for set() and load().
DEFAULTS = {
    "autosave_minutes": 20,        # the reference CAD application's default interval is not documented
    "recent_limit": 10,
    "default_print_size": "Letter",
}


class UserSettings:
    """A bounded, typed, newest-wins store of user preferences."""

    def __init__(self, path, defaults):
        self.path = path
        self._defaults = dict(defaults)
        # start at the defaults; load() refreshes from the file
        self._values = dict(self._defaults)

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
        """Write the store atomically (temp file in the same dir + os.replace)."""
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        tmp = f"{self.path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._values, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def load(self):
        """Read the store; wrong-type values fall back to the default for that key.

        A missing file, a file that is not valid JSON, or a JSON value that is
        not an object, all load as **all defaults** and return -- never raise,
        so a bad settings file cannot stop the app at startup.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self._values = dict(self._defaults)
            return
        if not isinstance(data, dict):
            self._values = dict(self._defaults)
            return
        values = {}
        for key, default in self._defaults.items():
            if key in data and type(data[key]) is type(default):
                values[key] = data[key]
            else:
                values[key] = default
        self._values = values
