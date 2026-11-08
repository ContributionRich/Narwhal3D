"""Optional Rust least-squares fit for Rebuild (`rebuild._fit`'s numeric part).

Rust repeats `_basis`, `_least_squares` and `_solve` in their operation order and with
Python's float semantics, so the control points are bit-identical to the Python's.
Python builds the `NurbsCurve` from them. Display-independent: the result IS the
rebuilt curve, which is why exact equality is required.
"""
from __future__ import annotations

import array
import ctypes
import os
from pathlib import Path
import sys
import threading

_ENV = "NARWHAL_PURE_CORE"
#: the export's code for a singular system: Python's "use fewer points" refusal
ERR_SINGULAR = -4
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


def _fit_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_rebuild_fit
            except (AttributeError, OSError):
                continue
            fn.argtypes = [_D, _D, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                           ctypes.c_ubyte, ctypes.c_ubyte, _D, _D, _D,
                           ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _fit_binding() is not None


def fit(points, params, n, p, closed, ends):
    """``(control_points, knots)`` of `rebuild._fit`, ``"singular"``, or ``None``.

    `points` are the samples' xyz, `params` their normalised parameters. For an open
    fit `ends` is ``(start, end)`` or ``(start, end, t0, t1)`` with the end tangents;
    for a closed one it is empty. ``"singular"`` is where the Python raises its
    singular-fit error; ``None`` (no library, or a failure) keeps the Python fit.
    """
    global _native_calls
    fn = _fit_binding()
    if fn is None:
        return None
    try:
        flat = array.array("d", (float(c) for q in points for c in q[:3]))
        prm = array.array("d", (float(u) for u in params))
        # never empty: a closed fit passes no ends, and ctypes needs a buffer
        end_values = array.array("d", (float(c) for v in ends for c in v[:3])) or array.array("d", [0.0])
        count = len(prm)
        cv_room = n + p
        ctrl = array.array("d", bytes(24 * cv_room))
        knots = array.array("d", bytes(8 * (n + 2 * p)))
        cv_count = ctypes.c_size_t()
        code = fn((ctypes.c_double * len(flat)).from_buffer(flat),
                  (ctypes.c_double * count).from_buffer(prm), count, int(n), int(p),
                  int(bool(closed)), int(len(ends) == 4),
                  (ctypes.c_double * len(end_values)).from_buffer(end_values),
                  (ctypes.c_double * len(ctrl)).from_buffer(ctrl),
                  (ctypes.c_double * len(knots)).from_buffer(knots),
                  ctypes.byref(cv_count))
    except Exception:  # noqa: BLE001 -- the Python fit remains the fallback
        return None
    if code == ERR_SINGULAR:
        return "singular"
    if code != 0:
        return None
    _native_calls += 1
    m = cv_count.value
    return (list(zip(ctrl[0:3 * m:3], ctrl[1:3 * m:3], ctrl[2:3 * m:3])),
            knots[:m + p - 1].tolist())
