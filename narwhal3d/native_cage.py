"""ctypes adapter for the Rust CageEdit volume (``native/pure_core/src/cage.rs``).

There is deliberately no Python copy of the deformation: CageEdit is Rust-only, and it
reports that it is unavailable when the pure core cannot be loaded.
"""
from __future__ import annotations

import array
import ctypes
import math
import operator

from . import native_surface


def _bind():
    lib = native_surface._lib()
    if lib is None:
        return None
    try:
        rest = lib.narwhal_pure_core_cage_rest_points
        deform = lib.narwhal_pure_core_cage_deform
    except AttributeError:
        return None
    d = ctypes.POINTER(ctypes.c_double)
    u = ctypes.POINTER(ctypes.c_uint64)
    rest.argtypes = [d, u, d, ctypes.c_size_t]
    rest.restype = ctypes.c_int64
    deform.argtypes = [d, u, d, d, ctypes.c_size_t, d]
    deform.restype = ctypes.c_int64
    return rest, deform


def available():
    return _bind() is not None


# Mirror the ABI's bounds BEFORE allocating caller-owned buffers. Geometry validation
# remains in Rust; Python must establish the sizes of the memory Rust may read.
_MAX_COUNT = 64
_MAX_POINTS = 10_000_000
_INPUT_ERRORS = (TypeError, ValueError, OverflowError, LookupError)


def _xyz(point):
    if isinstance(point, (str, bytes)) or len(point) != 3:
        raise ValueError("expected an xyz triple")
    xyz = tuple(float(point[i]) for i in range(3))
    if not all(math.isfinite(c) for c in xyz):
        raise ValueError("expected finite coordinates")
    return xyz


def _frame(origin, axes, degrees, counts):
    try:
        if len(axes) != 3 or len(degrees) != 3 or len(counts) != 3:
            return None
        deg = tuple(operator.index(degrees[i]) for i in range(3))
        cnt = tuple(operator.index(counts[i]) for i in range(3))
        if any(not 1 <= n <= _MAX_COUNT or not 0 <= d < n for d, n in zip(deg, cnt)):
            return None
        f = array.array("d", [*_xyz(origin), *_xyz(axes[0]),
                              *_xyz(axes[1]), *_xyz(axes[2])])
        s = array.array("Q", [*deg, *cnt])
    except _INPUT_ERRORS:
        return None
    return (f, (ctypes.c_double * 12).from_buffer(f),
            s, (ctypes.c_uint64 * 6).from_buffer(s))


def rest_points(origin, axes, degrees, counts):
    """The undeformed lattice as a list of xyz tuples, or None when unavailable/invalid."""
    bound = _bind()
    if bound is None:
        return None
    frame = _frame(origin, axes, degrees, counts)
    if frame is None:
        return None
    keep_f, f, keep_s, s = frame
    total = keep_s[3] * keep_s[4] * keep_s[5]
    out = array.array("d", [0.0]) * (3 * total)
    n = bound[0](f, s, (ctypes.c_double * len(out)).from_buffer(out), len(out))
    if n != total:
        return None
    return [tuple(out[3 * i:3 * i + 3]) for i in range(total)]


def deform(origin, axes, degrees, counts, lattice, points):
    """Deformed copies of `points` through `lattice`, or None when unavailable/invalid."""
    bound = _bind()
    if bound is None:
        return None
    frame = _frame(origin, axes, degrees, counts)
    if frame is None:
        return None
    keep_f, f, keep_s, s = frame
    total = keep_s[3] * keep_s[4] * keep_s[5]
    try:
        count = len(points)
        if len(lattice) != total or count > _MAX_POINTS:
            return None
        cage = array.array("d", (c for p in lattice for c in _xyz(p)))
        pts = array.array("d", (c for p in points for c in _xyz(p)))
    except _INPUT_ERRORS:
        return None
    if len(cage) != 3 * total or len(pts) != 3 * count:
        return None
    if count == 0:
        return []
    out = array.array("d", [0.0]) * len(pts)
    inside = bound[1](f, s, (ctypes.c_double * len(cage)).from_buffer(cage),
                      (ctypes.c_double * len(pts)).from_buffer(pts), count,
                      (ctypes.c_double * len(out)).from_buffer(out))
    if inside < 0:
        return None
    return [tuple(out[3 * i:3 * i + 3]) for i in range(count)]
