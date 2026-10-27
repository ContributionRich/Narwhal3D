"""Exact independent-end fillet acceptance; no Blender, GUI, builds or full suite.

Run with bundled Python, PYTHONPATH=vendor/occt and the documented OCCT LD_LIBRARY_PATH.
Use -B: mutations are compiled from source in memory, never written into shared modules.
"""
import copy
import inspect
import json
import math

from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg

import rhino3dm as r3
from narwhal3d import bridge
from narwhal3d import kernel
from narwhal3d.kernel import occt_backend as ob
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GeomAbs import GeomAbs_BSplineSurface
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec


def endpoint_witness(shape, anchors, radii):
    """Independent oracle: the box's cap contact vertices are exactly radius from its corner.

    Both ends are measured in WORLD space on the finished master, not its recipe or builder.
    Volume alone cannot distinguish swapped ends. This works after rigid moves/rotations.
    """
    vertices = [BRep_Tool.Pnt_s(v).Coord() for v in ob._unique_vertices(shape)]
    a, b = anchors
    length = math.dist(a, b)
    axis = [(b[i] - a[i]) / length for i in range(3)]
    measured = []
    for end, radius in zip(anchors, radii):
        on_cap = [p for p in vertices
                  if abs(sum((p[i] - end[i]) * axis[i] for i in range(3))) < 1e-6]
        assert on_cap, "no vertices on the original endpoint plane"
        distance = min(math.dist(p, end) for p in on_cap)
        assert abs(distance - radius) < 1e-6, (
            f"endpoint {end}: radius witness {distance}, expected {radius}")
        measured.append(distance)
    return measured


def smooth_solid(shape):
    assert ob.is_valid(shape), "invalid OCCT master"
    assert ob.is_airtight(shape), "non-airtight OCCT master"
    assert ob.face_types(shape) == {"PLANE": 6, "BSPLINE": 1}, ob.face_types(shape)
    blend = next(BRepAdaptor_Surface(face) for face in ob.unique_faces(shape)
                 if BRepAdaptor_Surface(face).GetType() == GeomAbs_BSplineSurface)
    assert blend.UDegree() >= 2 and blend.VDegree() >= 2, "faceted/ruled blend"


def source_box():
    return bridge.make_box((0, 0, 0), (10, 10, 0), 10)


def variable_box():
    source = source_box()
    anchors = bridge.bevel_edge_endpoints_occt(source, [1])[1]
    result = bridge.bevel_edges_occt(source, 1, edge_radii={1: (0.5, 1.5)})
    assert result is not None, bridge.last_occt_decline()
    return source, result, anchors


def transformed_anchors(anchors, xform):
    out = []
    for point in anchors:
        p = r3.Point3d(*point)
        p.Transform(xform)
        out.append([p.X, p.Y, p.Z])
    return out


