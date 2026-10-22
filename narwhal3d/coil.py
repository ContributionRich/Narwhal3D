"""Smooth tolerance-qualified NURBS construction for Helix and Spiral.

The reference CAD application's managed API documents its axis-based spiral
factory as a cubic C1 NURBS approximation. A finite NURBS cannot represent a circular helix exactly, so
this module constructs cubic Hermite spans from the analytic locus and reports
the independently sampled maximum deviation. The document geometry is always
one real NURBS curve, never the sampled viewport polyline.
"""
from __future__ import annotations

import math

import rhino3dm as r3

MAX_TURNS = 1000.0
MAX_SPANS_PER_TURN = 96
# Disabled until the complete native planner + validation + NURBS materialization
# is measured at least 10% faster than this complete Python fallback.
_NATIVE_MIN_SPANS = 6


def _add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _scale(a, value):
    return tuple(a[i] * value for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(value):
    length = math.sqrt(_dot(value, value))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("axis and radial direction must be non-zero")
    return _scale(value, 1.0 / length)


def _distance(a, b):
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def _frame(start, end, radial):
    axis_vector = _sub(end, start)
    axis = _unit(axis_vector)
    x = _unit(_sub(radial, _scale(axis, _dot(radial, axis))))
    y = _unit(_cross(axis, x))
    return axis, x, y, math.sqrt(_dot(axis_vector, axis_vector))


def _point_derivative(start, axis, x, y, axis_length,
                      radius0, radius1, theta_total, theta):
    fraction = theta / theta_total
    radius = radius0 + (radius1 - radius0) * fraction
    dr = (radius1 - radius0) / theta_total
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    radial = _add(_scale(x, cos_t), _scale(y, sin_t))
    tangent = _add(_scale(x, -sin_t), _scale(y, cos_t))
    point = _add(_add(start, _scale(radial, radius)),
                 _scale(axis, axis_length * fraction))
    derivative = _add(_add(_scale(radial, dr), _scale(tangent, radius)),
                      _scale(axis, axis_length / theta_total))
    return point, derivative


def _bezier(control, t):
    s = 1.0 - t
    return tuple(control[0][i] * s**3
                 + control[1][i] * 3.0 * s * s * t
                 + control[2][i] * 3.0 * s * t * t
                 + control[3][i] * t**3 for i in range(3))


def python_plan(start, end, radial, radius0, radius1, turns,
                reverse=False, tolerance=0.01, flat=False):
    """Return cubic poles, stored knots, spans and measured deviation."""
    start, end, radial = tuple(start), tuple(end), tuple(radial)
    radius0, radius1 = float(radius0), float(radius1)
    turns, tolerance = float(turns), float(tolerance)
    values = start + end + radial + (radius0, radius1, turns, tolerance)
    if (len(start) != 3 or len(end) != 3 or len(radial) != 3
            or not all(math.isfinite(value) for value in values)
            or turns <= 0.0 or turns > MAX_TURNS or tolerance <= 0.0):
        raise ValueError("coil parameters must be finite; turns and tolerance must be positive")
    axis, x, y, axis_length = _frame(start, end, radial)
    if flat:
        axis_length = 0.0
    theta_total = (-1.0 if reverse else 1.0) * math.tau * turns
    spans = max(1, math.ceil(turns * 6.0))
    maximum = max(1, math.ceil(turns * MAX_SPANS_PER_TURN))
    while True:
        h = theta_total / spans
        poles = []
        max_deviation = 0.0
        for span in range(spans):
            theta0, theta1 = h * span, h * (span + 1)
            p0, d0 = _point_derivative(start, axis, x, y, axis_length,
                                       radius0, radius1, theta_total, theta0)
            p1, d1 = _point_derivative(start, axis, x, y, axis_length,
                                       radius0, radius1, theta_total, theta1)
            control = (p0, _add(p0, _scale(d0, h / 3.0)),
                       _sub(p1, _scale(d1, h / 3.0)), p1)
            poles.extend(control if span == 0 else control[1:])
            for sample in range(1, 16):
                local = sample / 16.0
                expected = _point_derivative(
                    start, axis, x, y, axis_length, radius0, radius1,
                    theta_total, theta0 + h * local)[0]
                max_deviation = max(max_deviation,
                                    _distance(_bezier(control, local), expected))
        if max_deviation <= tolerance:
            knots = [0.0] * 3
            for boundary in range(1, spans):
                knots.extend([boundary / spans] * 3)
            knots.extend([1.0] * 3)
            return tuple(value for point in poles for value in point), \
                tuple(knots), spans, max_deviation
        if spans >= maximum:
            raise ValueError("requested coil tolerance was not achieved")
        spans = min(maximum, spans * 2)


def _native_plan(*args):
    try:
        from . import native_coil
        return native_coil.plan(*args)
    except Exception:  # noqa: BLE001 -- complete Python fallback is mandatory
        return None


def plan(start, end, radial, radius0, radius1, turns,
         reverse=False, tolerance=0.01, flat=False, *, force_native=False):
    estimated_spans = max(1, math.ceil(float(turns) * 6.0))
    if force_native or estimated_spans >= _NATIVE_MIN_SPANS:
        native = _native_plan(start, end, radial, radius0, radius1, turns,
                              reverse, tolerance, flat)
        if native is not None:
            return native
    return python_plan(start, end, radial, radius0, radius1, turns,
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
