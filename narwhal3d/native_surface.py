"""ctypes adapter for the Rust surface-construction decisions in ``native/pure_core``.

Each function returns ``None`` when the library or export is unavailable or the call
fails, so the caller runs its complete Python fallback. Inputs are sampled xyz points
from exact curves; Rust only decides, it never builds geometry.
"""
from __future__ import annotations

import array
import ctypes
import os
import sys
import threading
from pathlib import Path

_ABI = 1
_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()
_loaded = None
_library = None
calls = 0


def _name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    return "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else "libnarwhal_pure_core.so"


def _lib():
    global _loaded, _library
    explicit = os.environ.get(_ENV)
    key = explicit or "<packaged>"
    if _loaded == key:
        return _library
    with _lock:
        if _loaded == key:
            return _library
        _loaded, _library = key, None
        path = Path(explicit).expanduser() if explicit else \
            Path(__file__).resolve().parent / "native" / _name()
        if not path.is_file():
            return None
        try:
            lib = ctypes.CDLL(str(path))
            lib.narwhal_pure_core_abi_version.restype = ctypes.c_uint32
            if lib.narwhal_pure_core_abi_version() != _ABI:
                return None
            d = ctypes.POINTER(ctypes.c_double)
            lib.narwhal_pure_core_loft_should_reverse.argtypes = [d, d, ctypes.c_size_t, ctypes.c_ubyte]
            lib.narwhal_pure_core_loft_should_reverse.restype = ctypes.c_int32
            lib.narwhal_pure_core_nearest_index.argtypes = [d, d, ctypes.c_size_t]
            lib.narwhal_pure_core_nearest_index.restype = ctypes.c_int64
            lib.narwhal_pure_core_corner_problem.argtypes = [
                d, ctypes.c_size_t, ctypes.c_double, ctypes.POINTER(ctypes.c_uint64)]
            lib.narwhal_pure_core_corner_problem.restype = ctypes.c_int32
        except (AttributeError, OSError):
            return None
        _library = lib
        return lib


def _flat(points):
    data = array.array("d", (float(c) for p in points for c in p[:3]))
    return data, (ctypes.c_double * len(data)).from_buffer(data)


def should_reverse(previous, current, closed):
    """True/False from Rust, or None to fall back."""
    global calls
    lib = _lib()
    if lib is None or len(previous) != len(current) or len(previous) < 2:
        return None
    keep_a, a = _flat(previous)
    keep_b, b = _flat(current)
    result = lib.narwhal_pure_core_loft_should_reverse(a, b, len(previous), int(bool(closed)))
    if result not in (0, 1):
        return None
    calls += 1
    return bool(result)


def nearest_index(target, candidates):
    """Index of the candidate nearest `target`, or None to fall back."""
    global calls
    lib = _lib()
    if lib is None or not candidates:
        return None
    keep_t, t = _flat([target])
    keep_c, c = _flat(candidates)
    result = lib.narwhal_pure_core_nearest_index(t, c, len(candidates))
    if not 0 <= result < len(candidates):
        return None
    calls += 1
    return int(result)


def corner_problem(corners, tolerance):
    """(code, (i, j)) from Rust -- 0 ok, 1 count, 2 coincident pair, 3 collinear -- or
    None to fall back."""
    global calls
    lib = _lib()
    if lib is None or len(corners) > 16:
        return None
    keep, data = _flat(corners)
    pair = (ctypes.c_uint64 * 2)()
    code = lib.narwhal_pure_core_corner_problem(data, len(corners), float(tolerance), pair)
    if code not in (0, 1, 2, 3):
        return None
    calls += 1
    return code, (int(pair[0]), int(pair[1]))
