"""Required native curve search; Python owns SDK callbacks and ctypes only.

Sampling remains the existing optional integration, unchanged below. Analytic
SDK shortcuts remain in nearest.py. No curve is converted to a different NURBS.

A `PolylineCurve` (what Polyline and Rectangle make) is searched by the callback search,
which calls PointAt and DerivativeAt back through ctypes for every evaluation (about
0.5 ms a query). Where qualified, ``narwhal_pure_core_nearest_on_polyline`` runs that same
search with ``ON_PolylineCurve::Evaluate`` ported to Rust, for every target of a call at
once, reading the curve from its ``Encode()`` record. See `POLYLINE_QUALIFIED_PLATFORMS`.
"""
from __future__ import annotations
import array
import base64
import ctypes
import math
import os
from pathlib import Path
import platform
import random
import struct
import sys
import threading
import rhino3dm as r3
_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()
_signature = _binding = _sample_binding = None
_native_calls = _native_sample_calls = _native_many_calls = _native_callback_calls = 0
_curve_cache = None
_many_signature = _many_binding = None
_D = ctypes.POINTER(ctypes.c_double)
_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_double,
                            ctypes.c_uint32, _D)
# Do not lift platform admission without actual scalar/batch/callback qualification.
BATCH_QUALIFIED_PLATFORMS = frozenset({"linux"})


class NativeNearestError(RuntimeError):
    """Required library, ABI or protocol failure (never a geometry fallback)."""


class NearestInputError(ValueError):
    """Nonfinite, malformed or resource-excessive search input."""


def _nurbs_data():
    try:
        from . import nurbs_data
    except ImportError:          # loaded standalone (tests / probes)
        import nurbs_data
    return nurbs_data


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _bindings():
    """``(nearest, sample)`` from the pure-core library; either is None when missing."""
    global _signature, _binding, _sample_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _binding, _sample_binding
    with _lock:
        if _signature == signature:
            return _binding, _sample_binding
        _signature, _binding, _sample_binding = signature, None, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lib = ctypes.CDLL(str(path))
                fn = lib.narwhal_pure_core_nearest_on_nurbs
            except (AttributeError, OSError):
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                           ctypes.c_ubyte, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
                           ctypes.POINTER(ctypes.c_ubyte)]
            fn.restype = ctypes.c_int32
            _binding = fn
            sample_fn = getattr(lib, "narwhal_pure_core_sample_nurbs", None)
            if sample_fn is not None:
                sample_fn.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte,
                                      ctypes.c_double, ctypes.c_double, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_double), ctypes.c_size_t]
                sample_fn.restype = ctypes.c_int32
                _sample_binding = sample_fn
            return _binding, _sample_binding
        return None, None



def _required_bindings():
    global _curve_cache
    key = os.environ.get(_ENV) or "<packaged>"
    if _curve_cache is not None and _curve_cache[0] == key:
        return _curve_cache[1]
    path = (Path(key).expanduser() if key != "<packaged>" else
            Path(__file__).resolve().parent / "native" / _library_name())
    with _lock:
        if _curve_cache is not None and _curve_cache[0] == key:
            return _curve_cache[1]
        try:
            lib = ctypes.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], ctypes.c_uint32
            if version() != 1:
                raise NativeNearestError("required nearest ABI version mismatch")
            scalar = lib.narwhal_pure_core_nearest_on_nurbs
            scalar.argtypes = [ctypes.c_size_t, _D, ctypes.c_size_t, _D, ctypes.c_size_t,
                               ctypes.c_ubyte, ctypes.c_ubyte, _D, _D,
                               ctypes.POINTER(ctypes.c_ubyte)]
            scalar.restype = ctypes.c_int32
            callback = lib.narwhal_pure_core_nearest_on_callbacks_v1
            callback.argtypes = [ctypes.c_double, ctypes.c_double, _D, ctypes.c_size_t,
                                 ctypes.c_size_t, ctypes.c_ubyte, _CALLBACK,
                                 ctypes.c_void_p, _D, _D, ctypes.POINTER(ctypes.c_ubyte)]
            callback.restype = ctypes.c_int32
            many = getattr(lib, "narwhal_pure_core_nearest_on_nurbs_many", None)
            if many is not None:
                many.argtypes = [ctypes.c_size_t, _D, ctypes.c_size_t, _D, ctypes.c_size_t,
                                 ctypes.c_ubyte, ctypes.c_ubyte, _D, ctypes.c_size_t,
                                 _D, ctypes.POINTER(ctypes.c_ubyte)]
                many.restype = ctypes.c_int32
        except (OSError, AttributeError) as exc:
            raise NativeNearestError(f"required nearest library missing or stale: {path}: {exc}") from exc
        _curve_cache = key, (scalar, callback, many)
        return _curve_cache[1]


