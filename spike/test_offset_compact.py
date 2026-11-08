"""Compact TRUE-offset fitting, not Rebuild of a display/legacy approximation.

Public synthetic fixtures always run without private data. Optional read-only checks
require NARWHAL_TEST_OFFSET_PRIVATE_DIR, a directory containing 0118.3dm, 0120.3dm,
0125.3dm and BUG_REPORTS.md. Run: scripts/run_suite.sh spike/test_offset_compact.py
Mutation controls deliberately disable compaction / falsify acceptance, and require the
independent geometry/count contract to fail. No installed library/ABI is changed.
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
from narwhal3d import offset as off, interpolate, native_interpolate

NORMAL = (0.0, 0.0, 1.0)
TOL = 0.001


def smooth_fixture():
    # Public independent 17-control-point closed cubic, not copied report geometry.
    return r3.NurbsCurve.Create(True, 3, [
        r3.Point3d((4 + 0.3 * math.cos(3*a))*math.cos(a),
                   (2 + 0.15 * math.sin(2*a))*math.sin(a), 0)
        for a in (2*math.pi*i/14 for i in range(14))])


def boundary_fixture():
    """Independent closed cubic with small REAL tangent jumps at eight joins."""
    joined = r3.PolyCurve()
    h = 4/3 * math.tan(math.pi/16)
    for i in range(8):
        a, b = i*math.pi/4, (i+1)*math.pi/4
        p = (4*math.cos(a), 2*math.sin(a), 0)
        q = (4*math.cos(b % (2*math.pi)), 2*math.sin(b % (2*math.pi)), 0)
        ta = (-4*math.sin(a), 2*math.cos(a), 0)
        tx, ty = -4*math.sin(b), 2*math.cos(b)
        e = 0.0002
        tb = (tx*math.cos(e)-ty*math.sin(e), tx*math.sin(e)+ty*math.cos(e), 0)
        piece = r3.NurbsCurve.CreateControlPointCurve([
            r3.Point3d(*p), r3.Point3d(*(p[j]+h*ta[j] for j in range(3))),
            r3.Point3d(*(q[j]-h*tb[j] for j in range(3))), r3.Point3d(*q)], 3)
        piece.Domain = r3.Interval(i, i+1)
        joined.Append(piece)
    return joined.ToNurbsCurve()


def oracle(source, fit, distance, normal=NORMAL, samples=16384):
    """Independent pointwise max oracle, including knot-span interiors.

    Construct targets directly from PointAt/TangentAt, not production target or
    acceptance helpers. Matching normalized parameters gives a stricter distance
    than nearest-point error and checks the whole sampled fit in reverse too.
    """
    d, f = source.Domain, fit.Domain
    us = {i/samples for i in range(samples+1)}
    for c in (source, fit):
        cd = c.Domain
        knots = sorted({0.0, 1.0} | {
            (k-cd.T0)/(cd.T1-cd.T0) for k in c.Knots if cd.T0 < k < cd.T1})
        for a, b in zip(knots, knots[1:]):
            us.update(a+(b-a)*i/37 for i in range(38))
    worst = 0.0
    nx, ny, nz = normal
    for u in us:
        t = d.T0+(d.T1-d.T0)*u
        p, v = source.PointAt(t), source.TangentAt(t)
        x, y, z = ny*v.Z-nz*v.Y, nz*v.X-nx*v.Z, nx*v.Y-ny*v.X
        length = math.sqrt(x*x+y*y+z*z)
        q = fit.PointAt(f.T0+(f.T1-f.T0)*u)
        worst = max(worst, math.dist((p.X+distance*x/length,
                                     p.Y+distance*y/length,
                                     p.Z+distance*z/length), (q.X,q.Y,q.Z)))
    return worst


class CompactOffset(unittest.TestCase):
    def assert_contract(self, source, result, distance, normal=NORMAL, kinked=False):
        self.assertFalse(result.exact)
        self.assertEqual(result.warning, '')
        fit = result.curve
        self.assertIsInstance(fit, r3.NurbsCurve)
        self.assertTrue(fit.IsValid)
        self.assertEqual(fit.Degree, 3)
        self.assertLess(len(fit.Points), 80)
        self.assertEqual(fit.IsClosed, source.IsClosed)
        self.assertAlmostEqual(fit.Domain.T0, source.Domain.T0)
        self.assertAlmostEqual(fit.Domain.T1, source.Domain.T1)
        deviation = oracle(source, fit, distance, normal)
        self.assertLessEqual(deviation, TOL)
        self.assertLessEqual(result.max_deviation, TOL * 0.8)
        if source.IsClosed and not kinked:
            self.assertLess(math.dist(off._pt(fit.TangentAtStart),
                                      off._pt(fit.TangentAtEnd)), 1e-9)
        print(f'  controls={len(fit.Points)}, independent max={deviation:.9g}', flush=True)

    def test_public_closed_both_sides_domain_seam_and_input_unchanged(self):
        c = smooth_fixture()
        self.assertEqual(len(c.Points), 17)
        c.Domain = r3.Interval(-7, 29)
        before = c.Encode()
        for distance in (0.25, -0.25):
            self.assert_contract(c, off.curve_offset(c, distance), distance)
        self.assertEqual(c.Encode(), before)

    def test_open_rational_nonuniform_and_tilted(self):
        c = r3.NurbsCurve(3, True, 4, 7)
        for i in range(7):
            w = 1.0 + (0.4 if i == 3 else 0)
            c.Points[i] = r3.Point4d(i*w, math.sin(i*0.6)*w, 0, w)
        for i, k in enumerate((0,0,0,0.4,1.2,2,3,3,3)):
            c.Knots[i] = k
        c.Transform(r3.Transform.Rotation(math.pi/6, r3.Vector3d(1,0,0), r3.Point3d(0,0,0)))
        before = c.Encode()
        normal = (0,-0.5,math.sqrt(3)/2)
        result = off.curve_offset(c, 0.25, normal)
        self.assert_contract(c, result, 0.25, normal)
        self.assertEqual(c.Encode(), before)
        self.assertTrue(c.IsRational)

    def test_primitive_paths_do_not_compact(self):
        with patch.object(off, '_compact_offset', side_effect=AssertionError('primitive reached fitter')):
            for c in (r3.LineCurve(r3.Point3d(0,0,0), r3.Point3d(3,1,0)),
                      r3.Circle(r3.Point3d(0,0,0), 3).ToNurbsCurve(),
                      r3.Arc(r3.Point3d(0,0,0), 3, math.pi/2).ToNurbsCurve()):
                self.assertTrue(off.curve_offset(c, 0.25).exact)

    def test_joined_piece_and_knot_kinks_are_not_smoothed(self):
        c = smooth_fixture()
        joined = r3.PolyCurve()
        joined.Append(c.Trim(c.Domain.T0, (c.Domain.T0+c.Domain.T1)/2))
        self.assertIsNone(off._compact_offset(joined, .25, NORMAL, TOL))
        kink = r3.NurbsCurve(3, False, 4, 7)
        for i, p in enumerate(((0,0,0),(1,0,0),(2,0,0),(3,0,0),(3,1,0),(4,2,0),(5,2,0))):
            kink.Points[i] = r3.Point4d(*p, 1)
        for i, k in enumerate((0,0,0,1,1,1,2,2,2)):
            kink.Knots[i] = k
        self.assertTrue(kink.IsValid)
        with patch.object(off, '_offset_control_polygon_approx',
                          side_effect=AssertionError('must not smooth a known corner')):
            refused = off.curve_offset(kink, .25)
        self.assertIsNone(refused.curve)
        self.assertIn('boundary-preserving', refused.warning)
        self.assertIn('not implemented yet', refused.warning)

    def assert_boundaries(self, source, fit, distance, normal=NORMAL):
        d = source.Domain
        knots = list(source.Knots)
        breaks = [d.T0] + sorted(k for k in set(knots)
                                if d.T0 < k < d.T1 and knots.count(k) >= source.Degree) + [d.T1]
        for k in breaks[1:-1]:
            self.assertEqual(list(fit.Knots).count(k), 3)
        for a, b in zip(breaks, breaks[1:]):
            src, dst = source.Trim(a,b), fit.Trim(a,b)
            # These are one-sided END tangents, not ambiguous TangentAt(k).
            for left, right in ((src.TangentAtStart, dst.TangentAtStart),
                                (src.TangentAtEnd, dst.TangentAtEnd)):
                self.assertLess(math.dist(off._pt(left), off._pt(right)), 1e-9)
            self.assertLessEqual(oracle(src, dst, distance, normal, 4096), TOL)
        if source.IsClosed:
            # In particular, periodicising the seam must fail this contract.
            self.assertLess(math.dist(off._pt(source.TangentAtStart), off._pt(fit.TangentAtStart)), 1e-9)
            self.assertLess(math.dist(off._pt(source.TangentAtEnd), off._pt(fit.TangentAtEnd)), 1e-9)

    def test_public_boundary_fit_preserves_every_one_sided_tangent(self):
        source = boundary_fixture()
        before = source.Encode()
        for distance in (-.2, .2):
            result = off.curve_offset(source, distance)
            self.assert_contract(source, result, distance, kinked=True)
            self.assert_boundaries(source, result.curve, distance)
        self.assertEqual(source.Encode(), before)

    def test_boundary_rational_tilted_nonuniform_input_is_preserved(self):
        template = boundary_fixture()
        source = r3.NurbsCurve(3, True, 4, len(template.Points))
        for i, k in enumerate(template.Knots):
            source.Knots[i] = k
        for i in range(len(source.Points)):
            p = template.Points[i]
            w = 1.15 if i % 3 == 1 else 1.0
            source.Points[i] = r3.Point4d(p.X*w, p.Y*w, p.Z*w, w)
        # Nonuniform domains must not move or merge the original boundaries.
        values = {-0.0: -3.0}
        for i in range(1,9):
            values[float(i)] = values[float(i-1)] + (0.15 if i % 2 else 2.0)
        for i, k in enumerate(list(source.Knots)):
            source.Knots[i] = values[k]
        source.Transform(r3.Transform.Rotation(math.pi/6, r3.Vector3d(1,0,0), r3.Point3d(0,0,0)))
        normal = (0,-.5,math.sqrt(3)/2)
        before = source.Encode()
        result = off.curve_offset(source, -.2, normal)
        self.assert_contract(source, result, -.2, normal, kinked=True)
        self.assert_boundaries(source, result.curve, -.2, normal)
        self.assertEqual(source.Encode(), before)
        self.assertTrue(source.IsRational)

    def test_boundary_native_and_complete_existing_fallback(self):
        source = boundary_fixture()
        before = native_interpolate._native_calls
        result = off.curve_offset(source, -.2)
        if native_interpolate._fn() is not None:
            self.assertGreater(native_interpolate._native_calls, before)
        with patch.object(native_interpolate, 'interpolate', return_value=None):
            fallback = off.curve_offset(source, -.2)
        self.assertEqual(len(result.curve.Points), len(fallback.curve.Points))
        self.assertAlmostEqual(result.max_deviation, fallback.max_deviation, delta=1e-10)
        self.assert_boundaries(source, fallback.curve, -.2)

    def test_boundary_no_compaction_and_smoothing_mutants_are_red(self):
        source = boundary_fixture()
        with patch.object(off, '_compact_offset_boundaries', return_value=None):
            broken = off.curve_offset(source, -.2)
        with self.assertRaises(AssertionError):
            self.assert_contract(source, broken, -.2, kinked=True)
        # A smooth periodic fit can easily pass distance tolerance while erasing
        # every tiny kink. The boundary contract must independently reject it.
        d = source.Domain
        pts = [off._offset_point(source, d.T0+(d.T1-d.T0)*i/64, -.2, NORMAL)
               for i in range(64)]
        smoothed = interpolate.interpolate_closed_curve(pts, 'Uniform')
        smoothed.Domain = r3.Interval(d.T0,d.T1)
        self.assertLess(oracle(source, smoothed, -.2), TOL)
        self.assertGreater(math.dist(off._pt(source.TangentAtStart),
                                     off._pt(smoothed.TangentAtStart)), 1e-5)
        with self.assertRaises(AssertionError):
            self.assert_boundaries(source, smoothed, -.2)
        print('  RED witnessed: bypassing boundary compaction and smoothing true kinks')

    def test_boundary_weakened_accuracy_mutant_is_red(self):
        source = boundary_fixture()
        with patch.object(off, '_offset_correspondence_error', return_value=0.0):
            broken = off.curve_offset(source, -.2)
        error = oracle(source, broken.curve, -.2)
        self.assertGreater(error, TOL)
        with self.assertRaises(AssertionError):
            self.assert_contract(source, broken, -.2, kinked=True)
        print(f'  RED witnessed: weakened boundary check accepts error {error:.9g} > {TOL}')

    def test_boundary_stricter_tolerance_is_not_relaxed(self):
        source = boundary_fixture()
        tolerance = 0.0002
        result = off.curve_offset(source, -.2, tolerance=tolerance)
        self.assertIsNotNone(result.curve, result.warning)
        self.assertLessEqual(result.max_deviation, tolerance * .8)
        self.assertLessEqual(oracle(source, result.curve, -.2), tolerance)
        self.assert_boundaries(source, result.curve, -.2)

    def test_existing_native_interpolator_and_complete_fallback(self):
        c = smooth_fixture()
        before = native_interpolate._native_periodic_calls
        result = off.curve_offset(c, .25)
        if native_interpolate._periodic_fn() is not None:
            self.assertGreater(native_interpolate._native_periodic_calls, before)
        with patch.object(interpolate, '_native_periodic', return_value=None):
            fallback = off.curve_offset(c, .25)
        self.assertEqual(len(result.curve.Points), len(fallback.curve.Points))
        self.assertLess(abs(result.max_deviation-fallback.max_deviation), 1e-10)
        self.assert_contract(c, fallback, .25)

    def test_inner_native_validation_parity_is_separate_from_output_controls(self):
        # Old control-polygon construction is retained as the complete fallback.
        # Its fixed-fit native/legacy nearest validation contract remains unchanged.
        c = r3.NurbsCurve.CreateControlPointCurve([
            r3.Point3d(i, math.sin(i * .3), 0) for i in range(7)], 3)
        d = c.Domain
        fit = r3.NurbsCurve.CreateControlPointCurve([
            r3.Point3d(*off._offset_point(c, d.T0+(d.T1-d.T0)*i/48, .25, NORMAL))
            for i in range(49)], 3)
        native = off._offset_deviation(c, fit, .25, NORMAL)
        with patch.object(off, '_native_deviation', return_value=None):
            legacy = off._offset_deviation(c, fit, .25, NORMAL)
        self.assertAlmostEqual(native, legacy, delta=1e-9)

    def test_mutation_no_compaction_is_red(self):
        c = smooth_fixture()
        with patch.object(off, '_compact_offset', return_value=None):
            broken = off.curve_offset(c, .25)
        with self.assertRaises(AssertionError):
            self.assert_contract(c, broken, .25)
        print('  RED witnessed: bypassing compaction violates control-count/domain contract')

    def test_mutation_weakened_accuracy_is_red(self):
        c = smooth_fixture()
        with patch.object(off, '_offset_correspondence_error', return_value=0.0):
            broken = off.curve_offset(c, .25)
        with self.assertRaises(AssertionError):
            self.assert_contract(c, broken, .25)
        self.assertGreater(oracle(c, broken.curve, .25), TOL)
        print('  RED witnessed: falsified acceptance fails independent true-target oracle')

    def private_path(self, name):
        directory = os.environ.get('NARWHAL_TEST_OFFSET_PRIVATE_DIR')
        if not directory:
            self.skipTest('optional private data requires NARWHAL_TEST_OFFSET_PRIVATE_DIR')
        return os.path.join(directory, name)

    def test_personal_source_and_saved_history_if_available(self):
        path = self.private_path('0118.3dm')
        if not os.path.isfile(path):
            self.skipTest('optional immutable personal report unavailable')
        doc = r3.File3dm.Read(path)
        c = next(o.Geometry for o in doc.Objects if o.Attributes.Name == 'Projection3')
        self.assertEqual((c.Degree, len(c.Points), c.IsClosed), (3,17,True))
        legacy = off._offset_control_polygon_approx(c, .25, NORMAL, TOL)
        self.assertEqual(len(legacy.curve.Points), 193)
        self.assertAlmostEqual(legacy.max_deviation, 6.95e-4, delta=1e-6)
        self.assert_contract(c, off.curve_offset(c, .25), .25)
        self.assert_contract(c, off.curve_offset(c, -.25), -.25)
        saved_path = self.private_path('0125.3dm')
        saved = r3.File3dm.Read(saved_path)
        joined = next(o.Geometry for o in saved.Objects if o.Attributes.Name == 'Curve29')
        self.assertIsInstance(joined, r3.PolyCurve)
        counts = [len(joined.SegmentCurve(i).ToNurbsCurve().Points)
                  for i in range(joined.SegmentCount)]
        self.assertEqual(counts, [11,130,11,52,5,11,171])
        with open(self.private_path('BUG_REPORTS.md')) as f:
            log = f.read()
        self.assertIn('Created Offset1 (d=0.25, approx:', log)
        self.assertIn('Joined 7 curves into 1: Curve29', log)
        print(f'  MEASURED saved Curve29: seven preserved pieces, counts {counts}')

    def test_personal_125_exact_producer_is_curve16_minus_point2_not_projection3(self):
        path = self.private_path('0120.3dm')
        if not os.path.isfile(path):
            self.skipTest('optional immutable personal history unavailable')
        doc = r3.File3dm.Read(path)
        source = next(o.Geometry for o in doc.Objects if o.Attributes.Name == 'Curve16')
        produced = next(o.Geometry for o in doc.Objects if o.Attributes.Name == 'Offset1')
        self.assertEqual((source.Degree, len(source.Points)), (3,76))
        self.assertEqual(len(produced.Points), 385)
        d = source.Domain
        # Reconstruct the producer's CONTROL POLYGON, not points on its curve.
        # All 385 saved coordinates match EXACTLY, including endpoints.
        targets = [off._offset_point(source, d.T0+(d.T1-d.T0)*i/384, -.2, NORMAL)
                   for i in range(385)]
        saved_controls = [off._pt(p) for p in produced.Points]
        self.assertEqual(max(math.dist(a,b) for a,b in zip(targets,saved_controls)), 0.0)
        before = source.Encode()
        result = off.curve_offset(source, -.2)
        self.assert_contract(source, result, -.2, kinked=True)
        self.assert_boundaries(source, result.curve, -.2)
        self.assertEqual(source.Encode(), before)
        restored = r3.NurbsCurve.Decode(result.curve.Encode())
        self.assertEqual(restored.Encode(), result.curve.Encode())
        self.assert_boundaries(source, restored, -.2)
        saved = r3.File3dm.Read(self.private_path('0125.3dm'))
        joined = next(o.Geometry for o in saved.Objects if o.Attributes.Name == 'Curve29')
        for i in (1,3,6):
            # Trim changes the end controls. Compare ALL interior edge vectors
            # against contiguous source controls, invariant under later translation.
            piece = [off._pt(p) for p in joined.SegmentCurve(i).Points][3:-3]
            vectors = [tuple(b[j]-a[j] for j in range(3)) for a,b in zip(piece,piece[1:])]
            ref = [tuple(b[j]-a[j] for j in range(3))
                   for a,b in zip(saved_controls,saved_controls[1:])]
            error = min(max(math.dist(a,b) for a,b in zip(vectors,ref[k:k+len(vectors)]))
                        for k in range(len(ref)-len(vectors)+1))
            self.assertLess(error, 1e-12)
        print('  MEASURED #125: Curve16 -0.2 -> 385-control Offset1; '
              'interior controls preserved in all three dense joined pieces')


if __name__ == '__main__':
    unittest.main(verbosity=2)
