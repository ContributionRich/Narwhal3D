"""Optional Rust ear clipper for planar face fills and caps.

Display only: the triangles index a display mesh, never document geometry.
"""
from __future__ import annotations

import array
import ctypes
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


def _ear_clip_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_ear_clip_display
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_uint64)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _ear_clip_binding() is not None


def ear_clip(poly):
    """The triangles `tessellate.ear_clip` returns for `poly`, or ``None`` for fallback."""
    global _native_calls
    fn = _ear_clip_binding()
    if fn is None:
        return None
    try:
        n = len(poly)
        values = array.array("d", [float(v) for point in poly for v in point])
        if n < 3 or len(values) != 2 * n:
            return None
        out = array.array("Q", bytes(8 * 3 * (n - 2)))
        count = ctypes.c_uint64()
        code = fn((ctypes.c_double * len(values)).from_buffer(values), n,
                  (ctypes.c_uint64 * len(out)).from_buffer(out), len(out),
                  ctypes.byref(count))
    except Exception:  # noqa: BLE001 -- the Python ear clipper remains the fallback
        return None
    if code != 0 or count.value > n - 2:
        return None
    _native_calls += 1
    used = 3 * count.value
    return list(zip(out[0:used:3], out[1:used:3], out[2:used:3]))
