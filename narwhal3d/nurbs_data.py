"""A `NurbsCurve`'s order, knots and control points, read in one rhino3dm call.

The Rust adapters (`native_nearest`, `native_display`, `native_arclength`,
`native_intersect`, `nearest`) hand Rust a curve's knots and homogeneous control
points. Reading them through ``curve.Points`` costs one pybind call per point and one
per coordinate: MEASURED (i7-9700K) 0.8 us per call, 491 us for a 67-point curve,
against 26 us for the Rust evaluation it feeds.

`arrays` asks openNURBS for the curve once, through ``curve.Encode()``, and Rust
(``narwhal_pure_core_nurbs_curve_record``, native/pure_core/src/nurbs_record.rs) checks
the whole ``ON_NurbsCurve`` record (framing, CRCs, every payload field) and copies out
its doubles, the same bits ``Knots.ToList()`` and ``Points`` return. Python owns only
the ``Encode()`` call and the ctypes buffers. A record Rust does not recognise, a
platform outside `QUALIFIED_PLATFORMS` or a missing library keeps the per-point read,
so the answer never depends on the route.
"""
from __future__ import annotations

import array
import base64
import ctypes
import os
from pathlib import Path
import sys
import threading

_ENV = "NARWHAL_PURE_CORE"
_NOT_RECOGNISED = 1
# Platforms whose qualified library carries the export. A platform joins only after
# scripts/check_pure_core.py qualifies its artifact; elsewhere every curve is read point
# by point. The export does no floating-point arithmetic, only byte copies.
QUALIFIED_PLATFORMS = frozenset({"linux"})
_lock = threading.Lock()
_signature = None
_binding = None

_encoded_reads = 0     # curves read from the archive record (tests count these)
_point_reads = 0       # curves read point by point


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _record_binding():
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
        if sys.platform not in QUALIFIED_PLATFORMS:
            candidates = ()
        for path in candidates:
            if not path.is_file():
                continue
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_nurbs_curve_record
            except (AttributeError, OSError):
                continue
            d = ctypes.POINTER(ctypes.c_double)
            fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, d, ctypes.c_size_t, d,
                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _record_binding() is not None


def capability():
    """``"qualified"`` when the export is live, otherwise the reason it is not."""
    if sys.platform not in QUALIFIED_PLATFORMS:
        return f"not qualified on {sys.platform}; curves are read point by point"
    if _record_binding() is None:
        return "NURBS record export unavailable; curves are read point by point"
    return "qualified"


def arrays(curve):
    """``(order, rational, knots, cvs)`` of a `NurbsCurve`.

    `knots` and `cvs` are ``array('d')``; `cvs` holds ``x, y, z, w`` per control point,
    homogeneous as openNURBS stores them (``w`` is 1.0 on a non-rational curve), exactly
    what ``curve.Points`` gives. Raises what rhino3dm raises for an unreadable curve.
    """
    global _encoded_reads, _point_reads
    decoded = _from_archive(curve)
    if decoded is not None:
        _encoded_reads += 1
        return decoded
    cvs = array.array("d")
    for cv in curve.Points:
        cvs.extend((cv.X, cv.Y, cv.Z, cv.W))
    _point_reads += 1
    return int(curve.Order), bool(curve.IsRational), array.array("d", curve.Knots.ToList()), cvs


def _from_archive(curve):
    """`arrays` from ``curve.Encode()``, or None when the record route does not answer."""
    fn = _record_binding()
    if fn is None:
        return None
    try:
        data = base64.b64decode(curve.Encode()["data"])
    except Exception:  # noqa: BLE001 -- the per-point read is the reference
        return None
    return parse(data, fn)


def parse(data, fn=None):
    """`arrays` from one encoded ``ON_NurbsCurve`` record (bytes), or None.

    None when the export is unavailable or does not recognise the record. Any other
    refusal is a broken ABI contract and raises.
    """
    fn = fn or _record_binding()
    if fn is None:
        return None
    data = bytes(data)
    # Each knot takes 8 record bytes and each CV at least 24, so these always suffice.
    knots = array.array("d", bytes(8 * (len(data) // 8)))
    cvs = array.array("d", bytes(8 * (len(data) // 6)))
    shape = (ctypes.c_size_t * 4)()
    code = fn(data, len(data),
              (ctypes.c_double * len(knots)).from_buffer(knots) if knots else None, len(knots),
              (ctypes.c_double * len(cvs)).from_buffer(cvs) if cvs else None, len(cvs),
              shape)
    if code == _NOT_RECOGNISED:
        return None
    if code != 0:
        raise RuntimeError(f"NURBS record export refused a sized call ({code})")
    order, rational, knot_count, cv_count = shape
    del knots[knot_count:]
    del cvs[4 * cv_count:]
    return order, bool(rational), knots, cvs
