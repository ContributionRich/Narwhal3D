"""Optional Rust search for the Near object snap's nearest point on a curve.

`nearest.near_point` uses it: Python keeps the analytic line/arc/circle targets
(McNeel's own primitives), and Rust runs `nearest._search` on a `NurbsCurve`'s own
knots and control points, evaluating them with openNURBS's algorithms in the same
operation order, so its point is bit-identical to the Python search's. The answer
is a snap location only; it never changes document geometry.

`sample` uses the same evaluator for `bridge.sample_curve_xyz`: a `NurbsCurve`'s
uniform samples, bit-identical to `PointAt`, for previews and wireframes only.
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
_sample_binding = None
_native_calls = 0
_native_sample_calls = 0


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _bindings():
    """``(nearest, sample)`` from the pure-core library; either is None when missing."""
    global _signature, _binding, _sample_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _binding, _sample_binding
    with _lock:
        if _signature == signature:
            return _binding, _sample_binding
        _signature, _binding, _sample_binding = signature, None, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lib = ctypes.CDLL(str(path))
                fn = lib.narwhal_pure_core_nearest_on_nurbs
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                           ctypes.c_ubyte, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                           ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _binding = fn
            sample_fn = getattr(lib, "narwhal_pure_core_sample_nurbs", None)
            if sample_fn is not None:
                sample_fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                                      ctypes.c_double, ctypes.c_double, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_double), ctypes.c_size_t]
                sample_fn.restype = ctypes.c_int32
                _sample_binding = sample_fn
            return _binding, _sample_binding
        return None, None


def _nearest_binding():
    return _bindings()[0]


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


def sample(curve, n):
    """``bridge.sample_curve(curve, n)`` as ``(x, y, z)`` tuples, or ``None``.

    Only a `NurbsCurve`, evaluated from its own knots and control points: each point
    is bit-identical to ``curve.PointAt`` at the same parameter. ``None`` (any other
    curve, no library, or a point openNURBS could not evaluate) keeps the caller's
    Python sampling.
    """
    global _native_sample_calls
    fn = _bindings()[1]
    if fn is None or not isinstance(curve, r3.NurbsCurve):
        return None
    try:
        n = int(n)
        if n < 1:
            return None
        cvs = array.array("d")
        for cv in curve.Points:
            cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
        knots = array.array("d", curve.Knots.ToList())
        domain = curve.Domain
        out = array.array("d", bytes(8 * 3 * (n + 1)))
        c_knots = (ctypes.c_double * len(knots)).from_buffer(knots)
        c_cvs = (ctypes.c_double * len(cvs)).from_buffer(cvs)
        c_out = (ctypes.c_double * len(out)).from_buffer(out)
        code = fn(int(curve.Order), c_knots, len(knots), c_cvs, len(cvs) // 4,
                  int(bool(curve.IsRational)), float(domain.T0), float(domain.T1), n,
                  c_out, len(out))
    except Exception:  # noqa: BLE001 -- the Python sampling remains the fallback
        return None
    if code != 0:
        return None
    _native_sample_calls += 1
    return list(zip(out[0::3], out[1::3], out[2::3]))
