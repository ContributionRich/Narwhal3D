"""Caller-owned ctypes boundary for exact Rust interpolation and helper ABI v1.

Python only marshals host containers. There is no numeric replay on native
refusal. Optional legacy adapter calls still return None; production uses
``required=True`` and receives a typed failure instead.
"""
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
_MAX_POINTS = 2048
_MAX_VALUES = 4_200_000
STYLE = {"Uniform": 0, "Chord": 1, "SqrtChrd": 2}
_lock = threading.RLock()
_signature = _skin_signature = _periodic_signature = _helper_signature = None
_binding = _skin_binding = _periodic_binding = _helper_binding = None
_native_calls = _native_skin_calls = _native_periodic_calls = _native_helper_calls = 0
_D = ctypes.POINTER(ctypes.c_double)
_U = ctypes.POINTER(ctypes.c_uint64)


class NativeInterpolationUnavailable(RuntimeError):
    """The requested exact ABI is missing; Python replay is forbidden."""


class NativeInterpolationFailure(ValueError):
    """A native refusal, retaining its route, numeric status and diagnostic index."""
    def __init__(self, route, status, detail=0):
        self.route, self.status, self.detail = route, status, detail
        super().__init__(f"native interpolation {route} refused (status {status}, detail {detail})")


class HelperRequest(ctypes.Structure):
    _fields_ = [("operation", ctypes.c_uint32), ("style", ctypes.c_uint32),
                ("rows", ctypes.c_uint64), ("columns", ctypes.c_uint64),
                ("degree", ctypes.c_int64), ("index", ctypes.c_int64),
                ("parameter", ctypes.c_double), ("data", _D),
                ("data_len", ctypes.c_size_t)]


def _name():
    return "narwhal_pure_core.dll" if sys.platform == "win32" else "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else "libnarwhal_pure_core.so"


def _resolve(symbol, args):
    explicit = os.environ.get(_ENV)
    path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parent / "native" / _name()
    try:
        lib = ctypes.CDLL(str(path))
        version = lib.narwhal_pure_core_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != _ABI:
            return None
        fn = getattr(lib, symbol)
        fn.argtypes, fn.restype = args, ctypes.c_int32
        return fn
    except (OSError, AttributeError):
        return None


def _cached(kind, symbol, args):
    signature = os.environ.get(_ENV) or "<packaged>"
    sig, binding = ("_signature", "_binding") if kind == "" else (f"_{kind}_signature", f"_{kind}_binding")
    with _lock:
        if globals()[sig] != signature:
            globals()[binding] = _resolve(symbol, args)
            globals()[sig] = signature
        return globals()[binding]


def _fn():
    if _signature == (os.environ.get(_ENV) or "<packaged>"):
        return _binding
    return _cached("", "narwhal_pure_core_interpolate", [_D, ctypes.c_size_t, ctypes.c_int64, ctypes.c_ubyte, _D, ctypes.c_size_t, _D, ctypes.c_size_t, _U, _U, _U])


def _skin_fn():
    if _skin_signature == (os.environ.get(_ENV) or "<packaged>"):
        return _skin_binding
    return _cached("skin", "narwhal_pure_core_skin_control_net", [_D, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_int64, ctypes.c_ubyte, _D, ctypes.c_size_t, _D, ctypes.c_size_t, _U, _U, _U])


def _periodic_fn():
    if _periodic_signature == (os.environ.get(_ENV) or "<packaged>"):
        return _periodic_binding
    return _cached("periodic", "narwhal_pure_core_interpolate_periodic", [_D, ctypes.c_size_t, ctypes.c_ubyte, _D, ctypes.c_size_t, _D, ctypes.c_size_t, _U, _U])


def _helper_fn():
    if _helper_signature == (os.environ.get(_ENV) or "<packaged>"):
        return _helper_binding
    return _cached("helper", "narwhal_pure_core_interpolate_helper_v1", [ctypes.POINTER(HelperRequest), _D, ctypes.c_size_t, _U, _U])


def _view(values):
    return (ctypes.c_double * len(values)).from_buffer(values)


def _buffer(count, limit=_MAX_VALUES):
    if not 0 <= count <= limit:
        raise NativeInterpolationFailure("size", -3)
    return array.array("d", [0.0]) * count


def _flat(points):
    values = array.array("d", (float(c) for p in points for c in p[:3]))
    if len(values) != 3 * len(points) or any(not math.isfinite(v) for v in values):
        raise NativeInterpolationFailure("coordinates", -2)
    return values


