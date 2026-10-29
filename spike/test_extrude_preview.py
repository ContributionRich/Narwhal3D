"""CPU contracts for the display-only extrusion session, real curve/OCCT sources.

Use bundled Python -B with PYTHONPATH=vendor/occt + runtime LD_LIBRARY_PATH.
--timings measures preparation/update, NOT live GUI presentation. GPU tests are in
spike/test_extrude_preview_gpu.py. The CPU draw oracle interprets captured vertex
buffers/uniforms in tests only; production geometry math stays on GPU.
"""
import ast
from contextlib import ExitStack
import json
import math
from pathlib import Path
import statistics
import sys
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if 'narwhal3d' not in sys.modules:
    package = types.ModuleType('narwhal3d')
    package.__path__ = [str(ROOT / 'narwhal3d')]
    sys.modules['narwhal3d'] = package

import numpy as np
import rhino3dm as r3
from narwhal3d import bridge, extrude_preview as preview, kernel
from narwhal3d.kernel import occt_backend as occt


def shader_ui():
    """Current production shader source/packing, without importing the whole UI/app."""
    wanted = {'_AA_LINE_VERT_SRC', '_AA_LINE_FRAG_SRC', '_AA_LINE_CORNERS', '_aa_line_arrays'}
    tree = ast.parse((ROOT / 'narwhal3d/ui.py').read_text())
    nodes = [n for n in tree.body if getattr(n, 'name', None) in wanted or
             isinstance(n, ast.Assign) and any(getattr(t, 'id', None) in wanted for t in n.targets)]
    assert len(nodes) == len(wanted)
    module = types.ModuleType('narwhal3d.ui')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<current UI AA source>', 'exec'), module.__dict__)
    return module


def examples():
    circle = bridge.make_circle((0, 0, 0), 5)
    arc = bridge.make_arc((10, 0, 0), (10 / math.sqrt(2), 10 / math.sqrt(2), 0), (0, 10, 0))
    inner = bridge.make_circle((0, 0, 0), 2)
    annulus = bridge.make_planar_srf([circle, inner])[0]
    box = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
    face = bridge.faces_at_points(box, [(5, 5, 10)])[0]
    return {'circle': [(circle, None)], 'arc': [(arc, None)],
            'loops': [(circle, None), (inner, None)], 'annulus': [(annulus, None)],
            'face': [(box, ('face', face))]}


def immutable_sources(jobs):
    return [(geom.Encode(), kernel.recipe_of(geom),
             occt.shape_to_bytes(kernel.master_of(geom)) if kernel.master_of(geom) is not None else None)
            for geom, _part in jobs]


class FakeShader:
    def __init__(self):
        self.uniforms = {}
    def bind(self):
        pass
    def uniform_float(self, name, value):
        self.uniforms[name] = value


class FakeState:
    def __init__(self):
        self.blend, self.depth, self.mask = 'NONE', 'LESS_EQUAL', True
    def blend_get(self): return self.blend
    def depth_test_get(self): return self.depth
    def depth_mask_get(self): return self.mask
    def blend_set(self, value): self.blend = value
    def depth_test_set(self, value): self.depth = value
    def depth_mask_set(self, value): self.mask = value


