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

    A `NurbsCurve`, evaluated from its own knots and control points, a `LineCurve`
    where the line route is qualified (`line_capability`), a `PolylineCurve` where
    the polyline sample route is (`polyline_sample_capability`), or a `PolyCurve` where
    the polycurve one is (`polycurve_sample_capability`): each point is bit-identical to
    ``curve.PointAt`` at the same parameter. ``None`` (any other curve, no library, an
    unqualified route, or a point openNURBS could not evaluate) keeps the caller's Python
    sampling.
    """
    global _native_sample_calls
    if type(curve) is r3.LineCurve:
        return _line_samples(curve, n)
    if type(curve) is r3.PolylineCurve:
        return _polyline_samples(curve, n)
    if type(curve) is r3.PolyCurve:
        return _polycurve_samples(curve, n)
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


# PolylineCurve samples (narwhal_pure_core_polyline_samples, native/pure_core/src/line_samples.rs):
# ON_PolylineCurve::Evaluate (eb92af3) at `sample`'s parameters, read from the curve's Encode()
# record, the only place rhino3dm exposes its parameters. Like a line's, the point rounds
# (1-s)*P[i] + s*P[i+1] product by product, and an SDK build that fuses a multiply-add rounds
# PointAt differently. So the route needs BOTH a platform in POLYLINE_SAMPLES_QUALIFIED_PLATFORMS
# and the loaded rhino3dm passing `_polyline_sample_parity`: its own PointAt on the witness
# polylines, bit for bit. Any refusal, and any other SDK build, keeps PointAt.
POLYLINE_SAMPLES_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
# MEASURED (i7-9700K): Encode() costs ~0.36 us a point and PointAt ~6.4 us a sample, so
# reading the record of a polyline over ~18 points per sample costs more than PointAt
# saves. Longer polylines keep PointAt (a snap target's 129 samples route up to 1032 points).
POLYLINE_SAMPLES_POINTS_PER_SAMPLE = 8
_POLYLINE_SAMPLE_WITNESS_N = (128, 7)
_polyline_sample_state = None   # (signature, binding or None, capability text)
_native_polyline_sample_calls = 0


def _polyline_sample_call(fn, record, n):
    """Rust's samples of one PolylineCurve record, or None when the export refuses it."""
    out = array.array("d", bytes(8 * 3 * (n + 1)))
    code = fn(record, len(record), n, (ctypes.c_double * len(out)).from_buffer(out), len(out))
    if code != 0:
        return None
    return list(zip(out[0::3], out[1::3], out[2::3]))


def _polyline_sample_witnesses():
    """`_polyline_witnesses`' polylines (irregular doubles on shifted and uneven parameters,
    open and closed, -0.0), and one whose last sample parameter lands one ulp past T1."""
    curves = [curve for curve, _ in _polyline_witnesses()]
    h = float.fromhex
    past = r3.PolylineCurve([r3.Point3d(h(x), h(y), -0.0) for x, y in (
        ("0x1.3a1f8e0c6b1d2p+2", "-0x1.f02a7c3b1e5d4p+1"), ("-0x1.6c0d2e9a4f3b8p+0", "0x1.2b7e1c5a9d3f6p+3"),
        ("0x1.d4e6a2b8c0f17p+1", "0x1.0c3a5e7f9b2d4p-2"), ("-0x1.84b2d6f0a3c5ep+2", "-0x0.0p+0"))])
    past.Domain = r3.Interval(h("-0x1.f0e85cf904712p+5"), h("0x1.c4aaeac932a47p+8"))
    return curves + [past]


def _polyline_sample_parity(fn):
    """True when the loaded rhino3dm's PointAt gives Rust's bits on every witness polyline."""
    try:
        for curve in _polyline_sample_witnesses():
            record = base64.b64decode(curve.Encode()["data"])
            for n in _POLYLINE_SAMPLE_WITNESS_N:
                got = _polyline_sample_call(fn, record, n)
                if got is None or _packed(got) != _packed(_point_at_samples(curve, n)):
                    return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _polyline_sample_binding():
    global _polyline_sample_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _polyline_sample_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _polyline_sample_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "polyline samples export unavailable; polylines use PointAt"
        if signature[1] not in POLYLINE_SAMPLES_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; polylines use PointAt".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_polyline_samples
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, _D,
                               ctypes.c_size_t]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _polyline_sample_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} PointAt differs from "
                        "the Rust polyline samples on the witness polylines; polylines use PointAt")
            elif fn is not None:
                text = "qualified"
        _polyline_sample_state = signature, fn, text
        return fn


