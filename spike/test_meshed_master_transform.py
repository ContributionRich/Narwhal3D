"""Nonuniform transforms must not consume a shared master's display triangulation.

Run with bundled Python (no Blender window or native rebuild):
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=vendor/occt \
      tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 -B \
      spike/test_meshed_master_transform.py

MEASURED: a Gumball-extruded circle, unlike a primitive cylinder, exposes invalid
edge polygons after GTransform of its meshed master. Use the real producer and
real display/picking meshers, not a mock or a straight-edged solid.
"""
import json
import math
import os
import sys
import types
import unittest

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg

from narwhal3d import bridge, kernel
from narwhal3d.kernel import occt_backend as occt
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_GTransform
from OCP.BRepCheck import BRepCheck_Edge, BRepCheck_InvalidPolygonOnTriangulation
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ


CASES = (
    ("X", (2.0, 1.0, 1.0), 0.0),
    ("Y", (1.0, 2.0, 1.0), 0.0),
    ("Z", (1.0, 1.0, 2.0), 0.0),
    ("mirrored_X", (-2.0, 1.0, 1.0), 0.0),
    ("shear_XY", (1.0, 1.0, 1.0), 0.5),
    ("uniform", (2.0, 2.0, 2.0), 0.0),
    ("mirror_similarity", (-1.0, 1.0, 1.0), 0.0),
)
MESHERS = (occt.tessellate_with_edges, occt.subobject_topology)


def make_source(region=False):
    circle = bridge.make_circle((0.0, 0.0, 0.0), 3.0)
    if region:
        face = bridge.make_planar_srf([circle])[0]
        return bridge.make_extrude_srf(face, 5.0, direction=(0.0, 0.0, 1.0))
    return bridge.gumball_extrude(circle, (0.0, 0.0, 1.0), 5.0)


def xform_for(scales, shear):
    xform = r3.Transform.Scale(r3.Plane.WorldXY(), *scales)
    xform.M01 = shear
    return xform


def mesh_snapshot(shape):
    """Hold mesh handles too: a clear/rebuild of the original is not preservation."""
    rows = []
    for face in occt.unique_faces(shape):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            raise AssertionError("fixture was not meshed through the display path")
        nodes = tuple(tri.Node(i).Coord() for i in range(1, tri.NbNodes() + 1))
        triangles = tuple(tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1))
        polygons = []
        for edge in occt._unique_edges(face):
            poly = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
            polygons.append(None if poly is None else tuple(poly.Nodes()))
        rows.append((tri, nodes, triangles, tuple(polygons)))
    return rows


def raw_general_transform(shape, matrix):
    """Negative control: pre-fix OCCT call, intentionally bypassing the backend guard."""
    g = gp_GTrsf()
    g.SetVectorialPart(gp_Mat(matrix[0], matrix[1], matrix[2],
                             matrix[4], matrix[5], matrix[6],
                             matrix[8], matrix[9], matrix[10]))
    g.SetTranslationPart(gp_XYZ(matrix[3], matrix[7], matrix[11]))
    return BRepBuilderAPI_GTransform(shape, g, True).Shape()


