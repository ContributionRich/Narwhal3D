"""Background display-picking regressions; Main runs Blender, never a GUI/build.

  tools/blender-5.2.2-linux-x64/blender -b --factory-startup --python-exit-code 1 \
      --python spike/test_pick_candidates.py -- --mutations

Requires the existing packaged Rust selection core (or NARWHAL_SELECTION_CORE).
Actual r3 geometry, Blender objects, view3d_utils and mathutils BVHs; the small
adapter supplies display samples/parts, not ui.py or the application lifecycle.
Box triangles are viewport data only, never substituted for document geometry.
Known edge-only shaded occlusion regression is a hard assertion, not an xfail.
"""
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import bpy
import bpy_extras.view3d_utils as v3u
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
import rhino3dm as r3

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1] / 'narwhal3d'
PACKAGE = ModuleType('_pick_candidate_tests')
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE.__name__] = PACKAGE
SOURCES = {name: (ROOT / (name + '.py')).read_text()
           for name in ('native_selection', 'subobject', 'pick_candidates')}


def load(name, source=None):
    module = ModuleType(PACKAGE.__name__ + '.' + name)
    module.__package__, module.__file__ = PACKAGE.__name__, str(ROOT / (name + '.py'))
    sys.modules[module.__name__] = module
    setattr(PACKAGE, name, module)
    exec(compile(SOURCES[name] if source is None else source, module.__file__, 'exec'),
         module.__dict__)
    return module


native = load('native_selection')
load('subobject')
picker = load('pick_candidates')


class DisplayAdapter:
    CURVE_PICK_PX = SUBOBJECT_PICK_PX = 8.0

    def __init__(self):
        self.rows, self.parts, self.objects, self.meshes = [], {}, [], []
        self.bridge = NS(display_visible=lambda ob: not ob.hide_viewport and not ob.hide_get(),
                         curve_segments=lambda curve: [(0, self._curve_points(curve), ())])

    def _doc_rows(self):
        return self.rows

    def _doc_key(self):
        return id(self)

    def _unpickable_names_cached(self):
        return {ob.name for ob in self.objects if ob.hide_select}

    def _brep_parts_all(self):
        return self.parts

    @staticmethod
    def _curve_points(curve):
        return tuple((p.X, p.Y, p.Z) for p in (curve.PointAtStart, curve.PointAtEnd))

    @staticmethod
    def _view_depth(rv, point):
        return -(rv.view_matrix @ Vector(point)).z

    def _project_with_depth(self, region, rv, point):
        q = v3u.location_3d_to_region_2d(region, rv, Vector(point))
        return None if q is None else (q.x, q.y, self._view_depth(rv, point))

    def add(self, label, geom, mesh=None):
        ob = bpy.data.objects.new('PickTest_' + label, mesh)
        bpy.context.scene.collection.objects.link(ob)
        self.objects.append(ob)
        self.rows.append((ob.name, None, None, geom, r3.ObjectAttributes()))
        return ob

    def line(self, label, y=0.0, z=0.0):
        return self.add(label, r3.LineCurve(r3.Point3d(-.3, y, z), r3.Point3d(.3, y, z)))

    def box(self, label, low=(-.4, -.4, -.4), high=(.4, .4, 0.0)):
        x, y, z = low
        X, Y, Z = high
        vertices = ((x, y, z), (X, y, z), (X, Y, z), (x, Y, z),
                    (x, y, Z), (X, y, Z), (X, Y, Z), (x, Y, Z))
        faces = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
        edges = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
                 (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
        mesh = bpy.data.meshes.new('PickTest_display')
        self.meshes.append(mesh)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        geom = r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(r3.Point3d(*low), r3.Point3d(*high))))
        ob = self.add(label, geom, mesh)
        # Real Blender loop triangles, grouped by their display face index.
        mesh.calc_loop_triangles()
        self.parts[ob.name] = {
            'edges': [(i, tuple(vertices[j] for j in edge)) for i, edge in enumerate(edges)],
            'faces': [(i, tuple(tuple(vertices[j] for j in t.vertices)
                              for t in mesh.loop_triangles if t.polygon_index == i))
                      for i in range(len(faces))],
            'vertices': list(enumerate(vertices)),
        }
        return ob

    def close(self):
        for ob in self.objects:
            bpy.data.objects.remove(ob, do_unlink=True)
        for mesh in self.meshes:
            bpy.data.meshes.remove(mesh)