def polyline_sample_capability():
    """``"qualified"`` when polylines are sampled in Rust, otherwise the reason they are not."""
    _polyline_sample_binding()
    return _polyline_sample_state[2]


def _polyline_samples(curve, n):
    """`sample` for a PolylineCurve: None (unqualified, a polyline too long for its record to
    pay, or refused by the export) keeps PointAt."""
    global _native_polyline_sample_calls
    fn = _polyline_sample_binding()
    if fn is None:
        return None
    try:
        n = int(n)
        if not 1 <= n <= 1 << 20 or curve.PointCount > POLYLINE_SAMPLES_POINTS_PER_SAMPLE * (n + 1):
            return None
        got = _polyline_sample_call(fn, base64.b64decode(curve.Encode()["data"]), n)
    except Exception:  # noqa: BLE001 -- PointAt remains the route
        return None
    if got is not None:
        _native_polyline_sample_calls += 1
    return got


# PolyCurve samples (narwhal_pure_core_polycurve_samples, native/pure_core/src/line_samples.rs):
# ON_PolyCurve::Evaluate (eb92af3) at `sample`'s parameters, read from the curve's Encode()
# record, which holds every segment's record. Its LineCurve, PolylineCurve, NurbsCurve and
# nested PolyCurve segments are evaluated in Rust; a record with any other segment class (an
# ArcCurve read from a file) is refused and keeps PointAt. The segment parameter is mapped as
# b*s0 + a*s1, and an SDK build that fuses that multiply-add (or a line's or a polyline's)
# rounds PointAt differently. So the route needs BOTH a platform in
# POLYCURVE_SAMPLES_QUALIFIED_PLATFORMS and the loaded rhino3dm passing
# `_polycurve_sample_parity`: its own PointAt on the witness polycurves, bit for bit.
POLYCURVE_SAMPLES_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
# MEASURED (i7-9700K): Encode() costs ~4.8 us a cubic segment (one span) and ~0.36 us a
# polyline point (one span), and a PolyCurve's PointAt ~3.8 us a sample. So the route takes
# at most one span per 2 samples (64 for a snap target), where even a polycurve of cubics
# pays; one with more spans keeps PointAt.
POLYCURVE_SAMPLES_PER_SPAN = 2
_POLYCURVE_SAMPLE_WITNESS_N = (128, 7)
_POLYCURVE_SAMPLE_WITNESS_SEED = 20261123
_polycurve_sample_state = None  # (signature, binding or None, capability text)
_native_polycurve_sample_calls = 0


def _polycurve_sample_call(fn, record, n):
    """Rust's samples of one PolyCurve record, or None when the export refuses it."""
    out = array.array("d", bytes(8 * 3 * (n + 1)))
    code = fn(record, len(record), n, (ctypes.c_double * len(out)).from_buffer(out), len(out))
    if code != 0:
        return None
    return list(zip(out[0::3], out[1::3], out[2::3]))


def _polycurve_sample_witnesses():
    """Polycurves of irregular doubles, where a fused multiply-add would round most samples
    differently: a line, a polyline, a cubic and a rational quadratic joined on a shifted
    domain, so every segment's parameter is remapped; a polycurve nesting another, reversed;
    and a Trace Picture loop of cubic Beziers."""
    rng = random.Random(_POLYCURVE_SAMPLE_WITNESS_SEED)
    p = r3.Point3d

    def q():
        return p(rng.uniform(-5.0, 5.0), rng.uniform(-5.0, 5.0), rng.uniform(-1.0, 1.0))

    def interval():
        return r3.Interval(rng.uniform(-9.0, -1.0), rng.uniform(1.0, 9.0))

    joined = r3.PolyCurve()
    line = r3.LineCurve(q(), q())
    line.Domain = interval()
    joined.Append(line)
    joined.Append(r3.PolylineCurve([joined.PointAtEnd, q(), q(), q()]))
    cubic = r3.NurbsCurve.CreateControlPointCurve([joined.PointAtEnd, q(), q(), q(), q()], 3)
    cubic.Domain = interval()
    joined.Append(cubic)
    conic = r3.NurbsCurve(3, True, 3, 3)
    for i, a in enumerate((joined.PointAtEnd, q(), q())):
        w = rng.uniform(0.3, 2.0) if i == 1 else 1.0
        conic.Points[i] = r3.Point4d(a.X * w, a.Y * w, a.Z * w, w)
    for i, k in enumerate((0.0, 0.0, 1.0, 1.0)):
        conic.Knots[i] = k
    joined.Append(conic)
    joined.Domain = r3.Interval(rng.uniform(-80.0, -20.0), rng.uniform(10.0, 90.0))

    nested = r3.PolyCurve()
    nested.Append(r3.LineCurve(q(), joined.PointAtStart))
    nested.Append(joined)
    nested.Reverse()

    loop = r3.PolyCurve()
    start = q()
    for _ in range(4):
        bezier = r3.NurbsCurve(3, False, 4, 4)
        for i, a in enumerate((loop.PointAtEnd if loop.SegmentCount else start, q(), q(), q())):
            bezier.Points[i] = r3.Point4d(a.X, a.Y, a.Z, 1.0)
        for i, k in enumerate((0.0, 0.0, 0.0, 1.0, 1.0, 1.0)):
            bezier.Knots[i] = k
        loop.Append(bezier)
    return [joined, nested, loop]


