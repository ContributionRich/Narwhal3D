"""Native file dialogs and the "save changes?" question. **No `bpy`, no rhino3dm.**

the reference CAD application's Save / Save As / Open / New use the operating system's own dialogs. On
Linux there is no single native API, so this asks the desktop's own dialog tool:
``kdialog`` for KDE first, then ``zenity`` for GNOME/GTK. Either gives the user the same dialog
every other application on the desktop shows. If neither exists, every function
answers ``UNAVAILABLE`` and the commands fall back to a command-line prompt for the
file name -- never to a silent default path.

Every function returns ``(status, value)``:

* ``OK``          -- the user answered; `value` is the path / "yes" / "no";
* ``CANCEL``      -- the user dismissed the dialog;
* ``UNAVAILABLE`` -- no dialog tool exists.

The backend is swappable (:func:`set_backend`) so the commands can be tested without a
desktop. A backend is any object with ``save(title, start, filters)``,
``open(title, start, filters)`` and ``ask(title, text)`` methods returning the tuples
above.

The calls BLOCK, as a modal dialog does in the reference CAD application: the viewport waits until the user
answers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

OK = "ok"
CANCEL = "cancel"
UNAVAILABLE = "unavailable"

#: (description, glob) -- what the reference CAD application's own dialog lists for a 3dm file
NARWHAL_3DM = (("the reference CAD application 3D Models", "*.3dm"),)


class _Kdialog:
    """KDE's dialog tool. Exit 0 = answered, 1 = cancel/No, 2 = Cancel (3-button)."""

    exe = "kdialog"

    @staticmethod
    def _filter(filters):
        # kdialog: "*.3dm|the reference CAD application 3D Models"; several separated by newlines
        return "\n".join(f"{glob}|{desc} ({glob})" for desc, glob in filters)

    def _run(self, args):
        r = subprocess.run([self.exe] + args, capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def save(self, title, start, filters):
        rc, out = self._run(["--title", title, "--getsavefilename", start,
                             self._filter(filters)])
        return (OK, out) if rc == 0 and out else (CANCEL, None)

    def open(self, title, start, filters):
        rc, out = self._run(["--title", title, "--getopenfilename", start,
                             self._filter(filters)])
        return (OK, out) if rc == 0 and out else (CANCEL, None)

    def ask(self, title, text):
        rc, _out = self._run(["--title", title, "--warningyesnocancel", text])
        return {0: (OK, "yes"), 1: (OK, "no")}.get(rc, (CANCEL, None))


class _Zenity:
    """GNOME's dialog tool."""

    exe = "zenity"

    @staticmethod
    def _filter(filters):
        out = []
        for desc, glob in filters:
            out += ["--file-filter", f"{desc} | {glob}"]
        return out

    def _run(self, args):
        r = subprocess.run([self.exe] + args, capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def save(self, title, start, filters):
        rc, out = self._run(["--file-selection", "--save", "--confirm-overwrite",
                             f"--title={title}", f"--filename={start}"]
                            + self._filter(filters))
        return (OK, out) if rc == 0 and out else (CANCEL, None)

    def open(self, title, start, filters):
        rc, out = self._run(["--file-selection", f"--title={title}",
                             f"--filename={start}"] + self._filter(filters))
        return (OK, out) if rc == 0 and out else (CANCEL, None)

    def ask(self, title, text):
        rc, out = self._run(["--question", f"--title={title}", f"--text={text}",
                             "--ok-label=Yes", "--cancel-label=No",
                             "--extra-button=Cancel"])
        if rc == 0:
            return OK, "yes"
        if out == "Cancel":
            return CANCEL, None
        return OK, "no"


_backend = None
_backend_chosen = False


def set_backend(backend):
    """Use `backend` for every dialog (tests). ``None`` restores auto-detection."""
    global _backend, _backend_chosen
    _backend = backend
    _backend_chosen = backend is not None


def headless():
    """True when there is no user to answer a dialog.

    `blender -b` (every headless suite, every scripted run) has no UI. A dialog
    opened from there either pops up on someone's desktop and blocks the run, or
    exits immediately as a cancellation, silently turning a scripted `Open` into a no-op. `NARWHAL3D_NO_DIALOGS=1` forces the same for any
    other scripted use.
    """
    if os.environ.get("NARWHAL3D_NO_DIALOGS"):
        return True
    bpy = sys.modules.get("bpy")
    try:
        return bool(bpy is not None and bpy.app.background)
    except Exception:  # noqa: BLE001
        return False


def backend():
    """The dialog tool in use, or None when there is none (or no one to ask)."""
    global _backend, _backend_chosen
    if _backend_chosen:
        return _backend
    if headless():
        # not cached: the same process may later have a window
        return None
    _backend_chosen = True
    for cls in (_Kdialog, _Zenity):
        if shutil.which(cls.exe):
            _backend = cls()
            break
    return _backend


def ask_save_path(title, start, filters=NARWHAL_3DM):
    b = backend()
    return b.save(title, start, filters) if b is not None else (UNAVAILABLE, None)


def ask_open_path(title, start, filters=NARWHAL_3DM):
    b = backend()
    return b.open(title, start, filters) if b is not None else (UNAVAILABLE, None)


def ask_yes_no_cancel(title, text):
    b = backend()
    return b.ask(title, text) if b is not None else (UNAVAILABLE, None)


def start_dir(path=None):
    """Where a dialog should open: the current file's folder, else the home folder."""
    if path:
        d = os.path.dirname(os.path.abspath(path))
        if os.path.isdir(d):
            return d
    return os.path.expanduser("~")
