"""Optional Rust display tessellation of one NURBS curve (`bridge._display_samples`).

Rust repeats `_display_samples` on a `NurbsCurve`'s own knots and control points,
evaluating them with the Near snap's port of openNURBS and Python's own float idioms,
so the parameters and points are bit-identical to the Python route. Display only:
the samples are viewport vertices, never document geometry.
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
#: points the first call has room for; a larger curve reports its count and is
#: called once more with exactly that room
_FIRST_CAPACITY = 256
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


def _display_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_curve_display_samples
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                           ctypes.c_double, ctypes.c_double, ctypes.c_double,
                           ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _display_binding() is not None


def display_samples(curve, tolerance):
    """``(params, points)`` of `bridge._display_samples(curve, tolerance)`, or ``None``.

    Only a `NurbsCurve`. ``None`` (any other curve, no library, a curve openNURBS
    cannot evaluate, or input the Python would raise on) keeps the Python route.
    """
    global _native_calls
    fn = _display_binding()
    if fn is None or not isinstance(curve, r3.NurbsCurve):
        return None
    try:
        cvs = array.array("d")
        for cv in curve.Points:
            cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
        knots = array.array("d", curve.Knots.ToList())
        domain = curve.Domain
        args = (int(curve.Order), (ctypes.c_double * len(knots)).from_buffer(knots), len(knots),
                (ctypes.c_double * len(cvs)).from_buffer(cvs), len(cvs) // 4,
                int(bool(curve.IsRational)), float(domain.T0), float(domain.T1),
                float(tolerance))
        count = ctypes.c_size_t()
        capacity = _FIRST_CAPACITY
        for _attempt in range(2):
            params = array.array("d", bytes(8 * capacity))
            points = array.array("d", bytes(24 * capacity))
            code = fn(*args, (ctypes.c_double * capacity).from_buffer(params),
                      (ctypes.c_double * (3 * capacity)).from_buffer(points),
                      capacity, ctypes.byref(count))
            if code != _ERR_SIZE:
                break
            capacity = count.value
    except Exception:  # noqa: BLE001 -- the Python tessellation remains the fallback
        return None
    if code != 0:
        return None
    n = count.value
    _native_calls += 1
    return (params[:n].tolist(),
            list(zip(points[0:3 * n:3], points[1:3 * n:3], points[2:3 * n:3])))