def _polycurve_sample_parity(fn):
    """True when the loaded rhino3dm's PointAt gives Rust's bits on every witness polycurve."""
    try:
        for curve in _polycurve_sample_witnesses():
            record = base64.b64decode(curve.Encode()["data"])
            for n in _POLYCURVE_SAMPLE_WITNESS_N:
                got = _polycurve_sample_call(fn, record, n)
                if got is None or _packed(got) != _packed(_point_at_samples(curve, n)):
                    return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _polycurve_sample_binding():
    global _polycurve_sample_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _polycurve_sample_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _polycurve_sample_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "polycurve samples export unavailable; polycurves use PointAt"
        if signature[1] not in POLYCURVE_SAMPLES_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; polycurves use PointAt".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_polycurve_samples
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, _D,
                               ctypes.c_size_t]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _polycurve_sample_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} PointAt differs from "
                        "the Rust polycurve samples on the witness polycurves; polycurves "
                        "use PointAt")
            elif fn is not None:
                text = "qualified"
        _polycurve_sample_state = signature, fn, text
        return fn


def polycurve_sample_capability():
    """``"qualified"`` when polycurves are sampled in Rust, otherwise the reason they are not."""
    _polycurve_sample_binding()
    return _polycurve_sample_state[2]


def _polycurve_samples(curve, n):
    """`sample` for a PolyCurve: None (unqualified, too many spans for its record to pay, or
    refused by the export) keeps PointAt."""
    global _native_polycurve_sample_calls
    fn = _polycurve_sample_binding()
    if fn is None:
        return None
    try:
        n = int(n)
        if not 1 <= n <= 1 << 20 or POLYCURVE_SAMPLES_PER_SPAN * curve.SpanCount > n + 1:
            return None
        got = _polycurve_sample_call(fn, base64.b64decode(curve.Encode()["data"]), n)
    except Exception:  # noqa: BLE001 -- PointAt remains the route
        return None
    if got is not None:
        _native_polycurve_sample_calls += 1
    return got


# Free-form offsets (narwhal_pure_core_offset_fit, native/pure_core/src/offset.rs and
# offset_boundary.rs): `offset._compact_offset` for a NurbsCurve read from its Encode() record,
# both the smooth interpolate-and-check loop and the boundary-preserving fit for a C0 corner,
# an offset cusp or a kinked closed seam. The loops' PointAt / TangentAt / CurvatureAt calls
# are openNURBS's Evaluate, EvTangent and EvCurvature (eb92af3) in Rust, and so are Trim, the
# Sharp apex's line-line intersection, and the fit's SetDomain, IsValid and IsClosed; an SDK
# build that fuses a multiply-add in any of them rounds differently. So the route needs BOTH a
# platform in OFFSET_QUALIFIED_PLATFORMS and the loaded rhino3dm passing `_offset_parity`: the
# Python route on its own SDK gives Rust's answer, bit for bit, on the witness curves. Rust
# refuses whatever it does not decide exactly (a degenerate tangent, a near-singular apex,
# ...), and the Python route runs.
OFFSET_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
OFFSET_MAX_FIT_CVS = 125        # a closed cubic through 122 points; offset.rs MAX_FIT_CVS
OFFSET_MAX_FIT_KNOTS = 127
_OFFSET_ROOM_LIMIT = 1 << 22    # control points of the largest fit taken from the export
_OFFSET_FIT, _OFFSET_NO_FIT, _OFFSET_REFUSED, _OFFSET_ROOM = 0, 2, 3, 4
_OFFSET_WITNESS_SEED = 20261124
_offset_state = None            # (signature, binding or None, capability text)
_native_offset_calls = 0


