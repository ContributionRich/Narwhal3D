"""Required Rust Tween planning; Python owns ctypes and SDK buffer shapes only."""
from __future__ import annotations
import array
import ctypes as C
import math
import os
from pathlib import Path
import sys
import threading

_lock = threading.Lock()
_cache = None
_native_calls = 0
_MAX_VALUES = 50_000_000


class NativeTweenError(RuntimeError):
    """Required native support missing/stale or violating the protocol."""


def _binding():
    global _cache
    explicit = os.environ.get("NARWHAL_PURE_CORE")
    key = explicit or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    name = ("narwhal_pure_core.dll" if sys.platform == "win32" else
            "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
            "libnarwhal_pure_core.so")
    path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parent / "native" / name
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        try:
            lib = C.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativeTweenError("required Tween native ABI version mismatch")
            dp, up = C.POINTER(C.c_double), C.POINTER(C.c_uint64)
            signatures = {
                "plan": ([dp, dp, C.c_size_t, C.c_size_t, C.c_uint32, C.c_uint32,
                          dp, C.c_size_t, up], "tween_plan_v1"),
                "knots": ([dp, dp, C.c_size_t], "tween_knots_v1"),
                "lengths": ([C.c_double, C.c_size_t, dp, C.c_size_t, up], "tween_lengths_v1"),
            }
            functions = {}
            for key_name, (args, symbol) in signatures.items():
                fn = getattr(lib, "narwhal_pure_core_" + symbol)
                fn.argtypes, fn.restype = args, C.c_int32
                functions[key_name] = fn
            functions["library"] = lib
        except (AttributeError, OSError) as exc:
            raise NativeTweenError(f"required Tween native library missing or stale: {path}: {exc}") from exc
        _cache = key, functions
        return functions


def require_support():
    """Startup admission: require all owned Tween v1 exports and core ABI 1."""
    _binding()
    return True


def _error(rc):
    messages = {
        -2: "invalid Tween input",
        -10: "a control point has zero weight",
        -11: "interpolated control point is invalid",
        -12: "Tween source contains nonfinite or unrepresentable coordinates",
        -13: "Tween output value limit exceeded (50000000)",
        -20: "ControlPoint matching requires matching knot vectors",
    }
    if rc in messages:
        raise ValueError(messages[rc])
    raise NativeTweenError(f"Tween native protocol failure ({rc})")


def _view(values):
    return (C.c_double * len(values)).from_buffer(values)


def _invoke(fn, args, total):
    global _native_calls
    out = array.array("d", [0.0]) * total
    used = C.c_uint64(0xffffffffffffffff)
    try:
        rc = fn(*args, _view(out), total, C.byref(used))
    except (C.ArgumentError, OSError, TypeError, ValueError) as exc:
        raise NativeTweenError(f"Tween native ABI call failed: {exc}") from exc
    if rc != 0:
        if used.value != 0xffffffffffffffff:
            raise NativeTweenError("Tween native error changed output count")
        _error(rc)
    if used.value != total or any(not math.isfinite(x) for x in out):
        raise NativeTweenError("Tween native returned malformed output/count")
    _native_calls += 1
    return out


def interpolate(first, second, number, *, mode=1, flags=0):
    """Mode 0 raw homogeneous controls, 1 Euclidean controls, 2 sample points."""
    binding = _binding()
    width = 3 if mode == 2 else 4
    number = int(number)
    n = len(first)
    if not n or len(second) != n or number < 1 or mode not in (0, 1, 2):
        raise ValueError("invalid Tween input shapes or count")
    total = width * n * number
    if total > _MAX_VALUES:
        raise ValueError("Tween output value limit exceeded (50000000)")
    try:
        if any(len(p) != width for p in first) or any(len(p) != width for p in second):
            raise ValueError("invalid Tween point width")
        a = array.array("d", (v for p in first for v in p))
        b = array.array("d", (v for p in second for v in p))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid Tween input: {exc}") from exc
    out = _invoke(binding["plan"], (_view(a), _view(b), n, number, mode, flags), total)
    points = iter(zip(*[iter(out)] * width))
    return [[next(points) for _ in range(n)] for _ in range(number)]


def matching_knots(first, second):
    binding = _binding()
    if not first or len(first) != len(second):
        raise ValueError("ControlPoint matching requires equal degree and control-point structure")
    a, b = array.array("d", first), array.array("d", second)
    try:
        rc = binding["knots"](_view(a), _view(b), len(a))
    except (C.ArgumentError, OSError, TypeError, ValueError) as exc:
        raise NativeTweenError(f"Tween native knot ABI call failed: {exc}") from exc
    if rc:
        _error(rc)


def sample_lengths(total, sample_number):
    binding = _binding()
    if not 2 <= sample_number <= 9999:
        raise ValueError("Number must be >=1 and SampleNumber must be 2..9999")
    return _invoke(binding["lengths"], (total, sample_number), sample_number)


def _reset_for_tests():
    global _cache, _native_calls
    with _lock:
        _cache = None
        _native_calls = 0
