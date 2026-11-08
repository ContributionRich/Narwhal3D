"""Optional Rust inversion of arc length on a NURBS curve (`arclength.PreparedLength`).

Rust repeats `arclength._invert` on the prepared span table, evaluating the curve with
the Near snap's port of openNURBS, so each parameter is bit-identical to the Python's
(measured on Linux/glibc; the Windows and macOS runtimes are unmeasured).
A length the Python must answer (a refusal, or the table's end branch) comes back
marked, and the caller answers it in Python. The parameters are positions along an
existing curve; this never changes document geometry.
"""
from __future__ import annotations

import array
import ctypes
import os
from pathlib import Path
import sys
import threading

import rhino3dm as r3

_ENV = "NARWHAL_PURE_CORE"
#: `out_status` value for a length the Python must answer
PYTHON = 1
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


def _arclength_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_parameters_at_lengths
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, _D, ctypes.c_size_t, _D, ctypes.c_size_t,
                           ctypes.c_ubyte, ctypes.c_double, ctypes.c_double,
                           _D, ctypes.c_size_t, _D, ctypes.c_double,
                           _D, _D, ctypes.c_size_t, ctypes.c_double,
                           _D, ctypes.c_size_t, _D, ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _arclength_binding() is not None


def _doubles(values):
    buf = array.array("d", values)
    return buf, (ctypes.c_double * len(buf)).from_buffer(buf)


class PackedTable:
    """One prepared curve and its span table as ctypes arrays, packed once."""

    __slots__ = ("args", "_keep")

    def __init__(self, curve, table, nodes, weights):
        cvs = []
        for cv in curve.Points:
            cvs.extend((cv.X, cv.Y, cv.Z, cv.W))   # homogeneous, as openNURBS stores them
        knots, c_knots = _doubles(curve.Knots.ToList())
        cvs, c_cvs = _doubles(cvs)
        table_knots, c_table = _doubles(table.knots)
        cum, c_cum = _doubles(table.cum)
        nodes, c_nodes = _doubles(nodes)
        weights, c_weights = _doubles(weights)
        self._keep = (knots, cvs, table_knots, cum, nodes, weights)
        self.args = (int(curve.Order), c_knots, len(knots), c_cvs, len(cvs) // 4,
                     int(bool(curve.IsRational)), float(table.t0), float(table.t1),
                     c_table, len(table_knots), c_cum, float(table.total),
                     c_nodes, c_weights, len(nodes))


def pack(curve, table, nodes, weights):
    """The packing of a prepared `NurbsCurve` for :func:`parameters_at`, or ``None``."""
    if _arclength_binding() is None or not isinstance(curve, r3.NurbsCurve):
        return None
    if len(table.cum) != len(table.knots) + 2:
        return None
    try:
        return PackedTable(curve, table, nodes, weights)
    except Exception:  # noqa: BLE001 -- the Python inversion remains the fallback
        return None


def parameters_at(packed, lengths, tolerance):
    """``[(t, done)]`` for each of `lengths`, or ``None``.

    ``done`` is False where the Python must answer that length (`t` is then NaN).
    """
    global _native_calls
    fn = _arclength_binding()
    if fn is None or packed is None:
        return None
    try:
        values, c_values = _doubles(float(s) for s in lengths)
        n = len(values)
        out, c_out = _doubles(bytes(8 * n))
        status = (ctypes.c_ubyte * n)()
        code = fn(*packed.args, float(tolerance), c_values, n, c_out, status)
    except Exception:  # noqa: BLE001 -- the Python inversion remains the fallback
        return None
    if code != 0:
        return None
    _native_calls += 1
    return [(out[i], status[i] != PYTHON) for i in range(n)]