class PickCandidateTests(unittest.TestCase):
    def setUp(self):
        picker.clear()
        self.ui = DisplayAdapter()
        self.addCleanup(self.ui.close)
        self.addCleanup(picker.clear)
        self.region = NS(width=800, height=600)
        view = Matrix.Translation((0, 0, -5))
        projection = Matrix.Diagonal((1.0, 1.0, -.1, 1.0))
        self.rv = NS(view_matrix=view, perspective_matrix=projection @ view,
                     is_perspective=False, view_perspective='ORTHO')
        self.space = NS(region_3d=self.rv, shading=NS(type='WIREFRAME'))

    def hits(self, mode='object', xy=(400, 300), fallback=False):
        if fallback:
            with patch.object(native, 'nearest_projected_polylines', return_value=None):
                return picker.at(self.ui, None, self.space, self.region, *xy, mode=mode)
        real = native.nearest_projected_polylines
        calls = []
        def measured(*args, **kwargs):
            self.assertIs(kwargs.get('with_distances'), True)
            result = real(*args, **kwargs)
            self.assertIsNotNone(result, native.backend_status())
            calls.append(result)
            return result
        with patch.object(native, 'nearest_projected_polylines', side_effect=measured):
            result = picker.at(self.ui, None, self.space, self.region, *xy, mode=mode)
        self.assertEqual(len(calls), 1, 'native path must actually execute')
        return result

    def keys(self, mode='object', xy=(400, 300)):
        actual = [c.key for c in self.hits(mode, xy)]
        self.assertEqual(actual, [c.key for c in self.hits(mode, xy, fallback=True)],
                         'native with_distances / forced fallback ordered parity')
        self.assertEqual(len(actual), len(set(actual)), 'no duplicated face/edge candidates')
        return actual

    def test_all_near_curves_point_and_off_aperture_miss(self):
        a = self.ui.line('near0')
        b = self.ui.line('near1', y=.02)  # Six pixels, not only the nearest curve.
        p = self.ui.add('point', r3.Point(r3.Point3d(.01, 0, 0)))  # Four pixels.
        far = self.ui.line('far', y=.04)  # Twelve pixels, beyond the aperture.
        keys = self.keys()
        self.assertEqual(keys, [(a.name, 'Object', 0), (p.name, 'Object', 0),
                                (b.name, 'Object', 0)])
        self.assertNotIn((far.name, 'Object', 0), keys)
        self.assertEqual(self.keys(xy=(750, 550)), [])
        self.assertEqual(set(self.keys('part')), {(a.name, 'Segment', 0), (b.name, 'Segment', 0)})
        self.assertEqual(self.keys('edge'), [])

    def test_component_samples_accept_native_point3d(self):
        curve = self.ui.line('native_points')
        self.ui.bridge.curve_segments = lambda geom: [(0, (geom.PointAtStart, geom.PointAtEnd), ())]
        try:
            keys = self.keys('part')
        except TypeError as exc:
            self.fail('Native Point3d display samples must be adapted: ' + str(exc))
        self.assertEqual(keys, [(curve.name, 'Segment', 0)])

    def test_hidden_locked_and_missing_display_objects_filtered_after_cache(self):
        visible = self.ui.line('visible')
        hidden = self.ui.line('hidden')
        locked = self.ui.line('locked')
        first = picker.collect(self.ui, 'object')
        self.assertIs(picker.collect(self.ui, 'object'), first)
        self.assertEqual(len(first[0]), 3)
        hidden.hide_viewport, locked.hide_select = True, True
        self.ui.rows.append(('PickTest_missing', None, None,
                             r3.Point(r3.Point3d(0, 0, 0)), r3.ObjectAttributes()))
        self.assertEqual(self.keys(), [(visible.name, 'Object', 0)])
        hidden.hide_viewport, locked.hide_select = False, False
        self.assertEqual(len(self.keys()), 3)

    def test_edge_and_both_adjacent_faces_inside_aperture(self):
        box = self.ui.box('adjacent')
        # Two pixels inside the top/front boundary: the front face is edge-on,
        # so its boundary, not a ray-interior intersection, must qualify.
        keys = self.keys('part', (400, 182))
        for key in ((box.name, 'Edge', 4), (box.name, 'Face', 1), (box.name, 'Face', 2)):
            self.assertIn(key, keys)
        self.assertLess(keys.index((box.name, 'Edge', 4)), keys.index((box.name, 'Face', 1)))
        self.assertFalse(any(key[1] == 'Vertex' for key in keys))
        edge_keys = self.keys('edge', (400, 182))
        self.assertIn((box.name, 'Edge', 4), edge_keys)
        self.assertTrue(all(key[1] == 'Edge' for key in edge_keys))

    def test_face_interior_uses_actual_bvh_not_boundary_distance(self):
        box = self.ui.box('interior')
        candidates, _, _ = picker.collect(self.ui, 'part')
        top = next(c for c in candidates if c.key == (box.name, 'Face', 1))
        self.assertIsInstance(top.tree, BVHTree)
        origin = v3u.region_2d_to_origin_3d(self.region, self.rv, (452, 300))
        ray = v3u.region_2d_to_vector_3d(self.region, self.rv, (452, 300))
        self.assertIsNotNone(top.tree.ray_cast(origin, ray)[0])
        self.assertIn(top.key, self.keys('part', (452, 300)))
        self.assertEqual(self.keys('object', (452, 300)), [(box.name, 'Object', 0)])
        self.assertEqual(self.keys('part', (750, 550)), [])

    def occlusion_scene(self):
        front = self.ui.box('front')
        rear = self.ui.box('rear', low=(-.3, 0, -1.4), high=(.3, .6, -1))
        return front, rear, (rear.name, 'Edge', 4)

    def test_shaded_parts_occluded_but_objects_and_segments_remain_offered(self):
        front, rear, edge = self.occlusion_scene()
        curve = self.ui.line('behind_curve', z=-2)
        self.assertIn(edge, self.keys('part'))
        self.space.shading.type = 'SOLID'
        keys = self.keys('part')
        self.assertIn((front.name, 'Face', 1), keys)
        self.assertNotIn(edge, keys)
        self.assertFalse(any(key[0] == rear.name for key in keys))
        self.assertIn((curve.name, 'Segment', 0), keys)
        self.assertEqual({key[0] for key in self.keys()}, {front.name, rear.name, curve.name})

    def test_shaded_edge_only_still_uses_face_occluders(self):
        _front, _rear, edge = self.occlusion_scene()
        self.assertIn(edge, self.keys('edge'))  # Non-vacuous wireframe control.
        self.space.shading.type = 'SOLID'
        self.assertNotIn(edge, self.keys('edge'),
                         'edge-only mode must collect faces for depth, without offering faces')

    def test_native_distance_contract_keeps_default_world_points(self):
        lines = (((-.3, .02, 0.), (.3, .02, 0.)), ((.01, 0., 0.), (.01, 0., 0.)))
        args = (lines, self.rv.perspective_matrix, 800, 600, (400, 300))
        plain = native.nearest_projected_polylines(*args)
        measured = native.nearest_projected_polylines(*args, with_distances=True)
        self.assertIsNotNone(measured, native.backend_status())
        self.assertEqual(len(measured), 2)
        self.assertTrue(all(hit is not None and len(hit) == 2 for hit in measured))
        self.assertEqual(tuple(hit[0] for hit in measured), plain)
        for hit, distance in zip(measured, (6.0, 4.0)):
            self.assertAlmostEqual(hit[1], distance, places=5)


