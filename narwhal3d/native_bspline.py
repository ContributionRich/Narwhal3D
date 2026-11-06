"""Optional Rust sampler for the Curve command's control-point preview.

Display only: the samples are a preview polyline, never document geometry.
`bridge.make_polyline` stays the only producer of committed curves.
"""
from __future__ import annotations

import array
import ctypes
import math
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


def _preview_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_control_point_curve_preview
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_uint64)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _preview_binding() is not None


def control_point_curve_polyline(control, max_degree, per_span):
    """The samples `preview.control_point_curve_polyline` returns, or ``None`` for fallback.

    Only for degree two or more: the caller keeps the control-polygon case.
    """
    global _native_calls
    fn = _preview_binding()
    if fn is None:
        return None
    try:
        n = len(control)
        degree = min(int(max_degree), n - 1)
        if degree < 2 or int(per_span) < 0:
            return None
        count = max(1, int(per_span) * (n - degree)) + 1
        values = array.array("d", [float(v) for point in control for v in point])
        if len(values) != 3 * n:
            return None
        out = array.array("d", bytes(8 * 3 * count))
        used = ctypes.c_uint64()
        code = fn((ctypes.c_double * len(values)).from_buffer(values), n,
                  int(max_degree), int(per_span),
                  (ctypes.c_double * len(out)).from_buffer(out), len(out),
                  ctypes.byref(used))
    except Exception:  # noqa: BLE001 -- the Python sampler remains the fallback
        return None
    if code != 0 or used.value != len(out) or not all(map(math.isfinite, out)):
        return None
    _native_calls += 1
    return list(zip(out[0::3], out[1::3], out[2::3]))