def _nearest_binding():
    return _required_bindings()[0]


def available():
    try:
        _required_bindings()
        return True
    except NativeNearestError:
        return False


def _point(p):
    if hasattr(p, "X"):
        return p.X, p.Y, p.Z
    return float(p[0]), float(p[1]), float(p[2])


class PackedCurve:
    """Snapshot of original homogeneous NURBS data; no geometry conversion."""
    __slots__ = ("order", "knots", "cvs", "cv_count", "rational", "closed", "_knots", "_cvs")

    def __init__(self, order, knots, cvs, rational, closed):
        if not 2 <= order <= 32 or len(cvs) % 4 or len(cvs) > 4 * (1 << 20) or len(knots) > (1 << 20) + 32:
            raise NearestInputError("nearest packed curve shape/order/resource refusal")
        self._knots = array.array("d", knots)
        self._cvs = array.array("d", cvs)
        self.order = order
        self.cv_count = len(self._cvs) // 4
        self.knots = (ctypes.c_double * len(self._knots)).from_buffer(self._knots)
        self.cvs = (ctypes.c_double * len(self._cvs)).from_buffer(self._cvs)
        self.rational, self.closed = rational, closed


class CallbackCurve:
    """Original SDK object and metadata; all numerical searching stays in Rust."""
    __slots__ = ("curve", "t0", "t1", "knots", "_knots", "span_count", "closed")

    def __init__(self, curve):
        self.curve = curve
        d = curve.Domain
        self.t0, self.t1 = float(d.T0), float(d.T1)
        try:
            values = curve.Knots
        except Exception:
            values = None
        knots = []
        if values is not None:
            try:
                for k in values:
                    if len(knots) >= 100000:
                        raise NearestInputError("nearest callback knot limit 100000")
                    knots.append(float(k))
            except NearestInputError:
                raise
            except Exception:
                knots = []
        self._knots = array.array("d", knots)
        self.knots = (ctypes.c_double * len(knots)).from_buffer(self._knots)
        self.span_count = 1
        if not knots:
            try:
                self.span_count = max(1, int(curve.SpanCount))
            except Exception:
                self.span_count = 1
        if self.span_count > 20000:
            raise NearestInputError("nearest callback span limit 20000")
        try:
            self.closed = bool(curve.IsClosed)
        except Exception:
            self.closed = False


class PolylineCallbackCurve(CallbackCurve):
    """A `PolylineCurve` for the callback search, with its record for the Rust route."""
    __slots__ = ("record",)

    def __init__(self, curve):
        super().__init__(curve)
        self.record = None
        if _polyline_binding() is not None and curve.IsValid:
            try:
                self.record = base64.b64decode(curve.Encode()["data"])
            except Exception:  # noqa: BLE001 -- the callback search answers
                self.record = None


def pack(curve):
    """Pack original NURBS data or retain ORIGINAL SDK object for callbacks."""
    _required_bindings()
    if isinstance(curve, r3.PolylineCurve):
        return PolylineCallbackCurve(curve)
    if not isinstance(curve, r3.NurbsCurve) or int(curve.Order) > 32:
        return CallbackCurve(curve)
    if len(curve.Points) > 1 << 20:
        raise NearestInputError("nearest control-point resource limit")
    order, rational, knots, cvs = _nurbs_data().arrays(curve)   # homogeneous CVs
    return PackedCurve(order, knots, cvs, rational, bool(curve.IsClosed))


def _search_many_binding():
    global _many_signature, _many_binding
    if sys.platform not in BATCH_QUALIFIED_PLATFORMS:
        return None
    fn = _required_bindings()[2]
    if fn is None:
        raise NativeNearestError("required qualified nearest batch export missing")
    _many_signature, _many_binding = os.environ.get(_ENV) or "<packaged>", fn
    return fn