def mutations():
    global picker, native
    original_picker, original_native = picker, native
    probes = [
        ('pick_candidates', "hasattr(point, 'X')", 'False',
         'test_component_samples_accept_native_point3d'),
        ('pick_candidates', "if row[0] not in blocked", 'if True',
         'test_hidden_locked_and_missing_display_objects_filtered_after_cache'),
        ('pick_candidates', 'and ui.bridge.display_visible(ob)', 'and True',
         'test_hidden_locked_and_missing_display_objects_filtered_after_cache'),
        ('pick_candidates', 'tree = None\n    if triangles:', 'tree = None\n    if False:',
         'test_face_interior_uses_actual_bvh_not_boundary_distance'),
        ('pick_candidates', "spans.extend(t + (t[0],) for t in candidate.triangles)", 'pass',
         'test_edge_and_both_adjacent_faces_inside_aperture'),
        ('pick_candidates', "occlude = mode != 'object' and", 'occlude = False and',
         'test_shaded_parts_occluded_but_objects_and_segments_remain_offered'),
        ('native_selection', '(world, distance) if with_distances else world', 'world',
         'test_native_distance_contract_keeps_default_world_points'),
    ]
    controls = unittest.TestSuite(PickCandidateTests(test) for test in dict.fromkeys(
        probe[3] for probe in probes))
    baseline = unittest.TextTestRunner(verbosity=2).run(controls)
    if not baseline.wasSuccessful():
        raise AssertionError('Mutation controls must pass on unmodified source first')
    try:
        for module, old, new, test in probes:
            assert SOURCES[module].count(old) == 1, ('mutation anchor changed', old)
            changed = load(module, SOURCES[module].replace(old, new, 1))
            if module == 'pick_candidates':
                picker = changed
            else:
                native = changed
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output).run(PickCandidateTests(test))
            if result.wasSuccessful() or result.errors:
                raise AssertionError(f'{test}: expected assertion failure\n{output.getvalue()}')
            print('RED verified:', module, test)
            picker, native = original_picker, original_native
            PACKAGE.pick_candidates, PACKAGE.native_selection = picker, native
            sys.modules[picker.__name__], sys.modules[native.__name__] = picker, native
    finally:
        picker, native = original_picker, original_native
        PACKAGE.pick_candidates, PACKAGE.native_selection = picker, native
        sys.modules[picker.__name__], sys.modules[native.__name__] = picker, native


if __name__ == '__main__':
    # Fail explicitly instead of calling a silent fallback a native parity pass.
    if not native.available():
        raise RuntimeError('Existing Rust selection core required: ' + native.backend_status())
    if '--mutations' in sys.argv:
        mutations()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PickCandidateTests))
    if not result.wasSuccessful():
        raise AssertionError('display candidate regressions failed; see assertions above')