def _offset_call(fn, curve, distance, n, tolerance):
    """``(fit, deviation)``; ``()`` when no smooth fit meets the tolerance; ``(None, 0.0)``
    when the boundary-preserving fit refuses; or None when the export leaves the decision to
    Python."""
    distance, tolerance = float(distance), float(tolerance)
    n = tuple(float(c) for c in n)
    if (len(n) != 3 or not all(math.isfinite(c) for c in n) or not math.isfinite(distance)
            or abs(distance) < 1e-15 or math.isnan(tolerance)):
        return None
    record = base64.b64decode(curve.Encode()["data"])
    room = (OFFSET_MAX_FIT_CVS, OFFSET_MAX_FIT_KNOTS)
    for _ in range(2):
        cvs = array.array("d", bytes(8 * 3 * room[0]))
        knots = array.array("d", bytes(8 * room[1]))
        counts = (ctypes.c_uint64 * 2)()
        deviation = ctypes.c_double()
        code = fn(record, len(record), distance, (ctypes.c_double * 3)(*n), tolerance,
                  (ctypes.c_double * len(cvs)).from_buffer(cvs), len(cvs),
                  (ctypes.c_double * len(knots)).from_buffer(knots), len(knots),
                  counts, ctypes.byref(deviation))
        if code != _OFFSET_ROOM:
            break
        m, k = counts[0], counts[1]
        if not room[0] < m <= _OFFSET_ROOM_LIMIT or k != m + 2:
            return None
        room = (m, k)
    if code == _OFFSET_NO_FIT:
        return ()
    if code == _OFFSET_REFUSED:
        return None, 0.0
    m, k = counts[0], counts[1]
    if code != _OFFSET_FIT or not 4 <= m <= room[0] or k != m + 2:
        return None
    # As `interpolate.interpolate_curve` builds it; the knots are already in the source domain.
    fit = r3.NurbsCurve(3, False, 4, m)
    for i in range(m):
        fit.Points[i] = r3.Point4d(cvs[3 * i], cvs[3 * i + 1], cvs[3 * i + 2], 1.0)
    for i in range(k):
        fit.Knots[i] = knots[i]
    return fit, deviation.value


def _offset_witnesses():
    """``(curve, distance, n, tolerance)``: irregular doubles, where a fused multiply-add would
    round most products differently. An open cubic on a shifted domain in a tilted plane (its
    weighted points are divided out, as it is not rational), and a closed periodic cubic, each
    offset without a cusp; a rational cubic of three
    pieces whose C0 corners turn a few degrees left then right, so the offset trims two pieces
    back to one Sharp apex and adds two straight segments to the other; and a wave offset
    past its radius of curvature, which cusps twice."""
    rng = random.Random(_OFFSET_WITNESS_SEED)
    p = r3.Point3d
    wave = r3.NurbsCurve.CreateControlPointCurve(
        [p(i * 3.0 + rng.uniform(-0.3, 0.3), rng.uniform(-0.8, 0.8), rng.uniform(-0.1, 0.1))
         for i in range(5)], 3)
    for i in range(1, 4):
        q = wave.Points[i]
        w = rng.uniform(0.6, 1.6)
        wave.Points[i] = r3.Point4d(q.X * w, q.Y * w, q.Z * w, w)
    wave.Domain = r3.Interval(rng.uniform(-9.0, -1.0), rng.uniform(1.0, 9.0))
    tilt = (rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02), 1.0)
    m = math.sqrt(tilt[0] * tilt[0] + tilt[1] * tilt[1] + tilt[2] * tilt[2])
    loop = r3.NurbsCurve.Create(True, 3, [
        p(math.cos(2 * math.pi * i / 7) * rng.uniform(9.0, 11.0),
          math.sin(2 * math.pi * i / 7) * rng.uniform(9.0, 11.0), 0.0) for i in range(7)])
    witnesses = [(wave, rng.uniform(-0.5, -0.3), tuple(c / m for c in tilt), 0.05),
                 (loop, rng.uniform(0.8, 1.0), (0.0, 0.0, 1.0), 0.05)]
    corners = [(0.0, 0.0)]
    heading = 0.0
    for turn in (0.0, rng.uniform(5.0, 7.0), -rng.uniform(5.0, 7.0)):
        heading += math.radians(turn)
        for k in range(3):
            step = rng.uniform(2.5, 3.5)
            bend = math.radians(rng.uniform(-0.3, 0.3)) if k == 1 else 0.0
            x, y = corners[-1]
            corners.append((x + step * math.cos(heading + bend),
                            y + step * math.sin(heading + bend)))
    zigzag = r3.NurbsCurve(3, True, 4, len(corners))
    for i, (x, y) in enumerate(corners):
        w = 1.0 if i % 3 == 0 else rng.uniform(0.8, 1.25)
        zigzag.Points[i] = r3.Point4d(x * w, y * w, 0.0, w)
    for i, k in enumerate((0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0)):
        zigzag.Knots[i] = k
    zigzag.Domain = r3.Interval(rng.uniform(-5.0, -1.0), rng.uniform(2.0, 6.0))
    crests = r3.NurbsCurve.CreateControlPointCurve(
        [p(i * 10.0 + rng.uniform(-1.0, 1.0), math.sin(i * 0.7) * rng.uniform(6.0, 8.0), 0.0)
         for i in range(6)], 3)
    return witnesses + [(zigzag, rng.uniform(0.6, 0.9), (0.0, 0.0, 1.0), 0.05),
                        (crests, -rng.uniform(40.0, 50.0), (0.0, 0.0, 1.0), 0.05)]


