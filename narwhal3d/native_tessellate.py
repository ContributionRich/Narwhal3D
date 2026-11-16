"""Required planar-loop Rust boundary; Python only marshals buffers and indices.

Used by existing planar display, hit-test and cap callers. No geometry operation,
triangulation engine, alternate library search or Python replay lives here.
"""
from __future__ import annotations

import array
import ctypes
import itertools
import os
from pathlib import Path
import sys
import threading

_MAX_POINTS = 1 << 20
_UNWRITTEN = (1 << 64) - 1
_lock = threading.Lock()
_cache = None
_native_calls = 0
_native_area_calls = 0


class NativeTessellateError(RuntimeError):
    """Required native support is missing/stale or violates its protocol."""


class TessellationInputError(ValueError):
    """Input cannot cross the bounded binary64 planar-loop boundary."""


def _library_name():
    return ("narwhal_pure_core.dll" if sys.platform == "win32" else
            "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
            "libnarwhal_pure_core.so")


def _ear_clip_binding():
    global _cache
    key = os.environ.get("NARWHAL_PURE_CORE") or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        path = (Path(key).expanduser() if key != "<packaged>" else
                Path(__file__).resolve().parent / "native" / _library_name())
        try:
            library = ctypes.CDLL(str(path))
            version = library.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], ctypes.c_uint32
            if version() != 1:
                raise NativeTessellateError(f"required ear clip core ABI mismatch: {path}")
            dp, up = ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint64)
            clip = library.narwhal_pure_core_ear_clip_display
            clip.argtypes, clip.restype = [dp, ctypes.c_size_t, up, ctypes.c_size_t, up], ctypes.c_int32
            area = library.narwhal_pure_core_earclip_signed_area_v1
            area.argtypes, area.restype = [dp, ctypes.c_size_t, dp, up], ctypes.c_int32
        except (OSError, AttributeError) as error:
            raise NativeTessellateError(f"required ear clip native library missing or stale: {path}: {error}") from error
        binding = library, clip, area
        _cache = key, binding
        return binding


def require_support():
    """Resolve both required helpers and core ABI 1, even for an empty loop."""
    _ear_clip_binding()
    return True


def _pack(poly):
    n = len(poly)
    if n > _MAX_POINTS:
        raise TessellationInputError("ear clip point count exceeds 1048576")
    try:
        # Pair unpacking rejects mixed row arities. islice also bounds work when
        # a hostile container reports a smaller length than its actual stream.
        values = array.array("d", [v for x, y in itertools.islice(poly, n + 1) for v in (x, y)])
    except (TypeError, ValueError, OverflowError) as error:
        raise TessellationInputError(f"ear clip needs binary64 xy pairs: {error}") from error
    if len(values) != 2 * n:
        raise TessellationInputError("ear clip point count changed during packing")
    view = (ctypes.c_double * len(values)).from_buffer(values)
    return n, values, view


def _call(function, *args):
    try:
        code = function(*args)
    except Exception as error:
        raise NativeTessellateError(f"required ear clip ABI call failed: {error}") from error
    if code == -2:
        raise TessellationInputError("ear clip rejected nonfinite input or point limit")
    if code != 0:
        raise NativeTessellateError(f"required ear clip ABI refused a valid host span ({code})")


def ear_clip(poly):
    """Return exact ordered triples, including legitimate empty/partial results."""
    global _native_calls
    _library, clip, _area = _ear_clip_binding()
    n, owned, points = _pack(poly)
    capacity = 3 * max(0, n - 2)
    output = array.array("Q", [_UNWRITTEN]) * capacity
    view = (ctypes.c_uint64 * capacity).from_buffer(output)
    count = ctypes.c_uint64(_UNWRITTEN)
    _call(clip, points, n, view, capacity, ctypes.byref(count))
    if count.value > max(0, n - 2):
        raise NativeTessellateError("required ear clip returned an unwritten/invalid triangle count")
    used = 3 * count.value
    triples = list(zip(output[0:used:3], output[1:used:3], output[2:used:3]))
    if any(i >= n or j >= n or k >= n or i == j or j == k or i == k for i, j, k in triples):
        raise NativeTessellateError("required ear clip returned an invalid vertex index/triple")
    _native_calls += 1
    return triples


def signed_area(poly):
    """Original shoelace value, including its IEEE overflow/nonfinite results."""
    global _native_area_calls
    _library, _clip, area = _ear_clip_binding()
    n, owned, points = _pack(poly)
    output, count = ctypes.c_double(), ctypes.c_uint64(_UNWRITTEN)
    _call(area, points, n, ctypes.byref(output), ctypes.byref(count))
    if count.value != 1:
        raise NativeTessellateError("required signed area returned an unwritten/invalid value count")
    _native_area_calls += 1
    return output.value


def _reset_for_tests():
    global _cache, _native_calls, _native_area_calls
    with _lock:
        _cache, _native_calls, _native_area_calls = None, 0, 0
