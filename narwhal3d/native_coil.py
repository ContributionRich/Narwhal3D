"""Required Rust coil planner; optional independent catenary/preview adapters."""
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
_preview_signature = None
_preview_binding = None
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
        if _binding is None:
            raise RuntimeError("required coil native library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")
        return _binding
    with _lock:
        if _signature == signature:
            if _binding is None:
                raise RuntimeError("required coil native library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")
            return _binding
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
            _signature, _binding = signature, fn
            return fn
        _signature, _binding = signature, None
        raise RuntimeError("required coil native library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")


def _catenary_binding_for_library():
    global _catenary_signature, _catenary_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _catenary_signature == signature:
        if _catenary_binding is None:
            raise RuntimeError("required catenary native v2 library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")
        return _catenary_binding
    with _lock:
        if _catenary_signature == signature:
            if _catenary_binding is None:
                raise RuntimeError("required catenary native v2 library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")
            return _catenary_binding
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
                fn = library.narwhal_pure_core_catenary_plan_v2
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                    ctypes.c_double,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_uint32),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _catenary_signature, _catenary_binding = signature, fn
            return fn
        _catenary_signature, _catenary_binding = signature, None
        raise RuntimeError("required catenary native v2 library is missing or incompatible; rebuild pure_core or set NARWHAL_PURE_CORE")


def _preview_binding_for_library():
    global _preview_signature, _preview_binding
    explicit=os.environ.get(_ENV); signature=explicit or "<packaged>"
    if _preview_signature==signature:return _preview_binding
    with _lock:
        if _preview_signature==signature:return _preview_binding
        _preview_signature,_preview_binding=signature,None
        packaged=Path(__file__).resolve().parent/"native"/_library_name()
        for path in ((Path(explicit).expanduser(),) if explicit else (packaged,)):
            if not path.is_file():continue
            try:
                lib=ctypes.CDLL(str(path));fn=lib.narwhal_pure_core_coil_preview
                fn.argtypes=[ctypes.POINTER(ctypes.c_double)]*3+[ctypes.c_double,ctypes.c_double,ctypes.c_double,ctypes.c_ubyte,ctypes.c_ubyte,ctypes.c_size_t,ctypes.POINTER(ctypes.c_double),ctypes.c_size_t,ctypes.POINTER(ctypes.c_uint64)];fn.restype=ctypes.c_int32
            except (AttributeError,OSError):continue
            _preview_binding=fn;return fn
        return None


def preview_points(start,end,radial,radius0,radius1,turns,reverse,flat,count):
    fn=_preview_binding_for_library()
    if fn is None:return None
    try:
        inputs=[array.array("d",map(float,p)) for p in (start,end,radial)];views=[(ctypes.c_double*3).from_buffer(p) for p in inputs];out=array.array("d",[0.0])*(3*count);ov=(ctypes.c_double*len(out)).from_buffer(out);used=ctypes.c_uint64()
        code=fn(*views,float(radius0),float(radius1),float(turns),int(bool(reverse)),int(bool(flat)),count,ov,len(out),ctypes.byref(used))
    except Exception:return None
    if code!=0 or int(used.value)!=3*count or any(not math.isfinite(v) for v in out):return None
    return [tuple(out[3*i:3*i+3]) for i in range(count)]


def plan(start, end, radial, radius0, radius1, turns, reverse, tolerance, flat=False):
    """Return packed plan; legacy parameter ValueError or required-native RuntimeError."""
    global _native_calls
    fn = _coil_binding()
    start, end, radial = tuple(start), tuple(end), tuple(radial)
    radius0, radius1 = float(radius0), float(radius1)
    turns, tolerance = float(turns), float(tolerance)
    values = start + end + radial + (radius0, radius1, turns, tolerance)
    if (len(start) != 3 or len(end) != 3 or len(radial) != 3
            or not all(math.isfinite(value) for value in values)
            or turns <= 0.0 or turns > _MAX_TURNS or tolerance <= 0.0):
        raise ValueError("coil parameters must be finite; turns and tolerance must be positive")
    try:
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
    except (ctypes.ArgumentError, OSError) as exc:
        raise RuntimeError("required coil native invocation failed") from exc
    if code == -2:
        raise ValueError("axis and radial direction must be non-zero")
    if code == -4:
        raise ValueError("requested coil tolerance was not achieved")
    npoles = int(pole_count.value)
    nknots = int(knot_count.value)
    nspans = int(span_count.value)
    achieved = float(deviation.value)
    if (code != 0 or npoles > pole_capacity or nknots > knot_capacity
            or npoles < 12 or npoles % 3
            or nknots != npoles // 3 + 2 or nspans < 1
            or npoles != 3 * (3 * nspans + 1)
            or not math.isfinite(achieved) or achieved < 0.0 or achieved > tolerance
            or any(not math.isfinite(value) for value in poles[:npoles])
            or any(not math.isfinite(value) for value in knots[:nknots])):
        raise RuntimeError("required coil native planner returned an invalid plan")
    used_knots = tuple(knots[:nknots])
    if (used_knots != tuple(sorted(used_knots))
            or used_knots[:3] != (0.0, 0.0, 0.0)
            or used_knots[-3:] != (1.0, 1.0, 1.0)):
        raise RuntimeError("required coil native planner returned an invalid plan")
    _native_calls += 1
    return tuple(poles[:npoles]), used_knots, nspans, achieved