def _offset_bits(curve, deviation):
    return (b"".join(struct.pack("<3d", p.X, p.Y, p.Z) for p in curve.Points)
            + struct.pack(f"<{len(curve.Knots)}d", *curve.Knots)
            + struct.pack("<d", deviation))


def _offset_parity(fn, reference):
    """True when `reference` (the Python route) on the loaded rhino3dm gives Rust's fit, bit
    for bit, on every witness curve."""
    try:
        for curve, distance, n, tolerance in _offset_witnesses():
            got = _offset_call(fn, curve, distance, n, tolerance)
            want = reference(curve, distance, n, tolerance)
            if (not got or got[0] is None or want is None or want.curve is None
                    or _offset_bits(*got) != _offset_bits(want.curve, want.max_deviation)):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _offset_binding(reference):
    global _offset_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _offset_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _offset_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "offset fit export unavailable; offsets use the Python route"
        if signature[1] not in OFFSET_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; offsets use the Python route".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_offset_fit
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_double, _D,
                               ctypes.c_double, _D, ctypes.c_size_t, _D, ctypes.c_size_t,
                               ctypes.POINTER(ctypes.c_uint64), _D]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _offset_parity(fn, reference):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} differs from the Rust "
                        "offset fit on the witness curves; offsets use the Python route")
            elif fn is not None:
                text = "qualified"
        _offset_state = signature, fn, text
        return fn


def offset_capability(reference):
    """``"qualified"`` when free-form offsets are fitted in Rust, otherwise the reason.
    `reference` is the Python route the one-time parity check compares with."""
    _offset_binding(reference)
    return _offset_state[2]


def offset_fit(curve, distance, n, tolerance, reference):
    """`offset._compact_offset` for a free-form `NurbsCurve` through Rust: ``(fit, deviation)``,
    ``()`` where it returns None, ``(None, 0.0)`` where it returns the boundary-preserving
    fit's refusal, or None where `reference` (the Python route) must decide."""
    global _native_offset_calls
    if not isinstance(curve, r3.NurbsCurve):
        return None
    fn = _offset_binding(reference)
    if fn is None:
        return None
    try:
        got = _offset_call(fn, curve, distance, n, tolerance)
    except Exception:  # noqa: BLE001 -- the Python route decides
        return None
    if got is not None:
        _native_offset_calls += 1
    return got


# Surface offsets (narwhal_pure_core_surface_offset, native/pure_core/src/surface_offset.rs):
# `offset.surface_offset`'s refinement loop for a NurbsSurface read from its Encode() record.
# The loop's PointAt / NormalAt / InsertKnot calls are openNURBS's ON_NurbsSurface::Evaluate,
# ON_Surface::EvNormal (with its singular-point branch) and ON_NurbsSurface::InsertKnot
# (eb92af3) in Rust; an SDK build that fuses a multiply-add in any of them rounds
# differently. So the route needs BOTH a platform in SURFACE_OFFSET_QUALIFIED_PLATFORMS and
# the loaded rhino3dm passing `_surface_offset_parity`: the Python route on its own SDK gives
# Rust's surface, bit for bit, on the witness surfaces. Rust refuses whatever it does not
# decide exactly (a periodic knot vector, a zero weight, ...), and the Python route runs.
SURFACE_OFFSET_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
_SURFACE_OFFSET_ROOM = 1 << 19     # bytes of the first record buffer
_SURFACE_OFFSET_LIMIT = 1 << 27    # bytes of the largest record taken from the export
_SURFACE_FIT, _SURFACE_FOLDED, _SURFACE_ROOM, _SURFACE_APPROXIMATE = 0, 3, 4, 5
_SURFACE_WITNESS_SEED = 20261126
_surface_offset_state = None       # (signature, binding or None, capability text)
_native_surface_offset_calls = 0


