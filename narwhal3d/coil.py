"""Smooth tolerance-qualified NURBS construction for Helix and Spiral.

The reference CAD application's managed API documents its axis-based spiral
factory as a cubic C1 NURBS approximation. A finite NURBS cannot represent a
circular helix exactly, so the planner constructs cubic Hermite spans from the
analytic locus and reports the independently sampled maximum deviation. The
document geometry is always one real NURBS curve, never the sampled viewport
polyline.

The Rust planner is required. Numeric compatibility is checked against frozen
original CPython 3.13 answers; geometric accuracy remains tolerance-qualified,
not an assertion that a finite cubic NURBS is an exact helix.
"""
from __future__ import annotations

import rhino3dm as r3

MAX_TURNS = 1000.0
MAX_SPANS_PER_TURN = 96


def plan(start, end, radial, radius0, radius1, turns,
         reverse=False, tolerance=0.01, flat=False, *, force_native=False):
    """Return (poles, knots, spans, deviation) from the native Rust planner.

    Invalid parameters -- non-finite values, non-positive turns or tolerance,
    an unachievable tolerance, a degenerate frame -- are rejected with
    ``ValueError``. ``force_native`` remains a compatibility no-op; the
    pure-Python fallback was retired. Missing or incompatible native code raises
    ``RuntimeError``, never a parameter error or a silent fallback.
    """
    from . import native_coil
    return native_coil.plan(start, end, radial, radius0, radius1, turns,
                               reverse, tolerance, flat)


def curve_from_plan(planned):
    poles, knots, _spans, _deviation = planned
    count = len(poles) // 3
    if count < 4 or len(poles) % 3 or len(knots) != count + 2:
        raise ValueError("invalid coil NURBS plan")
    curve = r3.NurbsCurve(3, False, 4, count)
    for index in range(count):
        curve.Points[index] = r3.Point4d(
            poles[3 * index], poles[3 * index + 1], poles[3 * index + 2], 1.0)
    for index, value in enumerate(knots):
        curve.Knots[index] = value
    if not curve.IsValid:
        raise ValueError("coil NURBS construction failed")
    return curve


def make_curve(start, end, radial, radius0, radius1, turns,
               reverse=False, tolerance=0.01, flat=False, *, force_native=False):
    planned = plan(start, end, radial, radius0, radius1, turns,
                   reverse, tolerance, flat, force_native=force_native)
    return curve_from_plan(planned), planned[3]
