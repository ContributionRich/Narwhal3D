"""Exact rational-quadratic conic construction for drawing commands."""
from __future__ import annotations

import math

import rhino3dm as r3


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _barycentric(point, a, b, c):
    v0, v1, v2 = _sub(b, a), _sub(c, a), _sub(point, a)
    d00, d01, d11 = _dot(v0, v0), _dot(v0, v1), _dot(v1, v1)
    d20, d21 = _dot(v2, v0), _dot(v2, v1)
    denominator = d00 * d11 - d01 * d01
    scale = max(d00 * d11, 1.0)
    if abs(denominator) <= 1e-14 * scale:
        raise ValueError("start, apex and end do not define a conic plane")
    lb = (d11 * d20 - d01 * d21) / denominator
    lc = (d00 * d21 - d01 * d20) / denominator
    la = 1.0 - lb - lc
    reconstructed = tuple(la * a[i] + lb * b[i] + lc * c[i] for i in range(3))
    if math.dist(reconstructed, point) > 1e-8 * max(1.0, math.dist(a, b), math.dist(a, c)):
        raise ValueError("the through point is not in the conic plane")
    return la, lb, lc


def rho_from_point(start, end, apex, point):
    """Return the rational conic rho whose segment passes through *point*."""
    l0, l1, l2 = _barycentric(point, start, apex, end)
    if min(l0, l1, l2) <= 0.0:
        raise ValueError("the through point must lie inside the conic control triangle")
    root0, root2 = math.sqrt(l0), math.sqrt(l2)
    t = root2 / (root0 + root2)
    base = 2.0 * t * (1.0 - t)
    denominator = l0 / ((1.0 - t) ** 2)
    weight = l1 * denominator / base
    rho = weight / (1.0 + weight)
    if not 0.0 < rho < 1.0:
        raise ValueError("the through point does not define 0 < rho < 1")
    return rho


def _unit(value):
    length = math.sqrt(_dot(value, value))
    if length <= 1e-12:
        raise ValueError("direction must be non-zero")
    return tuple(component / length for component in value)


def _quadratic(p0, p1, p2):
    curve = r3.NurbsCurve(3, False, 3, 3)
    for index, point in enumerate((p0, p1, p2)):
        curve.Points[index] = r3.Point4d(point[0], point[1], point[2], 1.0)
    curve.Knots[0] = curve.Knots[1] = 0.0
    curve.Knots[2] = curve.Knots[3] = 1.0
    if not curve.IsValid:
        raise ValueError("quadratic conic construction failed")
    return curve


def parabola_from_vertex(vertex, focus, end, half=False):
    """Exact finite parabola segment from vertex/focus and one end."""
    axis = _unit(_sub(focus, vertex))
    a = math.dist(vertex, focus)
    delta = _sub(end, vertex)
    axial = _dot(delta, axis)
    side_vector = _sub(delta, tuple(axial * component for component in axis))
    side = _unit(side_vector)
    transverse = _dot(delta, side)
    if axial <= 0.0 or abs(transverse) <= 1e-12:
        raise ValueError("end must lie off the axis and beyond the vertex")
    expected = transverse * transverse / (4.0 * a)
    if abs(axial - expected) > 1e-7 * max(1.0, axial, expected):
        # The picked end sets the finite extent; project it to the exact parabola
        # with the supplied vertex/focus rather than fitting a different conic.
        axial = expected
        end = tuple(vertex[i] + axial * axis[i] + transverse * side[i]
                    for i in range(3))
    u = transverse / (2.0 * a)
    if half:
        control = tuple(vertex[i] + a * u * side[i] for i in range(3))
        return _quadratic(vertex, control, end), vertex, focus
    other = tuple(vertex[i] + axial * axis[i] - transverse * side[i] for i in range(3))
    middle = tuple(vertex[i] - axial * axis[i] for i in range(3))
    return _quadratic(other, middle, end), vertex, focus