def _surface_offset_call(fn, surface, distance, tolerance, max_refine):
    """``(offset surface, deviation, within tolerance)``, ``(None, 0.0, False)`` when the
    offset inverts the surface, or None when the export leaves the decision to Python. The
    export writes the offset surface as an archive record, which `CommonObject.Decode` reads
    in one call."""
    distance, tolerance, max_refine = float(distance), float(tolerance), int(max_refine)
    if (not math.isfinite(distance) or abs(distance) < 1e-15 or math.isnan(tolerance)
            or not 0 <= max_refine <= 10):
        return None
    encoded = surface.Encode()
    record = base64.b64decode(encoded["data"])
    room = _SURFACE_OFFSET_ROOM
    for _ in range(2):
        out = ctypes.create_string_buffer(room)
        length = ctypes.c_uint64()
        deviation = ctypes.c_double()
        code = fn(record, len(record), distance, tolerance, max_refine, out, room,
                  ctypes.byref(length), ctypes.byref(deviation))
        if code != _SURFACE_ROOM:
            break
        if not room < length.value <= _SURFACE_OFFSET_LIMIT:
            return None
        room = length.value
    if code == _SURFACE_FOLDED:
        return None, 0.0, False
    if code not in (_SURFACE_FIT, _SURFACE_APPROXIMATE) or not 0 < length.value <= room:
        return None
    result = r3.CommonObject.Decode({**encoded, "data": base64.b64encode(
        out.raw[:length.value]).decode("ascii")})
    if not isinstance(result, r3.NurbsSurface):
        return None
    return result, deviation.value, code == _SURFACE_FIT


def _surface_offset_witnesses():
    """``(surface, distance, tolerance, max_refine)``: irregular doubles, where a fused
    multiply-add would round most products differently. A rational sphere off the origin,
    whose poles take EvNormal's singular-point branch, and a non-rational cubic-by-quadratic
    bump on non-uniform knots in shifted domains, each refined at least once."""
    rng = random.Random(_SURFACE_WITNESS_SEED)
    sphere = r3.Sphere(r3.Point3d(rng.uniform(-3.0, 3.0), rng.uniform(-3.0, 3.0), 0.0),
                       rng.uniform(4.0, 6.0)).ToNurbsSurface()
    bump = r3.NurbsSurface.Create(3, False, 4, 3, 6, 5)
    for i in range(6):
        for j in range(5):
            bump.Points[i, j] = r3.Point4d(i * rng.uniform(1.8, 2.2), j * rng.uniform(1.8, 2.2),
                                           math.sin(i * 0.9) * math.cos(j * 0.7)
                                           * rng.uniform(0.4, 0.6), 1.0)
    for knots, order, count in ((bump.KnotsU, 4, 6), (bump.KnotsV, 3, 5)):
        inner = sorted(rng.uniform(0.5, 9.5) for _ in range(count - order))
        a, b = rng.uniform(-9.0, -1.0), rng.uniform(2.0, 9.0)
        for i, k in enumerate([0.0] * (order - 1) + inner + [10.0] * (order - 1)):
            knots[i] = a + (b - a) * k / 10.0
    return [(sphere, rng.uniform(0.6, 0.9), 3e-2, 6),
            (bump, -rng.uniform(0.3, 0.5), 6e-2, 6)]


def _surface_bits(surface, deviation):
    size = 4 if surface.IsRational else 3
    out = []
    for i in range(surface.Points.CountU):
        for j in range(surface.Points.CountV):
            p = surface.Points[i, j]
            out += (p.X, p.Y, p.Z, p.W)[:size]
    out += list(surface.KnotsU.ToList()) + list(surface.KnotsV.ToList()) + [deviation]
    return struct.pack(f"<{len(out)}d", *out)


