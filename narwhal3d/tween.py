"""TweenCurves: required Rust plans, with rhino3dm getters/construction only.

SamplePoints retains the existing equal-arc-length SDK sampling and NURBS
interpolation semantics; no tessellated geometry or replacement kernel is used.
"""
from __future__ import annotations

import rhino3dm as r3

from . import native_tween


def _data(curve):
    nurbs = curve.ToNurbsCurve()
    # SDK Point4d values are homogeneous. Rust owns dehomogenization,
    # weight validation, interpolation and rehomogenization in original order.
    points = [(point.X, point.Y, point.Z, point.W) for point in nurbs.Points]
    return nurbs, points, tuple(float(value) for value in nurbs.Knots.ToList())


def sample_point_tweens(first, second, number=1, sample_number=100, flip=False):
    """Tween incompatible curves through corresponding equal-length samples."""
    from . import arclength, interpolate
    number, sample_number = int(number), int(sample_number)
    if number < 1 or not 2 <= sample_number <= 9999:
        raise ValueError("Number must be >=1 and SampleNumber must be 2..9999")
    a, b = first.ToNurbsCurve(), second.ToNurbsCurve()
    if flip:
        b.Reverse()
    def samples(curve):
        total = arclength.length_of(curve)
        out=[]
        for distance in native_tween.sample_lengths(total, sample_number):
            parameter = arclength.parameter_at_length(curve, distance)
            point=curve.PointAt(parameter);out.append((point.X,point.Y,point.Z))
        return out
    pa, pb = samples(a), samples(b)
    rows = native_tween.interpolate(pa, pb, number, mode=2)
    curves = [interpolate.interpolate_curve(points, degree=3, style="Chord")
              for points in rows]
    return curves


def compatible_tweens(first, second, number=1, flip=False):
    """Return control-point tween NURBS for structurally compatible curves."""
    number = int(number)
    if number < 1:
        raise ValueError("Number must be at least 1")
    a = first.ToNurbsCurve()
    a.Domain = r3.Interval(0.0, 1.0)
    a, ap, ak = _data(a)
    b = second.ToNurbsCurve()
    if flip:
        b.Reverse()
    b.Domain = r3.Interval(0.0, 1.0)
    b, bp, bk = _data(b)
    if a.Degree != b.Degree or len(ap) != len(bp) or len(ak) != len(bk):
        raise ValueError("ControlPoint matching requires equal degree and control-point structure")
    native_tween.matching_knots(ak, bk)
    rational = a.IsRational or b.IsRational
    flags = int(a.IsRational) | (int(b.IsRational) << 1)
    rows = native_tween.interpolate(ap, bp, number, mode=0, flags=flags)
    curves = []
    for values_row in rows:
        curve = r3.NurbsCurve(3, rational, int(a.Degree)+1, len(ap))
        for point_index, values in enumerate(values_row):
            curve.Points[point_index] = r3.Point4d(*values)
        for knot_index, knot in enumerate(ak):
            curve.Knots[knot_index] = knot
        if not curve.IsValid:
            raise ValueError("tween construction produced an invalid curve")
        curves.append(curve)
    return curves