def batch_capability():
    if sys.platform not in BATCH_QUALIFIED_PLATFORMS:
        return f"not qualified on {sys.platform}; Near searches each query"
    _search_many_binding()
    return "qualified"


def _result(code, out, end):
    if code == -2:
        raise NearestInputError("native nearest refused nonfinite/malformed input or work budget")
    if code != 0:
        raise NativeNearestError(f"native nearest protocol status {code}")
    if not all(map(math.isfinite, out)) or end not in (0, 1):
        raise NativeNearestError("native nearest invalid result protocol")
    return out[0], (out[1], out[2], out[3]), out[4], bool(end)


def _callback_search(packed, target):
    global _native_callback_calls
    failures = []
    def evaluate(context, t, order, output):
        try:
            if order == 0:
                values = _point(packed.curve.PointAt(t))
            elif order == 2:
                try:
                    derivatives = packed.curve.DerivativeAt(t, 2)
                except Exception:
                    return 1  # Original Newton stops here; not an error/replay.
                if derivatives is None or len(derivatives) < 3:
                    return 1
                values = tuple(v for p in derivatives[:3] for v in _point(p))
            else:
                raise NativeNearestError(f"unexpected native SDK derivative order {order}")
            for i, v in enumerate(values):
                output[i] = v
            return 0
        except BaseException as exc:
            failures.append(exc)
            return -4
    callback = _CALLBACK(evaluate)  # Strong references live for the synchronous C call.
    point = (ctypes.c_double * 3)(*_point(target))
    out, end = (ctypes.c_double * 5)(), ctypes.c_ubyte()
    code = _required_bindings()[1](packed.t0, packed.t1, packed.knots,
        len(packed.knots), packed.span_count, int(packed.closed), callback, None,
        point, out, ctypes.byref(end))
    if failures:
        raise failures[0]
    result = _result(code, out, end.value)
    _native_callback_calls += 1
    return result


def search(packed, target):
    """Required original-curve search; errors explicit, never None for replay."""
    global _native_calls, _native_polyline_calls
    if isinstance(packed, CallbackCurve):
        rows = _polyline_rows(packed, [target])
        if rows is not None:
            _native_polyline_calls += 1
            return rows[0]
        return _callback_search(packed, target)
    if not isinstance(packed, PackedCurve):
        raise NativeNearestError("nearest search requires packed original curve")
    point = (ctypes.c_double * 3)(*_point(target))
    out, end = (ctypes.c_double * 5)(), ctypes.c_ubyte()
    code = _nearest_binding()(packed.order, packed.knots, len(packed.knots), packed.cvs,
        packed.cv_count, int(packed.rational), int(packed.closed), point, out, ctypes.byref(end))
    result = _result(code, out, end.value)
    _native_calls += 1
    return result


def search_many(packed, targets):
    """Qualified NURBS batch or required native scalar calls; no Python math."""
    global _native_many_calls, _native_polyline_calls
    if isinstance(packed, CallbackCurve):
        bounded = []
        for q in targets:
            if len(bounded) >= 100000:
                raise NearestInputError("nearest batch target limit 100000")
            bounded.append(q)
        rows = _polyline_rows(packed, bounded)
        if rows is not None:
            _native_polyline_calls += 1
            return rows
        return [search(packed, q) for q in bounded]
    fn = _search_many_binding()
    if fn is None:
        return None  # Platform routing only; caller performs required native scalar calls.
    if not isinstance(packed, PackedCurve):
        raise NativeNearestError("nearest batch requires packed original curve")
    points = []
    for q in targets:
        if len(points) >= 100000:
            raise NearestInputError("nearest batch target limit 100000")
        point = _point(q)
        if not all(map(math.isfinite, point)):
            raise NearestInputError("nearest target must be finite")
        points.append(point)
    flat = array.array("d", (v for q in points for v in q))
    n = len(points)
    out = array.array("d", bytes(40 * n))
    at_end = (ctypes.c_ubyte * n)()
    code = fn(packed.order, packed.knots, len(packed.knots), packed.cvs, packed.cv_count,
              int(packed.rational), int(packed.closed),
              (ctypes.c_double * len(flat)).from_buffer(flat), n,
              (ctypes.c_double * len(out)).from_buffer(out), at_end)
    if code != 0:
        _result(code, (), 0)
    rows = [_result(0, out[5*i:5*i+5], at_end[i]) for i in range(n)]
    _native_many_calls += 1
    return rows


