"""Owner bug #127: Offset of a closed, C0-kinked spline was refused on both sides.

Public regression on SYNTHETIC curves only. The report's curve (private artwork, never
published) is a closed cubic whose joins are real Sharp corners of about 0.75 degrees, and
whose offset runs past the radius of curvature on each side. Two failure classes:

  * corners: a corner whose miter apex moved an endpoint by more than 20% of the tolerance
    was refused as "not implemented yet". kinked_ellipse() reproduces it: both sides must
    offset exactly within tolerance (outer corners filled, inner corners trimmed to apex).
  * cusps: where the offset distance reaches the radius of curvature on the offset side the
    true offset cusps and doubles back. As the reference does (Trim=No), the offset is
    returned with each cusp as a kink. thin_ellipse() swallowtails on its inner side.

Run: scripts/run_suite.sh spike/test_offset_report127.py
"""
import math
import os
import sys
import types
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [os.path.join(ROOT, 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
from narwhal3d import offset as off

NORMAL = (0.0, 0.0, 1.0)
TOL = 0.001
JOINS = 8


def kinked_ellipse(a=4.0, b=2.0, turn=0.013):
    """Closed cubic of JOINS Bezier arcs of an a x b ellipse. Each join turns by `turn` rad;
    knots have multiplicity three, as in the report's curve (C0 at every join)."""
    def at(t):
        return (a * math.cos(t), b * math.sin(t), 0.0)

    def tangent(t, angle):
        x, y = -a * math.sin(t), b * math.cos(t)
        c, s = math.cos(angle), math.sin(angle)
        length = math.hypot(x, y)
        return ((c * x - s * y) / length, (s * x + c * y) / length, 0.0)

    pts = []
    for i in range(JOINS):
        t0 = 2 * math.pi * i / JOINS
        t1 = 2 * math.pi * (i + 1) / JOINS
        p0, p1 = at(t0), at(t1)
        h = math.dist(p0, p1) / 3.0
        out_dir, in_dir = tangent(t0, turn / 2), tangent(t1, -turn / 2)
        pts += [p0,
                tuple(p + h * d for p, d in zip(p0, out_dir)),
                tuple(p - h * d for p, d in zip(p1, in_dir))]
    pts.append(at(2 * math.pi))
    knots = [0.0] * 3 + [float(k) for k in range(1, JOINS) for _ in range(3)] + [float(JOINS)] * 3
    curve = r3.NurbsCurve(3, False, 4, len(pts))
    for k, p in enumerate(pts):
        curve.Points[k] = r3.Point4d(*p, 1.0)
    for k, value in enumerate(knots):
        curve.Knots[k] = value
    return curve


def thin_ellipse():
    """Smooth (C1) closed cubic on a 1 x 0.3 ellipse: its radius of curvature at the major
    vertices is 0.09, so an offset of 0.2 cusps there on the inner side only."""
    return kinked_ellipse(a=1.0, b=0.3, turn=0.0)


def _xyz(p):
    return (p.X, p.Y, p.Z) if hasattr(p, 'X') else tuple(p)


def offset_locus(source, distance, samples=1200):
    """The sharp-corner offset as polylines: the offset samples, plus at each corner the two
    tangent segments from the offset endpoints to their apex. Built from PointAt/TangentAt."""
    d = source.Domain
    pts = [off._offset_point(source, d.T0 + (d.T1 - d.T0) * i / samples, distance, NORMAL)
           for i in range(samples + 1)]
    polylines = [pts]
    for k in range(1, JOINS + 1):
        before, after = source.Trim(k - 1, k), source.Trim(k % JOINS, k % JOINS + 1)
        corner = _xyz(source.PointAt(k % JOINS))
        tin = _xyz(before.TangentAtEnd)
        tout = _xyz(after.TangentAtStart)
        a = off._add(corner, off._scale(off._unit(off._cross(NORMAL, tin)), distance))
        b = off._add(corner, off._scale(off._unit(off._cross(NORMAL, tout)), distance))
        apex = off._line_line_point(a, off._add(a, tin), b, off._add(b, tout))
        polylines.append([a, apex, b])
    return polylines, pts


def distance_to(polylines, p):
    """Exact distance from p to a union of polylines (point-to-segment, no sampling)."""
    best = math.inf
    for poly in polylines:
        for a, b in zip(poly, poly[1:]):
            ab = [y - x for x, y in zip(a, b)]
            ap = [x - y for x, y in zip(p, a)]
            length2 = sum(c * c for c in ab)
            t = 0.0 if length2 == 0.0 else min(1.0, max(0.0, sum(x * y for x, y in zip(ap, ab)) / length2))
            q = [x + t * y for x, y in zip(a, ab)]
            best = min(best, math.dist(p, q))
    return best


def cusp_grid_crossings(source, distance, samples=4000):
    """Parameters where 1 - distance * kappa_s changes sign, on a fixed grid of the source
    (the test's own count and location of the cusps, independent of the implementation's
    bisection)."""
    d = source.Domain
    vals = []
    for i in range(samples + 1):
        t = d.T0 + (d.T1 - d.T0) * i / samples
        normal = off._unit(off._cross(NORMAL, off._pt(source.TangentAt(t))))
        k = source.CurvatureAt(t)
        vals.append((t, 1.0 - distance * off._dot((k.X, k.Y, k.Z), normal)))
    return [0.5 * (t0 + t1) for (t0, v0), (t1, v1) in zip(vals, vals[1:]) if v0 * v1 < 0.0]


def reversal_parameters(out):
    """Interior breaks of `out` (multiplicity three) where the one-sided tangents point in
    opposite directions: the cusps that the output keeps as kinks."""
    d = out.Domain
    knots = list(out.Knots)
    breaks = [d.T0] + sorted({k for k in knots if d.T0 < k < d.T1 and knots.count(k) >= 3}) + [d.T1]
    found = []
    for i in range(1, len(breaks) - 1):
        left = out.Trim(breaks[i - 1], breaks[i])
        right = out.Trim(breaks[i], breaks[i + 1])
        if off._dot(off._pt(left.TangentAtEnd), off._pt(right.TangentAtStart)) < 0.0:
            found.append(breaks[i])
    return found


class OffsetReport127(unittest.TestCase):
    def assert_sharp_offset_within_tolerance(self, source, distance):
        result = off.curve_offset(source, distance, NORMAL, 'Sharp', TOL)
        self.assertIsNotNone(result.curve, result.warning)
        self.assertEqual(result.warning, '')
        out = result.curve
        self.assertTrue(out.IsValid)
        self.assertEqual(out.Degree, 3)
        self.assertEqual(out.IsClosed, source.IsClosed)
        self.assertAlmostEqual(out.Domain.T0, source.Domain.T0)
        self.assertAlmostEqual(out.Domain.T1, source.Domain.T1)
        self.assertLessEqual(result.max_deviation, TOL * 0.8)
        locus, pts = offset_locus(source, distance)
        d = out.Domain
        out_line = [_xyz(out.PointAt(d.T0 + (d.T1 - d.T0) * i / 3000)) for i in range(3001)]
        worst_out = max(distance_to(locus, p) for p in out_line[::3])
        worst_locus = max(distance_to([out_line], p) for p in pts)
        print(f'  distance={distance:+g}: output->locus {worst_out:.3g}, '
              f'locus->output {worst_locus:.3g}, controls={len(out.Points)}', flush=True)
        self.assertLessEqual(worst_out, TOL)
        self.assertLessEqual(worst_locus, TOL)

    def assert_true_offset(self, source, distance, samples=400):
        """Full contract for a true offset that may cusp: a valid curve on the source domain;
        at matched parameters the output lies |distance| from its foot point on the curve and
        agrees with the offset; no output point is farther than |distance| from the curve (a
        nearer point of another arc can lie closer, so only the one-sided bound holds)."""
        result = off.curve_offset(source, distance, NORMAL, 'Sharp', TOL)
        self.assertIsNotNone(result.curve, result.warning)
        out = result.curve
        self.assertTrue(out.IsValid)
        self.assertEqual(out.Degree, 3)
        self.assertAlmostEqual(out.Domain.T0, source.Domain.T0)
        self.assertAlmostEqual(out.Domain.T1, source.Domain.T1)
        self.assertLessEqual(result.max_deviation, TOL * 0.8)
        d = source.Domain
        source_line = [_xyz(source.PointAt(d.T0 + (d.T1 - d.T0) * i / 4000)) for i in range(4001)]
        worst_matched = 0.0
        worst_foot = 0.0
        worst_band = 0.0
        for i in range(samples + 1):
            t = d.T0 + (d.T1 - d.T0) * i / samples
            p = _xyz(out.PointAt(t))
            worst_matched = max(worst_matched,
                                math.dist(p, off._offset_point(source, t, distance, NORMAL)))
            foot = _xyz(source.PointAt(t))
            worst_foot = max(worst_foot, abs(math.dist(p, foot) - abs(distance)))
            worst_band = max(worst_band, distance_to([source_line], p) - abs(distance))
        print(f'  distance={distance:+g}: matched {worst_matched:.3g}, foot distance '
              f'{worst_foot:.3g}, band {worst_band:.3g}, controls={len(out.Points)}', flush=True)
        self.assertLessEqual(worst_matched, TOL)
        self.assertLessEqual(worst_foot, TOL)
        self.assertLessEqual(worst_band, TOL)
        return out

    def test_kinked_closed_curve_offsets_both_sides_within_tolerance(self):
        source = kinked_ellipse()
        self.assertTrue(source.IsValid)
        before = source.Encode()
        for distance in (0.2, -0.2):
            self.assert_sharp_offset_within_tolerance(source, distance)
        self.assertEqual(source.Encode(), before)

    def test_corner_fill_and_trim_are_the_only_route_mutant_is_red(self):
        source = kinked_ellipse()
        with patch.object(off, '_inner_trim_param', return_value=None):
            refused_sides = [distance for distance in (0.2, -0.2)
                             if off.curve_offset(source, distance, NORMAL, 'Sharp', TOL).curve is None]
            self.assertEqual(len(refused_sides), 1)
            with self.assertRaises(AssertionError):
                self.assert_sharp_offset_within_tolerance(source, refused_sides[0])
        print(f'  RED witnessed: inner corners without the apex trim refuse {refused_sides[0]:+g}', flush=True)

    def test_swallowtailing_side_is_returned_with_cusps_as_kinks(self):
        source = thin_ellipse()
        before = source.Encode()
        for distance in (0.2, -0.2):
            out = self.assert_true_offset(source, distance)
            cusps = cusp_grid_crossings(source, distance)
            reversed_at = reversal_parameters(out)
            print(f'  distance={distance:+g}: cusps {len(cusps)}, kinks kept {len(reversed_at)}',
                  flush=True)
            self.assertEqual(len(reversed_at), len(cusps))
            for t in cusps:
                self.assertTrue(any(abs(t - r) < 2.0 / 4000 * 8 for r in reversed_at), t)
        self.assertEqual(source.Encode(), before)
        self.assertEqual(len(cusp_grid_crossings(source, 0.2)), 4)
        self.assertEqual(len(cusp_grid_crossings(source, -0.2)), 0)

    def test_without_cusp_boundaries_the_swallowtail_is_refused_mutant_is_red(self):
        source = thin_ellipse()
        with self.assertRaises(AssertionError):
            with patch.object(off, '_cusp_parameters', return_value=[]):
                self.assert_true_offset(source, 0.2)
        print('  RED witnessed: without cusp boundaries the swallowtail side is refused', flush=True)


if __name__ == '__main__':
    unittest.main()
