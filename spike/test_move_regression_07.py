"""MAIN-REGRESSIONS-07: test the bridge, not just the native movability catalog.

Run in background Blender with --python-exit-code 1 and this script. Supply the
launcher-selected NARWHAL_GEOMETRY_CORE and NARWHAL_APPLICATION_CORE for Rust undo.
Optional arguments after `--`: --mutations, or --fresh-read FILE EXPECTED_JSON.
Python here exercises Blender/rhino3dm integration; geometry remains in OCCT/Rust.
No builds, staging, UI interaction, or shared artifact writes.
"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import bpy
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spike"))
# Load the real geometry modules without registering UI or starting workers.
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import bridge, kernel, native_geometry as native, undo
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.model import NarwhalModel
from test_local_brep_move import (independent_translated_matches,
                                  strict_vertex_containment, unchanged_faces_preserved)

VECTOR = (1.0, 2.0, 3.0)
RECT = [(0, 0, 0), (30, 0, 0), (30, 20, 0), (0, 20, 0)]
L_SHAPE = [(0, 0, 0), (30, 0, 0), (30, 10, 0),
           (10, 10, 0), (10, 30, 0), (0, 30, 0)]


def extrusion(points=RECT):
    profile = bridge.make_polyline(points, degree=1, closed=True)
    return bridge.make_extrusion(profile, 10)


def shape_of(geom):
    return kernel.master_of(geom) or bridge._occt_operand(geom)


def signature(shape):
    return {"faces": ob.face_types(shape),
            "topology": [len(ob.unique_faces(shape)), len(ob._unique_edges(shape)),
                         len(ob._unique_vertices(shape))],
            "vertices": sorted([list(ob._local_point(v)) for v in ob._unique_vertices(shape)]),
            "volume": ob.volume(shape)}


def assert_same(test, shape, expected):
    got = signature(shape)
    test.assertEqual(got["faces"], expected["faces"])
    test.assertEqual(got["topology"], expected["topology"])
    test.assertAlmostEqual(got["volume"], expected["volume"], delta=1e-7)
    for a, b in zip(got["vertices"], expected["vertices"]):
        test.assertLess(ob._local_distance(a, b), 1e-8)
    test.assertTrue(ob.is_valid(shape) and ob.is_airtight(shape))


class MoveRegression(unittest.TestCase):
    def certify_move(self, geom, kind, index=1, vector=VECTOR):
        before = shape_of(geom)
        original = signature(before)
        recipe = kernel.recipe_of(geom)
        geom.SetUserString("move-regression", "metadata survives")
        with patch.object(native, "move_component", wraps=native.move_component) as call:
            moved = bridge.move_subobject(geom, kind, index, vector)
        self.assertEqual(call.call_count, 1, "bridge bypassed native or retried a refusal")
        self.assertFalse(call.call_args.kwargs["live"], "release used reduced LIVE validation")
        master = kernel.master_of(moved)
        self.assertIsNotNone(master, "move only changed the proxy")
        self.assertIsNot(moved, geom)
        self.assertTrue(ob.is_valid(master) and ob.is_airtight(master))
        self.assertIsNone(kernel.honesty_problem(moved))
        self.assertEqual(signature(master)["topology"], original["topology"])
        self.assertTrue(strict_vertex_containment(before, master, kind, index, vector))
        self.assertEqual(len(independent_translated_matches(before, master, kind, index, vector)), 1)
        self.assertTrue(unchanged_faces_preserved(before, master, kind, index))
        self.assertEqual(moved.GetUserString("move-regression"), "metadata survives")
        updated = kernel.recipe_of(moved)
        self.assertEqual(updated["kind"], "local_move")
        self.assertEqual(updated["a"], recipe)
        self.assertEqual(updated["component"], kind)
        self.assertEqual(updated["vector"], list(vector))
        assert_same(self, ob.from_recipe(updated), signature(master))
        assert_same(self, shape_of(geom), original)
        self.assertEqual(kernel.recipe_of(geom), recipe, "source recipe was mutated")
        return moved

    def test_planar_producers_reach_native(self):
        fixtures = {"rectangle extrusion": extrusion,
                    "L extrusion": lambda: extrusion(L_SHAPE),
                    "box": lambda: bridge.make_box((0, 0, 0), (30, 20, 0), 10)}
        for name, producer in fixtures.items():
            for kind in ("vertex", "edge", "face"):
                with self.subTest(producer=name, kind=kind):
                    moved = self.certify_move(producer(), kind)
                    if name != "L extrusion" and kind in ("vertex", "edge"):
                        self.assertEqual(ob.face_types(shape_of(moved)).get("BSPLINE"),
                                         3 if kind == "vertex" else 2,
                                         "warped faces became planar facets")

    def test_standalone_polyline_grip_and_whole_curve_move(self):
        curve = bridge.make_polyline(RECT, degree=1, closed=True)
        before = bridge.control_points(curve)
        self.assertEqual(bridge.offset_control_points(curve, [0], VECTOR), 1)
        after = bridge.control_points(curve)
        self.assertTrue(curve.IsClosed)
        self.assertEqual(curve.Degree, 1)
        self.assertEqual(after[1:-1], before[1:-1])
        target = tuple(before[0][a] + VECTOR[a] for a in range(3))
        self.assertEqual(after[0], target)
        self.assertEqual(after[-1], target)
        moved = bridge.transform_geometry(curve, r3.Transform.Translation(*VECTOR))
        for old, new in zip(after, bridge.control_points(moved)):
            self.assertLess(ob._local_distance(
                tuple(old[a] + VECTOR[a] for a in range(3)), new), 1e-9)

    def test_standalone_surface_parts_remain_exact_and_open(self):
        surface = bridge.extract_subobject(extrusion(), "face", 1)
        before = shape_of(surface)
        for kind in ("vertex", "edge", "face"):
            with self.subTest(kind=kind):
                with patch.object(native, "move_component", wraps=native.move_component) as call:
                    moved = bridge.move_subobject(surface, kind, 1, VECTOR)
                self.assertEqual(call.call_count, 1)
                exact = kernel.master_of(moved)
                self.assertIsNotNone(exact)
                self.assertTrue(ob.is_valid(exact))
                self.assertFalse(ob.is_airtight(exact), "open surface falsely became a solid")
                self.assertEqual(len(ob.unique_faces(exact)), 1)
                self.assertIsNone(kernel.honesty_problem(moved))
                self.assertTrue(strict_vertex_containment(before, exact, kind, 1, VECTOR))
                replay = ob.from_recipe(kernel.recipe_of(moved))
                self.assertEqual(ob.face_types(exact), ob.face_types(replay))
                self.assertTrue(ob.is_valid(replay))
                self.assertFalse(ob.is_airtight(replay))
                if kind == "vertex":
                    self.assertEqual(ob.face_types(exact), {"BSPLINE": 1})

    def test_planar_compatibility_result_remains_editable(self):
        geom = extrusion()
        with patch.object(bridge, "_native_geometry", return_value=None):
            planar = bridge.move_subobject(geom, "face", 1, VECTOR)
        self.assertEqual(kernel.recipe_of(planar)["kind"], "polyhedron")
        self.certify_move(planar, "vertex")

    def test_sequential_curved_edit(self):
        first = self.certify_move(extrusion(), "vertex")
        self.certify_move(first, "vertex", 2, (0.2, 0.1, 0.3))

    def test_native_refusal_is_final_and_source_unchanged(self):
        geom = extrusion()
        before = signature(shape_of(geom))
        result = native.MoveResult("refused", status=-7, detail="regression sentinel refusal")
        with patch.object(native, "move_component", return_value=result) as call:
            with self.assertRaisesRegex(ValueError, "^regression sentinel refusal$"):
                bridge.move_subobject(geom, "face", 1, VECTOR)
        self.assertEqual(call.call_count, 1)
        assert_same(self, shape_of(geom), before)
        with self.assertRaises(bridge.SubObjectError):
            bridge.move_subobject(geom, "vertex", 999, VECTOR)

    def test_missing_native_keeps_honest_planar_fallback(self):
        geom = extrusion()
        before = signature(shape_of(geom))
        with patch.object(bridge, "_native_geometry", return_value=None):
            with self.assertRaisesRegex(ValueError, "keep every face planar"):
                bridge.move_subobject(geom, "vertex", 1, VECTOR)
            moved = bridge.move_subobject(geom, "face", 1, VECTOR)
        self.assertEqual(kernel.recipe_of(moved)["kind"], "polyhedron")
        self.assertTrue(ob.is_valid(shape_of(moved)) and ob.is_airtight(shape_of(moved)))
        assert_same(self, shape_of(geom), before)

    def test_rust_undo_redo_and_fresh_process_roundtrip(self):
        model = NarwhalModel()
        stack = undo.UndoStack()
        self.addCleanup(stack.close)
        self.assertEqual(stack.backend, "rust")
        oid, _attr = model.add_object(extrusion(), "Polygon")
        def geometry():
            return model.file.Objects.FindId(oid).Geometry
        original = signature(shape_of(geometry()))
        stack.begin("Move polygon vertex")
        self.assertTrue(stack.capture(model.snapshot()))
        moved = self.certify_move(geometry(), "vertex")
        expected = signature(shape_of(moved))
        self.assertTrue(model.replace_geometry(oid, moved))
        self.assertTrue(stack.end())
        stack.undo(model.snapshot(), restore=model.restore)
        assert_same(self, shape_of(geometry()), original)
        stack.redo(model.snapshot(), restore=model.restore)
        assert_same(self, shape_of(geometry()), expected)
        self.assertEqual(geometry().GetUserString("move-regression"), "metadata survives")
        with tempfile.TemporaryDirectory(prefix="narwhal-move-07-") as directory:
            path = Path(directory) / "move.3dm"
            self.assertTrue(model.save(str(path)))
            command = [bpy.app.binary_path, "-b", "-t", "1", "--factory-startup",
                       "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
                       "--", "--fresh-read", str(path), json.dumps(expected)]
            child = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            self.assertIn("FRESH MOVE ROUNDTRIP PASSED", child.stdout)


# Mutation is in-memory: another agent/process can never import a temporarily broken file.
NATIVE_PLANAR_ROUTE = '''    if isinstance(recipe, dict):
        # A planar source need not stay planar after editing. Try the certified native
        # engine before the planar-only compatibility path, regardless of recipe family.
        native_result = _move_modified_box_subobject(
            geom, shape, kind, i, vector, recipe, base, occt,
            live=live, native_only=True)
        if native_result is not None:
            return native_result
'''


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    assert native.available(), native.backend_status()
    binding = native._binding_cache
    print("BLENDER:", bpy.app.binary_path, bpy.app.version_string, flush=True)
    print("NATIVE:", native.backend_status(), "sha256=" + hashlib.sha256(
        binding.path.read_bytes()).hexdigest(), flush=True)
    if args and args[0] == "--fresh-read":
        geometry = r3.File3dm.Read(args[1]).Objects[0].Geometry
        assert kernel.master_of(geometry) is None, "fresh child inherited a master"
        assert bridge.rehydrate_document([geometry]) == (1, 0)
        assert_same(unittest.TestCase(), kernel.master_of(geometry), json.loads(args[2]))
        assert kernel.honesty_problem(geometry) is None
        print("FRESH MOVE ROUNDTRIP PASSED", flush=True)
        return
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(MoveRegression))
    assert result.wasSuccessful(), "MOVE REGRESSION FAILED"
    if "--mutations" in args:
        source = inspect.getsource(bridge.move_subobject)
        assert source.count(NATIVE_PLANAR_ROUTE) == 1, "mutation no longer matches the route"
        namespace = dict(bridge.__dict__)
        exec(compile(source.replace(NATIVE_PLANAR_ROUTE, ""), "<old-planar-routing>", "exec"), namespace)
        with patch.object(bridge, "move_subobject", namespace["move_subobject"]):
            red = runner.run(MoveRegression("test_planar_producers_reach_native"))
        assert not red.wasSuccessful(), "routing mutation survived"
        assert any("keep every face planar" in detail for _test, detail in red.errors), red.errors
        green = runner.run(MoveRegression("test_planar_producers_reach_native"))
        assert green.wasSuccessful(), "restored route failed"
        print("MUTATION RED / RESTORED GREEN", flush=True)
    print("MOVE REGRESSION 07 PASSED", flush=True)


if __name__ == "__main__":
    main()