def sample(curve, n):
    """``bridge.sample_curve(curve, n)`` as ``(x, y, z)`` tuples, or ``None``.

    A `NurbsCurve`, evaluated from its own knots and control points, or a `LineCurve`
    where the line route is qualified (`line_capability`): each point is bit-identical
    to ``curve.PointAt`` at the same parameter. ``None`` (any other curve, no library,
    an unqualified line route, or a point openNURBS could not evaluate) keeps the
    caller's Python sampling.
    """
    global _native_sample_calls
    if type(curve) is r3.LineCurve:
        return _line_samples(curve, n)
    fn = _bindings()[1]
    if fn is None or not isinstance(curve, r3.NurbsCurve):
        return None
    try:
        n = int(n)
        if n < 1:
            return None
        order, rational, knots, cvs = _nurbs_data().arrays(curve)   # homogeneous CVs
        domain = curve.Domain
        out = array.array("d", bytes(8 * 3 * (n + 1)))
        c_knots = (ctypes.c_double * len(knots)).from_buffer(knots)
        c_cvs = (ctypes.c_double * len(cvs)).from_buffer(cvs)
        c_out = (ctypes.c_double * len(out)).from_buffer(out)
        code = fn(order, c_knots, len(knots), c_cvs, len(cvs) // 4,
                  int(rational), float(domain.T0), float(domain.T1), n,
                  c_out, len(out))
    except Exception:  # noqa: BLE001 -- the Python sampling remains the fallback
        return None
    if code != 0:
        return None
    _native_sample_calls += 1
    return list(zip(out[0::3], out[1::3], out[2::3]))


# LineCurve samples (narwhal_pure_core_line_samples, native/pure_core/src/line_samples.rs):
# openNURBS's ON_LineCurve::Evaluate and ON_Line::PointAt (eb92af3) at `sample`'s
# parameters. Python passes only the ends, read through ``curve.Line`` (which keeps a -0.0
# that the evaluated PointAtStart/PointAtEnd lose), and the domain. Rust never fuses a
# multiply-add, but an SDK build that does (clang's default on arm64) rounds PointAt
# differently. So the route needs BOTH a platform in `LINE_QUALIFIED_PLATFORMS` and the
# loaded rhino3dm passing `_line_parity`, its own PointAt on the witness lines, bit for
# bit. Any other SDK build, even on a qualified platform, keeps PointAt.
LINE_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
# from.xyz, to.xyz, T0, T1, n: -0.0 ends, scales 1e-9 and 1e15, shifted domains, and a
# span whose last parameter lands one ulp past T1, so PointAt keeps an end's -0.0.
_LINE_WITNESSES = (
    ("-0x0.0p+0", "0x1.999999999999ap-4", "0x1.8p+1", "0x1.cp+2", "-0x0.0p+0",
     "0x1.5555555555555p-2", "0x0.0p+0", "0x1.df732362bf072p+2", 128),
    ("0x1.c6bf52634p+49", "-0x1.c6bf52634p+47", "0x1.3e52b9abep+49", "-0x1.10d9316ecp+48",
     "0x1.9945ca262p+49", "0x1.c6bf52634p+49", "-0x1.d99999999999ap+1", "0x1.6333333333333p+3", 128),
    ("0x1.12e0be826d695p-30", "0x1.12e0be826d695p-29", "-0x0.0p+0", "0x1.12e0be826d695p-30",
     "-0x1.80d43de9cc603p-31", "0x1.5798ee2308c3ap-28", "0x1.e848p+19", "0x1.e84809999999ap+19", 128),
    ("0x1.4bd44241e210ap+24", "0x0.0p+0", "0x1.0eed107b52e44p+29", "0x1.c671c2188fd1ap+29",
     "-0x0.0p+0", "0x1.be8157208e2c6p+28", "0x0.0p+0", "0x1.bea058bf8622ap+29", 3),
)
_LINE_RANDOM_WITNESSES = 4      # plus seeded lines of irregular doubles, where a fused
_LINE_WITNESS_SEED = 20261119   # multiply-add would round most samples differently
_line_state = None              # (signature, binding or None, capability text)
_native_line_calls = 0


def _here():
    return sys.platform, platform.machine()


