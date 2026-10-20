"""Surveyor bearings.

Pure, standard library only, no bpy. the reference CAD application (user_interface/accurate_modeling,
"Other coordinates"): a Surveyor coordinate is ``distance<N/Sangle E/W``, e.g.
``11<N30d22'54.43"W``. A bearing names a quadrant: start at North or South,
then turn the angle toward East or West.

Angles are measured counter-clockwise from +X ("zero degrees at 3 o'clock"),
so, with the quadrant angle ``th`` in [0, 90] degrees:

    N th E = 90 - th
    N th W = 90 + th
    S th E = 270 + th
    S th W = 270 - th

normalised to [0, 360).

API (both return ``(value, error)`` -- a failure is ALWAYS an error string,
never a wrong number):
    bearing_angle(text) -> (float | None, str | None)   # degrees CCW from +X
    parse_surveyor(text) -> ((distance, angle_deg) | None, str | None)
"""

import importlib.util as _ilu

import os as _os

import re

import sys

__all__ = ["bearing_angle", "parse_surveyor"]


def _native_bearing():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to the ``numparse._native_pure()`` / ``calc._native_calc()``
    accessors: returns the ``native_pure`` module when it can be imported
    (production, where this file is ``narwhal3d.bearing``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged Python
    parser" -- the native library is absent, it predates the bearing exports,
    or the adapter cannot be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        _canon = "narwhal3d_native_pure"
        if _canon in sys.modules:
            return sys.modules[_canon]
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "native_pure.py")
        if not _os.path.isfile(_path):
            return None
        _spec = _ilu.spec_from_file_location(_canon, _path)
        _mod = _ilu.module_from_spec(_spec)
        sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


# The angle part: degrees-minutes-seconds like 30d22'54.43" (or 30d22'), or
# a plain number of degrees (30). _NUM allows a decimal where the reference CAD application does.
_NUM = r"(?:\d+(?:\.\d+)?)"
_ANGLE_RE = re.compile(
    r"^(?:"
    # 30d22'54.43"  (or 30d22')
    r"(?P<d>\d+)d(?P<min>" + _NUM + r")'(?P<sec>" + _NUM + r")?\"?"
    r"|"
    # plain degrees
    r"(?P<plain>" + _NUM + r")"
    r")$"
)

_BEARING_RE = re.compile(
    r"^(?P<ns>[NS])\s*(?P<ang>[^EW]+)\s*(?P<ew>[EW])$",
    re.IGNORECASE,
)

_SURVEYOR_RE = re.compile(r"^\s*(?P<dist>[+-]?\d+(?:\.\d+)?)\s*<\s*(?P<bearing>.+)$")

_QUADRANT_BASE = {
    ("N", "E"): (90.0, -1.0),   # 90 - th
    ("N", "W"): (90.0, +1.0),   # 90 + th
    ("S", "E"): (270.0, +1.0),  # 270 + th
    ("S", "W"): (270.0, -1.0),  # 270 - th
}


def _angle_degrees(text):
    """Parse the angle part to degrees. Returns (degrees, error)."""
    t = text.strip()
    m = _ANGLE_RE.match(t)
    if not m:
        return None, "not an angle: %r" % (text,)
    if m.group("d") is not None:
        val = int(m.group("d"))
        if m.group("min") is not None:
            val += float(m.group("min")) / 60.0
        if m.group("sec") is not None:
            val += float(m.group("sec")) / 3600.0
        return val, None
    return float(m.group("plain")), None


def bearing_angle(text):
    """``N th E``/``N th W``/``S th E``/``S th W`` -> degrees CCW from +X.

    This tiny regex stays in Python because its standalone FFI route remained slower
    after adapter hardening. Rust still handles the larger surveyor-coordinate parse.
    """
    if not isinstance(text, str):
        return None, "expected a string"
    m = _BEARING_RE.match(text.strip())
    if not m:
        return None, "not a bearing (need N/S + angle + E/W): %r" % (text,)
    ns = m.group("ns").upper()
    ew = m.group("ew").upper()
    ang, err = _angle_degrees(m.group("ang"))
    if err is not None:
        return None, err
    if ang < 0 or ang > 90:
        return None, "quadrant angle must be in [0, 90]: %g" % ang
    base, sign = _QUADRANT_BASE[(ns, ew)]
    deg = (base + sign * ang) % 360.0
    return deg, None


def parse_surveyor(text):
    """``distance<N/Sangle E/W`` -> ``(distance, angle_degrees)``.

    Standalone calls stay in Python; Rust reuses this grammar internally when the larger
    coordinate parser crosses FFI once for the complete coordinate.
    """
    if not isinstance(text, str):
        return None, "expected a string"
    m = _SURVEYOR_RE.match(text)
    if not m:
        return None, "not a surveyor coordinate (need dist<N/SangE/W): %r" % (text,)
    dist = float(m.group("dist"))
    ang, err = bearing_angle(m.group("bearing"))
    if err is not None:
        return None, err
    return (dist, ang), None
