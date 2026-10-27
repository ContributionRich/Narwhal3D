"""ctypes adapter for the Rust CageEdit volume (``native/pure_core/src/cage.rs``).

There is deliberately no Python copy of the deformation: CageEdit is Rust-only, and it
reports that it is unavailable when the pure core cannot be loaded.
"""
from __future__ import annotations

import array
import ctypes

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


def _frame(origin, axes, degrees, counts):
    f = array.array("d", [*origin, *axes[0], *axes[1], *axes[2]])
    s = array.array("Q", [*degrees, *counts])
    return (f, (ctypes.c_double * 12).from_buffer(f),
            s, (ctypes.c_uint64 * 6).from_buffer(s))


def rest_points(origin, axes, degrees, counts):
    """The undeformed lattice as a list of xyz tuples, or None when unavailable/invalid."""
    bound = _bind()
    if bound is None:
        return None
    total = counts[0] * counts[1] * counts[2]
    keep_f, f, keep_s, s = _frame(origin, axes, degrees, counts)
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
    total = counts[0] * counts[1] * counts[2]
    if len(lattice) != total:
        return None
    if not points:
        return []
    keep_f, f, keep_s, s = _frame(origin, axes, degrees, counts)
    cage = array.array("d", (float(c) for p in lattice for c in p[:3]))
    pts = array.array("d", (float(c) for p in points for c in p[:3]))
    out = array.array("d", [0.0]) * len(pts)
    inside = bound[1](f, s, (ctypes.c_double * len(cage)).from_buffer(cage),
                      (ctypes.c_double * len(pts)).from_buffer(pts), len(points),
                      (ctypes.c_double * len(out)).from_buffer(out))
    if inside < 0:
        return None
    return [tuple(out[3 * i:3 * i + 3]) for i in range(len(points))]
