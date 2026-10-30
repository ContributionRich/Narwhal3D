"""Exact component inputs for Loft/EdgeSrf/PlanarSrf; backend-only, no builds.

Run with bundled Python and PYTHONPATH=vendor/occt; append --mutations for
in-memory command mutations. No shared source or bytecode is rewritten.
Python here tests UI/rhino3dm/OCCT orchestration, not a new geometry algorithm.
"""
import importlib
import inspect
import io
import json
import math
from pathlib import Path
import sys
import textwrap
import types
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
commands = importlib.import_module("narwhal3d.commands")
from narwhal3d import bridge, curve_inputs as ci, kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402


def snapshot(geom):
    return json.dumps(geom.Encode(), sort_keys=True)


def arc(z):
    curve = r3.NurbsCurve.CreateFromArc(r3.Arc(
        r3.Point3d(2, 0, z), r3.Point3d(math.sqrt(2), math.sqrt(2), z),
        r3.Point3d(0, 2, z)))
    curve.Domain = r3.Interval(7, 19)
    return curve


class Context:
    def __init__(self, **geometries):
        self.geometries = geometries
        self.selection = []
        self.parts = []
        self.added = []
        self.cleared = 0

    def geom_by_name(self, name):
        # The real context is string-only; reference handling must not leak here.
        assert isinstance(name, str), name
        return self.geometries.get(name)

    def selected_objects(self):
        return [types.SimpleNamespace(name=name) for name in self.selection]

    def selected_subobjects(self):
        return list(self.parts)

    def clear_subobject_selection(self):
        self.parts.clear()
        self.cleared += 1

    def select_by_names(self, names):
        self.selection = list(names)

    def add_geometry(self, geom, name):
        self.added.append(geom)
        return str(len(self.added)), name + str(len(self.added))


class CurveConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ob.available():
            raise RuntimeError(f"OCCT required: {ob.unavailable_reason()}")
        cone = bridge.make_cone((0, 0, 0), 2, 4)
        cls.shape = bridge._occt_operand(cone)
        cls.proxy = bridge._triangulated_proxy(cls.shape)
        kernel.register_master(cls.proxy, cls.shape)
        kernel.record_recipe(cls.proxy, kernel.recipe_of(cone))

    def setUp(self):
        self.joined = r3.PolyCurve()
        self.assertTrue(self.joined.Append(arc(0)))
        self.assertTrue(self.joined.Append(r3.LineCurve(
            r3.Point3d(0, 2, 0), r3.Point3d(2, 0, 4))))
        self.assertTrue(self.joined.Append(arc(4)))
        self.ctx = Context(Joined=self.joined, Proxy=self.proxy,
                           A=arc(0), B=arc(4), Circle=r3.Circle(2).ToNurbsCurve(),
                           Point=r3.Point(r3.Point3d(0, 0, 0)))

    def consumers(self):
        return commands.LoftCmd, commands.EdgeSrfCmd, commands.PlanarSrfCmd

    def collector(self, kind):
        # Exercise the shared single-input path without enabling another consumer.
        class Collector(commands.Command):
            name = "Collector"
            accepts_curve_subobjects = True
            accepts_subobjects = True
            steps = [("Curve:", kind), ("Next curve:", commands.OBJECTS_MULTI)]
        return Collector()

    def assert_exact(self, actual, expected):
        self.assertIsInstance(actual, r3.NurbsCurve)
        self.assertTrue(actual.IsRational)
        self.assertEqual(actual.Degree, expected.Degree)
        self.assertEqual(list(actual.Knots), list(expected.Knots))
        self.assertEqual((actual.Domain.T0, actual.Domain.T1),
                         (expected.Domain.T0, expected.Domain.T1))
        for a, b in zip(actual.Points, expected.Points, strict=True):
            self.assertEqual((a.X, a.Y, a.Z, a.W), (b.X, b.Y, b.Z, b.W))
        for f in (0, .17, .5, .81, 1):
            t = expected.Domain.T0 + f * (expected.Domain.T1 - expected.Domain.T0)
            self.assertLess(actual.PointAt(t).DistanceTo(expected.PointAt(t)), 1e-12)

    def test_only_three_consumers_opt_in(self):
        enabled = {name for name, cls in vars(commands).items()
                   if isinstance(cls, type) and cls.__module__ == commands.__name__
                   and issubclass(cls, commands.Command)
                   and cls.accepts_curve_subobjects}
        self.assertEqual(enabled, {"LoftCmd", "EdgeSrfCmd", "PlanarSrfCmd"})
        for cls in self.consumers():
            self.assertTrue(cls.accepts_subobjects)
            self.assertIs(type(commands.COMMANDS[cls.name.lower()]), cls)
            self.assertEqual(cls.steps[0][1], commands.OBJECTS_MULTI)
        for key in ("fillet", "chamfer", "split", "trim", "delete", "join"):
            self.assertFalse(commands.COMMANDS[key].accepts_curve_subobjects, key)

    def test_single_component_and_typed_name_clear_selection(self):
        for source in ("component", "typed", "whole"):
            with self.subTest(source=source):
                cmd = self.collector(commands.OBJECTS)
                self.ctx.parts = [("Joined", "Segment", 2)] if source != "whole" else []
                self.ctx.selection = ["A"]
                msg, done = cmd.feed("B" if source == "typed" else "", self.ctx)
                expected = {"component": ("segment", "Joined", 2),
                            "typed": "B", "whole": "A"}[source]
                self.assertEqual(cmd.args, [expected], msg)
                self.assertFalse(done)
                self.assertEqual(self.ctx.parts, [])
                self.assertEqual(self.ctx.selection, [])
                self.assertEqual(cmd.feed("", self.ctx), ("Nothing selected", False))

    def test_single_rejects_multiple_components(self):
        cmd = self.collector(commands.OBJECTS)
        self.ctx.parts = [("Joined", "Segment", 0), ("Joined", "Segment", 2)]
        msg, done = cmd.feed("", self.ctx)
        self.assertIn("select one", msg)
        self.assertFalse(done)
        self.assertEqual(cmd.args, [])

    def test_multi_preserves_typed_names_distinct_refs_and_getter_order(self):
        cmd = self.collector(commands.OBJECTS_MULTI)
        self.assertFalse(cmd.feed("A", self.ctx)[1])
        self.assertEqual(cmd.args, [])
        self.ctx.selection = ["Circle", "Joined"]
        self.ctx.parts = [("Joined", "Segment", 2), ("Joined", "Segment", 0)]
        self.assertFalse(cmd.feed("", self.ctx)[1])
        self.assertEqual(cmd.args, [["A", ("segment", "Joined", 2),
                                    ("segment", "Joined", 0)]])
        self.assertEqual(cmd._pending_multi, [])
        self.assertEqual(self.ctx.parts, [])
        self.assertEqual(self.ctx.selection, [])

    def test_wrong_stale_components_never_fall_back_to_whole_names(self):
        invalid = [("Joined", "Face", 0), ("Joined", "Vertex", 0),
                   ("Joined", "Segment", 99), ("Gone", "Segment", 0),
                   ("Joined", "Segment", True), ("Proxy", "Edge", 1),
                   ("Point", "Edge", 2), ("Joined", "Edge", 1)]
        for cls in self.consumers():
            for selection in invalid:
                for begin in (False, True):
                    with self.subTest(command=cls.name, selection=selection, begin=begin):
                        cmd = cls()
                        self.ctx.parts = [("Joined", "Segment", 0), selection]
                        self.ctx.selection = ["Circle"]
                        msg, done = cmd.begin(self.ctx) if begin else cmd.feed("", self.ctx)
                        self.assertFalse(done, msg)
                        self.assertIn(cls.name, msg)
                        self.assertEqual(cmd.args, [])
                        self.assertEqual(cmd.step_index, 0)
                        self.assertEqual(len(self.ctx.parts), 2)
                        self.assertEqual(self.ctx.added, [])
        cmd = self.collector(commands.OBJECTS)
        self.ctx.parts = [("Joined", "Face", 0)]
        self.assertFalse(cmd.feed("", self.ctx)[1])
        self.assertEqual(cmd.args, [])

    def test_invalid_component_preserves_pending_names_for_retry(self):
        cmd = self.collector(commands.OBJECTS_MULTI)
        cmd.feed("A", self.ctx)
        self.ctx.parts = [("Joined", "Vertex", 0)]
        self.assertFalse(cmd.feed("", self.ctx)[1])
        self.assertEqual(cmd._pending_multi, ["A"])
        self.assertEqual(cmd.args, [])
        self.ctx.parts = [("Joined", "Segment", 2)]
        cmd.feed("", self.ctx)
        self.assertEqual(cmd.args, [["A", ("segment", "Joined", 2)]])

    def test_ui_getter_and_clearer_fallback(self):
        parts = [("Joined", "Segment", 2)]
        fake_ui = types.ModuleType("narwhal3d.ui")
        fake_ui.ordered_selected_subobjects = lambda: list(parts)
        fake_ui.clear_subobject_selection = parts.clear
        self.ctx.selected_subobjects = None
        self.ctx.clear_subobject_selection = None
        self.ctx.selection = ["Circle"]
        with patch.dict(sys.modules, {"narwhal3d.ui": fake_ui}), patch.object(
                pkg, "ui", fake_ui, create=True):
            cmd = self.collector(commands.OBJECTS)
            cmd.feed("", self.ctx)
        self.assertEqual(cmd.args, [("segment", "Joined", 2)])
        self.assertEqual(parts, [])
        self.assertEqual(self.ctx.selection, [])

    def test_failed_getter_does_not_fall_back(self):
        self.ctx.selection = ["Circle"]
        with patch.object(self.ctx, "selected_subobjects", side_effect=ValueError("bad pick")):
            cmd = commands.PlanarSrfCmd()
            msg, done = cmd.begin(self.ctx)
        self.assertFalse(done)
        self.assertIn("bad pick", msg)
        self.assertEqual(self.ctx.added, [])

    def test_resolver_returns_detached_exact_nurbs(self):
        before = snapshot(self.joined)
        refs = [("segment", "Joined", 0), ("segment", "Joined", 2), "A"]
        curves, bad = commands._curves_named(self.ctx, refs)
        self.assertEqual(bad, [])
        self.assertEqual([ref for ref, _ in curves], refs)
        for (_, curve), expected in zip(curves, [self.joined.SegmentCurve(0),
                                                self.joined.SegmentCurve(2),
                                                self.ctx.geometries["A"]], strict=True):
            self.assert_exact(curve, expected)
            self.assertIsNot(curve, expected)
            self.assertIsNone(kernel.recipe_of(curve))
            self.assertIsNone(kernel.master_of(curve))
            self.assertTrue(curve.Reverse())
        self.assertEqual(snapshot(self.joined), before)
        self.assert_exact(self.ctx.geometries["A"], arc(0))

    def test_rational_segment_profiles_reach_existing_producers(self):
        for cls, producer in ((commands.LoftCmd, "loft_brep"),
                              (commands.EdgeSrfCmd, "make_edge_srf")):
            for begin in (False, True):
                with self.subTest(command=cls.name, begin=begin):
                    self.ctx.added.clear()
                    self.ctx.parts = [("Joined", "Segment", 0), ("Joined", "Segment", 2)]
                    self.ctx.selection = ["Joined", "Circle"]
                    before = snapshot(self.joined)
                    cmd = cls()
                    with patch.object(bridge, producer, wraps=getattr(bridge, producer)) as called:
                        msg, done = cmd.begin(self.ctx) if begin else cmd.feed("", self.ctx)
                    self.assertTrue(done, msg)
                    called.assert_called_once()
                    profiles = called.call_args.args[0]
                    self.assertEqual(len(profiles), 2)
                    self.assert_exact(profiles[0], self.joined.SegmentCurve(0))
                    self.assert_exact(profiles[1], self.joined.SegmentCurve(2))
                    self.assertEqual(len(self.ctx.added), 1, msg)
                    output = self.ctx.added[0]
                    self.assertIsNotNone(kernel.recipe_of(output))
                    master = kernel.master_of(output)
                    self.assertIsNotNone(master)
                    # Without the optional exact-Brep writer, output is a display
                    # proxy; its unset display-edge tolerances do not describe the
                    # manufacturing surface. Validate the authoritative master.
                    self.assertTrue(ob.is_valid(master))
                    self.assertIsNone(kernel.honesty_problem(output))
                    self.assertEqual(snapshot(self.joined), before)
                    self.assertEqual(self.ctx.parts, [])
                    self.assertEqual(self.ctx.selection, [])

    def test_planar_surface_from_one_exact_brep_rim_keeps_parent_immutable(self):
        before = snapshot(self.proxy)
        recipe = kernel.recipe_of(self.proxy)
        master = kernel.master_of(self.proxy)
        self.ctx.parts = [("Proxy", "Edge", 3)]
        self.ctx.selection = ["Proxy"]
        cmd = commands.PlanarSrfCmd()
        with patch.object(bridge, "make_planar_srf", wraps=bridge.make_planar_srf) as called:
            msg, done = cmd.begin(self.ctx)
        self.assertTrue(done, msg)
        profile = called.call_args.args[0][0]
        self.assertTrue(profile.IsCircle())
        self.assertTrue(profile.IsRational)
        self.assertEqual(profile.Degree, 2)
        self.assertAlmostEqual(profile.TryGetCircle().Radius, 2, places=12)
        self.assertIsNone(kernel.recipe_of(profile))
        self.assertIsNone(kernel.master_of(profile))
        self.assertEqual(len(self.ctx.added), 1, msg)
        result = self.ctx.added[0]
        self.assertEqual(len(result.Faces), 1)
        self.assertFalse(result.IsSolid)
        tree = json.loads(json.dumps(kernel.recipe_of(result)))
        self.assertIsNotNone(tree)
        restored = ob.from_recipe(tree)
        self.assertEqual(ob.face_types(restored), {"PLANE": 1})
        self.assertAlmostEqual(ob.mass_properties(restored, solid=False)["area"],
                               4 * math.pi, places=10)
        profile.Reverse()
        self.assertEqual(snapshot(self.proxy), before)
        self.assertEqual(kernel.recipe_of(self.proxy), recipe)
        self.assertIs(kernel.master_of(self.proxy), master)
        self.assertIs(self.ctx.geometries["Proxy"], self.proxy)

    def test_loft_two_distinct_exact_brep_edges_of_same_parent(self):
        cylinder = bridge.make_cylinder((0, 0, 0), 2, 4)
        self.ctx.geometries["Cylinder"] = cylinder
        rims = [("Cylinder", "Edge", i) for i, _ in bridge.subobject_parts(cylinder)["edges"]
                if bridge.extract_subobject(cylinder, "edge", i).IsCircle()]
        self.assertEqual(len(rims), 2)
        before = snapshot(cylinder)
        self.ctx.parts = list(rims)
        self.ctx.selection = ["Cylinder"]
        cmd = commands.LoftCmd()
        msg, done = cmd.feed("", self.ctx)
        self.assertTrue(done, msg)
        self.assertEqual(cmd.args, [[ci.reference_from_selection(p) for p in rims]])
        self.assertEqual(len(self.ctx.added), 1, msg)
        self.assertIsNotNone(kernel.recipe_of(self.ctx.added[0]))
        self.assertEqual(snapshot(cylinder), before)

    def test_planar_open_segment_refused_without_using_closed_whole_selection(self):
        self.ctx.parts = [("Joined", "Segment", 0)]
        self.ctx.selection = ["Circle"]
        cmd = commands.PlanarSrfCmd()
        msg, done = cmd.feed("", self.ctx)
        self.assertTrue(done)
        self.assertIn("CLOSED", msg)
        self.assertEqual(cmd.args, [[("segment", "Joined", 0)]])
        self.assertEqual(self.ctx.added, [])

    def test_stranded_display_proxy_never_becomes_profile(self):
        orphan = self.proxy.Duplicate()
        orphan.SetUserString(kernel.MASTER_KEY, "missing-curve-consumer-master")
        kernel.record_recipe(orphan, {"kind": "unsupported-curve-consumer-test"})
        self.ctx.geometries["Orphan"] = orphan
        self.ctx.parts = [("Orphan", "Edge", 3)]
        self.ctx.selection = ["Circle"]
        with patch.object(bridge, "subobject_parts", side_effect=AssertionError("display topology")):
            cmd = commands.PlanarSrfCmd()
            msg, done = cmd.feed("", self.ctx)
        self.assertFalse(done)
        self.assertIn("analytic edge source is unavailable", msg)
        self.assertEqual(cmd.args, [])
        self.assertEqual(self.ctx.added, [])

    def test_stale_references_revalidated_at_execution(self):
        for cls in self.consumers():
            with self.subTest(command=cls.name):
                cmd = cls()
                cmd.args = [["Circle", ("segment", "Gone", 0)]]
                msg, done = cmd.execute(self.ctx)
                self.assertTrue(done)
                self.assertIn("segment 0 of Gone", msg)
                self.assertEqual(self.ctx.added, [])

    def test_whole_names_and_legacy_diagnostics(self):
        for cls, expected in ((commands.LoftCmd, "Loft: only curves can be lofted (Missing)"),
                              (commands.EdgeSrfCmd, "EdgeSrf: only curves can be edges (Missing)"),
                              (commands.PlanarSrfCmd, "PlanarSrf: select planar curves")):
            cmd = cls()
            cmd.feed("Missing", self.ctx)
            self.assertEqual(cmd.feed("", self.ctx), (expected, True))
        cmd = commands.PlanarSrfCmd()
        cmd.feed("Circle", self.ctx)
        cmd.feed("Point", self.ctx)
        msg, done = cmd.feed("", self.ctx)
        self.assertTrue(done)
        self.assertIn("Not curves, ignored: Point", msg)
        self.assertEqual(len(self.ctx.added), 1)
        # A non-opt-in command keeps its name-only behavior and does not read parts.
        cmd = commands.Command()
        cmd.steps = [("Objects:", commands.OBJECTS_MULTI), ("Next:", commands.NUMBER)]
        self.ctx.selection = ["A"]
        self.ctx.parts = [("Joined", "Face", 0)]
        cmd.feed("", self.ctx)
        self.assertEqual(cmd.args, [["A"]])
        self.assertEqual(self.ctx.parts, [("Joined", "Face", 0)])


