"""Focused exact-input interop regressions; no Blender, build, or user documents.

Run with the bundled Python 3.13 and PYTHONPATH=vendor/occt, for example:
  PYTHONPATH=vendor/occt tools/blender-patched/5.2/python/bin/python3.13 -B spike/test_curve_inputs.py
Append --mutations to run bounded, in-memory mutations after the green suite.
No production file or shared bytecode is rewritten by the mutation checks.
"""
import importlib
import io
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
# Load the geometry helpers without the application's bpy-dependent __init__.
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
ci = importlib.import_module("narwhal3d.curve_inputs")
EAGER_BRIDGE = "narwhal3d.bridge" in sys.modules
from narwhal3d import bridge, kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402


def xyz(p):
    return p.X, p.Y, p.Z


def snapshot(geom):
    return json.dumps(geom.Encode(), sort_keys=True)


def quarter_arc():
    return r3.NurbsCurve.CreateFromArc(r3.Arc(
        r3.Point3d(2, 0, 0), r3.Point3d(math.sqrt(2), math.sqrt(2), 0),
        r3.Point3d(0, 2, 0)))


class Context:
    def __init__(self, **geometries):
        self.geometries = geometries

    def geom_by_name(self, name):
        return self.geometries.get(name)


class CurveInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ob.available():
            raise RuntimeError(f"OCCT is required for these regressions: {ob.unavailable_reason()}")
        cls.cone = bridge.make_cone((0, 0, 0), 2, 4)
        cls.cone_shape = bridge._occt_operand(cls.cone)
        cls.proxy = bridge._triangulated_proxy(cls.cone_shape)
        kernel.register_master(cls.proxy, cls.cone_shape)

    def setUp(self):
        self.arc = quarter_arc()
        self.joined = r3.PolyCurve()
        self.assertTrue(self.joined.Append(self.arc))
        self.assertTrue(self.joined.Append(r3.LineCurve(
            r3.Point3d(0, 2, 0), r3.Point3d(0, 5, 0))))
        self.polyline = r3.PolylineCurve([r3.Point3d(*p) for p in
                                       [(0, 0, 0), (2, 0, 0), (2, 0, 0), (2, 3, 0)]])
        self.ctx = Context(Joined=self.joined, Arc=self.arc, Polyline=self.polyline,
                           Degree1=self.polyline.ToNurbsCurve(), Cone=self.cone,
                           Proxy=self.proxy, Circle=r3.Circle(2).ToNurbsCurve(),
                           Point=r3.Point(r3.Point3d(0, 0, 0)), Mesh=r3.Mesh())

    def assert_curve_same(self, a, b):
        self.assertIsInstance(b, r3.Curve)
        self.assertTrue(b.IsValid)
        for fraction in (0, .13, .31, .5, .79, 1):
            pa = a.PointAt(a.Domain.T0 + fraction * (a.Domain.T1 - a.Domain.T0))
            pb = b.PointAt(b.Domain.T0 + fraction * (b.Domain.T1 - b.Domain.T0))
            self.assertLess(pa.DistanceTo(pb), 1e-10)

    def assert_roundtrip(self, curve, ctype):
        recipe = bridge.curve_recipe(curve)
        self.assertEqual(recipe["ctype"], ctype)
        # JSON serialization models a producer nesting the curve in its recipe.
        tree = json.loads(json.dumps({"kind": "sweep1", "rail": recipe}))
        restored = bridge.curve_from_recipe(tree["rail"])
        self.assert_curve_same(curve, restored)
        self.assertEqual(restored.Degree, curve.Degree)
        self.assertEqual(restored.ToNurbsCurve().IsRational, curve.ToNurbsCurve().IsRational)
        return restored

    def test_import_is_late(self):
        self.assertFalse(EAGER_BRIDGE)
        self.assertNotIn("narwhal3d.commands", sys.modules)
        self.assertNotIn("bpy", sys.modules)

    def test_reference_helpers_and_delete_guard(self):
        segment = ci.reference_from_selection(("Joined", "Segment", 0))
        edge = ci.reference_from_selection(("Cone", "Edge", 3))
        self.assertEqual(segment, ("segment", "Joined", 0))
        self.assertEqual(edge, ("edge", "Cone", 3))
        self.assertEqual(ci.humanlabel(segment), "segment 0 of Joined")
        self.assertEqual(ci.humanlabel(edge), "edge 3 of Cone")
        self.assertEqual(ci.humanlabel("Arc"), "Arc")
        self.assertEqual(ci.owner_name(segment), "Joined")
        self.assertEqual(ci.owner_name(edge), "Cone")
        self.assertEqual(ci.owner_name("Arc"), "Arc")
        refs = ["Arc", "Joined", segment, edge]
        protected = {ci.owner_name(ref) for ref in refs if not isinstance(ref, str)}
        deletable = [ref for ref in refs if isinstance(ref, str) and ref not in protected]
        self.assertEqual(deletable, ["Arc"])

    def test_invalid_selection(self):
        bad = [None, (), ("Joined", "Segment"), ("Joined", "Face", 1),
               ("Joined", "segment", 0), ("Joined", "Vertex", 1),
               ("Joined", [], 0), ("", "Segment", 0), (3, "Edge", 1)]
        for kind, indices in (("Segment", [-1, True, False, 1.0, "0", None]),
                              ("Edge", [0, -1, True, False, 1.0, "1", None])):
            bad.extend(("Joined", kind, i) for i in indices)
        for triple in bad:
            with self.subTest(triple=triple), self.assertRaises(ci.CurveInputError):
                ci.reference_from_selection(triple)

    def test_invalid_references(self):
        refs = [None, 1, "", "  ", (), ("edge", "Cone"), ["edge", "Cone", 3],
                ("Segment", "Joined", 0), ([], "Joined", 0),
                ("segment", "Joined", -1), ("edge", "Cone", 0),
                ("edge", "Cone", True), ("segment", "Joined", False),
                ("edge", "Cone", "3"), ("segment", "Joined", 0.0),
                ("edge", "", 3), ("segment", None, 0)]
        for ref in refs:
            for helper in (ci.owner_name, ci.humanlabel, lambda r: ci.resolve(self.ctx, r)):
                with self.subTest(ref=ref, helper=helper), self.assertRaises(ci.CurveInputError):
                    helper(ref)
        self.assertTrue(issubclass(ci.CurveInputError, ValueError))

    def test_whole_name_duplicate(self):
        before = snapshot(self.arc)
        curve = ci.resolve(self.ctx, "Arc")
        self.assertIsNot(curve, self.arc)
        self.assert_curve_same(self.arc, curve)
        self.assertTrue(curve.Reverse())
        self.assertEqual(snapshot(self.arc), before)
        self.assertIsInstance(ci.resolve(self.ctx, "Joined"), r3.PolyCurve)
        self.assertTrue(ci.resolve(self.ctx, "Circle").IsCircle())

    def test_rational_arc_segment_detached(self):
        before = snapshot(self.joined)
        curve = ci.resolve(self.ctx, ("segment", "Joined", 0))
        expected = self.joined.SegmentCurve(0).ToNurbsCurve()
        nc = curve.ToNurbsCurve()
        self.assertEqual(nc.Degree, 2)
        self.assertTrue(nc.IsRational)
        self.assertEqual(list(nc.Knots), list(expected.Knots))
        self.assertEqual([(p.X, p.Y, p.Z, p.W) for p in nc.Points],
                         [(p.X, p.Y, p.Z, p.W) for p in expected.Points])
        self.assert_curve_same(expected, curve)
        self.assert_roundtrip(curve, "arc")
        self.assertIsNone(kernel.recipe_of(curve))
        self.assertTrue(curve.Reverse())
        self.assertEqual(snapshot(self.joined), before)
        self.assert_curve_same(expected, ci.resolve(self.ctx, ("segment", "Joined", 0)))

    def test_polyline_original_indices(self):
        for name in ("Polyline", "Degree1"):
            parent = self.ctx.geom_by_name(name)
            before = snapshot(parent)
            for index, ends in ((0, ((0., 0., 0.), (2., 0., 0.))),
                                (2, ((2., 0., 0.), (2., 3., 0.)))):
                curve = ci.resolve(self.ctx, ("segment", name, index))
                self.assertEqual((xyz(curve.PointAtStart), xyz(curve.PointAtEnd)), ends)
                self.assertEqual(curve.Degree, 1)
                self.assert_roundtrip(curve, "polyline")
                self.assertTrue(curve.Reverse())
            self.assertEqual(snapshot(parent), before)
            with self.assertRaisesRegex(ci.CurveInputError, "segment.*1.*zero-length"):
                ci.resolve(self.ctx, ("segment", name, 1))
            with self.assertRaisesRegex(ci.CurveInputError, "segment.*3.*out of range"):
                ci.resolve(self.ctx, ("segment", name, 3))

    def test_smooth_closed_curve_has_no_segment(self):
        with self.assertRaisesRegex(ci.CurveInputError, "segment 0 of Circle.*Segment requires"):
            ci.resolve(self.ctx, ("segment", "Circle", 0))
        # Closedness alone is not a reason to reject a real polyline segment.
        self.ctx.geometries["Closed"] = r3.PolylineCurve([r3.Point3d(*p) for p in
                                                       [(0, 0, 0), (2, 0, 0), (2, 3, 0), (0, 0, 0)]])
        curve = ci.resolve(self.ctx, ("segment", "Closed", 2))
        self.assertEqual(xyz(curve.PointAtEnd), (0., 0., 0.))

    def test_wrong_kind_and_stale_references(self):
        for ref in ("Point", "Mesh", "Cone", ("segment", "Cone", 0),
                    ("segment", "Arc", 0), ("edge", "Arc", 1),
                    ("edge", "Mesh", 1), ("segment", "Joined", 2),
                    ("edge", "Cone", 999)):
            with self.subTest(ref=ref), self.assertRaises(ci.CurveInputError) as caught:
                ci.resolve(self.ctx, ref)
            self.assertIn(ci.humanlabel(ref), str(caught.exception))
        del self.ctx.geometries["Joined"]
        for ref in ("Joined", ("segment", "Joined", 0), ("edge", "Deleted", 2)):
            with self.assertRaisesRegex(ci.CurveInputError, "owner no longer exists"):
                ci.resolve(self.ctx, ref)
        self.ctx.geometries["Joined"] = self.arc
        with self.assertRaises(ci.CurveInputError):
            ci.resolve(self.ctx, ("segment", "Joined", 1))

    def test_cone_noncontiguous_edge_ids(self):
        self.assertEqual([i for i, _ in bridge.subobject_parts(self.cone)["edges"]], [2, 3])
        before = snapshot(self.cone)
        with self.assertRaisesRegex(ci.CurveInputError, "edge 1 of Cone.*unavailable"):
            ci.resolve(self.ctx, ("edge", "Cone", 1))
        seam = ci.resolve(self.ctx, ("edge", "Cone", 2))
        rim = ci.resolve(self.ctx, ("edge", "Cone", 3))
        self.assertTrue(seam.IsLinear())
        self.assertTrue(rim.IsCircle())
        self.assertEqual(rim.Degree, 2)
        self.assertTrue(rim.IsRational)
        self.assertAlmostEqual(rim.TryGetCircle().Radius, 2., places=12)
        self.assertIsNone(kernel.recipe_of(rim))
        self.assertIsNone(kernel.master_of(rim))
        self.assert_roundtrip(seam, "polyline")
        recipe = json.loads(json.dumps(bridge.curve_recipe(rim)))
        self.assertEqual(recipe["ctype"], "circle")
        rebuilt = bridge.curve_from_recipe(recipe)
        self.assertTrue(rebuilt.IsCircle())
        self.assertEqual(rebuilt.Degree, 2)
        self.assertTrue(rebuilt.IsRational)
        self.assertAlmostEqual(rebuilt.TryGetCircle().Radius, 2., places=12)
        self.assertLess(rebuilt.TryGetCircle().Center.DistanceTo(rim.TryGetCircle().Center), 1e-12)
        self.assertTrue(rim.Reverse())
        self.assertEqual(snapshot(self.cone), before)

    def test_proxy_master_available(self):
        self.assertGreater(len(self.proxy.Faces), 2)
        before = snapshot(self.proxy)
        rim = ci.resolve(self.ctx, ("edge", "Proxy", 3))
        self.assertTrue(rim.IsCircle())
        self.assertTrue(rim.IsRational)
        self.assertAlmostEqual(rim.TryGetCircle().Radius, 2., places=12)
        self.assertTrue(rim.Reverse())
        self.assertEqual(snapshot(self.proxy), before)

    def test_proxy_without_backend_refused_before_topology(self):
        with patch.object(bridge, "_occt", return_value=None), patch.object(
                bridge, "subobject_parts", side_effect=AssertionError("proxy topology traversed")):
            with self.assertRaisesRegex(ci.CurveInputError, "Proxy.*analytic edge source.*unavailable"):
                ci.resolve(self.ctx, ("edge", "Proxy", 3))

    def test_orphan_proxy_refused_unless_recipe_rebuilds(self):
        orphan = self.proxy.Duplicate()
        orphan.SetUserString(kernel.MASTER_KEY, "missing-curve-input-test-master")
        self.ctx.geometries["Orphan"] = orphan
        self.assertIsNone(kernel.master_of(orphan))
        with self.assertRaisesRegex(ci.CurveInputError, "analytic edge source.*unavailable"):
            ci.resolve(self.ctx, ("edge", "Orphan", 3))
        # A representation tag alone must also prevent the dangerous fallback.
        orphan.SetUserString(kernel.MASTER_KEY, "")
        with self.assertRaisesRegex(ci.CurveInputError, "analytic edge source.*unavailable"):
            ci.resolve(self.ctx, ("edge", "Orphan", 3))
        kernel.record_recipe(orphan, {"kind": "not-implemented"})
        with self.assertRaisesRegex(ci.CurveInputError, "analytic edge source.*unavailable"):
            ci.resolve(self.ctx, ("edge", "Orphan", 3))
        kernel.record_recipe(orphan, kernel.recipe_of(self.cone))
        before = snapshot(orphan)
        rim = ci.resolve(self.ctx, ("edge", "Orphan", 3))
        self.assertTrue(rim.IsCircle())
        self.assertIsNone(kernel.recipe_of(rim))
        self.assertEqual(snapshot(orphan), before)

    def test_exact_brep_without_backend(self):
        exact = r3.Brep.CreateFromBoundingBox(r3.BoundingBox(
            r3.Point3d(0, 0, 0), r3.Point3d(2, 3, 4)))
        self.ctx.geometries["Exact"] = exact
        with patch.object(bridge, "_occt", return_value=None):
            self.assertTrue(ci.resolve(self.ctx, ("edge", "Exact", 1)).IsLinear())

    def test_generic_rational_recipe_is_not_parent_recipe(self):
        curve = r3.NurbsCurve(3, True, 3, 3)
        for i, (x, y, w) in enumerate(((0, 0, 1), (3, 5, .4), (7, 0, 1))):
            curve.Points[i] = r3.Point4d(x * w, y * w, 0, w)
        curve.Knots.CreateUniformKnots(1.)
        parent = r3.PolyCurve()
        self.assertTrue(parent.Append(curve))
        kernel.record_recipe(parent, {"kind": "not-the-extracted-curve"})
        self.ctx.geometries["Rational"] = parent
        extracted = ci.resolve(self.ctx, ("segment", "Rational", 0))
        self.assertIsNone(kernel.recipe_of(extracted))
        self.assert_roundtrip(extracted, "nurbs")