class CaptureBatch:
    def __init__(self, attrs):
        self.attrs = attrs
        self.draws = 0
        self.visible_segments = None
    def draw(self, shader):
        self.draws += 1
        a = self.attrs
        pos = np.asarray(a['pos']).reshape(-1, 6, 3)[:, [0, 2]]
        factors = np.asarray(a['factor']).reshape(-1, 6)[:, [0, 2]]
        axis = np.asarray(shader.uniforms['extrusion'][:3], dtype=float)
        shift = axis / np.linalg.norm(axis) * shader.uniforms['extrusion'][3]
        self.visible_segments = pos + factors[..., None] * shift


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = examples()

    def gpu(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        shader, state, batches = FakeShader(), FakeState(), []
        def batch_for_shader(_shader, kind, attrs):
            self.assertEqual(kind, 'TRIS')
            batch = CaptureBatch(attrs)
            batches.append(batch)
            return batch
        ui = shader_ui()
        stack.enter_context(patch.dict(sys.modules, {
            'gpu': types.SimpleNamespace(state=state), 'narwhal3d.ui': ui,
            'gpu_extras.batch': types.SimpleNamespace(batch_for_shader=batch_for_shader)}))
        stack.enter_context(patch.object(sys.modules['narwhal3d'], 'ui', ui, create=True))
        stack.enter_context(patch.object(preview, '_shader', return_value=shader))
        return shader, state, batches

    def test_profiles_sparse_connectors_and_annular_face_loops(self):
        for name, jobs in self.cases.items():
            session = preview.prepare(jobs)
            self.assertTrue(session, name)
            self.assertTrue(all(len(line) > 1 for line in session.polylines))
            self.assertEqual(len(session.connectors), {'circle': 1, 'arc': 2, 'loops': 2,
                                                      'annulus': 2, 'face': 4}[name])
            self.assertEqual(len(session.loop_ranges), 2 if name in ('loops', 'annulus') else 1)
            self.assertEqual(session.provisional, name in ('annulus', 'face'))
            if name == 'annulus':
                radii = sorted(round(math.hypot(*line[0][:2]), 6) for line in session.polylines)
                self.assertEqual(radii, [2, 5])
            if name == 'face':
                self.assertTrue(all(abs(p[2] - 10) < 1e-8 for line in session.polylines for p in line))

    def test_polyline_corners_and_polycurve_segment_ends_not_sample_vertices(self):
        poly = r3.PolylineCurve([r3.Point3d(*p) for p in ((0, 0, 0), (3, 0, 0), (3, 2, 0))])
        session = preview.prepare([(poly, None)])
        self.assertEqual(len(session.connectors), 3)
        joined = r3.PolyCurve()
        joined.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)))
        joined.Append(self.cases['arc'][0][0])
        session = preview.prepare([(joined, None)])
        self.assertEqual(len(session.connectors), 3)
        self.assertGreater(sum(map(len, session.polylines)), 3)

    def test_source_master_recipe_and_triangulation_cache_are_unchanged(self):
        for jobs in self.cases.values():
            before = immutable_sources(jobs)
            registry = kernel.master_registry_snapshot()
            with patch.object(occt, 'subobject_topology', side_effect=AssertionError('mesher')), \
                 patch.object(occt, 'tessellate', side_effect=AssertionError('mesher')), \
                 patch.object(bridge, '_proxy_from_occt', side_effect=AssertionError('writer')):
                session = preview.prepare(jobs)
                session.update((3, 4, 0), -7)
            self.assertEqual(immutable_sources(jobs), before)
            self.assertEqual(kernel.master_registry_snapshot(), registry)

    def test_actual_draw_buffers_near_far_generators_signed_axis_latest_and_cached(self):
        shader, state, batches = self.gpu()
        geom = bridge.make_line((1, 2, 3), (5, 2, 3))
        session = preview.prepare([(geom, None)])
        region = types.SimpleNamespace(width=256, height=256)
        rv3d = types.SimpleNamespace(perspective_matrix=np.eye(4))
        with ExitStack() as guards:
            for owner, names in ((bridge, ('gumball_extrude', '_occt_operand', '_proxy_from_occt',
                                          '_curve_display_points', 'curve_from_recipe', 'subobject_parts')),
                                 (occt, ('from_recipe', 'subobject_topology', 'tessellate'))):
                for name in names:
                    guards.enter_context(patch.object(owner, name, side_effect=AssertionError(name)))
            for axis, distance in (((0, 0, 2), 7), ((3, 4, 0), -5), ((0, 0, 1), 2), ((0, 0, 1), 0)):
                session.update(axis, 99)  # Intermediate queued values must not survive.
                session.update(axis, distance)
                session.draw(region, rv3d, (1, .5, 0, 1))
                p, q = np.array([1, 2, 3]), np.array([5, 2, 3])
                delta = np.array(axis) / np.linalg.norm(axis) * distance
                expected = np.array([[p, q], [p + delta, q + delta], [p, p + delta], [q, q + delta]])
                np.testing.assert_allclose(batches[0].visible_segments, expected)
                self.assertEqual((state.blend, state.depth, state.mask), ('NONE', 'LESS_EQUAL', True))
            self.assertEqual(len(batches), 1, 'GPU batch recreated during updates/draws')
            self.assertEqual(batches[0].draws, 4)
            self.assertEqual(shader.uniforms['extrusion'][3], 0)
        self.assertTrue(session)

    def test_rejects_invalid_sources_parts_and_bounded_inputs_by_name(self):
        box, part = self.cases['face'][0]
        for jobs in ([(r3.Point3d(0, 0, 0), None)], [(box, None)], [(box, ('edge', 1))],
                     [(box, ('face', 0))], [(box, ('face', True))], [(box, ('face', 999))]):
            with self.assertRaisesRegex(preview.PreviewError, 'job 1'):
                preview.prepare(jobs)
        with patch.object(preview, 'MAX_POINTS', 2):
            with self.assertRaisesRegex(preview.PreviewError, 'budget'):
                preview.prepare(self.cases['circle'])
        self.assertFalse(preview.prepare([]))
        # Bare Brep with no authority must not quietly use its proxy-looking faces.
        bare = r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(0, 0, 0, 10, 10, 10)))
        with self.assertRaisesRegex(preview.PreviewError, 'exact master'):
            preview.prepare([(bare, part)])

    def test_draw_failure_restores_gpu_state(self):
        _shader, state, _batches = self.gpu()
        session = preview.prepare(self.cases['circle'])
        session.prepare_gpu()
        with patch.object(session._batch, 'draw', side_effect=RuntimeError('GPU refused')):
            with self.assertRaisesRegex(RuntimeError, 'GPU refused'):
                session.draw(types.SimpleNamespace(width=256, height=256),
                             types.SimpleNamespace(perspective_matrix=np.eye(4)), (1, .5, 0, 1))
        self.assertEqual((state.blend, state.depth, state.mask), ('NONE', 'LESS_EQUAL', True))

    def test_invalid_update_clears_old_distance(self):
        session = preview.prepare(self.cases['circle'])
        for axis, distance in (((0, 0, 0), 1), ((1, 2), 1), ((0, 0, math.inf), 1),
                               ((0, 0, 1), math.nan), ((0, 0, 1), math.inf)):
            session.update((0, 0, 1), 8)
            with self.assertRaises(preview.PreviewError):
                session.update(axis, distance)
            self.assertEqual(session.distance, 0)


def timings():
    for name, jobs in examples().items():
        before = immutable_sources(jobs)
        start = time.perf_counter_ns()
        session = preview.prepare(jobs)
        prepared = (time.perf_counter_ns() - start) / 1e6
        samples = []
        for i in range(101):
            start = time.perf_counter_ns()
            session.update((3, 4, 0), (i - 50) / 10)
            samples.append((time.perf_counter_ns() - start) / 1e6)
        assert immutable_sources(jobs) == before
        print(json.dumps({'case': name, 'prepare_ms': prepared, 'first_update_ms': samples[0],
                          'warm_median_ms': statistics.median(samples[1:]),
                          'warm_p95_ms': sorted(samples[1:])[94], 'warm_n': 100,
                          'points': sum(map(len, session.polylines)), 'connectors': len(session.connectors)}))


if __name__ == '__main__':
    if '--timings' in sys.argv:
        timings()
    else:
        unittest.main(verbosity=2)
