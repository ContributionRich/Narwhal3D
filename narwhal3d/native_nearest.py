"""Required native curve search; Python owns SDK callbacks and ctypes only.

Sampling remains the existing optional integration, unchanged below. Analytic
SDK shortcuts remain in nearest.py. No curve is converted to a different NURBS.
"""
from __future__ import annotations
import array
import ctypes
import math
import os
from pathlib import Path
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


def pack(curve):
    """Pack original NURBS data or retain ORIGINAL SDK object for callbacks."""
    _required_bindings()
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
    global _native_calls
    if isinstance(packed, CallbackCurve):
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
    global _native_many_calls
    if isinstance(packed, CallbackCurve):
        bounded = []
        for q in targets:
            if len(bounded) >= 100000:
                raise NearestInputError("nearest batch target limit 100000")
            bounded.append(q)
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

    Only a `NurbsCurve`, evaluated from its own knots and control points: each point
    is bit-identical to ``curve.PointAt`` at the same parameter. ``None`` (any other
    curve, no library, or a point openNURBS could not evaluate) keeps the caller's
    Python sampling.
    """
    global _native_sample_calls
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