def mutation_checks():
    """Execute changed source in a disposable module, never in the shared tree."""
    global ci
    original = ci
    source = Path(ci.__file__).read_text()
    mutations = [
        ("alias instead of duplicate", "result = curve.Duplicate()", "result = curve",
         "test_whole_name_duplicate"),
        ("shift original segment indices", "vertices[index], vertices[index + 1]",
         "vertices[max(0, index - 1)], vertices[index]", "test_polyline_original_indices"),
        ("replace rational segment with chord", "return _detached(geom.SegmentCurve(index))",
         "return _detached(r3.LineCurve(geom.SegmentCurve(index).PointAtStart, "
         "geom.SegmentCurve(index).PointAtEnd))", "test_rational_arc_segment_detached"),
        ("renumber noncontiguous edges", '(edge_id for edge_id, _ in parts["edges"])',
         'range(1, len(parts["edges"]) + 1)', "test_cone_noncontiguous_edge_ids"),
        ("allow proxy fallback", "if known_proxy:", "if False:",
         "test_proxy_without_backend_refused_before_topology"),
    ]
    try:
        for label, old, new, test in mutations:
            if source.count(old) != 1:
                raise AssertionError(f"mutation anchor changed: {label}")
            mutant = types.ModuleType("narwhal3d._curve_inputs_mutation")
            mutant.__package__ = "narwhal3d"
            exec(compile(source.replace(old, new, 1), original.__file__, "exec"), mutant.__dict__)
            ci = mutant
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output).run(
                unittest.TestSuite([CurveInputsTests(test)]))
            if result.wasSuccessful() or result.testsRun != 1:
                raise AssertionError(f"mutation did not go RED: {label}\n{output.getvalue()}")
            print(f"MUTATION RED: {label}")
    finally:
        ci = original


if __name__ == "__main__":
    run_mutations = "--mutations" in sys.argv
    if run_mutations:
        sys.argv.remove("--mutations")
    program = unittest.main(exit=False, verbosity=2)
    if not program.result.wasSuccessful():
        sys.exit(1)
    if run_mutations:
        mutation_checks()
        print("Production module restored; re-running the focused suite.")
        restored = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(CurveInputsTests))
        sys.exit(0 if restored.wasSuccessful() else 1)