class SectionPreviewTests(unittest.TestCase):
    def check_sections(self, records, anchors, radii):
        self.assertEqual(len(records), 2)
        for record, anchor, radius in zip(records, anchors, radii):
            self.assertLess(math.dist(record["anchor"], anchor), 1e-8)
            self.assertAlmostEqual(record["radius"], radius, places=8)
            arc = record["curve"]
            self.assertEqual(arc["ctype"], "arc")
            self.assertEqual(arc["center"], record["circle_center"])
            self.assertAlmostEqual(arc["radius"], radius, places=8)
            self.assertAlmostEqual(arc["end"] - arc["start"], math.pi / 2, places=8)
            curve = bridge.curve_from_recipe(arc)
            self.assertTrue(curve.IsValid and curve.IsRational)
            self.assertEqual(curve.Degree, 2)
            for point in (curve.PointAtStart, curve.PointAtEnd):
                p = [point.X, point.Y, point.Z]
                # On a right-angle box, exact contact points are r away from the source corner.
                self.assertAlmostEqual(math.dist(p, anchor), radius, places=7)
                self.assertAlmostEqual(math.dist(p, arc["center"]), radius, places=7)
            self.assertAlmostEqual(math.dist(record["grip_point"], arc["center"]), radius, places=8)
            middle = curve.PointAt((curve.Domain.T0 + curve.Domain.T1) / 2)
            self.assertLess(math.dist(record["grip_point"], [middle.X, middle.Y, middle.Z]), 1e-7)

    def test_constant_and_variable_sections_all_box_orientations(self):
        source = source_box()
        anchors = bridge.bevel_edge_endpoints_occt(source, range(1, 13))
        for index, ends in anchors.items():
            for radii in (None, {index: (0.5, 1.5)}):
                with self.subTest(edge=index, variable=radii is not None):
                    records = bridge.bevel_edge_sections_occt(source, 0.8, [index], edge_radii=radii)
                    self.assertIsNotNone(records, bridge.last_occt_decline())
                    self.assertEqual(set(records), {index})
                    self.check_sections(records[index], ends, (0.5, 1.5) if radii else (0.8, 0.8))
        # Multiple independent edges, with a default radius on the second one.
        other = next(i for i, (a, b) in anchors.items()
                     if a[:2] == [10.0, 10.0] and b[:2] == [10.0, 10.0])
        records = bridge.bevel_edge_sections_occt(source, 0.8, [other, 1],
                                                  edge_radii={1: (0.5, 1.5)})
        self.assertIsNotNone(records, bridge.last_occt_decline())
        self.check_sections(records[1], anchors[1], (0.5, 1.5))
        self.check_sections(records[other], anchors[other], (0.8, 0.8))

    def test_preview_no_never_builds_candidate_or_mesh(self):
        import OCP.BRepFilletAPI as fillet_api
        from contextlib import ExitStack
        source = source_box()
        shape = bridge._occt_operand(source)
        kernel.register_master(source, shape)
        anchors = bridge.bevel_edge_endpoints_occt(source, [1])[1]
        registry = kernel.master_registry_snapshot()
        before = ob.shape_to_bytes(shape, triangles=False)
        native_builder = fillet_api.BRepFilletAPI_MakeFillet
        calls = []
        class SimulationOnly:
            def __init__(self, shape):
                self.builder = native_builder(shape)
            def __getattr__(self, name):
                if name in ("Build", "Shape"):
                    raise AssertionError(f"Preview=No must not call {name}")
                return getattr(self.builder, name)
            def Simulate(self, contour):
                calls.append(contour)
                return self.builder.Simulate(contour)
        with ExitStack() as stack:
            stack.enter_context(patch.object(fillet_api, "BRepFilletAPI_MakeFillet", SimulationOnly))
            for module, name in [(ob, "bevel_edges"), (ob, "from_recipe"), (ob, "tessellate"),
                                 (bridge, "_proxy_from_occt"), (kernel, "register_master")]:
                stack.enter_context(patch.object(module, name,
                                                side_effect=AssertionError(f"unexpected {name}")))
            records = bridge.bevel_edge_sections_occt(source, 1, [1], edge_radii={1: (0.5, 1.5)})
            self.assertIsNotNone(records, bridge.last_occt_decline())
            self.check_sections(records[1], anchors, (0.5, 1.5))
            # Cached results hold plain geometry metadata, not a lazy builder or solid candidate.
            self.assertEqual(json.loads(json.dumps(records))["1"], records[1])
        self.assertEqual(calls, [1])
        self.assertEqual(kernel.master_registry_snapshot(), registry)
        self.assertEqual(ob.shape_to_bytes(shape, triangles=False), before)

    def test_section_anchors_after_reverse_reorder_and_move(self):
        source = source_box()
        move = r3.Transform.Translation(37, -21, 6)
        source = bridge.transform_geometry(source, move)
        anchors = bridge.bevel_edge_endpoints_occt(source, [1])[1]
        unique = ob._unique_edges
        def reversed_edges(shape):
            return [TopoDS.Edge(e.Reversed()) for e in reversed(unique(shape))]
        with patch.object(ob, "_unique_edges", reversed_edges):
            records = bridge.bevel_edge_sections_occt(source, 1, [12],
                edge_radii={12: (0.5, 1.5)}, edge_endpoints={12: anchors})
        self.assertIsNotNone(records, bridge.last_occt_decline())
        self.check_sections(records[12], anchors, (0.5, 1.5))
        reversed_anchors = list(reversed(anchors))
        records = bridge.bevel_edge_sections_occt(source, 1, [1],
            edge_radii={1: (0.5, 1.5)}, edge_endpoints={1: reversed_anchors})
        self.assertIsNotNone(records, bridge.last_occt_decline())
        self.check_sections(records[1], reversed_anchors, (0.5, 1.5))
        # Mutation: leaving native section order unchanged must not pass the endpoint oracle.
        code = inspect.getsource(ob.bevel_edge_sections)
        self.assertEqual(code.count("positions.reverse()"), 1)
        scope = dict(ob.__dict__)
        exec(compile(code.replace("positions.reverse()", "pass"), "<section mutation>", "exec"), scope)
        with patch.object(ob, "bevel_edge_sections", scope["bevel_edge_sections"]):
            broken = bridge.bevel_edge_sections_occt(source, 1, [1],
                edge_radii={1: (0.5, 1.5)}, edge_endpoints={1: reversed_anchors})
            with self.assertRaises(AssertionError):
                self.assertIsNotNone(broken, bridge.last_occt_decline())
            print("MUTATION RED (section ordering):", bridge.last_occt_decline())
        restored = bridge.bevel_edge_sections_occt(source, 1, [1],
            edge_radii={1: (0.5, 1.5)}, edge_endpoints={1: reversed_anchors})
        self.check_sections(restored[1], reversed_anchors, (0.5, 1.5))

    def test_ambiguous_section_preview_refuses_and_clears_reason_on_success(self):
        cylinder = ob.make_cylinder(5, 10)
        ends = ob.bevel_edge_endpoints(cylinder, range(1, len(ob._unique_edges(cylinder)) + 1))
        rim = next(i for i, (a, b) in ends.items() if math.dist(a, b) < 1e-8)
        with self.assertRaisesRegex(ob.KernelError, "closed contour"):
            ob.bevel_edge_sections(cylinder, 0.5, [rim])
        source = source_box()
        self.assertIsNone(bridge.bevel_edge_sections_occt(source, 0.5, [1],
            edge_endpoints={1: [[30, 0, 0], [30, 0, 10]]}))
        self.assertIn("do not match", bridge.last_occt_decline())
        self.assertIsNone(bridge.bevel_edge_sections_occt(source, 0.5, []))
        self.assertIn("no edges", bridge.last_occt_decline())
        self.assertIsNotNone(bridge.bevel_edge_sections_occt(source, 0.5, [1]))
        self.assertIsNone(bridge.last_occt_decline())


