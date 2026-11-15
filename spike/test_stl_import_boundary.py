"""Real STL Import -> Rust -> rhino3dm boundary, without Blender/window startup.

Only command registration/UI state are stubbed. The importer, bridge mesh
constructor and document objects are real. Native failures are never skipped.
"""
import importlib.util
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "stl_import_boundary"
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules[PACKAGE] = pkg
commands = types.ModuleType(PACKAGE + ".commands")
commands.TEXT, commands.Command, commands.COMMANDS = "TEXT", object, {}
sys.modules[commands.__name__] = commands
sys.modules[PACKAGE + ".filedialog"] = types.ModuleType(PACKAGE + ".filedialog")
state = types.ModuleType(PACKAGE + ".state")
state.app_state = types.SimpleNamespace(ctx=None, model=None)
sys.modules[state.__name__] = state


def load(name):
    qualified = PACKAGE + "." + name
    spec = importlib.util.spec_from_file_location(qualified, ROOT / "narwhal3d" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


bridge = load("bridge")
native = load("native_stl")
stl = load("stl_writer")
imports = load("import_commands")
TRIANGLE = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
CUBE = [
    ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
    ((1, 0, 0), (0, 0, 0), (0, 1, 0)), ((1, 1, 0), (1, 0, 0), (0, 1, 0)),
    ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 1, 1), (1, 0, 1), (1, 0, 0)),
    ((0, 1, 0), (0, 0, 0), (0, 0, 1)), ((0, 1, 1), (0, 1, 0), (0, 0, 1)),
    ((1, 1, 0), (0, 1, 0), (0, 1, 1)), ((0, 1, 1), (1, 1, 1), (1, 1, 0)),
    ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((1, 0, 1), (0, 0, 1), (0, 0, 0)),
]


class Context:
    def __init__(self):
        file = r3.File3dm()
        layer = r3.Layer()
        layer.Name = "existing"
        file.Layers.Add(layer)
        file.Objects.AddPoint(r3.Point3d(5, 6, 7))
        self.model = types.SimpleNamespace(file=file)
        self.undo = types.SimpleNamespace(enabled=True, captured=False)
        self.changes = []
        self.selected = ["existing"]
        self.adds = 0

    def note_change(self, name):
        self.changes.append(name)
        self.undo.captured = True
        return True

    def add_geometry(self, geom, base, layer):
        self.adds += 1
        attrs = r3.ObjectAttributes()
        attrs.Name = f"{base}{self.adds:02d}"
        object_id = self.model.file.Objects.Add(geom, attrs)
        return object_id, attrs.Name

    def select_by_names(self, names):
        self.selected = names

    def snapshot(self):
        return (self.model.file.Encode(), tuple(self.changes), self.undo.enabled,
                self.undo.captured, tuple(self.selected), self.adds)


class STLImportBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native._binding()  # real ABI required, missing is not a skip

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stl-import-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.stl"

    def refuse(self, data, text):
        self.path.write_bytes(data)
        ctx = Context()
        before = ctx.snapshot()
        counter = native._native_mesh_calls
        with patch.object(bridge, "mesh_from_triangles", wraps=bridge.mesh_from_triangles) as construct, \
                patch.object(stl, "read_stl", side_effect=AssertionError("file-codec replay")):
            ok, message = imports.import_into(ctx, str(self.path))
        self.assertFalse(ok, message)
        self.assertIn(text, message)
        self.assertEqual(construct.call_count, 0, "unsafe data reached geometry construction")
        self.assertEqual(ctx.snapshot(), before, "refusal changed document, selection or Undo")
        self.assertEqual(native._native_mesh_calls, counter)

    def test_unsafe_ascii_small_and_old_large_threshold(self):
        for value in (1e100, -1e100, float("nan"), float("inf"), -float("inf")):
            triangle = ((value, 0., 0.), (1., 0., 0.), (0., 1., 0.))
            for count in (1, 1000):
                with self.subTest(value=value, count=count):
                    data = native.encode_stl([triangle] * count, binary=False)
                    if count == 1000:
                        self.assertGreaterEqual(len(data), 50000)
                    self.refuse(data, "invalid numeric coordinate at facet 1, vertex 1, component x")

    def test_unsafe_binary_small_and_old_large_threshold(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            triangle = ((value, 0., 0.), (1., 0., 0.), (0., 1., 0.))
            for count in (1, 1001):
                with self.subTest(value=value, count=count):
                    data = native.encode_stl([triangle] * count)
                    self.refuse(data, "nonfinite (NaN or infinity)")

    def test_late_invalid_vertex_and_degenerate_input_never_reach_bridge(self):
        bad = ((0., 0., 0.), (1., 0., 0.), (0., 1e100, 0.))
        data = native.encode_stl([TRIANGLE, bad], binary=False)
        self.refuse(data, "facet 2, vertex 3, component y")
        bad = ((float("inf"), 0., 0.),) * 3
        self.refuse(native.encode_stl([bad], binary=False), "invalid numeric coordinate")

    def test_max_finite_f32_survives_real_bridge_in_both_formats(self):
        largest = struct.unpack("<f", bytes.fromhex("ffff7f7f"))[0]
        for binary in (True, False):
            for value in (largest, -largest):
                with self.subTest(binary=binary, value=value):
                    triangle = ((value, 0., 0.), (0., 1., 0.), (0., 0., 1.))
                    stl.write_stl(self.path, [triangle], binary)
                    result = imports._read_stl(str(self.path), None)
                    mesh = result.items[0][0]
                    self.assertIsInstance(mesh, r3.Mesh)
                    self.assertEqual(mesh.Vertices[0].X, value)
                    self.assertTrue(all(math.isfinite(v) for p in mesh.Vertices for v in (p.X, p.Y, p.Z)))
        self.refuse(b"solid\nfacet\nvertex 3.4028236e38 0 0\nvertex 0 1 0\nvertex 0 0 1\nendfacet\n", "float32 range")

    def test_closed_cube_winding_welding_and_one_undo(self):
        for binary in (True, False):
            for repeats in (1, 1000):
                with self.subTest(binary=binary, repeats=repeats):
                    stl.write_stl(self.path, CUBE * repeats, binary)
                    before = native._native_mesh_calls
                    with patch.object(stl, "read_stl", side_effect=AssertionError("file-codec replay")):
                        got = imports._read_stl(str(self.path), None)
                    self.assertEqual(native._native_mesh_calls, before + 1)
                    mesh, name, layer = got.items[0]
                    self.assertEqual((name, layer), ("Mesh", None))
                    self.assertEqual(len(mesh.Vertices), 8)
                    self.assertEqual(len(mesh.Faces), 12 * repeats)
                    for face, expected in zip(mesh.Faces, CUBE * repeats):
                        actual = tuple(tuple((mesh.Vertices[i].X, mesh.Vertices[i].Y, mesh.Vertices[i].Z)) for i in face[:3])
                        self.assertEqual(actual, expected)
                    if repeats == 1:
                        self.assertTrue(mesh.IsClosed)
                        self.assertTrue(mesh.IsValid)
                        ctx = Context()
                        ok, message = imports.import_into(ctx, str(self.path))
                        self.assertTrue(ok, message)
                        self.assertEqual(ctx.changes, ["Import"])
                        self.assertEqual(ctx.adds, 1)
                        self.assertEqual(len(ctx.model.file.Objects), 2)
                        self.assertTrue(ctx.undo.enabled)
                        self.assertEqual(ctx.selected, ["Mesh01"])

    def test_import_keeps_binary_first_and_both_old_ascii_route_spellings(self):
        binary = native.encode_stl([TRIANGLE])
        binary = b"solid binary".ljust(80, b" ") + binary[80:]
        for data in (binary, native.encode_stl([TRIANGLE], binary=False).replace(b"solid ", b"SOLID ", 1),
                     b"\x0bsolid legacy\nfacet\nvertex\xa00 0 0 ignored\nvertex 1_0e-1 0 0\nvertex 0 1 0\nendfacet\n"):
            with self.subTest(header=data[:20]):
                self.path.write_bytes(data)
                got = imports._read_stl(str(self.path), None)
                mesh = got.items[0][0]
                self.assertEqual(len(mesh.Vertices), 3)
                self.assertEqual(len(mesh.Faces), 1)
                self.assertEqual(tuple(mesh.Faces[0])[:3], (0, 1, 2))
        self.refuse(b"SOLID legacy\nfacet\nvertex inf 0 0\nvertex 1 0 0\nvertex 0 1 0\nendfacet\n", "nonfinite")

    def test_file_codec_remains_permissive_not_a_geometry_certificate(self):
        for value in (1e100, float("inf"), float("nan")):
            triangle = ((value, 0., 0.), (1., 0., 0.), (0., 1., 0.))
            stl.write_stl(self.path, [triangle], binary=False)
            got = stl.read_stl(self.path)[0][0][0]
            self.assertTrue(math.isnan(got) if math.isnan(value) else got == value)
            self.refuse(self.path.read_bytes(), "invalid numeric coordinate")

    def test_missing_stale_and_failed_natives_refuse_without_replay(self):
        good = native.encode_stl([TRIANGLE])
        with patch.dict(os.environ, NARWHAL_PURE_CORE=str(Path(self.temp.name) / "missing.so")):
            self.refuse(good, "required STL native library")
        binding = native._binding()
        # Existing owned file exports are insufficient when the geometry export
        # is absent. No legacy optional/file reader may substitute for it.
        library = binding["library"]
        class FileOnly:
            def __getattr__(self, name):
                if name == "narwhal_pure_core_stl_import_mesh_decode":
                    raise AttributeError(name)
                return getattr(library, name)
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=FileOnly()):
            self.refuse(good, "stale")
        bad = dict(binding, stl_import_mesh_decode=lambda *args: -2)
        with patch.object(native, "_binding", return_value=bad):
            self.refuse(good, "inconsistent decode diagnostic")
        bad = dict(binding, stl_import_mesh_decode=lambda *args: 0)
        with patch.object(native, "_binding", return_value=bad):
            self.refuse(good, "malformed mesh counts")

    def test_malformed_input_and_limits_do_not_construct_or_mutate(self):
        self.refuse(b"solid\nfacet", "facet not closed by endfacet")
        self.refuse(b"binary short", "STL binary truncated")
        data = native.encode_stl([TRIANGLE])
        with patch.object(native, "_MAX_BYTES", 10):
            self.refuse(data, "byte limit")
        with patch.object(native, "_MAX_TRIANGLES", 0):
            self.refuse(data, "excessive coordinate count")
        binding = native._binding()
        # 1 does not contain a complete packed facet and must never allocate.
        bad = dict(binding, stl_file_size=lambda *args: 1)
        with patch.object(native, "_binding", return_value=bad):
            self.refuse(data, "excessive coordinate count")

    def test_empty_mesh_is_not_an_import_or_undo(self):
        self.path.write_bytes(native.encode_stl([]))
        ctx = Context()
        before = ctx.snapshot()
        with patch.object(bridge, "mesh_from_triangles", wraps=bridge.mesh_from_triangles) as construct:
            ok, message = imports.import_into(ctx, str(self.path))
        self.assertFalse(ok)
        self.assertIn("nothing", message)
        self.assertEqual(construct.call_count, 0)
        self.assertEqual(ctx.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
