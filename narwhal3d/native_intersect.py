"""Optional Rust curve/curve subdivision for NURBS and polyline curves (`intersect._subdivide`).

Rust repeats `_subdivide` and `_dedupe`, with `Curve.Split`, `GetBoundingBox`,
`PointAt` and `Intersection.LineLine` ported from the openNURBS rhino3dm 8.35.0 pins,
so every hit is bit-identical to the Python's. The hits are intersection parameters
and points on existing curves; this never changes document geometry.

The route is live only on a platform in `QUALIFIED_PLATFORMS` (measured: Linux/glibc).
It reaches libm only through `sqrt` and `fma`, but another platform joins only once
scripts/check_pure_core.py has qualified its artifact against the pinned witness hits.

A pair with a `PolylineCurve` (what Polyline and Rectangle make) goes to
``narwhal_pure_core_curve_curve_record_hits``, which reads each curve from its
``Encode()`` record (rhino3dm does not expose a polyline's parameters) and also ports
``ON_PolylineCurve``'s Evaluate, bounding box and Split. Those round each product on its
own, and an SDK build that fuses a multiply-add rounds them differently. So that route
needs a platform in `POLYLINE_QUALIFIED_PLATFORMS` AND the loaded rhino3dm passing
`_polyline_parity`: the Python recursion, on the SDK's own Split and PointAt, gives
Rust's hits bit for bit on the witness pairs. Any other SDK build keeps the Python.
"""
from __future__ import annotations

import array
import base64
import ctypes
import math
import operator
import os
from pathlib import Path
import platform
import random
import struct
import sys
import threading

import rhino3dm as r3

_ENV = "NARWHAL_PURE_CORE"
_ERR_SIZE = -3
_FIRST_CAPACITY = 64
_MAX_DEPTH = 64          # the export's recursion bound; a deeper request stays in Python
# Platforms whose qualified library carries the export. A platform joins only after
# scripts/check_pure_core.py qualifies its artifact; elsewhere the Python recursion answers.
QUALIFIED_PLATFORMS = frozenset({"linux"})
_lock = threading.Lock()
_signature = None
_binding = None
_native_calls = 0
# Pairs with a polyline (see the module docstring): both a platform here and the parity
# check on the loaded SDK.
POLYLINE_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
_record_state = None    # (signature, binding or None, capability text)
_native_record_calls = 0


def _nurbs_data():
    try:
        from . import nurbs_data
    except ImportError:          # loaded standalone (tests / probes)
        import nurbs_data
    return nurbs_data

