"""Compatible control-point interpolation for TweenCurves."""
from __future__ import annotations

import math
import rhino3dm as r3

NATIVE_TWEEN_MIN_VALUES = 50_000_001


def _data(curve):
    nurbs = curve.ToNurbsCurve()
    points = []
    for point in nurbs.Points:
        weight = float(point.W) if nurbs.IsRational else 1.0
        if abs(weight) <= 1e-15:
            raise ValueError("a control point has zero weight")
        points.append((point.X / weight, point.Y / weight, point.Z / weight, weight))
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
        for index in range(sample_number):
            parameter = arclength.parameter_at_length(
                curve, total * index / (sample_number-1))
            point=curve.PointAt(parameter);out.append((point.X,point.Y,point.Z))
        return out
    pa,pb=samples(a),samples(b);curves=[]
    for index in range(1,number+1):
        fraction=index/(number+1.0)
        points=[tuple(x[k]+fraction*(y[k]-x[k]) for k in range(3)) for x,y in zip(pa,pb)]
        curves.append(interpolate.interpolate_curve(points,degree=3,style="Chord"))
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
    if any(abs(x-y) > 1e-10 * max(1.0, abs(x), abs(y)) for x,y in zip(ak,bk)):
        raise ValueError("ControlPoint matching requires matching knot vectors")
    rational = a.IsRational or b.IsRational
    rows = None
    if len(ap) * number >= NATIVE_TWEEN_MIN_VALUES:
        try:
            from . import native_tween
            rows = native_tween.interpolate(ap, bp, number)
        except Exception:  # noqa: BLE001 -- complete Python fallback
            rows = None
    if rows is None:
        rows = []
        for index in range(1, number+1):
            fraction = index / (number+1.0)
            rows.append([tuple(pa[k] + fraction*(pb[k]-pa[k]) for k in range(4))
                         for pa,pb in zip(ap,bp)])
    curves = []
    for values_row in rows:
        curve = r3.NurbsCurve(3, rational, int(a.Degree)+1, len(ap))
        for point_index, values in enumerate(values_row):
            x,y,z,w = values
            if not all(math.isfinite(value) for value in values) or abs(w) <= 1e-15:
                raise ValueError("interpolated control point is invalid")
            curve.Points[point_index] = r3.Point4d(x*w, y*w, z*w, w)
        for knot_index, knot in enumerate(ak):
            curve.Knots[knot_index] = knot
        if not curve.IsValid:
            raise ValueError("tween construction produced an invalid curve")
        curves.append(curve)
    return curves