def _point_at_samples(curve, n):
    """``bridge.sample_curve``'s points through the SDK's own PointAt, as tuples."""
    d = curve.Domain
    return [(p.X, p.Y, p.Z) for p in (curve.PointAt(d.T0 + (d.T1 - d.T0) * i / n)
                                      for i in range(n + 1))]


def _line_witness_curves():
    h = float.fromhex
    out = []
    for *values, n in _LINE_WITNESSES:
        v = [h(x) for x in values]
        out.append((v, n))
    rng = random.Random(_LINE_WITNESS_SEED)
    for _ in range(_LINE_RANDOM_WITNESSES):
        scale = rng.choice((1e-6, 1.0, 1e4, 1e12))
        t0 = rng.uniform(-1e3, 1e3)
        out.append(([rng.uniform(-1, 1) * scale for _ in range(6)]
                    + [t0, t0 + rng.uniform(1e-3, 1e3)], 128))
    curves = []
    for v, n in out:
        curve = r3.LineCurve(r3.Point3d(*v[:3]), r3.Point3d(*v[3:6]))
        curve.Domain = r3.Interval(v[6], v[7])
        curves.append((curve, n))
    return curves


def _line_call(fn, curve, n):
    """Rust's samples of one LineCurve, or None when the export refuses it."""
    line, d = curve.Line, curve.Domain
    a, b = line.From, line.To
    ends = (ctypes.c_double * 6)(a.X, a.Y, a.Z, b.X, b.Y, b.Z)
    out = array.array("d", bytes(8 * 3 * (n + 1)))
    code = fn(ends, float(d.T0), float(d.T1), n,
              (ctypes.c_double * len(out)).from_buffer(out), len(out))
    if code != 0:
        return None
    return list(zip(out[0::3], out[1::3], out[2::3]))


def _packed(points):
    return b"".join(struct.pack("<3d", *p) for p in points)


def _line_parity(fn):
    """True when the loaded rhino3dm's PointAt gives Rust's bits on every witness line."""
    try:
        for curve, n in _line_witness_curves():
            got = _line_call(fn, curve, n)
            if got is None or _packed(got) != _packed(_point_at_samples(curve, n)):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _line_binding():
    global _line_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _line_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _line_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "line samples export unavailable; lines use PointAt"
        if signature[1] not in LINE_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; lines use PointAt".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_line_samples
                fn.argtypes = [_D, ctypes.c_double, ctypes.c_double, ctypes.c_size_t, _D,
                               ctypes.c_size_t]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _line_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} PointAt differs from "
                        "the Rust line samples on the witness lines; lines use PointAt")
            elif fn is not None:
                text = "qualified"
        _line_state = signature, fn, text
        return fn


def line_capability():
    """``"qualified"`` when lines are sampled in Rust, otherwise the reason they are not."""
    _line_binding()
    return _line_state[2]


def _line_samples(curve, n):
    """`sample` for a LineCurve: None (unqualified, or refused by the export) keeps PointAt."""
    global _native_line_calls
    fn = _line_binding()
    if fn is None:
        return None
    try:
        n = int(n)
        if not 1 <= n <= 1 << 20:
            return None
        got = _line_call(fn, curve, n)
    except Exception:  # noqa: BLE001 -- PointAt remains the route
        return None
    if got is not None:
        _native_line_calls += 1
    return got


# PolylineCurve searches (narwhal_pure_core_nearest_on_polyline): the callback search with
# ON_PolylineCurve::Evaluate (eb92af3) in Rust instead of PointAt and DerivativeAt(t, 2)
# callbacks. Its point rounds (1-s)*P[i] + s*P[i+1] product by product, and an SDK build that
# fuses a multiply-add rounds PointAt differently. So the route needs BOTH a platform in
# POLYLINE_QUALIFIED_PLATFORMS and the loaded rhino3dm passing `_polyline_parity`: the callback
# search on its own PointAt/DerivativeAt gives Rust's answers, bit for bit, on witness
# polylines. Any refusal, and any other SDK build, keeps the callback search.
POLYLINE_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
_POLYLINE_WITNESS_SEED = 20261122
_polyline_state = None          # (signature, binding or None, capability text)
_native_polyline_calls = 0


