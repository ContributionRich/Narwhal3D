"""Tolerance-qualified symmetric catenary NURBS, planned by required Rust v2.

All valid numeric planning is Rust-owned. Frozen original CPython answers test
bitwise compatibility separately from geometric approximation tolerances.
OURS safety correction: NaN/inf, malformed triples, nonpositive tolerance and
nonfinite arithmetic are refused, never materialized as garbage geometry.
"""
from __future__ import annotations

from . import coil, native_coil


def plan(start, end, axis_point, through, tolerance=0.01, *, force_native=False):
    """Return poles/knots/spans/deviation; force_native is a compatibility no-op.

    Invalid parameters raise named ValueError; a missing or incompatible v2
    native library raises RuntimeError, never a silent Python fallback.
    """
    return native_coil.catenary_plan(start, end, axis_point, through, tolerance)


def make_curve(start, end, axis_point, through, tolerance=0.01, *, force_native=False):
    planned = plan(start, end, axis_point, through, tolerance, force_native=force_native)
    return coil.curve_from_plan(planned), planned[3]
