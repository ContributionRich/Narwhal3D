"""Optional Rust curve/curve subdivision for two NURBS curves (`intersect._subdivide`).

Rust repeats `_subdivide` and `_dedupe`, with `Curve.Split`, `GetBoundingBox`,
`PointAt` and `Intersection.LineLine` ported from the openNURBS rhino3dm 8.35.0 pins,
so every hit is bit-identical to the Python's. The hits are intersection parameters
and points on existing curves; this never changes document geometry.
"""
from __future__ import annotations

import array
import ctypes
import os
from pathlib import Path
import sys
import threading

import rhino3dm as r3

_ENV = "NARWHAL_PURE_CORE"
_ERR_SIZE = -3
_FIRST_CAPACITY = 64
_lock = threading.Lock()
_signature = None
_binding = None
_native_calls = 0

_D = ctypes.POINTER(ctypes.c_double)


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _hits_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_curve_curve_hits
            except (AttributeError, OSError):
                continue
            curve = [ctypes.c_size_t, _D, ctypes.c_size_t, _D, ctypes.c_size_t, ctypes.c_ubyte]
            fn.argtypes = curve + curve + [ctypes.c_double, ctypes.c_size_t, _D,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _hits_binding() is not None


def _pack(curve):
    """``(args, keep)`` for one curve, or None when it is not a valid `NurbsCurve`.

    `Split` refuses an invalid curve, which the port does not reproduce, so such a
    curve stays in Python.
    """
    if not isinstance(curve, r3.NurbsCurve) or not curve.IsValid:
        return None
    cvs = array.array("d")
    for cv in curve.Points:
        cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
    knots = array.array("d", curve.Knots.ToList())
    args = (int(curve.Order), (ctypes.c_double * len(knots)).from_buffer(knots), len(knots),
            (ctypes.c_double * len(cvs)).from_buffer(cvs), len(cvs) // 4,
            int(bool(curve.IsRational)))
    return args, (knots, cvs)


def point_hits(a, b, tol, max_depth):
    """``[(point, t_a, t_b)]`` of `_dedupe(_subdivide(a, b, ...))`, or None.

    None (either curve not a valid `NurbsCurve`, no library, or a case the port does
    not cover) keeps the Python recursion.
    """
    global _native_calls
    fn = _hits_binding()
    if fn is None:
        return None
    try:
        pa, pb = _pack(a), _pack(b)
        if pa is None or pb is None:
            return None
        count = ctypes.c_size_t()
        capacity = _FIRST_CAPACITY
        for _attempt in range(2):
            out = array.array("d", bytes(40 * capacity))
            code = fn(*pa[0], *pb[0], float(tol), int(max_depth),
                      (ctypes.c_double * len(out)).from_buffer(out), capacity,
                      ctypes.byref(count))
            if code != _ERR_SIZE:
                break
            capacity = count.value
    except Exception:  # noqa: BLE001 -- the Python recursion remains the fallback
        return None
    if code != 0:
        return None
    _native_calls += 1
    n = count.value
    return [((out[5 * i], out[5 * i + 1], out[5 * i + 2]), out[5 * i + 3], out[5 * i + 4])
            for i in range(n)]