def mutation_checks():
    """Re-break command routing in memory, so concurrent agents never see mutants."""
    cases = [
        (commands.Command, "_curve_component_refs", "return refs", "return []",
         "test_multi_preserves_typed_names_distinct_refs_and_getter_order"),
        (commands.Command, "begin", "if components:", "if False:",
         "test_wrong_stale_components_never_fall_back_to_whole_names"),
        (commands.Command, "_feed_objects_step", "if self.accepts_curve_subobjects and not text:",
         "if False:", "test_single_component_and_typed_name_clear_selection"),
        (commands.Command, "_curve_component_refs", "curve_inputs.resolve(ctx, ref)", "None",
         "test_wrong_stale_components_never_fall_back_to_whole_names"),
        (commands.Command, "_feed_objects_multi_step", "names.extend(self._curve_component_refs(ctx))",
         "names = self._curve_component_refs(ctx)",
         "test_multi_preserves_typed_names_distinct_refs_and_getter_order"),
        (commands.Command, "_feed_objects_multi_step", "self._clear_curve_input_selection(ctx)", "None",
         "test_multi_preserves_typed_names_distinct_refs_and_getter_order"),
        (commands, "_curves_named", "curve_inputs.resolve(ctx, ref)",
         "ctx.geom_by_name(ref if isinstance(ref, str) else ref[1])",
         "test_resolver_returns_detached_exact_nurbs"),
        (commands, "_curves_named", "raise\n", "continue\n",
         "test_stale_references_revalidated_at_execution"),
    ]
    for owner, name, old, new, test in cases:
        original = getattr(owner, name)
        source = textwrap.dedent(inspect.getsource(original))
        if source.count(old) != 1:
            raise AssertionError(f"mutation anchor changed: {name}: {old}")
        scope = dict(vars(commands))
        exec(compile(source.replace(old, new, 1), "<curve-consumer-mutation>", "exec"), scope)
        output = io.StringIO()
        with patch.object(owner, name, scope[name]):
            result = unittest.TextTestRunner(stream=output).run(
                unittest.TestSuite([CurveConsumerTests(test)]))
        if result.wasSuccessful() or result.testsRun != 1:
            raise AssertionError(f"mutation survived: {name}: {old}\n{output.getvalue()}")
        print(f"MUTATION RED: {name}: {old}")


if __name__ == "__main__":
    mutate = "--mutations" in sys.argv
    if mutate:
        sys.argv.remove("--mutations")
    result = unittest.main(exit=False, verbosity=2).result
    if not result.wasSuccessful():
        sys.exit(1)
    if mutate:
        mutation_checks()
        # Restored behavior must be green in the SAME process too.
        restored = unittest.TextTestRunner(verbosity=1).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(CurveConsumerTests))
        sys.exit(not restored.wasSuccessful())