def parabola_from_three(start, end, vertex):
    """Exact symmetric parabola through two endpoints with a picked vertex."""
    midpoint = tuple((start[i] + end[i]) * 0.5 for i in range(3))
    axis_vector = _sub(midpoint, vertex)
    axis = _unit(axis_vector)
    axial = _dot(axis_vector, axis)
    half_chord = _sub(end, midpoint)
    transverse = math.sqrt(_dot(half_chord, half_chord))
    if transverse <= 1e-12 or axial <= 1e-12:
        raise ValueError("endpoints and vertex must define a non-degenerate symmetric parabola")
    if abs(_dot(half_chord, axis)) > 1e-8 * max(1.0, transverse):
        raise ValueError("vertex must lie on the perpendicular bisector of the endpoints")
    a = transverse * transverse / (4.0 * axial)
    focus = tuple(vertex[i] + a * axis[i] for i in range(3))
    curve, _vertex, _focus = parabola_from_vertex(vertex, focus, end, half=False)
    if curve.PointAtStart.DistanceTo(r3.Point3d(*start)) > 1e-8:
        curve.Reverse()
    return curve, focus


def parabola_from_focus(focus, direction, end, half=False):
    """Exact parabola where focus + direction establish the opening axis."""
    axis = _unit(_sub(direction, focus))
    delta = _sub(end, focus)
    x = _dot(delta, axis)
    transverse_vector = _sub(delta, tuple(x * component for component in axis))
    y = math.sqrt(_dot(transverse_vector, transverse_vector))
    if y <= 1e-12:
        raise ValueError("end must lie off the parabola axis")
    a = (-x + math.sqrt(x * x + y * y)) / 2.0
    if a <= 1e-12:
        raise ValueError("focus, direction and end do not define a parabola")
    vertex = tuple(focus[i] - a * axis[i] for i in range(3))
    projected_end = tuple(vertex[i] + (y * y / (4.0 * a)) * axis[i]
                          + transverse_vector[i] for i in range(3))
    return parabola_from_vertex(vertex, focus, projected_end, half=half)


def hyperbola_from_center(center, focus, end):
    """Exact rational-quadratic finite hyperbola branch."""
    axis = _unit(_sub(focus, center))
    c = math.dist(center, focus)
    delta = _sub(end, center)
    x = _dot(delta, axis)
    side_vector = _sub(delta, tuple(x * component for component in axis))
    y = math.sqrt(_dot(side_vector, side_vector))
    if x <= 0.0 or y <= 1e-12:
        raise ValueError("end must lie off the center-focus axis on the focus branch")
    side = _unit(side_vector)
    total = x * x + y * y + c * c
    discriminant = total * total - 4.0 * x * x * c * c
    if discriminant <= 0.0:
        raise ValueError("center, focus and end do not define a finite hyperbola")
    a2 = (total - math.sqrt(discriminant)) / 2.0
    b2 = c * c - a2
    if a2 <= 0.0 or b2 <= 0.0:
        raise ValueError("hyperbola coefficients are not positive")
    a, b = math.sqrt(a2), math.sqrt(b2)
    t = (y / b) / (x / a + 1.0)
    if not 0.0 < t < 1.0:
        raise ValueError("hyperbola end parameter is outside the finite branch")
    vertex = tuple(center[i] + a * axis[i] for i in range(3))
    control = tuple(center[i] + a * axis[i] + b * t * side[i] for i in range(3))
    end_weight = 1.0 - t * t
    curve = r3.NurbsCurve(3, True, 3, 3)
    curve.Points[0] = r3.Point4d(vertex[0], vertex[1], vertex[2], 1.0)
    curve.Points[1] = r3.Point4d(control[0], control[1], control[2], 1.0)
    curve.Points[2] = r3.Point4d(end[0] * end_weight, end[1] * end_weight,
                                end[2] * end_weight, end_weight)
    curve.Knots[0] = curve.Knots[1] = 0.0
    curve.Knots[2] = curve.Knots[3] = 1.0
    if not curve.IsValid:
        raise ValueError("hyperbola construction produced an invalid NURBS")
    return curve, a, b


def make_conic(start, end, apex, rho):
    """Create one exact rational quadratic conic segment."""
    rho = float(rho)
    if not math.isfinite(rho) or not 0.0 < rho < 1.0:
        raise ValueError("rho must be greater than 0 and less than 1")
    if math.dist(start, end) <= 1e-12:
        raise ValueError("start and end must be different")
    weight = rho / (1.0 - rho)
    curve = r3.NurbsCurve(3, True, 3, 3)
    for index, (point, w) in enumerate(((start, 1.0), (apex, weight), (end, 1.0))):
        curve.Points[index] = r3.Point4d(point[0] * w, point[1] * w, point[2] * w, w)
    curve.Knots[0] = curve.Knots[1] = 0.0
    curve.Knots[2] = curve.Knots[3] = 1.0
    if not curve.IsValid:
        raise ValueError("conic construction produced an invalid NURBS curve")
    return curve
