"""Optional Rust search for the Near object snap's nearest point on a curve.

`nearest.near_point` uses it: Python keeps the analytic line/arc/circle targets
(McNeel's own primitives), and Rust runs `nearest._search` on the curve's NURBS
form. The answer is a snap location only; it never changes document geometry.
"""
from __future__ import annotations

import array
import ctypes
import math
import os
from pathlib import Path
import sys
import threading

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
                           ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                           ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _nearest_binding() is not None


class PackedCurve:
    """A curve's NURBS form as ctypes arrays, packed once and reused per query."""

    __slots__ = ("order", "knots", "cvs", "cv_count", "closed", "_knots", "_cvs")

    def __init__(self, order, knots, cvs, closed):
        self._knots = array.array("d", knots)
        self._cvs = array.array("d", cvs)
        self.order = order
        self.cv_count = len(self._cvs) // 4
        self.knots = (ctypes.c_double * len(self._knots)).from_buffer(self._knots)
        self.cvs = (ctypes.c_double * len(self._cvs)).from_buffer(self._cvs)
        self.closed = closed


def pack(curve):
    """`curve`'s NURBS form for :func:`search`, or ``None`` when it has none."""
    if _nearest_binding() is None:
        return None
    try:
        nurbs = curve.ToNurbsCurve()
        if nurbs is None:
            return None
        points = nurbs.Points
        cvs = []
        for i in range(len(points)):
            cv = points[i]
            cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
        return PackedCurve(int(nurbs.Order), list(nurbs.Knots.ToList()), cvs,
                           bool(curve.IsClosed))
    except Exception:  # noqa: BLE001 -- the Python search remains the fallback
        return None


def search(packed, target):
    """``(t, point, distance, at_end)`` of `nearest._search` on the NURBS form, or None.

    `t` is a parameter of the NURBS form, which for an arc, line or poly curve is not
    the original curve's parameterisation; the POINT is the same geometric answer.
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
                  int(packed.closed), point, out, ctypes.byref(at_end))
    except Exception:  # noqa: BLE001 -- the Python search remains the fallback
        return None
    if code != 0 or not all(math.isfinite(v) for v in out):
        return None
    _native_calls += 1
    return out[0], (out[1], out[2], out[3]), out[4], bool(at_end.value)
