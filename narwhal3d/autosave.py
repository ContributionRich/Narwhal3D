"""Autosave + recovery files. **No `bpy`, no rhino3dm.**

Autosave supports safe exit and recovery. The behaviour follows the reference CAD application's options
help (options/files.htm):

    "AutoSave: Save every ___ minutes ... a copy of the model you are working on
     automatically saves to the Autosave file. ... If a model has been saved,
     the Autosave file name will include the model name."

So a *saved* model gets ``<folder>/<modelname>_autosave.3dm`` (the model name is
the file name without ``.3dm``), and an *unsaved* one gets a timestamped name
``Untitled_autosave_<YYYYmmdd-HHMMSS>.3dm`` so successive autosaves never
overwrite each other. All four functions here are pure and take their inputs
(``now``, ``folder``) as parameters, so they test under ``tempfile.mkdtemp()``
and never touch the real home directory.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

#: the extension recovery files (and the model itself) carry
EXT = ".3dm"
_UNTITLED = "Untitled"


def _model_name(doc_path):
    """The model name from a saved path: the file name without its extension.

    ``/home/u/MyModel.3dm`` -> ``MyModel``; ``.3dm`` and any other extension
    are both stripped (``splitext`` takes the last dot). A name with no dot at
    all (``/u/Model``) keeps its whole base. Empty -> ``Untitled``.
    """
    base = os.path.basename(doc_path)
    base = os.path.splitext(base)[0]
    return base or _UNTITLED


def autosave_path(folder, doc_path_or_None, now=None):
    """Where the autosave copy goes.

    * a saved model:  ``<folder>/<modelname>_autosave.3dm``
    * an unsaved one: ``<folder>/Untitled_autosave_<YYYYmmdd-HHMMSS>.3dm``

    ``now`` (a ``datetime``) is used only for the unsaved timestamp so callers
    can test a specific instant; it defaults to the current time.
    """
    if now is None:
        now = datetime.now()
    folder = folder or os.curdir
    if doc_path_or_None:
        name = f"{_model_name(doc_path_or_None)}_autosave{EXT}"
    else:
        stamp = now.strftime("%Y%m%d-%H%M%S")
        name = f"{_UNTITLED}_autosave_{stamp}{EXT}"
    return os.path.join(folder, name)


def due(now, last_save_time, minutes):
    """Is an autosave due now?

    True when at least ``minutes`` have elapsed since ``last_save_time``.
    ``minutes <= 0`` means **never** (autosave is switched off in the reference CAD application's
    options), so it is always False -- even a very old ``last_save_time``.
    A ``None`` / equal instant is treated as "just saved" (False).
    """
    if minutes is None or minutes <= 0:
        return False
    if last_save_time is None:
        # never saved -> first autosave is due immediately
        return True
    elapsed = (now - last_save_time).total_seconds()
    return elapsed >= minutes * 60.0


def recovery_candidates(folder):
    """``[(path, mtime), ...]`` for every ``*.3dm`` in ``folder``, newest first.

    Other files (and sub-directories) are ignored. A missing folder yields an
    empty list rather than raising -- recovery must never crash on startup.
    """
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for entry in os.listdir(folder):
        full = os.path.join(folder, entry)
        if not os.path.isfile(full):
            continue
        if not entry.lower().endswith(EXT):
            continue
        out.append((full, os.path.getmtime(full)))
    out.sort(key=lambda pm: pm[1], reverse=True)
    return out


def prune(folder, keep):
    """Keep the newest ``keep`` ``*.3dm`` files, delete the rest; return what was removed.

    ``keep`` < 1 removes **every** ``*.3dm`` (not a silent no-op -- the caller
    asked for zero to keep). Uses :func:`recovery_candidates` for the ordering,
    so a missing folder is a clean empty result.
    """
    if keep is None or keep < 1:
        keep = 0
    candidates = recovery_candidates(folder)
    removed = candidates[keep:]
    for path, _mtime in removed:
        try:
            os.remove(path)
        except OSError:
            continue  # gone already; do not abort the rest
    return [path for path, _ in removed]
