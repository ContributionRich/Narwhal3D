"""Actual SDK admission probe for the exact Join candidate; no UI/binary activation.

Run with bundled Python -B and an explicit NARWHAL_GEOMETRY_CORE candidate.
This minimal Python suite is required to exercise rhino3dm/OCP handles, exact
writer, recipe/.3dm persistence and bridge glue that native tests cannot call.
Bridge/kernel source is copied to private temporary storage before import;
worker/runtime helpers retain their source-package path for isolated execution.
The source runner requires run_admission_probe() before binary activation.
"""
import copy
import importlib
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BOX = {"kind": "box", "dx": 2, "dy": 3, "dz": 4}
CYLINDER = {"kind": "cylinder", "radius": 2, "height": 3}
MOVE = [1, 0, 0, 100, 0, 1, 0, -4, 0, 0, 1, 7, 0, 0, 0, 1]


class PrivateHost:
    def __init__(self):
        # Public temp storage only; no writes into the personal worktree.
        self.temp = tempfile.TemporaryDirectory(prefix="join-host-", dir=ROOT / "tmp")
        path = Path(self.temp.name)
        shutil.copytree(ROOT / "narwhal3d/kernel", path / "kernel",
                        ignore=shutil.ignore_patterns("__pycache__", "bin"))
        for name in ("bridge.py", "native_geometry.py"):
            shutil.copy2(ROOT / "narwhal3d" / name, path / name)
        self.name = "_join_surface_qualification"
        package = types.ModuleType(self.name)
        # Workers must launch from their real package path; copying their script
        # alone would lose the canonical narwhal3d import root in the child.
        package.__path__ = [str(path), str(ROOT / "narwhal3d")]
        sys.modules[self.name] = package
        self.bridge = importlib.import_module(self.name + ".bridge")
        self.kernel = importlib.import_module(self.name + ".kernel")
        self.occt = importlib.import_module(self.name + ".kernel.occt_backend")
        self.native = importlib.import_module(self.name + ".native_geometry")
        writer = ROOT / "narwhal3d/kernel/bin" / (
            f"brep_writer-{sys.platform}-{platform.machine().lower()}"
            + (".exe" if sys.platform == "win32" else ""))
        self.env = patch.dict(os.environ, {"NARWHAL3D_BREP_WRITER": str(writer)})
        self.env.start()

    def close(self):
        self.env.stop()
        for name in list(sys.modules):
            if name == self.name or name.startswith(self.name + "."):
                del sys.modules[name]
        self.temp.cleanup()

    def faces(self, recipe, prefix="f"):
        parent = self.occt.from_recipe(recipe)
        return [(f"{prefix}{i}", self.bridge.occt_backed(face, {
            "kind": "face", "index": i, "a": copy.deepcopy(recipe)}))
                for i, face in enumerate(self.occt.unique_faces(parent), 1)]


def run_admission_probe():
    """Required source runner entry: raises on failure, returns concrete source qualification."""
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(JoinSurfaces).run(result)
    if not result.wasSuccessful():
        raise AssertionError("\n".join(text for _, text in result.errors + result.failures))
    return {"suite": "exact Join SDK admission", "tests": result.testsRun,
            "platform": sys.platform, "activated": False}


class JoinSurfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = PrivateHost()
        cls.b, cls.k, cls.ob, cls.ng = (cls.host.bridge, cls.host.kernel,
                                       cls.host.occt, cls.host.native)
        if not cls.ng.join_exact_available():
            cls.host.close()
            raise RuntimeError("explicit source candidate with mapped Join/verification is required")

    @classmethod
    def tearDownClass(cls):
        cls.host.close()

    def assert_shape(self, geom, faces, types, volume=None):
        master = self.k.master_of(geom)
        self.assertIsNotNone(master)
        self.assertIsNone(self.k.honesty_problem(geom))
        self.assertTrue(geom.IsValid)
        self.assertEqual(len(geom.Faces), faces)
        self.assertEqual(geom.GetUserString(self.k.DOCUMENT_FORM_KEY), "exact")
        for shape in (master, self.ob.from_recipe(self.k.recipe_of(geom))):
            self.assertTrue(self.ob.is_valid(shape))
            self.assertEqual(self.ob.face_types(shape), types)
            self.assertEqual(self.ob.is_airtight(shape), volume is not None)
            if volume is not None:
                self.assertAlmostEqual(self.ob.volume(shape), volume, places=9)

    def test_real_box_cylinder_and_immutable_inputs(self):
        for recipe, count, types_, volume in [
                (BOX, 6, {"PLANE": 6}, 24.),
                (CYLINDER, 3, {"PLANE": 2, "CYLINDER": 1}, 12 * math.pi)]:
            items = self.host.faces(recipe)
            before = [geom.Encode() for _, geom in items]
            result = self.b.join_breps(items, 1e-6)
            self.assertEqual(len(result), 1)
            names, geom, closed = result[0]
            self.assertEqual(names, [name for name, _ in items])
            self.assertTrue(closed)
            self.assert_shape(geom, count, types_, volume)
            self.assertEqual(before, [geom.Encode() for _, geom in items])
            tree = self.k.recipe_of(geom)
            self.assertEqual(tree["kind"], "join_exact")
            self.assertEqual(len(tree["operands"]), count)
            self.assertEqual(tree["operands"][0]["a"], recipe)

    def test_partial_join_remains_open_and_cap_is_distinct(self):
        items = self.host.faces(CYLINDER)
        names, geom, closed = self.b.join_breps(items[:2], 1e-6)[0]
        self.assertEqual(names, [name for name, _ in items[:2]])
        self.assertFalse(closed)
        self.assert_shape(geom, 2, {"PLANE": 1, "CYLINDER": 1})
        capped = self.b.cap_planar_holes_occt(geom)
        self.assertIsNotNone(capped)
        self.assert_shape(capped, 3, {"PLANE": 2, "CYLINDER": 1}, 12 * math.pi)
        self.assertFalse(geom.IsSolid)

    def test_nested_join_tree_composes_without_consuming_singletons(self):
        items = self.host.faces(CYLINDER)
        first = self.b.join_breps(items[:2], 1e-6)[0][1]
        names, final, closed = self.b.join_breps([("open", first), items[2]], 1e-6)[0]
        self.assertEqual(names, ["open", items[2][0]])
        self.assertTrue(closed)
        self.assert_shape(final, 3, {"PLANE": 2, "CYLINDER": 1}, 12*math.pi)
        self.assertEqual(self.k.recipe_of(final)["operands"][0]["kind"], "join_exact")
        self.assertFalse(first.IsSolid)

    def test_multiple_groups_native_membership_and_singletons(self):
        first = self.host.faces(BOX, "a")
        second_recipe = dict(CYLINDER, xform=MOVE)
        second = self.host.faces(second_recipe, "b")
        single = self.host.faces(dict(BOX, xform=[1,0,0,300,0,1,0,0,0,0,1,0,0,0,0,1]), "s")[0]
        # Interleave selection order so grouping cannot use consecutive positions.
        items = [first[0], second[0], single, first[1], second[1], second[2]] + first[2:]
        before = [geom.Encode() for _, geom in items]
        result = self.b.join_breps(items, 1e-6)
        self.assertEqual(len(result), 2)
        self.assertEqual({tuple(names) for names, _, _ in result},
                         {tuple(name for name, _ in first), tuple(name for name, _ in second)})
        self.assertEqual(before, [geom.Encode() for _, geom in items])
        self.assertEqual(self.b.join_breps([first[0], single], 1e-6), [])
        solids = [("one", self.b.occt_backed(self.ob.from_recipe(BOX), BOX)),
                  ("two", self.b.occt_backed(self.ob.from_recipe(second_recipe), second_recipe))]
        self.assertEqual(self.b.join_breps(solids, 1e-6), [])

    def test_plain_sdk_faces_are_not_modified(self):
        import rhino3dm as r3
        source = self.b.make_box((0,0,0), (2,3,0), 4)
        items = [(str(i), f.DuplicateFace(False)) for i, f in enumerate(source.Faces)]
        before = [g.Encode() for _, g in items]
        result = self.b.join_breps(items, 1e-6)
        self.assertEqual(len(result), 1)
        self.assert_shape(result[0][1], 6, {"PLANE": 6}, 24.)
        self.assertEqual(before, [g.Encode() for _, g in items])

    def test_bridge_uses_supplied_tolerance_without_document_multiplier(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln, gp_Ax3
        first = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 0., 1., 0., 1.).Face()
        second = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 1.00005, 2., 0., 1.).Face()
        items = [(name, self.b.occt_backed(shape, self.ob.native_master_recipe(shape)))
                 for name, shape in [("left", first), ("right", second)]]
        self.assertEqual(self.b.join_breps(items, 3e-5), [])
        result = self.b.join_breps(items, 1e-4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], ["left", "right"])
        self.assertFalse(result[0][2])
        self.assert_shape(result[0][1], 2, {"PLANE": 2})

    def test_default_point02_and_unit_scaled_exact_results(self):
        for recipe, tolerance, count, types_, volume in [
                (BOX, .02, 6, {"PLANE": 6}, 24.),
                (CYLINDER, .02, 3, {"PLANE": 2, "CYLINDER": 1}, 12*math.pi),
                ({"kind": "box", "dx": 50.8, "dy": 76.2, "dz": 101.6},
                 .508, 6, {"PLANE": 6}, 24*25.4**3)]:
            items = self.host.faces(recipe)
            before = [g.Encode() for _, g in items]
            result = self.b.join_breps(items, tolerance)
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0][2])
            self.assert_shape(result[0][1], count, types_, volume)
            self.assertEqual(self.k.recipe_of(result[0][1])["tolerance"], tolerance)
            self.assertEqual(before, [g.Encode() for _, g in items])

    def test_default_boundary_gap_inside_and_outside_without_clipping(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Pln, gp_Ax3
        for gap, expected in [(.0199, 1), (.0201, 0)]:
            faces = [BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 0., 1., 0., 1.).Face(),
                     BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 1.+gap, 2., 0., 1.).Face()]
            items = [(str(i), self.b.occt_backed(f, self.ob.native_master_recipe(f)))
                     for i, f in enumerate(faces)]
            before = [g.Encode() for _, g in items]
            result = self.b.join_breps(items, .02)
            self.assertEqual(len(result), expected)
            if result:
                self.assert_shape(result[0][1], 2, {"PLANE": 2})
            self.assertEqual(before, [g.Encode() for _, g in items])

    def test_scale_and_extreme_tolerance_refusals_are_explicit(self):
        items = self.host.faces(BOX)
        data = self.ob.join_pack([self.k.master_of(g) for _, g in items])
        for tolerance, status in [(1e-9, -7), (.5001, -7), (1e100, -7),
                                  (1e-10, -1), (float("inf"), -1), (1e155, -1)]:
            outcome, groups = self.ng.join_exact(data, tolerance)
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.status, status)
            self.assertEqual(groups, ())
            self.assertIsNone(outcome.data)
        self.assertEqual(self.ng.join_exact(data, .02)[0].kind, "ok")

    def test_snapshot_tree_tampering_is_refused(self):
        geom = self.b.join_breps(self.host.faces(BOX), 1e-6)[0][1]
        tree = self.k.recipe_of(geom)
        for mutate in (lambda r: r["operands"][0]["a"].update(dy=7),
                       lambda r: r.update(result=self.ob.native_master_recipe(self.ob.from_recipe(CYLINDER))),
                       lambda r: r.update(version=2),
                       lambda r: r["result"].update(xform=MOVE)):
            broken = copy.deepcopy(tree)
            mutate(broken)
            with self.assertRaises(self.k.KernelError):
                self.ob.from_recipe(broken)

    def test_missing_capability_writer_and_unsafe_input_are_named(self):
        items = self.host.faces(BOX)
        with patch.object(self.ng, "join_exact_available", return_value=False):
            with self.assertRaisesRegex(self.k.KernelError, "capability unavailable"):
                self.b.join_breps(items, 1e-6)
        with patch.object(self.b._onbrep(), "exact_brep", return_value=None):
            with self.assertRaisesRegex(self.k.KernelError, "no faceted fallback"):
                self.b.join_breps(items, 1e-6)
        with self.assertRaises(self.k.KernelError):
            self.b.join_breps(items + [("duplicate", items[0][1])], 1e-6)
        with self.assertRaises(self.k.KernelError):
            self.b.join_breps(items, float("nan"))

    def test_document_fresh_reload_transform_boolean_and_step(self):
        import rhino3dm as r3
        geom = self.b.join_breps(self.host.faces(CYLINDER), 1e-6)[0][1]
        transform = r3.Transform.Identity()
        for i in range(4):
            for j in range(4):
                setattr(transform, f"M{i}{j}", MOVE[4*i+j])
        self.b.transform_geometry(geom, transform)
        self.assert_shape(geom, 3, {"PLANE": 2, "CYLINDER": 1}, 12*math.pi)
        with tempfile.TemporaryDirectory(prefix="join-document-", dir=ROOT/"tmp") as tmp:
            file = Path(tmp)/"joined.3dm"
            document = r3.File3dm()
            document.Objects.AddBrep(geom, r3.ObjectAttributes())
            self.assertTrue(document.Write(str(file), 8))
            process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                      "--reload", str(file)], capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertIn("fresh Join recipe reload PASS", process.stdout)


def fresh_reload(path):
    import rhino3dm as r3
    host = PrivateHost()
    try:
        ob, k = host.occt, host.kernel
        geom = r3.File3dm.Read(path).Objects[0].Geometry
        master = ob.from_recipe(k.recipe_of(geom))
        assert ob.is_airtight(master) and ob.face_types(master) == {"PLANE": 2, "CYLINDER": 1}
        assert abs(ob.volume(master)-12*math.pi) < 1e-9
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps(); BRepGProp.VolumeProperties_s(master, props)
        assert abs(props.CentreOfMass().X()-100) < 1e-9
        inner = ob.transform(ob.make_box(.5,.5,.5), MOVE)
        fused = ob.boolean(master, inner, "UNION")
        assert ob.is_airtight(fused) and ob.face_types(fused).get("CYLINDER") == 1
        output = Path(path).with_suffix(".step")
        ob.export_step(master, str(output))
        assert "CYLINDRICAL_SURFACE" in output.read_text()
        print("fresh Join recipe reload PASS")
    finally:
        host.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reload":
        fresh_reload(sys.argv[2])
    else:
        unittest.main()
