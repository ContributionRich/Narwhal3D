"""Required exact Rust Rebuild fit; no Python numeric replay."""
from __future__ import annotations

import math
from dataclasses import dataclass
import array
import ctypes
import os
from pathlib import Path
import sys
import threading

_ENV = "NARWHAL_PURE_CORE"
#: the export's code for a singular system: Python's "use fewer points" refusal
ERR_SINGULAR = -4
#: the export's code for a fit past its unknown bound, refused before allocating
ERR_TOO_LARGE = -5
_lock = threading.Lock()
_binding = None
_native_calls = 0

_D = ctypes.POINTER(ctypes.c_double)


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


class NativeRebuildError(RuntimeError):
    """Native fit unavailable, stale, or failed; never replayed in Python."""


@dataclass(frozen=True)
class _Binding:
    signature: tuple[str, str | None]
    library: object
    function: object


def _fit_binding():
    global _binding
    explicit = os.environ.get(_ENV)
    signature = ("env", explicit) if explicit is not None else ("packaged", None)
    binding = _binding
    if binding is not None and binding.signature == signature:
        return binding
    with _lock:
        binding = _binding
        if binding is not None and binding.signature == signature:
            return binding
        path = (str(Path(explicit).expanduser()) if explicit else "") if explicit is not None else str(
            Path(__file__).resolve().parent / "native" / _library_name())
        try:
            lib = ctypes.CDLL(path)
        except OSError as exc:
            raise NativeRebuildError(f"Rebuild native library unavailable: {signature}: {exc}") from exc
        try:
            version = lib.narwhal_pure_core_abi_version
            version.argtypes = []
            version.restype = ctypes.c_uint32
            if version() != 1:
                raise NativeRebuildError("Rebuild native library stale: requires core ABI 1")
            fn = lib.narwhal_pure_core_rebuild_fit
        except AttributeError as exc:
            raise NativeRebuildError("Rebuild native library stale: missing ABI or fit symbol") from exc
        fn.argtypes = [_D, _D, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                       ctypes.c_ubyte, ctypes.c_ubyte, _D, _D, _D,
                       ctypes.POINTER(ctypes.c_size_t)]
        fn.restype = ctypes.c_int32
        _binding = _Binding(signature, lib, fn)
        return _binding


def available():
    """Introspection only; production fit always requires the binding and raises."""
    try:
        _fit_binding()
        return True
    except NativeRebuildError:
        return False


def _xyz_rows(rows):
    """Three finite coordinates per row, or ValueError before any native call."""
    out = []
    for row in rows:
        if len(row) != 3:
            raise ValueError("invalid Rebuild input schema")
        xyz = tuple(float(c) for c in row)
        if not all(math.isfinite(c) for c in xyz):
            raise ValueError("invalid Rebuild input schema")
        out.append(xyz)
    return out


def fit(points, params, n, p, closed, ends):
    """Return exact control points/knots or named singular/too-large refusals.

    Invalid input raises ValueError; unavailable/stale/runtime failure raises
    NativeRebuildError. All lengths are validated before passing raw pointers.
    """
    global _native_calls
    binding = _fit_binding()
    try:
        if isinstance(n, bool) or isinstance(p, bool) or int(n) != n or int(p) != p or closed not in (False, True):
            raise ValueError("invalid Rebuild counts or closure")
        n, p, closed = int(n), int(p), bool(closed)
        samples = _xyz_rows(points)
        prm = [float(u) for u in params]
        end_rows = _xyz_rows(ends)
        if not samples or len(prm) != len(samples):
            raise ValueError("invalid Rebuild input schema")
        if p < 1 or (closed and (end_rows or n < 3 or p > n)) or (
                not closed and (len(end_rows) not in (2, 4) or n < p + 1)):
            raise ValueError("invalid Rebuild input schema")
        tangent = len(end_rows) == 4
        if tangent and (n < 4 or p < 2):
            raise ValueError("invalid Rebuild input schema")
        if n > 4096 or len(samples) > (1 << 20) or not all(math.isfinite(u) for u in prm):
            raise ValueError("invalid Rebuild input bounds or parameters")
        unknowns = 3 * n if closed else (2 + 3 * (n - 4) if tangent else 3 * (n - 2))
        if unknowns > 4096:
            return "too large"
        flat = array.array("d", (c for q in samples for c in q))
        prm_buf = array.array("d", prm)
        # never empty: a closed fit passes no ends, and ctypes needs a buffer
        end_values = array.array("d", (c for v in end_rows for c in v)) or array.array("d", [0.0])
        count = len(prm_buf)
        ctrl = array.array("d", bytes(24 * (n + p)))
        knots = array.array("d", bytes(8 * (n + 2 * p)))
        cv_count = ctypes.c_size_t()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid Rebuild input: {exc}") from exc
    try:
        code = binding.function((ctypes.c_double * len(flat)).from_buffer(flat),
                  (ctypes.c_double * count).from_buffer(prm_buf), count, n, p,
                  int(closed), int(tangent),
                  (ctypes.c_double * len(end_values)).from_buffer(end_values),
                  (ctypes.c_double * len(ctrl)).from_buffer(ctrl),
                  (ctypes.c_double * len(knots)).from_buffer(knots),
                  ctypes.byref(cv_count))
    except Exception as exc:
        raise NativeRebuildError(f"Rebuild native runtime failure: {exc}") from exc
    if code == ERR_SINGULAR:
        return "singular"
    if code == ERR_TOO_LARGE:
        return "too large"
    if code != 0:
        raise NativeRebuildError(f"Rebuild native runtime failure: status {code}")
    m = cv_count.value
    expected = n + p if closed else n
    if m != expected or 3 * m > len(ctrl) or m + p - 1 > len(knots):
        raise NativeRebuildError("Rebuild native malformed output: control count/capacity")
    values = ctrl[:3 * m]
    actual_knots = knots[:m + p - 1].tolist()
    if closed:
        expected_knots = [float(i) for i in range(n + 2 * p - 1)]
    else:
        inner = n - p - 1
        expected_knots = [0.0] * p + [(i + 1) / (inner + 1) for i in range(inner)] + [1.0] * p
    if not all(math.isfinite(v) for v in values) or actual_knots != expected_knots:
        raise NativeRebuildError("Rebuild native malformed output: finite controls/knots schema")
    if closed and list(ctrl[3 * n:3 * m]) != list(ctrl[:3 * p]):
        raise NativeRebuildError("Rebuild native malformed output: periodic controls")
    _native_calls += 1
    return (list(zip(ctrl[0:3 * m:3], ctrl[1:3 * m:3], ctrl[2:3 * m:3])),
            knots[:m + p - 1].tolist())