def catenary_plan(start, end, axis_point, through, tolerance):
    """Required v2 plan; preserve named legacy errors, never return a fallback.

    OURS safety correction: malformed/nonfinite inputs, nonpositive tolerance,
    and nonfinite native results are refused instead of publishing Python's
    former NaN/inf poles. All valid numerical work belongs to Rust.
    """
    global _native_catenary_calls
    fn = _catenary_binding_for_library()
    inputs = [array.array("d", point) for point in (start, end, axis_point, through)]
    if any(len(item) != 3 for item in inputs):
        raise ValueError("catenary points must contain three coordinates")
    tolerance = float(tolerance)
    # Shape validation is host ABI marshalling; finite/domain validation and
    # the error reason live in Rust, including for direct non-Python callers.
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
    reason = ctypes.c_uint32(0)
    try:
        code = fn(*views, tolerance, pole_view, pole_capacity, knot_view, knot_capacity,
                  ctypes.byref(npoles), ctypes.byref(nknots), ctypes.byref(nspans),
                  ctypes.byref(deviation), ctypes.byref(reason))
    except (ctypes.ArgumentError, OSError) as exc:
        raise RuntimeError("required catenary native invocation failed") from exc
    errors = {
        1: "catenary parameters must be finite; tolerance must be positive",
        2: "direction must be non-zero",
        3: "the supported catenary needs equal-height endpoints",
        4: "ThroughPoint must be the positive midpoint sag on the catenary axis",
        5: "requested catenary tolerance was not achieved",
        6: "catenary arithmetic could not resolve a finite curve",
    }
    if code in (-2, -4) and reason.value in errors:
        if code != (-4 if reason.value == 5 else -2):
            raise RuntimeError("required catenary native planner returned an invalid reason")
        raise ValueError(errors[reason.value])
    npoles, nknots, nspans = int(npoles.value), int(nknots.value), int(nspans.value)
    achieved = float(deviation.value)
    if (code != 0 or reason.value != 0
            or npoles > pole_capacity or nknots > knot_capacity
            or npoles < 12 or npoles % 3 or not 8 <= nspans <= 128
            or nknots != npoles // 3 + 2 or npoles != 3 * (3 * nspans + 1)
            or not math.isfinite(achieved) or not 0.0 <= achieved <= tolerance
            or any(not math.isfinite(v) for v in poles[:npoles])
            or any(not math.isfinite(v) for v in knots[:nknots])):
        raise RuntimeError("required catenary native planner returned an invalid plan")
    used_knots = tuple(knots[:nknots])
    if (used_knots != tuple(sorted(used_knots))
            or used_knots[:3] != (0.0, 0.0, 0.0)
            or used_knots[-3:] != (1.0, 1.0, 1.0)):
        raise RuntimeError("required catenary native planner returned an invalid plan")
    _native_catenary_calls += 1
    return tuple(poles[:npoles]), used_knots, nspans, achieved


def _reset_for_tests():
    global _signature, _binding, _catenary_signature, _catenary_binding
    global _preview_signature, _preview_binding
    global _native_calls, _native_catenary_calls
    with _lock:
        _signature = None
        _binding = None
        _catenary_signature = None
        _catenary_binding = None
        _preview_signature = None
        _preview_binding = None
        _native_calls = 0
        _native_catenary_calls = 0
