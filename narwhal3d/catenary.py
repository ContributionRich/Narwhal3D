"""Tolerance-qualified smooth NURBS approximation of a symmetric catenary."""
from __future__ import annotations

import math

from . import coil

_NATIVE_MIN_SPANS = 8


def _add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _scale(a, value):
    return tuple(a[i] * value for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _length(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    length = _length(a)
    if length <= 1e-12:
        raise ValueError("direction must be non-zero")
    return _scale(a, 1.0 / length)


def _bezier(control, t):
    s = 1.0 - t
    return tuple(control[0][i] * s**3 + control[1][i] * 3*s*s*t
                 + control[2][i] * 3*s*t*t + control[3][i] * t**3
                 for i in range(3))


def python_plan(start, end, axis_point, through, tolerance=0.01):
    start, end, axis_point, through = map(tuple, (start, end, axis_point, through))
    tolerance = float(tolerance)
    middle = _scale(_add(start, end), 0.5)
    gravity = _unit(_sub(axis_point, middle))
    chord = _sub(end, start)
    vertical = _dot(chord, gravity)
    horizontal_vector = _sub(chord, _scale(gravity, vertical))
    length = _length(horizontal_vector)
    if length <= 1e-12 or abs(vertical) > tolerance:
        raise ValueError("the supported catenary needs equal-height endpoints")
    horizontal = _scale(horizontal_vector, 1.0 / length)
    through_delta = _sub(through, middle)
    through_x = _dot(through_delta, horizontal)
    off_plane = _length(_sub(_sub(through_delta, _scale(horizontal, through_x)),
                             _scale(gravity, _dot(through_delta, gravity))))
    sag = _dot(through_delta, gravity)
    if abs(through_x) > tolerance or off_plane > tolerance or sag <= tolerance:
        raise ValueError("ThroughPoint must be the positive midpoint sag on the catenary axis")
    half = length * 0.5

    def value(a):
        z = half / a
        return math.inf if z > 700 else a * (math.cosh(z) - 1.0)

    low, high = half / 700.0, max(half, sag)
    while value(high) > sag:
        high *= 2.0
    for _ in range(100):
        mid = (low + high) * 0.5
        if value(mid) > sag:
            low = mid
        else:
            high = mid
    a = (low + high) * 0.5

    def analytic(x):
        point = _add(_add(middle, _scale(horizontal, x)),
                     _scale(gravity, a * (math.cosh(half/a) - math.cosh(x/a))))
        derivative = _sub(horizontal, _scale(gravity, math.sinh(x/a)))
        return point, derivative

    spans = 8
    while True:
        dx = length / spans
        poles, maximum = [], 0.0
        for span in range(spans):
            x0, x1 = -half + dx * span, -half + dx * (span + 1)
            p0, d0 = analytic(x0)
            p1, d1 = analytic(x1)
            control = (p0, _add(p0, _scale(d0, dx/3)),
                       _sub(p1, _scale(d1, dx/3)), p1)
            poles.extend(control if span == 0 else control[1:])
            for sample in range(1, 16):
                t = sample / 16
                maximum = max(maximum,
                              _length(_sub(_bezier(control, t), analytic(x0 + dx*t)[0])))
        if maximum <= tolerance:
            knots = [0.0] * 3
            for boundary in range(1, spans):
                knots.extend([boundary/spans] * 3)
            knots.extend([1.0] * 3)
            return tuple(value for point in poles for value in point), tuple(knots), spans, maximum
        if spans >= 128:
            raise ValueError("requested catenary tolerance was not achieved")
        spans *= 2


def plan(start, end, axis_point, through, tolerance=0.01, *, force_native=False):
    if force_native or 8 >= _NATIVE_MIN_SPANS:
        try:
            from . import native_coil
            native = native_coil.catenary_plan(start, end, axis_point, through, tolerance)
        except Exception:  # noqa: BLE001
            native = None
        if native is not None:
            return native
    return python_plan(start, end, axis_point, through, tolerance)


def make_curve(start, end, axis_point, through, tolerance=0.01, *, force_native=False):
    planned = plan(start, end, axis_point, through, tolerance, force_native=force_native)
    return coil.curve_from_plan(planned), planned[3]