def helper(operation, data, *, rows=0, columns=0, degree=0, index=0, parameter=0.0, style=0, capacity):
    """Execute a bounded helper; return doubles or raise a typed native refusal."""
    global _native_helper_calls
    fn = _helper_fn()
    if fn is None:
        raise NativeInterpolationUnavailable("exact interpolation helper ABI v1 is unavailable; install the matching native core (no Python fallback)")
    if not (0 <= rows <= _MAX_POINTS + (operation == 9) and 0 <= columns <= _MAX_POINTS and 0 <= degree <= _MAX_POINTS and -(2**63) <= index < 2**63):
        raise NativeInterpolationFailure(operation, -3)
    values = array.array("d", data)
    if len(values) > _MAX_VALUES:
        raise NativeInterpolationFailure(operation, -3)
    out = _buffer(capacity)
    req = HelperRequest(operation, style, rows, columns, degree, index, parameter, _view(values), len(values))
    used, detail = ctypes.c_uint64(), ctypes.c_uint64()
    code = fn(ctypes.byref(req), _view(out), len(out), ctypes.byref(used), ctypes.byref(detail))
    if code != 0:
        raise NativeInterpolationFailure(operation, code, int(detail.value))
    if used.value != capacity or any(not math.isfinite(v) for v in out):
        raise NativeInterpolationFailure(operation, -7)
    _native_helper_calls += 1
    return list(out)


def _solve(points, degree, style, route, required):
    global _native_calls, _native_skin_calls, _native_periodic_calls
    try:
        if required and _helper_fn() is None:
            raise NativeInterpolationUnavailable("exact interpolation helper ABI v1 is unavailable; install the matching native core (no Python fallback)")
        fn = (_skin_fn() if route == "skin" else _periodic_fn() if route == "periodic" else _fn())
        if fn is None:
            raise NativeInterpolationUnavailable(f"exact native {route} interpolation is unavailable (no Python fallback)")
        if style not in STYLE:
            raise NativeInterpolationFailure(route, -2)
        pts = list(points)
        n = len(pts)
        width = len(pts[0]) if route == "skin" and pts else 1
        if n < (3 if route == "periodic" else 2) or n > _MAX_POINTS or width < 1 or n * width > 2_000_000:
            raise NativeInterpolationFailure(route, -3)
        if route == "skin":
            if any(len(row) != width for row in pts):
                raise NativeInterpolationFailure(route, -2)
            flat = _flat([p for row in pts for p in row])
        else:
            flat = _flat(pts)
        p = int(degree)
        if not (0 if route == "skin" else 1) <= p < 2**63:
            raise NativeInterpolationFailure(route, -2)
        actual = max(1, min(p, n - 1))
        ctrl_count = n + 3 if route == "periodic" else n * width
        knot_count = n + 7 if route == "periodic" else n + actual + 1
        controls, knots = _buffer(3 * ctrl_count, 6_000_000), _buffer(knot_count)
        oc, ok, od = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
        fv, cv, kv = _view(flat), _view(controls), _view(knots)
        if route == "periodic":
            code = fn(fv, n, STYLE[style], cv, len(controls), kv, len(knots), ctypes.byref(oc), ctypes.byref(ok))
            od.value = 3
        elif route == "skin":
            code = fn(fv, n, width, p, STYLE[style], cv, len(controls), kv, len(knots), ctypes.byref(od), ctypes.byref(oc), ctypes.byref(ok))
        else:
            code = fn(fv, n, p, STYLE[style], cv, len(controls), kv, len(knots), ctypes.byref(od), ctypes.byref(oc), ctypes.byref(ok))
        if code != 0:
            raise NativeInterpolationFailure(route, code)
        expected_count = len(controls) if route == "skin" else ctrl_count
        if oc.value != expected_count or ok.value != knot_count or od.value != (3 if route == "periodic" else actual):
            raise NativeInterpolationFailure(route, -7)
        if any(not math.isfinite(v) for v in controls) or any(not math.isfinite(v) for v in knots) or any(a > b for a, b in zip(knots, knots[1:])):
            raise NativeInterpolationFailure(route, -7)
        rows = [tuple(controls[i:i + 3]) for i in range(0, len(controls), 3)]
        if route == "skin":
            result = [rows[i:i + n] for i in range(0, len(rows), n)]
            _native_skin_calls += 1
        else:
            result = rows
            if route == "periodic":
                _native_periodic_calls += 1
            else:
                _native_calls += 1
        return result, list(knots), int(od.value)
    except (NativeInterpolationFailure, NativeInterpolationUnavailable, TypeError, ValueError, OverflowError, IndexError):
        if required:
            raise
        return None


def interpolate(points, degree, style, *, required=False):
    return _solve(points, degree, style, "open", required)


def interpolate_periodic(points, style, *, required=False):
    return _solve(points, 3, style, "periodic", required)


def skin_control_net(sections, degree, style, *, required=False):
    return _solve(sections, degree, style, "skin", required)


def _reset_for_tests():
    global _signature, _binding, _skin_signature, _skin_binding, _periodic_signature, _periodic_binding, _helper_signature, _helper_binding
    global _native_calls, _native_skin_calls, _native_periodic_calls, _native_helper_calls
    with _lock:
        _signature = _skin_signature = _periodic_signature = _helper_signature = None
        _binding = _skin_binding = _periodic_binding = _helper_binding = None
        _native_calls = _native_skin_calls = _native_periodic_calls = _native_helper_calls = 0