def _surface_offset_parity(fn, reference):
    """True when `reference` (the Python route) on the loaded rhino3dm gives Rust's surface,
    bit for bit, on every witness surface."""
    try:
        for surface, distance, tolerance, max_refine in _surface_offset_witnesses():
            got = _surface_offset_call(fn, surface, distance, tolerance, max_refine)
            want = reference(surface, distance, tolerance, max_refine)
            if (got is None or got[0] is None or want[0] is None or got[2] != want[2]
                    or _surface_bits(got[0], got[1]) != _surface_bits(want[0], want[1])):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _surface_offset_binding(reference):
    global _surface_offset_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _surface_offset_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _surface_offset_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "surface offset export unavailable; surface offsets use the Python route"
        if signature[1] not in SURFACE_OFFSET_QUALIFIED_PLATFORMS:
            text = ("not qualified on {}/{}; surface offsets use the Python route"
                    .format(*signature[1]))
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_surface_offset
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_double,
                               ctypes.c_double, ctypes.c_uint32, ctypes.c_char_p,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64), _D]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _surface_offset_parity(fn, reference):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} differs from the Rust "
                        "surface offset on the witness surfaces; surface offsets use the "
                        "Python route")
            elif fn is not None:
                text = "qualified"
        _surface_offset_state = signature, fn, text
        return fn


def surface_offset_capability(reference):
    """``"qualified"`` when surface offsets run in Rust, otherwise the reason. `reference` is
    the Python route the one-time parity check compares with."""
    _surface_offset_binding(reference)
    return _surface_offset_state[2]


def surface_offset_fit(surface, distance, tolerance, max_refine, reference):
    """`offset._python_surface_offset` for a `NurbsSurface` through Rust: ``(offset surface,
    deviation, within tolerance)``, ``(None, 0.0, False)`` where the offset inverts the
    surface, or None where `reference` (the Python route) must decide."""
    global _native_surface_offset_calls
    if not isinstance(surface, r3.NurbsSurface):
        return None
    fn = _surface_offset_binding(reference)
    if fn is None:
        return None
    try:
        got = _surface_offset_call(fn, surface, distance, tolerance, max_refine)
    except Exception:  # noqa: BLE001 -- the Python route decides
        return None
    if got is not None:
        _native_surface_offset_calls += 1
    return got


# Surface display grids (narwhal_pure_core_surface_grid, native/pure_core/src/surface_offset.rs):
# `bridge._build_grid`'s PointAt / NormalAt at every node of a NurbsSurface's display grid, read
# from its Encode() record with the same openNURBS ports as the surface offset. A BrepFace is
# evaluated through its UnderlyingSurface only where the face itself gives the same bits at a
# few nodes (openNURBS may hold a face's surface transposed, which rhino3dm does not expose).
# Gated by SURFACE_GRID_QUALIFIED_PLATFORMS and the loaded rhino3dm passing
# `_surface_grid_parity` on the surface offset witnesses.
SURFACE_GRID_QUALIFIED_PLATFORMS = frozenset({("linux", "x86_64")})
_SURFACE_GRID_MAX_NODES = 1 << 20
_surface_grid_state = None          # (signature, binding or None, capability text)
_native_surface_grid_calls = 0


def _point_normal_grid(surface, us, vs):
    """The Python route: rhino3dm's PointAt / NormalAt at `us` x `vs`, as tuples."""
    points, normals = [], []
    for u in us:
        prow, nrow = [], []
        for v in vs:
            p, n = surface.PointAt(u, v), surface.NormalAt(u, v)
            prow.append((p.X, p.Y, p.Z))
            nrow.append((n.X, n.Y, n.Z))
        points.append(prow)
        normals.append(nrow)
    return points, normals


def _surface_grid_call(fn, surface, us, vs):
    """``(points, normals)`` rows of ``(x, y, z)`` for `surface` when its record is an
    ON_NurbsSurface, or None."""
    us, vs = [float(u) for u in us], [float(v) for v in vs]
    nu, nv = len(us), len(vs)
    if not nu or not nv or nu * nv > _SURFACE_GRID_MAX_NODES:
        return None
    record = base64.b64decode(surface.Encode()["data"])
    points = array.array("d", bytes(24 * nu * nv))
    normals = array.array("d", bytes(24 * nu * nv))
    code = fn(record, len(record), (ctypes.c_double * nu)(*us), nu, (ctypes.c_double * nv)(*vs),
              nv, (ctypes.c_double * len(points)).from_buffer(points),
              (ctypes.c_double * len(normals)).from_buffer(normals), len(points))
    if code != 0:
        return None
    rows = lambda flat: [[tuple(flat[3 * (i * nv + j):3 * (i * nv + j) + 3]) for j in range(nv)]
                         for i in range(nu)]
    return rows(points), rows(normals)


