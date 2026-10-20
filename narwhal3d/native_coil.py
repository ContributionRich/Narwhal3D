"""Optional Rust planner for smooth axis-based Helix and Spiral NURBS curves."""
from __future__ import annotations

import array
import ctypes
import math
import os
from pathlib import Path
import sys
import threading

_ABI = 1
_ENV = "NARWHAL_PURE_CORE"
_MAX_TURNS = 1000.0
_MAX_SPANS_PER_TURN = 96
_lock = threading.Lock()
_signature = None
_binding = None
_catenary_signature = None
_catenary_binding = None
_native_calls = 0
_native_catenary_calls = 0


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _coil_binding():
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
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI:
                    continue
                fn = library.narwhal_pure_core_coil_plan
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_double, ctypes.c_double, ctypes.c_double,
                    ctypes.c_ubyte, ctypes.c_double, ctypes.c_ubyte,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_double),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _binding = fn
            return fn
        return None


def _catenary_binding_for_library():
    global _catenary_signature, _catenary_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _catenary_signature == signature:
        return _catenary_binding
    with _lock:
        if _catenary_signature == signature:
            return _catenary_binding
        _catenary_signature, _catenary_binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI:
                    continue
                fn = library.narwhal_pure_core_catenary_plan
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                    ctypes.c_double,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_double),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _catenary_binding = fn
            return fn
        return None


def plan(start, end, radial, radius0, radius1, turns, reverse, tolerance, flat=False):
    """Return packed cubic poles/knots/deviation, or ``None`` for fallback."""
    global _native_calls
    fn = _coil_binding()
    if fn is None:
        return None
    try:
        values = [float(value) for point in (start, end, radial) for value in point]
        if len(values) != 9:
            return None
        radius0 = float(radius0)
        radius1 = float(radius1)
        turns = float(turns)
        tolerance = float(tolerance)
        if (not all(math.isfinite(value) for value in values)
                or not all(math.isfinite(value) for value in
                           (radius0, radius1, turns, tolerance))
                or turns <= 0.0 or turns > _MAX_TURNS or tolerance <= 0.0):
            return None
        maximum_spans = max(1, math.ceil(turns * _MAX_SPANS_PER_TURN))
        pole_capacity = 3 * (3 * maximum_spans + 1)
        knot_capacity = 3 * maximum_spans + 3
        inputs = [array.array("d", point) for point in (start, end, radial)]
        input_views = [(ctypes.c_double * 3).from_buffer(item) for item in inputs]
        poles = array.array("d", [0.0]) * pole_capacity
        knots = array.array("d", [0.0]) * knot_capacity
        pole_view = (ctypes.c_double * pole_capacity).from_buffer(poles)
        knot_view = (ctypes.c_double * knot_capacity).from_buffer(knots)
        pole_count = ctypes.c_uint64(0)
        knot_count = ctypes.c_uint64(0)
        span_count = ctypes.c_uint64(0)
        deviation = ctypes.c_double(0.0)
        code = fn(input_views[0], input_views[1], input_views[2],
                  radius0, radius1, turns, int(bool(reverse)), tolerance,
                  int(bool(flat)), pole_view, pole_capacity, knot_view, knot_capacity,
                  ctypes.byref(pole_count), ctypes.byref(knot_count),
                  ctypes.byref(span_count), ctypes.byref(deviation))
    except Exception:  # noqa: BLE001 -- geometry command retains Python fallback
        return None
    npoles = int(pole_count.value)
    nknots = int(knot_count.value)
    nspans = int(span_count.value)
    achieved = float(deviation.value)
    if (code != 0 or npoles < 12 or npoles % 3
            or nknots != npoles // 3 + 2 or nspans < 1
            or npoles != 3 * (3 * nspans + 1)
            or not math.isfinite(achieved) or achieved < 0.0 or achieved > tolerance
            or any(not math.isfinite(value) for value in poles[:npoles])
            or any(not math.isfinite(value) for value in knots[:nknots])):
        return None
    used_knots = tuple(knots[:nknots])
    if (used_knots != tuple(sorted(used_knots))
            or used_knots[:3] != (0.0, 0.0, 0.0)
            or used_knots[-3:] != (1.0, 1.0, 1.0)):
        return None
    _native_calls += 1
    return tuple(poles[:npoles]), used_knots, nspans, achieved


def catenary_plan(start, end, axis_point, through, tolerance):
    """Return packed catenary poles/knots/deviation, or ``None``."""
    global _native_catenary_calls
    fn = _catenary_binding_for_library()
    if fn is None:
        return None
    try:
        inputs = [array.array("d", (float(value) for value in point))
                  for point in (start, end, axis_point, through)]
        if any(len(item) != 3 or any(not math.isfinite(v) for v in item)
               for item in inputs):
            return None
        tolerance = float(tolerance)
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            return None
        views = [(ctypes.c_double * 3).from_buffer(item) for item in inputs]
        pole_capacity, knot_capacity = 3 * (3 * 128 + 1), 3 * 128 + 3
        poles = array.array("d", [0.0]) * pole_capacity
        knots = array.array("d", [0.0]) * knot_capacity
        pole_view = (ctypes.c_double * pole_capacity).from_buffer(poles)
        knot_view = (ctypes.c_double * knot_capacity).from_buffer(knots)
        npoles = ctypes.c_uint64(0)
        nknots = ctypes.c_uint64(0)
        nspans = ctypes.c_uint64(0)
        deviation = ctypes.c_double(0.0)
        code = fn(*views, tolerance, pole_view, pole_capacity, knot_view, knot_capacity,
                  ctypes.byref(npoles), ctypes.byref(nknots), ctypes.byref(nspans),
                  ctypes.byref(deviation))
    except Exception:  # noqa: BLE001
        return None
    npoles, nknots, nspans = int(npoles.value), int(nknots.value), int(nspans.value)
    achieved = float(deviation.value)
    if (code != 0 or npoles < 12 or npoles % 3
            or nknots != npoles // 3 + 2 or npoles != 3 * (3 * nspans + 1)
            or not math.isfinite(achieved) or not 0.0 <= achieved <= tolerance):
        return None
    _native_catenary_calls += 1
    return tuple(poles[:npoles]), tuple(knots[:nknots]), nspans, achieved


def _reset_for_tests():
    global _signature, _binding, _catenary_signature, _catenary_binding
    global _native_calls, _native_catenary_calls
    with _lock:
        _signature = None
        _binding = None
        _catenary_signature = None
        _catenary_binding = None
        _native_calls = 0
        _native_catenary_calls = 0
