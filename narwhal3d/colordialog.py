"""Desktop colour picker + colour parsing. **No `bpy`, no rhino3dm.**

Used by the Layers panel's colour swatch. It follows the structure of
``filedialog.py``:
statuses ``OK`` / ``CANCEL`` / ``UNAVAILABLE``, a swappable backend
(:func:`set_backend`), and a hard rule that a dialog is NEVER opened under
``blender -b`` or with ``NARWHAL3D_NO_DIALOGS=1`` -- a modal colour picker
opened from a headless run either blocks the suite or silently cancels.

Every function returns a tuple:

* ``ask_color``    -> ``(status, (r, g, b) | None)`` -- 0..255 ints, or ``None``;
* ``parse_color``  -> ``((r, g, b) | None, error | None)`` -- exactly one of the two.

The supported desktop tools use these command lines:

* **kdialog** (KDE): ``kdialog --title T --getcolor --default "#rrggbb"``
  prints ``#rrggbb`` on answer, nothing on cancel;
* **zenity** (GNOME/GTK): ``zenity --color-selection --color="#rrggbb"``
  prints ``rgb(r,g,b)`` or ``rgba(r,g,b,a)`` on answer, nothing on cancel.

A backend is any object with ``ask(title, hex_color) -> (status, rgb|None)``.
The two desktop backends live here; tests swap in a fake.

``parse_color`` accepts, and only these: ``#rgb``, ``#rrggbb``,
``rgb(r,g,b)``, ``rgba(r,g,b,a)``, and a bare ``r,g,b`` / ``r g b``.
Everything else -- out-of-range values, wrong component count, non-numeric
components, garbage -- is refused with a specific error string, never guessed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

OK = "ok"
CANCEL = "cancel"
UNAVAILABLE = "unavailable"

_HEX_RE = re.compile(r"^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$")
_CSS_RE = re.compile(r"^\s*(rgba?)\s*\(([^()]*)\)\s*$", re.IGNORECASE)
_NUM_RE = re.compile(r"^[+-]?\d+$")


def to_hex(rgb):
    """``(r, g, b)`` 0..255 ints -> ``"#rrggbb"`` (lowercase).

    Raises ``ValueError`` on the wrong shape or an out-of-range component --
    a colour dialog should never hand back a colour this function cannot
    name.
    """
    try:
        seq = tuple(int(c) for c in rgb)
    except (TypeError, ValueError):
        raise ValueError(f"colour must be 3 numbers, got {rgb!r}") from None
    if len(seq) != 3 or any(c < 0 or c > 255 for c in seq):
        raise ValueError(f"colour components must be 0..255, got {rgb!r}")
    return "#%02x%02x%02x" % seq


def parse_color(text):
    """Parse a colour string -> ``((r, g, b) | None, error | None)``.

    Accepted: ``#rgb``, ``#rrggbb``, ``rgb(r,g,b)``, ``rgba(r,g,b,a)``,
    and a bare 3-number list (``r,g,b`` or ``r g b``). The ``rgba`` alpha is
    read and ignored. Refused, with a named error: out-of-range components,
    a component count that is not 3 (or 4 for ``rgba``), non-numeric
    components, and anything unrecognisable.
    """
    if text is None:
        return None, "empty colour"
    t = str(text).strip()
    if not t:
        return None, "empty colour"

    # --- hex ---------------------------------------------------------------
    if t.startswith("#"):
        h = t[1:]
        if _HEX_RE.match(h):
            if len(h) == 3:
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16)), None
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), None
        return None, f"bad hex colour {text!r} (want #rgb or #rrggbb)"

    # --- css functions: rgb(...) / rgba(...) --------------------------------
    m = _CSS_RE.match(t)
    if m:
        kind = m.group(1).lower()
        parts = [p for p in re.split(r"[,\s]+", m.group(2).strip()) if p]
        n = 4 if kind == "rgba" else 3
        if len(parts) != n:
            return None, f"{kind}() needs {n} numbers, got {len(parts)}"
        for p in parts:
            if not _NUM_RE.match(p):
                return None, f"bad number {p!r} in {kind}()"
            v = int(p)
            if v < 0 or v > 255:
                return None, f"{p} out of range 0..255 in {kind}()"
        return (int(parts[0]), int(parts[1]), int(parts[2])), None

    # --- bare numbers: r,g,b or r g b ---------------------------------------
    # Count the numeric parts BEFORE stripping whitespace: "10 20 30 40" is a
    # refusal (4 parts), not a misparse.
    parts = [p for p in re.split(r"[,\s]+", t) if p]
    if len(parts) != 3:
        return None, f"expected 3 numbers (r,g,b), got {len(parts)}"
    for p in parts:
        if not _NUM_RE.match(p):
            return None, f"bad number {p!r}"
        v = int(p)
        if v < 0 or v > 255:
            return None, f"{p} out of range 0..255"
    return (int(parts[0]), int(parts[1]), int(parts[2])), None


# ---------------------------------------------------------------------------
# Desktop backends
# ---------------------------------------------------------------------------

class _Kdialog:
    """KDE: ``kdialog --title T --getcolor --default "#rrggbb"`` -> ``#rrggbb``."""

    exe = "kdialog"

    def _run(self, args):
        r = subprocess.run([self.exe] + args, capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def ask(self, title, hex_color):
        rc, out = self._run(["--title", title, "--getcolor", "--default", hex_color])
        if rc != 0 or not out:
            return CANCEL, None
        rgb, err = parse_color(out)
        if rgb is None:
            return CANCEL, None
        return OK, rgb


class _Zenity:
    """GNOME: ``zenity --color-selection --color="#rrggbb"`` -> ``rgb(r,g,b)``."""

    exe = "zenity"

    def _run(self, args):
        r = subprocess.run([self.exe] + args, capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def ask(self, title, hex_color):
        rc, out = self._run(["--color-selection", "--title=" + title,
                             "--color=" + hex_color])
        if rc != 0 or not out:
            return CANCEL, None
        rgb, err = parse_color(out)
        if rgb is None:
            return CANCEL, None
        return OK, rgb


_backend = None
_backend_chosen = False


def set_backend(backend):
    """Use `backend` for every colour dialog (tests). ``None`` restores auto-detection."""
    global _backend, _backend_chosen
    _backend = backend
    _backend_chosen = backend is not None


def headless():
    """True when there is no user to answer a dialog.

    ``blender -b`` (every headless suite, every scripted run) has no UI. A colour
    picker opened from there either pops up on someone's desktop and BLOCKS the run,
    or exits at once as a "cancel". ``NARWHAL3D_NO_DIALOGS=1`` forces the same for any
    other scripted use. Same rule as ``filedialog.headless``.
    """
    if os.environ.get("NARWHAL3D_NO_DIALOGS"):
        return True
    bpy = sys.modules.get("bpy")
    try:
        return bool(bpy is not None and bpy.app.background)
    except Exception:  # noqa: BLE001
        return False


def backend():
    """The colour dialog tool in use, or None when there is none (or no one to ask)."""
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


def ask_color(title, initial_rgb):
    """Ask the desktop for a colour -> ``(status, (r, g, b) | None)``.

    ``initial_rgb`` is a 0..255 int triple; it is converted to ``#rrggbb`` for the
    desktop tool. The backend in use is :func:`backend`'s: an explicitly
    ``set_backend`` one (a deliberate test / scripting stub that never spawns a
    process), else -- only when there IS a user -- an auto-detected desktop tool.
    Under ``blender -b`` / ``NARWHAL3D_NO_DIALOGS=1`` with no backend set, :func:`backend`
    yields None and this answers ``(UNAVAILABLE, None)`` -- never a silent default,
    never a dialog popped onto a desktop or a run left BLOCKED.
    """
    b = backend()
    if b is None:
        return UNAVAILABLE, None
    try:
        default = to_hex(initial_rgb)
    except ValueError:
        default = "#000000"
    return b.ask(title, default)