def _grid_bits(grid):
    return _packed(p for matrix in grid for row in matrix for p in row)


def _surface_grid_parity(fn):
    """True when the loaded rhino3dm's PointAt / NormalAt give Rust's bits on the surface offset
    witnesses, over grids that include their edges (the sphere's poles)."""
    try:
        for surface, *_ in _surface_offset_witnesses():
            du, dv = surface.Domain(0), surface.Domain(1)
            us = [du.T0 + (du.T1 - du.T0) * i / 7 for i in range(8)]
            vs = [dv.T0 + (dv.T1 - dv.T0) * j / 5 for j in range(6)]
            got = _surface_grid_call(fn, surface, us, vs)
            if got is None or _grid_bits(got) != _grid_bits(_point_normal_grid(surface, us, vs)):
                return False
    except Exception:  # noqa: BLE001 -- an SDK that cannot be checked is not qualified
        return False
    return True


def _surface_grid_binding():
    global _surface_grid_state
    signature = (os.environ.get(_ENV) or "<packaged>", _here(), id(r3))
    state = _surface_grid_state
    if state is not None and state[0] == signature:
        return state[1]
    with _lock:
        state = _surface_grid_state
        if state is not None and state[0] == signature:
            return state[1]
        fn, text = None, "surface grid export unavailable; surface display uses PointAt"
        if signature[1] not in SURFACE_GRID_QUALIFIED_PLATFORMS:
            text = "not qualified on {}/{}; surface display uses PointAt".format(*signature[1])
        else:
            explicit = os.environ.get(_ENV)
            path = (Path(explicit).expanduser() if explicit else
                    Path(__file__).resolve().parent / "native" / _library_name())
            try:
                fn = ctypes.CDLL(str(path)).narwhal_pure_core_surface_grid
                fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, _D, ctypes.c_size_t, _D,
                               ctypes.c_size_t, _D, _D, ctypes.c_size_t]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                fn = None
            if fn is not None and not _surface_grid_parity(fn):
                fn = None
                text = (f"rhino3dm {getattr(r3, '__version__', '?')} differs from the Rust "
                        "surface grid on the witness surfaces; surface display uses PointAt")
            elif fn is not None:
                text = "qualified"
        _surface_grid_state = signature, fn, text
        return fn


def surface_grid_capability():
    """``"qualified"`` when surface display grids are evaluated in Rust, otherwise the reason."""
    _surface_grid_binding()
    return _surface_grid_state[2]


def surface_grid(surface, us, vs):
    """`_point_normal_grid(surface, us, vs)` through Rust for a NurbsSurface, or a BrepFace whose
    underlying NurbsSurface gives the face's own bits; None where PointAt must run."""
    global _native_surface_grid_calls
    fn = _surface_grid_binding()
    if fn is None:
        return None
    try:
        face = isinstance(surface, r3.BrepFace)
        # rhino3dm hands a face's surface back as a plain Surface; its record still names
        # its class, and the export reads only ON_NurbsSurface records.
        source = surface.UnderlyingSurface() if face else surface
        if not isinstance(source, r3.Surface):
            return None
        got = _surface_grid_call(fn, source, us, vs)
        if got is None:
            return None
        if face:
            # The face must evaluate as its surface: at an off-diagonal interior node and at
            # the grid's corners and middle.
            du, dv = surface.Domain(0), surface.Domain(1)
            if (du.T0, du.T1, dv.T0, dv.T1) != tuple(
                    x for d in (source.Domain(0), source.Domain(1)) for x in (d.T0, d.T1)):
                return None
            probe = ([du.T0 + (du.T1 - du.T0) * 0.3], [dv.T0 + (dv.T1 - dv.T0) * 0.7])
            witness = _surface_grid_call(fn, source, *probe)
            if witness is None or _grid_bits(witness) != _grid_bits(
                    _point_normal_grid(surface, *probe)):
                return None
            nu, nv = len(us), len(vs)
            for i, j in {(0, 0), (nu - 1, nv - 1), (nu // 2, nv // 3), (nu // 3, nv - 1)}:
                p, n = surface.PointAt(us[i], vs[j]), surface.NormalAt(us[i], vs[j])
                if _packed((got[0][i][j], got[1][i][j])) != _packed(
                        ((p.X, p.Y, p.Z), (n.X, n.Y, n.Z))):
                    return None
    except Exception:  # noqa: BLE001 -- PointAt decides
        return None
    _native_surface_grid_calls += 1
    return got