_D = ctypes.POINTER(ctypes.c_double)


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _hits_binding():
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
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_curve_curve_hits
            except (AttributeError, OSError):
                continue
            curve = [ctypes.c_size_t, _D, ctypes.c_size_t, _D, ctypes.c_size_t, ctypes.c_ubyte]
            fn.argtypes = curve + curve + [ctypes.c_double, ctypes.c_size_t, _D,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_int32
            _binding = fn
            return fn
        return None


def available():
    return _hits_binding() is not None


def capability():
    """``"qualified"`` when the export is live, otherwise the reason it is not."""
    if sys.platform not in QUALIFIED_PLATFORMS:
        return f"not qualified on {sys.platform}; curve_curve recurses in Python"
    if _hits_binding() is None:
        return "curve/curve export unavailable; curve_curve recurses in Python"
    return "qualified"


def _pack(curve):
    """``(args, keep)`` for one curve, or None when it is not a valid `NurbsCurve`.

    `Split` refuses an invalid curve, which the port does not reproduce, so such a
    curve stays in Python.
    """
    if not isinstance(curve, r3.NurbsCurve) or not curve.IsValid:
        return None
    order, rational, knots, cvs = _nurbs_data().arrays(curve)   # homogeneous CVs
    count = len(cvs) // 4
    if order < 2 or count < order or len(knots) != order + count - 2:
        return None
    args = (order, (ctypes.c_double * len(knots)).from_buffer(knots), len(knots),
            (ctypes.c_double * len(cvs)).from_buffer(cvs), count,
            int(rational))
    return args, (knots, cvs)


def point_hits(a, b, tol, max_depth):
    """``[(point, t_a, t_b)]`` of `_dedupe(_subdivide(a, b, ...))`, or None.

    None (a curve that is not a valid `NurbsCurve` or `PolylineCurve`, no library, an
    unqualified polyline route, or a case the port does not cover) keeps the Python
    recursion. So does a `tol` that is not a finite number or a `max_depth` that is not
    an integer from 0 to 64: everything the export is given is checked here first.
    """
    global _native_calls, _native_record_calls
    try:
        depth = operator.index(max_depth)
        tol = float(tol)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (0 <= depth <= _MAX_DEPTH and math.isfinite(tol)):
        return None
    if isinstance(a, r3.PolylineCurve) or isinstance(b, r3.PolylineCurve):
        fn = _record_binding()
        if fn is None:
            return None
        try:
            got = _record_call(fn, a, b, tol, depth)
        except Exception:  # noqa: BLE001 -- the Python recursion remains the fallback
            return None
        if got is not None:
            _native_record_calls += 1
        return got
    fn = _hits_binding()
    if fn is None:
        return None
    try:
        pa, pb = _pack(a), _pack(b)
        if pa is None or pb is None:
            return None
        count = ctypes.c_size_t()
        capacity = _FIRST_CAPACITY
        for _attempt in range(2):
            out = array.array("d", bytes(40 * capacity))
            code = fn(*pa[0], *pb[0], tol, depth,
                      (ctypes.c_double * len(out)).from_buffer(out), capacity,
                      ctypes.byref(count))
            if code != _ERR_SIZE:
                break
            capacity = count.value
    except Exception:  # noqa: BLE001 -- the Python recursion remains the fallback
        return None
    if code != 0:
        return None
    _native_calls += 1
    n = count.value
    return [((out[5 * i], out[5 * i + 1], out[5 * i + 2]), out[5 * i + 3], out[5 * i + 4])
            for i in range(n)]


def _intersect():
    try:
        from . import intersect
    except ImportError:          # loaded standalone (tests / probes)
        import intersect
    return intersect


def _here():
    return sys.platform, platform.machine()


def _encoded(curve):
    """The record of a valid `NurbsCurve` or `PolylineCurve`, as bytes, or None."""
    if not isinstance(curve, (r3.NurbsCurve, r3.PolylineCurve)) or not curve.IsValid:
        return None
    return base64.b64decode(curve.Encode()["data"])


def _record_call(fn, a, b, tol, depth):
    """The records export's hits for `a` and `b`, or None when it does not answer."""
    ra, rb = _encoded(a), _encoded(b)
    if ra is None or rb is None:
        return None
    count = ctypes.c_size_t()
    capacity = _FIRST_CAPACITY
    for _attempt in range(2):
        out = array.array("d", bytes(40 * capacity))
        code = fn(ra, len(ra), rb, len(rb), tol, depth,
                  (ctypes.c_double * len(out)).from_buffer(out), capacity,
                  ctypes.byref(count))
        if code != _ERR_SIZE:
            break
        capacity = count.value
    if code != 0:
        return None
    return [((out[5 * i], out[5 * i + 1], out[5 * i + 2]), out[5 * i + 3], out[5 * i + 4])
            for i in range(count.value)]


def _python_hits(a, b, tol, depth):
    """`_dedupe(_subdivide(...))` on the SDK, as `point_hits` returns hits."""
    ix = _intersect()
    hits = []
    ix._subdivide(a, b, tol, 0, depth, hits)
    return [(h.point, h.t_a, h.t_b) for h in ix._dedupe(hits, tol)]


def _hit_bits(hits):
    return b"".join(struct.pack("<5d", *point, t_a, t_b) for point, t_a, t_b in hits)


_POLYLINE_WITNESS_SEED = 20261121


def _polyline_witnesses():
    """``(a, b, tol)`` pairs on which the loaded SDK must give Rust's hits bit for bit.

    Irregular doubles (so a fused multiply-add would round most products differently) on
    shifted domains: a zigzag crossing a cubic, a closed quadrilateral crossing the zigzag, and a
    polyline whose recursion halves land a hair short of a break, so `Split` snaps there
    (`ParameterSearch`) and `Trim` reparameterises.
    """
    rng = random.Random(_POLYLINE_WITNESS_SEED)
    p = r3.Point3d
    zig = r3.PolylineCurve([p(-4.0 + 1.6 * i + rng.uniform(-0.3, 0.3),
                              rng.uniform(0.6, 2.2) * (-1) ** i, 0.0) for i in range(4)])
    zig.Domain = r3.Interval(rng.uniform(-50.0, -10.0), rng.uniform(10.0, 90.0))
    wave = r3.NurbsCurve.Create(False, 3, [p(-5.0 + 2.6 * i, rng.uniform(-1.0, 1.0), 0.0)
                                           for i in range(4)])
    corners = [p(rng.uniform(-3.0, -2.0), rng.uniform(-3.0, -2.0), 0.0),
               p(rng.uniform(2.0, 3.0), rng.uniform(-3.0, -2.0), 0.0),
               p(rng.uniform(2.0, 3.0), rng.uniform(2.0, 3.0), 0.0),
               p(rng.uniform(-3.0, -2.0), rng.uniform(2.0, 3.0), 0.0)]
    quad = r3.PolylineCurve(corners + corners[:1])
    quad.Domain = r3.Interval(rng.uniform(0.1, 1.0), rng.uniform(5.0, 7.0))
    # [0, 1, 1.999999996]: the first halving lands within ParameterSearch's snap of 1.
    near = r3.PolylineCurve([p(0.0, 0.0, 0.0), p(1.0, rng.uniform(0.5, 1.5), 0.0),
                             p(2.0, 0.0, 0.0), p(3.0, 1.0, 0.0)]).Split(1.999999996)[0]
    cut = r3.LineCurve(p(0.99, -1.0, 0.0), p(1.01, 3.0, 0.0)).ToNurbsCurve()
    return [(zig, wave, 1e-3), (quad, zig, 1e-6), (near, cut, 1e-6)]


def _polyline_parity(fn):
    """True when the Python recursion on the loaded rhino3dm gives Rust's hits, bit for
    bit, on every witness pair."""
    try:
        for a, b, tol in _polyline_witnesses():
            got = _record_call(fn, a, b, tol, 40)
            if got is None or _hit_bits(got) != _hit_bits(_python_hits(a, b, tol, 40)):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _record_binding():
    global _record_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _record_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _record_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "curve/curve record export unavailable; polylines recurse in Python"
        if signature[1] not in POLYLINE_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; polylines recurse in Python".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_curve_curve_record_hits
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                               ctypes.c_size_t, ctypes.c_double, ctypes.c_size_t, _D,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _polyline_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} Split/PointAt differ "
                        "from the Rust polyline port on the witness pairs; polylines "
                        "recurse in Python")
            elif fn is not None:
                text = "qualified"
        _record_state = signature, fn, text
        return fn


def polyline_capability():
    """``"qualified"`` when pairs with a polyline recurse in Rust, otherwise the reason."""
    _record_binding()
    return _record_state[2]
