"""Optional Rust search for the Near object snap's nearest point on a curve.

`nearest.near_point` uses it: Python keeps the analytic line/arc/circle targets
(McNeel's own primitives), and Rust runs `nearest._search` on a `NurbsCurve`'s own
knots and control points, evaluating them with openNURBS's algorithms in the same
operation order, so its point is bit-identical to the Python search's. The answer
is a snap location only; it never changes document geometry.
"""
from __future__ import annotations

import array
import ctypes
import math
import os
from pathlib import Path
import sys
import threading

import rhino3dm as r3

_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()
_signature = None
_binding = None
_native_calls = 0


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _nearest_binding():
    global _signature, _binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _binding
    with _lock:
        if _signature == signature:
            return _binding
        _signature, _binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_nearest_on_nurbs
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                           ctypes.c_ubyte, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                           ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _nearest_binding() is not None


class PackedCurve:
    """A curve's NURBS form as ctypes arrays, packed once and reused per query."""

    __slots__ = ("order", "knots", "cvs", "cv_count", "rational", "closed", "_knots", "_cvs")

    def __init__(self, order, knots, cvs, rational, closed):
        self._knots = array.array("d", knots)
        self._cvs = array.array("d", cvs)
        self.order = order
        self.cv_count = len(self._cvs) // 4
        self.knots = (ctypes.c_double * len(self._knots)).from_buffer(self._knots)
        self.cvs = (ctypes.c_double * len(self._cvs)).from_buffer(self._cvs)
        self.rational = rational
        self.closed = closed


def pack(curve):
    """A `NurbsCurve`'s knots and control points for :func:`search`, or ``None``.

    Only a curve that IS a NURBS curve: the Python search evaluates the curve itself,
    and only then is that the same data Rust evaluates. A line, arc or poly curve
    returns ``None`` and keeps the Python search.
    """
    if _nearest_binding() is None or not isinstance(curve, r3.NurbsCurve):
        return None
    try:
        nurbs = curve
        points = nurbs.Points
        cvs = []
        for i in range(len(points)):
            cv = points[i]
            cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
        return PackedCurve(int(nurbs.Order), list(nurbs.Knots.ToList()), cvs,
                           bool(nurbs.IsRational), bool(curve.IsClosed))
    except Exception:  # noqa: BLE001 -- the Python search remains the fallback
        return None


_many_signature = None
_many_binding = None
_native_many_calls = 0


def _search_many_binding():
    """`narwhal_pure_core_nearest_on_nurbs_many`, or None (no library, or one without it)."""
    global _many_signature, _many_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _many_signature == signature:
        return _many_binding
    with _lock:
        if _many_signature == signature:
            return _many_binding
        _many_signature, _many_binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_nearest_on_nurbs_many
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                           ctypes.c_ubyte, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _many_binding = fn
            return fn
        return None


def search_many(packed, targets):
    """:func:`search` for each of `targets` in one native call, or None.

    An entry is None where :func:`search` would give None (a non-finite answer), so the
    caller answers that target in Python.
    """
    global _native_many_calls
    fn = _search_many_binding()
    if fn is None or packed is None:
        return None
    try:
        flat = array.array("d", (float(c) for q in targets for c in q[:3]))
        n = len(flat) // 3
        out = array.array("d", bytes(40 * n))
        at_end = (ctypes.c_ubyte * max(1, n))()
        code = fn(packed.order, packed.knots, len(packed.knots), packed.cvs, packed.cv_count,
                  int(packed.rational), int(packed.closed),
                  (ctypes.c_double * len(flat)).from_buffer(flat), n,
                  (ctypes.c_double * len(out)).from_buffer(out), at_end)
    except Exception:  # noqa: BLE001 -- the Python search remains the fallback
        return None
    if code != 0:
        return None
    _native_many_calls += 1
    rows = []
    for i in range(n):
        v = out[5 * i:5 * i + 5]
        rows.append((v[0], (v[1], v[2], v[3]), v[4], bool(at_end[i]))
                    if all(math.isfinite(x) for x in v) else None)
    return rows


def search(packed, target):
    """``(t, point, distance, at_end)`` of `nearest._search` on the NURBS form, or None.

    Every value is bit-identical to `nearest._search` on the packed curve.
    """
    global _native_calls
    fn = _nearest_binding()
    if fn is None or packed is None:
        return None
    try:
        point = (ctypes.c_double * 3)(*target)
        out = (ctypes.c_double * 5)()
        at_end = ctypes.c_ubyte()
        code = fn(packed.order, packed.knots, len(packed.knots), packed.cvs, packed.cv_count,
                  int(packed.rational), int(packed.closed), point, out, ctypes.byref(at_end))
    except Exception:  # noqa: BLE001 -- the Python search remains the fallback
        return None
    if code != 0 or not all(math.isfinite(v) for v in out):
        return None
    _native_calls += 1
    return out[0], (out[1], out[2], out[3]), out[4], bool(at_end.value)