def _polyline_call(fn, record, closed, points):
    """Rust's rows for `points` (``(x, y, z)`` tuples), or None when the export refuses."""
    n = len(points)
    flat = array.array("d", (v for q in points for v in q))
    out = array.array("d", bytes(40 * n))
    ends = (ctypes.c_ubyte * n)()
    code = fn(record, len(record), int(closed),
              (ctypes.c_double * len(flat)).from_buffer(flat) if n else None, n,
              (ctypes.c_double * len(out)).from_buffer(out) if n else None, ends)
    if code != 0:
        return None
    return [_result(0, out[5 * i:5 * i + 5], ends[i]) for i in range(n)]


def _polyline_rows(packed, targets):
    """`search` of every target through Rust, or None to take the callback search."""
    record = getattr(packed, "record", None)
    if record is None:
        return None
    fn = _polyline_binding()
    if fn is None:
        return None
    points = [_point(q) for q in targets]
    if not all(math.isfinite(v) for q in points for v in q):
        return None                 # the callback search refuses it, as before
    return _polyline_call(fn, record, packed.closed, points)


def _polyline_witnesses():
    """``(curve, targets)``: irregular doubles (a fused multiply-add would round most
    products differently) on shifted and uneven parameters, open and closed, 2-D and 3-D,
    with targets whose closest points lie inside slanted segments."""
    rng = random.Random(_POLYLINE_WITNESS_SEED)
    p = r3.Point3d
    zig = r3.PolylineCurve([p(-4.0 + 1.7 * i + rng.uniform(-0.3, 0.3),
                              rng.uniform(0.5, 2.0) * (-1) ** i, 0.0) for i in range(5)])
    zig.Domain = r3.Interval(rng.uniform(-60.0, -10.0), rng.uniform(5.0, 70.0))
    spiral = r3.PolylineCurve([p(math.cos(k) * (1 + 0.1 * k), math.sin(k), 0.37 * k + 1e-3)
                               for k in range(6)]).Split(rng.uniform(3.2, 3.8))[0]
    corners = [p(-0.0, -0.0, 0.0), p(rng.uniform(2.0, 3.0), -0.0, 0.0),
               p(rng.uniform(2.0, 3.0), rng.uniform(1.0, 2.0), 0.0),
               p(-0.0, rng.uniform(1.0, 2.0), 0.0)]
    rect = r3.PolylineCurve(corners + corners[:1])

    def targets(curve):
        # near points inside segments (not at a vertex, where the point is exact either way)
        d = curve.Domain
        out = []
        for _ in range(4):
            q = curve.PointAt(d.T0 + (d.T1 - d.T0) * rng.uniform(0.05, 0.95))
            out.append((q.X + rng.uniform(-0.2, 0.2), q.Y + rng.uniform(-0.2, 0.2),
                        q.Z + rng.uniform(-0.05, 0.05)))
        return out
    return [(zig, targets(zig)), (spiral, targets(spiral)), (rect, targets(rect))]


def _polyline_parity(fn):
    """True when the callback search on the loaded rhino3dm gives Rust's rows, bit for
    bit, on every witness polyline and target."""
    try:
        for curve, targets in _polyline_witnesses():
            packed = CallbackCurve(curve)
            record = base64.b64decode(curve.Encode()["data"])
            got = _polyline_call(fn, record, packed.closed, targets)
            want = [_callback_search(packed, q) for q in targets]
            if got is None or _row_bits(got) != _row_bits(want):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _row_bits(rows):
    return b"".join(struct.pack("<5d?", t, *q, d, end) for t, q, d, end in rows)


def _polyline_binding():
    global _polyline_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _polyline_state
    if state is not None and state[0] == signature:
        return state[1]
    try:
        _required_bindings()     # the parity check's callback search; it takes _lock itself
    except NativeNearestError:
        pass                     # then the parity check fails and the route stays off
    with _lock:
        state = _polyline_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "polyline nearest export unavailable; polylines use SDK callbacks"
        if signature[1] not in POLYLINE_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; polylines use SDK callbacks".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_nearest_on_polyline
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_ubyte, _D,
                               ctypes.c_size_t, _D, ctypes.POINTER(ctypes.c_ubyte)]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _polyline_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} PointAt/DerivativeAt "
                        "differ from the Rust polyline search on the witness polylines; "
                        "polylines use SDK callbacks")
            elif fn is not None:
                text = "qualified"
        _polyline_state = signature, fn, text
        return fn


def polyline_capability():
    """``"qualified"`` when polylines are searched in Rust, otherwise the reason."""
    _polyline_binding()
    return _polyline_state[2]
