"""File > Recent (MRU list). **No `bpy`, no rhino3dm.**

A most-recently-used list of model files for the File > Recent menu.
Pure and testable: the store path is passed in (tests use ``tempfile.mkdtemp()``),
the store is JSON, and it is written **atomically** -- a temp file in the same
directory is filled and fsynced, then ``os.replace``d over the store -- so a
crash mid-write never leaves a half-written store that the next startup would
read.

The contract:

* ``add(path)`` moves a file to the front and de-duplicates by **absolute**
  path, so ``a/../a/x.3dm`` and ``a/x.3dm`` are the same entry;
* ``items(exists=os.path.exists)`` returns the list newest first, leaving out
  files for which ``exists(path)`` is False -- but the **store** still keeps
  them (a missing entry is a drive that came back, not a file to forget);
* ``save`` / ``load`` are the JSON round-trip; a **missing or corrupt** store
  file loads as empty (the app must never crash at startup because of a bad
  Recent list);
* the list never grows past ``limit`` (default 10): the oldest entry drops off
  as new ones arrive.
"""
from __future__ import annotations

import json
import os


class RecentFiles:
    """A bounded, de-duplicated, newest-first list of model files."""

    def __init__(self, store_path, limit=10):
        self.store_path = store_path
        self.limit = int(limit)
        self._items = []          # absolute paths, newest first

    # -- list API ----------------------------------------------------------
    def add(self, path):
        """Move `path` to the front, de-duplicating by absolute path.

        Returns the absolute path that was stored.
        """
        ap = os.path.abspath(path)
        self._items = [p for p in self._items if p != ap]
        self._items.insert(0, ap)
        self._items = self._items[: self.limit]
        return ap

    def items(self, exists=os.path.exists):
        """Newest first, entries for which ``exists(path)`` is False left out.

        Pass ``exists=lambda p: True`` to see the full store (missing files
        included) -- they are kept for when a drive comes back.
        """
        return [p for p in self._items if exists(p)]

    # -- persistence -------------------------------------------------------
    def save(self):
        """Write the store atomically (temp file in the same dir + os.replace)."""
        d = os.path.dirname(os.path.abspath(self.store_path))
        os.makedirs(d, exist_ok=True)
        tmp = f"{self.store_path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "items": self._items}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.store_path)

    def load(self):
        """Read the store into ``self``.

        A missing file, or a file that is not valid Recent-list JSON, loads as
        empty and returns ``[]`` -- it never raises, so a bad store can't stop
        the app at startup.
        """
        try:
            with open(self.store_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self._items = []
            return []
        raw = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            self._items = []
            return []
        self._items = [p for p in raw if isinstance(p, str) and p]
        self._items = self._items[: self.limit]
        return self._items
