"""Required display sampler ABI. Python owns ctypes and arbitrary plane callbacks only.

Safety correction: malformed/nonfinite inputs, segment counts outside 1..100000,
and nonfinite computed coordinates are explicitly refused. No Python math replay.
"""
import array
import ctypes as C
import operator
import os
from pathlib import Path
import sys
import threading


class NativePreviewError(RuntimeError):
    """Missing/stale native library or invalid native protocol."""


class PreviewInputError(ValueError):
    """Unsafe, malformed or resource-excessive preview request."""


class Metadata(C.Structure):
    _fields_ = [("values", C.c_size_t), ("rows", C.c_size_t),
                ("lengths", C.c_size_t * 3)]


_cache = None
_lock = threading.Lock()
_native_calls = 0


def _binding():
    global _cache
    key = os.environ.get("NARWHAL_PURE_CORE") or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    name = ("narwhal_pure_core.dll" if sys.platform == "win32" else
            "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
            "libnarwhal_pure_core.so")
    path = Path(key).expanduser() if key != "<packaged>" else Path(__file__).resolve().parent / "native" / name
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        try:
            lib = C.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativePreviewError("required preview ABI version mismatch")
            fn = lib.narwhal_pure_core_preview_sample_v1
            fn.argtypes = [C.c_uint32, C.POINTER(C.c_double), C.c_size_t,
                           C.c_size_t, C.c_uint32, C.POINTER(C.c_double),
                           C.c_size_t, C.POINTER(Metadata)]
            fn.restype = C.c_int32
        except (OSError, AttributeError) as exc:
            raise NativePreviewError(f"required preview library missing or stale: {path}: {exc}") from exc
        _cache = key, fn
        return fn


def _point(p):
    try:
        values = tuple(p)
        if len(values) != 3:
            raise PreviewInputError("preview point must contain exactly three coordinates")
        return tuple(map(float, values))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreviewInputError("preview point must contain three real coordinates") from exc


def _scalar(value):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreviewInputError("preview scalar must be a real number") from exc


def _sample(op, data, segments, flags=0):
    global _native_calls
    fn = _binding()
    try:
        n = operator.index(segments)
    except TypeError as exc:
        raise PreviewInputError("preview segments must be an integer") from exc
    if op != 6 and not 1 <= n <= 100000:
        raise PreviewInputError("preview segments must be in 1..100000")
    values = array.array("d", data)
    # Buffer sizing is host marshalling, not sampling. Bound before allocating.
    capacity = (15 if op == 6 else (n + 1) * 9 if op == 5 else
                (n + 1) * 3 + 6 if op == 4 else (n + 1) * 3)
    output = array.array("d", bytes(capacity * 8))
    meta = Metadata()
    code = fn(op, (C.c_double * len(values)).from_buffer(values), len(values),
              n, flags, (C.c_double * capacity).from_buffer(output), capacity,
              C.byref(meta))
    if code == -2:
        raise PreviewInputError("native preview refused nonfinite, malformed or excessive input/output")
    if code != 0:
        raise NativePreviewError(f"native preview protocol status {code}")
    if (meta.rows > 3 or meta.values > capacity or
            sum(meta.lengths[:meta.rows]) * 3 != meta.values or
            any(meta.lengths[meta.rows:])):
        raise NativePreviewError("native preview returned invalid output metadata")
    rows, offset = [], 0
    for count in meta.lengths[:meta.rows]:
        stop = offset + count * 3
        rows.append(list(zip(output[offset:stop:3], output[offset+1:stop:3], output[offset+2:stop:3])))
        offset = stop
    _native_calls += 1
    return rows


def circle(plane, center, radius, segments):
    return _sample(1, _point(center) + _point(plane.xaxis) + _point(plane.yaxis) + (_scalar(radius),), segments)[0]


def ring(center, u, v, radius, segments):
    return _sample(2, _point(center) + _point(u) + _point(v) + (_scalar(radius),), segments)[0]


def polygon(plane, center, radius, sides, inscribed=True, *, start_angle=0.0, direction=1.0):
    # Old public helper returns [] for fewer than three sides, including negative.
    if sides < 3:
        return []
    return _sample(3, _point(center) + _point(plane.xaxis) + _point(plane.yaxis) +
                   (_scalar(radius), _scalar(start_angle), _scalar(direction)), sides, int(bool(inscribed)))[0]


def rectangle(plane, corner, opposite):
    local = _sample(6, _point(plane.from_world(corner)) +
                    _point(plane.from_world(opposite)), 0)[0]
    return [plane.to_world(*p) for p in local]


def picked_ellipse(points, cursor, segments):
    center = _point(points[0])
    axis = _point(points[1]) if len(points) > 1 else center
    axis2 = _point(points[2]) if len(points) > 2 else _point(cursor)
    rows = _sample(4, center + axis + axis2 + _point(cursor), segments, min(3, len(points)))
    # Construction lines preserve the host pick objects/types, not just coordinates.
    if len(points) == 1:
        return [[points[0], cursor]]
    if len(points) == 2:
        if len(rows[0]) == 2:
            return [[points[0], points[1]], [points[0], cursor]]
        return [rows[0], [points[0], tuple(cursor)]]
    return rows


def ellipsoid(args, cursor, plane, segments):
    center = _point(args[0])
    axis = _point(args[1]) if len(args) > 1 else center
    size = _scalar(args[2]) if len(args) > 2 else 0.0
    normal = _point(plane.zaxis) if len(args) > 1 else (0.0, 0.0, 0.0)
    rows = _sample(5, center + axis + _point(cursor) + normal +
                   (size,), segments, min(3, len(args)))
    if len(args) == 1:
        return [[args[0], tuple(cursor)]]
    if len(rows) == 1 and len(rows[0]) == 2:
        return [[args[0], args[1]]]
    return rows