class MeshedMasterTransformTests(unittest.TestCase):
    def assert_shape(self, shape, scales, shear):
        self.assertTrue(occt.is_valid(shape))
        self.assertTrue(occt.is_airtight(shape))
        self.assertEqual(occt.solid_count(shape), 1)
        self.assertEqual(occt.face_count(shape), 3)
        self.assertEqual(occt.face_types(shape).get("PLANE"), 2)
        sx, sy, sz = scales
        radius_x = 3.0 * math.hypot(sx, shear)
        radius_y = 3.0 * abs(sy)
        expected = ((-radius_x, -radius_y, 0.0),
                    (radius_x, radius_y, 5.0 * sz))
        for actual_row, expected_row in zip(occt.tight_bbox(shape), expected):
            for actual, want in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual, want, delta=1e-6)
        volume = math.pi * 9.0 * 5.0 * abs(sx * sy * sz)
        self.assertAlmostEqual(occt.volume(shape), volume, delta=volume * 1e-8)
        sides = [f for f in occt.unique_faces(shape) if occt.face_plane(f) is None]
        self.assertEqual(len(sides), 1)
        surface = BRepAdaptor_Surface(sides[0])
        # Inverse-map points on the exact surface, not tessellation vertices. These
        # samples VERIFY its equation; no sampled shape is ever constructed.
        for i in range(25):
            u = surface.FirstUParameter() + (surface.LastUParameter()
                                            - surface.FirstUParameter()) * i / 24
            for j in range(5):
                v = surface.FirstVParameter() + (surface.LastVParameter()
                                                - surface.FirstVParameter()) * j / 4
                p = surface.Value(u, v)
                y = p.Y() / sy
                x = (p.X() - shear * y) / sx
                self.assertAlmostEqual((x * x + y * y) / 9.0, 1.0, delta=1e-9)
                self.assertGreaterEqual(p.Z() / sz, -1e-7)
                self.assertLessEqual(p.Z() / sz, 5.0 + 1e-7)

    def assert_cache_unchanged(self, shape, before):
        after = mesh_snapshot(shape)
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            self.assertIs(old[0], new[0], "transform replaced the shared display mesh")
            self.assertEqual(old[1:], new[1:], "transform changed cached display data")

    def test_display_mesh_is_the_trigger(self):
        matrix = bridge._xform_matrix(xform_for((2.0, 1.0, 1.0), 0.0))
        for mesher in MESHERS:
            with self.subTest(mesher=mesher.__name__):
                geom = make_source()
                produced = kernel.master_of(geom)
                # Proxy construction may already have meshed the producer. Make
                # the diagnostic's unmeshed baseline explicit without changing
                # that producer or the separate production-transform matrix.
                mesher(produced)
                producer_cache = mesh_snapshot(produced)
                master = BRepBuilderAPI_Copy(produced, True, False).Shape()
                self.assertTrue(all(BRep_Tool.Triangulation_s(face, TopLoc_Location()) is None
                                    for face in occt.unique_faces(master)))
                self.assert_shape(master, (1.0, 1.0, 1.0), 0.0)
                self.assert_cache_unchanged(produced, producer_cache)
                self.assertTrue(occt.is_airtight(raw_general_transform(master, matrix)))
                mesher(master)
                cache = mesh_snapshot(master)
                self.assertTrue(occt.is_airtight(master))
                broken = raw_general_transform(master, matrix)
                self.assertFalse(occt.is_valid(broken))
                self.assertEqual(occt.solid_count(broken), 1)
                statuses = [BRepCheck_Edge(e).CheckPolygonOnTriangulation(e)
                            for e in occt._unique_edges(broken)]
                self.assertIn(BRepCheck_InvalidPolygonOnTriangulation, statuses)
                self.assertFalse(occt.is_airtight(raw_general_transform(
                    BRepBuilderAPI_Copy(master, True, True).Shape(), matrix)))
                clean = BRepBuilderAPI_Copy(master, True, False).Shape()
                self.assertTrue(occt.is_airtight(raw_general_transform(clean, matrix)))
                self.assert_cache_unchanged(master, cache)
                self.assert_cache_unchanged(produced, producer_cache)

    def test_meshed_master_transforms_and_roundtrips(self):
        for region in (False, True):
            for mesher in MESHERS:
                for label, scales, shear in CASES:
                    with self.subTest(region=region, mesher=mesher.__name__, case=label):
                        original = make_source(region)
                        master = kernel.master_of(original)
                        recipe = kernel.recipe_of(original)
                        mesher(master)
                        cache = mesh_snapshot(master)
                        original_bytes = occt.shape_to_bytes(master, triangles=True)
                        original_strings = dict(original.GetUserStrings())
                        duplicate = original.Duplicate()
                        self.assertTrue(kernel.master_of(duplicate).IsSame(master))
                        xform = xform_for(scales, shear)
                        matrix = bridge._xform_matrix(xform)
                        if not occt.is_similarity(matrix):
                            raw = occt.transform(master, matrix, recognise=False)
                            self.assertTrue(occt.is_airtight(raw),
                                            "GTransform failed before face recognition")
                        result = bridge.transform_geometry(duplicate, xform)
                        transformed = kernel.master_of(result)
                        self.assertIsNotNone(transformed)
                        self.assertFalse(transformed.IsSame(master))
                        self.assert_shape(transformed, scales, shear)
                        self.assertIsNone(kernel.honesty_problem(result))
                        updated = kernel.recipe_of(result)
                        self.assertEqual(updated["kind"], "prism" if region else "extrusion")
                        self.assert_shape(occt.from_recipe(json.loads(json.dumps(updated))),
                                          scales, shear)

                        # Actual .3dm serialization in memory, with the registry empty:
                        # a surviving process-local key must not make reload appear sound.
                        doc = r3.File3dm()
                        doc.Objects.AddBrep(result, r3.ObjectAttributes())
                        encoded = doc.Encode()
                        registry = kernel.master_registry_snapshot()
                        try:
                            kernel.forget_masters()
                            restored = list(r3.File3dm.Decode(encoded).Objects)[0].Geometry
                            self.assertIsNone(kernel.master_of(restored))
                            self.assertTrue(bridge.rehydrate_master(restored))
                            self.assert_shape(kernel.master_of(restored), scales, shear)
                        finally:
                            kernel.restore_master_registry(registry)

                        self.assertTrue(kernel.master_of(original).IsSame(master))
                        self.assertEqual(dict(original.GetUserStrings()), original_strings)
                        self.assertEqual(kernel.recipe_of(original), recipe)
                        self.assert_shape(master, (1.0, 1.0, 1.0), 0.0)
                        self.assert_cache_unchanged(master, cache)
                        self.assertEqual(occt.shape_to_bytes(master, triangles=True),
                                         original_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