class VariableFilletTests(unittest.TestCase):
    def test_exact_master_recipe_and_input_unchanged(self):
        source = source_box()
        master = bridge._occt_operand(source)
        before = ob.shape_to_bytes(master, triangles=False)
        recipe_before = kernel.recipe_of(source)
        strings_before = source.GetUserStrings()
        anchors = bridge.bevel_edge_endpoints_occt(source, [1])[1]
        result = bridge.bevel_edges_occt(source, 1, edge_radii=[(1, (0.5, 1.5))])
        self.assertIsNotNone(result, bridge.last_occt_decline())
        smooth_solid(kernel.master_of(result))
        endpoint_witness(kernel.master_of(result), anchors, (0.5, 1.5))
        self.assertIsNone(kernel.honesty_problem(result))
        self.assertEqual(kernel.representation_of(result), kernel.ANALYTIC_MASTER)
        recipe = kernel.recipe_of(result)
        self.assertEqual(recipe["edges"], [1])
        self.assertEqual(recipe["edge_radii"], [[0.5, 1.5]])
        self.assertEqual(recipe["edge_endpoints"], [anchors])
        self.assertEqual(len(recipe["edge_targets"]), 1)
        replay = ob.from_recipe(json.loads(json.dumps(recipe)))
        smooth_solid(replay)
        endpoint_witness(replay, anchors, (0.5, 1.5))
        self.assertEqual(ob.shape_to_bytes(master, triangles=False), before)
        self.assertEqual(kernel.recipe_of(source), recipe_before)
        self.assertEqual(source.GetUserStrings(), strings_before)
        print("MEASURED variable master: 6 PLANE + 1 BSPLINE; endpoint radii 0.5, 1.5")

    def test_every_box_edge_and_multiple_independent_edges(self):
        source = ob.make_box(10, 10, 10)
        anchors = ob.bevel_edge_endpoints(source, range(1, 13))
        for index, ends in anchors.items():
            with self.subTest(edge=index):
                result = ob.bevel_edges(source, 1, edge_radii={index: (0.5, 1.5)})
                smooth_solid(result)
                endpoint_witness(result, ends, (0.5, 1.5))
        # Opposite vertical corners; selection and mapping iteration orders intentionally differ.
        other = next(i for i, (a, b) in anchors.items()
                     if a[:2] == [10.0, 10.0] and b[:2] == [10.0, 10.0])
        result = ob.bevel_edges(source, 1, [other, 1],
                               edge_radii=[(1, (0.5, 1.5)), (other, (0.8, 1.2))])
        self.assertTrue(ob.is_valid(result) and ob.is_airtight(result))
        endpoint_witness(result, anchors[1], (0.5, 1.5))
        endpoint_witness(result, anchors[other], (0.8, 1.2))

    def test_mixed_default_radius_recipe(self):
        source = source_box()
        shape = bridge._occt_operand(source)
        anchors = ob.bevel_edge_endpoints(shape, range(1, 13))
        other = next(i for i, (a, b) in anchors.items()
                     if a[:2] == [10.0, 10.0] and b[:2] == [10.0, 10.0])
        result = bridge.bevel_edges_occt(source, 0.8, edge_indices=[other, 1, other],
                                        edge_radii={1: (0.5, 1.5)},
                                        edge_endpoints={1: anchors[1]})
        self.assertIsNotNone(result, bridge.last_occt_decline())
        recipe = kernel.recipe_of(result)
        self.assertEqual(recipe["edges"], [other, 1])
        self.assertEqual(recipe["edge_radii"], [[0.8, 0.8], [0.5, 1.5]])
        for candidate in (kernel.master_of(result), ob.from_recipe(recipe)):
            self.assertTrue(ob.is_valid(candidate) and ob.is_airtight(candidate))
            endpoint_witness(candidate, anchors[1], (0.5, 1.5))
            endpoint_witness(candidate, anchors[other], (0.8, 0.8))

    def test_scalar_compatibility(self):
        source = source_box()
        scalar = bridge.bevel_edges_occt(source, 0.5, edge_indices=[1])
        self.assertIsNotNone(scalar, bridge.last_occt_decline())
        shape = kernel.master_of(scalar)
        self.assertEqual(ob.face_types(shape), {"PLANE": 6, "CYLINDER": 1})
        recipe = kernel.recipe_of(scalar)
        self.assertNotIn("edge_radii", recipe)
        self.assertEqual(ob.face_types(ob.from_recipe(recipe)), ob.face_types(shape))
        legacy = copy.deepcopy(recipe)
        del legacy["edge_targets"]
        self.assertEqual(ob.face_types(ob.from_recipe(legacy)), ob.face_types(shape))
        constant = ob.bevel_edges(bridge._occt_operand(source), 1, edge_radii={1: (0.5, 0.5)})
        self.assertEqual(ob.face_types(constant), ob.face_types(shape))
        all_edges = ob.bevel_edges(ob.make_box(10, 10, 10), 0.5)
        self.assertEqual(ob.face_types(all_edges), {"PLANE": 6, "CYLINDER": 12, "SPHERE": 8})
        self.assertTrue(ob.is_airtight(all_edges))
        cylinder = ob.make_cylinder(5, 10)
        ends = ob.bevel_edge_endpoints(cylinder, range(1, len(ob._unique_edges(cylinder)) + 1))
        rim = next(i for i, (a, b) in ends.items() if math.dist(a, b) < 1e-8)
        rounded = ob.bevel_edges(cylinder, 0.5, [rim])
        self.assertTrue(ob.is_valid(rounded) and ob.is_airtight(rounded))
        self.assertIn("TORUS", ob.face_types(rounded))

    def test_reordered_and_reversed_topology_replay(self):
        _, result, anchors = variable_box()
        recipe = kernel.recipe_of(result)
        unique = ob._unique_edges
        def reversed_edges(shape):
            return [TopoDS.Edge(e.Reversed()) for e in reversed(unique(shape))]
        with patch.object(ob, "_unique_edges", reversed_edges):
            replay = ob.from_recipe(recipe)
        smooth_solid(replay)
        endpoint_witness(replay, anchors, (0.5, 1.5))
        # Explicit anchors also let live callers preserve direction across a reversed source edge.
        shape = ob.make_box(10, 10, 10)
        with patch.object(ob, "_unique_edges", reversed_edges):
            live = ob.bevel_edges(shape, 1, edge_radii={12: (0.5, 1.5)},
                                  edge_endpoints={12: anchors})
        endpoint_witness(live, anchors, (0.5, 1.5))

    def test_transform_before_and_after_fillet_and_fresh_file_reload(self):
        source = source_box()
        source = bridge.transform_geometry(source, r3.Transform.Rotation(
            math.pi, r3.Vector3d(0, 0, 1), r3.Point3d(0, 0, 0)))
        source = bridge.transform_geometry(source, r3.Transform.Translation(100, 20, 3))
        anchors = bridge.bevel_edge_endpoints_occt(source, [1])[1]
        result = bridge.bevel_edges_occt(source, 1, edge_radii={1: (0.5, 1.5)})
        self.assertIsNotNone(result, bridge.last_occt_decline())
        xform = r3.Transform.Rotation(math.pi, r3.Vector3d(1, 0, 0), r3.Point3d(0, 0, 0))
        result = bridge.transform_geometry(result, xform)
        anchors = transformed_anchors(anchors, xform)
        move = r3.Transform.Translation(7, -9, 13)
        result = bridge.transform_geometry(result, move)
        anchors = transformed_anchors(anchors, move)
        recipe = kernel.recipe_of(result)
        endpoint_witness(kernel.master_of(result), anchors, (0.5, 1.5))
        endpoint_witness(ob.from_recipe(recipe), anchors, (0.5, 1.5))
        with tempfile.TemporaryDirectory(prefix="variable-fillet-") as directory:
            path = Path(directory) / "fillet.3dm"
            expected = Path(directory) / "expected.json"
            doc = r3.File3dm()
            doc.Objects.AddBrep(result, r3.ObjectAttributes())
            self.assertTrue(doc.Write(str(path), 8))
            expected.write_text(json.dumps({"anchors": anchors, "recipe": recipe}))
            loaded = r3.File3dm.Read(str(path)).Objects[0].Geometry
            self.assertEqual(kernel.recipe_of(loaded), recipe)
            self.assertTrue(bridge.rehydrate_master(loaded))
            endpoint_witness(kernel.master_of(loaded), anchors, (0.5, 1.5))
            child = subprocess.run([sys.executable, "-B", __file__, "--read", str(path),
                                    str(expected)], capture_output=True, text=True, timeout=60)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            print(child.stdout.strip())

    def test_refusals_are_explicit_no_stale_candidate(self):
        source, good, _ = variable_box()
        self.assertIsNotNone(good)
        snapshot = kernel.master_registry_snapshot()
        original = ob.shape_to_bytes(bridge._occt_operand(source), triangles=False)
        # MEASURED: OCCT IsDone is true at R2=100, but its resulting shape is invalid.
        cases = [({1: (0.5, 100)}, "invalid or not airtight"),
                 ({1: (0, 1)}, "positive"), ({1: (-1, 1)}, "positive"),
                 ({1: (math.nan, 1)}, "finite"), ({1: (1, math.inf)}, "finite"),
                 ({99: (0.5, 1.5)}, "out of range"), ({}, "no endpoint"),
                 ([(1, (0.5, 1)), (1, (1, 2))], "duplicate")]
        for radii, reason in cases:
            with self.subTest(radii=radii):
                self.assertIsNone(bridge.bevel_edges_occt(source, 1, edge_radii=radii))
                self.assertIn(reason, bridge.last_occt_decline())
                self.assertEqual(kernel.master_registry_snapshot(), snapshot)
                self.assertEqual(ob.shape_to_bytes(bridge._occt_operand(source), triangles=False),
                                 original)
        self.assertIsNone(bridge.bevel_edges_occt(source, 1, chamfer=True,
                                                edge_radii={1: (0.5, 1.5)}))
        self.assertIn("chamfers", bridge.last_occt_decline())
        self.assertIsNone(bridge.bevel_edges_occt(source, 1, edge_indices=[2],
                                                edge_radii={1: (0.5, 1.5)}))
        self.assertIn("outside", bridge.last_occt_decline())
        self.assertIsNone(bridge.bevel_edges_occt(source, 1, edge_radii={1: (0.5, 1.5)},
                                                edge_endpoints={1: [[30, 0, 0], [30, 0, 10]]}))
        self.assertIn("do not match", bridge.last_occt_decline())
        again = bridge.bevel_edges_occt(source, 1, edge_radii={1: (0.5, 1.5)})
        self.assertIsNotNone(again)
        self.assertIsNone(bridge.last_occt_decline())
        recipe = kernel.recipe_of(again)
        del recipe["edge_endpoints"]
        with self.assertRaisesRegex(ob.KernelError, "malformed"):
            ob.from_recipe(recipe)

    def test_closed_and_multi_edge_contour_refusals(self):
        cylinder = ob.make_cylinder(5, 10)
        ends = ob.bevel_edge_endpoints(cylinder, range(1, len(ob._unique_edges(cylinder)) + 1))
        rim = next(i for i, (a, b) in ends.items() if math.dist(a, b) < 1e-8)
        with self.assertRaisesRegex(ob.KernelError, "closed contour"):
            ob.bevel_edges(cylinder, 1, edge_radii={rim: (0.5, 1.5)})
        polygon = BRepBuilderAPI_MakePolygon()
        for point in [(0, 0, 0), (0, 0, 5), (0, 0, 10), (0, 10, 10), (0, 10, 0)]:
            polygon.Add(gp_Pnt(*point))
        polygon.Close()
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        split = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(),
                                     gp_Vec(10, 0, 0)).Shape()
        ends = ob.bevel_edge_endpoints(split, range(1, len(ob._unique_edges(split)) + 1))
        halves = [i for i, (a, b) in ends.items() if a[:2] == [0.0, 0.0] and b[:2] == [0.0, 0.0]]
        self.assertEqual(len(halves), 2)
        self.assertEqual(set(ob.bevel_affected_edges(split, 0.5, [halves[0]])), set(halves))
        with self.assertRaisesRegex(ob.KernelError, "multi-edge tangent contour"):
            ob.bevel_edge_sections(split, 0.5, [halves[0]])
        with self.assertRaisesRegex(ob.KernelError, "multi-edge tangent contour"):
            ob.bevel_edges(split, 1, edge_radii={halves[0]: (0.5, 1.5)})
        with self.assertRaisesRegex(ob.KernelError, "conflicting radius laws"):
            ob.bevel_edges(split, 1, edge_radii={halves[0]: (0.5, 0.5), halves[1]: (1, 1)})

    def test_mutations_collapse_and_swap_are_red(self):
        original = ob._set_bevel_laws
        source = inspect.getsource(original)
        call = "mk.SetRadius(r0, r1, contour, 1)"
        self.assertEqual(source.count(call), 1)
        shape = ob.make_box(10, 10, 10)
        anchors = ob.bevel_edge_endpoints(shape, [1])[1]
        for label, mutated_call in [("collapse R2=R1", "mk.SetRadius(r0, r0, contour, 1)"),
                                    ("swap endpoints", "mk.SetRadius(r1, r0, contour, 1)")]:
            scope = dict(ob.__dict__)
            exec(compile(source.replace(call, mutated_call), "<fillet mutation>", "exec"), scope)
            with patch.object(ob, "_set_bevel_laws", scope[original.__name__]):
                broken = ob.bevel_edges(shape, 1, edge_radii={1: (0.5, 1.5)})
                with self.assertRaisesRegex(AssertionError, "radius witness") as caught:
                    endpoint_witness(broken, anchors, (0.5, 1.5))
                print(f"MUTATION RED ({label}): {caught.exception}")
            endpoint_witness(ob.bevel_edges(shape, 1, edge_radii={1: (0.5, 1.5)}),
                             anchors, (0.5, 1.5))
        self.assertIs(ob._set_bevel_laws, original)


def read_fresh(path, expected_path):
    expected = json.loads(Path(expected_path).read_text())
    doc = r3.File3dm.Read(path)
    geom = doc.Objects[0].Geometry
    assert kernel.master_of(geom) is None, "fresh process unexpectedly has the saved master"
    assert kernel.recipe_of(geom) == expected["recipe"]
    assert bridge.rehydrate_master(geom), bridge.last_occt_decline()
    assert kernel.honesty_problem(geom) is None
    smooth_solid(kernel.master_of(geom))
    radii = endpoint_witness(kernel.master_of(geom), expected["anchors"], (0.5, 1.5))
    print(f"FRESH PROCESS .3dm replay: valid, airtight, smooth, endpoint witnesses {radii}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--read":
        read_fresh(sys.argv[2], sys.argv[3])
    else:
        unittest.main(verbosity=2)
