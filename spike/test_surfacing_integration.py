"""Explicit bridge producer audit for OURS sweep1_parallel; no command/UI integration.

Run with bundled plain Python (-B). Native-success cases skip with an explicit reason
when ABI-4 bit 6 is missing; --require-native turns that limitation into a failing gate.
--mutations isolates each deliberate regression in a fresh process, without disk edits.
"""

import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_surfacing_parallel import (
    AXIS, ACROSS, MATRIX, NONUNIFORM, bridge, curve_value, fixture,
    kernel, ng, ob, r3, sb, surface,
)
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.TopoDS import TopoDS
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt


HAS_NATIVE = ng.sweep1_parallel_available()
NATIVE_REASON = 'ABI-4 parallel-section capability bit 6/export unavailable; Main must build under lock'


def inputs():
    rail, sections, stations = fixture(parallel=True)
    # Noncircular rational quadratic profiles: curve_recipe intentionally preserves
    # circular arcs as ctype=arc, which this bounded backend does not accept.
    for section in sections:
        anchor = section['poles'][0][0]
        for pole in section['poles']:
            pole[0] = anchor + 1.2 * (pole[0] - anchor)
    return rail, sections, stations


def curves():
    rail, sections, stations = inputs()
    return bridge.curve_from_recipe(rail), [bridge.curve_from_recipe(s) for s in sections], stations


def produce(**kw):
    rail, sections, stations = curves()
    return bridge.sweep1_parallel(rail, sections, stations, axis=AXIS, across=ACROSS, **kw)


def transform(matrix):
    xf = r3.Transform.Identity()
    for i in range(4):
        for j in range(4):
            setattr(xf, f'M{i}{j}', matrix[4*i+j])
    return xf


def placed(point, matrix):
    if matrix is None:
        return list(point)
    return [sum(matrix[4*k+i]*point[i] for i in range(3))+matrix[4*k+3] for k in range(3)]


COMPOSED = [sum(NONUNIFORM[4*i+k]*MATRIX[4*k+j] for k in range(4))
            for i in range(4) for j in range(4)]


def verify_producer(geom, matrix=None):
    """Gates: honest master, one smooth rational face, open topology, geometric agreement.

    sweep1_parallel is intentionally named, not hidden from producer coverage: this
    explicit audit also covers its exact document form and recipe, unlike make_* discovery.
    """
    assert geom.IsValid and not geom.IsSolid and len(geom.Faces) == 1
    assert geom.GetUserString(kernel.DOCUMENT_FORM_KEY) == 'exact'
    assert kernel.honesty_problem(geom) is None
    master = kernel.master_of(geom)
    assert master is not None
    surf = surface(master)
    replay = surface(ob.from_recipe(kernel.recipe_of(geom)))
    rail, sections, stations = inputs()
    maximum = 0.
    for section, v in zip(sections, stations):
        for i in range(33):
            u = i/32
            expected = placed(curve_value(section, u), matrix)
            for actual in (surf.Value(u, v).Coord(), replay.Value(u, v).Coord()):
                maximum = max(maximum, math.dist(expected, actual))
            q = geom.Faces[0].PointAt(u, v)
            maximum = max(maximum, math.dist(expected, [q.X, q.Y, q.Z]))
            assert GeomAPI_ProjectPointOnSurf(gp_Pnt(*expected), surf).LowerDistance() < 1e-7
    for i in range(65):
        assert math.dist(placed(curve_value(rail, i/64), matrix), surf.Value(0, i/64).Coord()) < 1e-7
    assert maximum < 1e-7, ('master/proxy/recipe section error', maximum)
    return maximum


def ordinary_surface():
    """Existing single-section OCCT fixture: real geometry, independent of new native bit."""
    rail, sections, _ = fixture(parallel=True)
    recipe = dict(kind='sweep1', rail=rail, section=sections[0])
    return bridge.occt_backed(ob.from_recipe(recipe), recipe)


def invalid_solid():
    sew = BRepBuilderAPI_Sewing(1e-7)
    for face in ob.unique_faces(ob.make_box(2,3,4))[:-1]:
        sew.Add(face)
    sew.Perform()
    shape = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sew.SewedShape())).Solid()
    assert not ob.is_valid(shape) and not ob.is_airtight(shape)
    return shape


def snapshot(geom):
    return geom.Encode(), kernel.recipe_of(geom), kernel.master_of(geom), kernel.master_registry_snapshot()


class BridgeContractTests(unittest.TestCase):
    def test_exact_conversion_and_arguments(self):
        rail, sections, stations = curves()
        before = [c.Encode() for c in [rail]+sections]
        with patch.object(sb, 'sweep1_parallel', side_effect=kernel.KernelError('transport sentinel')) as call:
            with self.assertRaisesRegex(kernel.KernelError, 'transport sentinel'):
                bridge.sweep1_parallel(rail, sections, stations, axis=AXIS, across=ACROSS,
                                       reverse=[False, True, False], tolerance=2e-7)
        args, kw = call.call_args
        self.assertEqual(args[2], stations)
        for actual, expected in zip([args[0]]+args[1], [inputs()[0]]+inputs()[1]):
            for key in ('degree', 'weights', 'knots'):
                self.assertEqual(actual[key], expected[key])
            for a, b in zip(actual['poles'], expected['poles']):
                self.assertLess(math.dist(a, b), 1e-14)
        self.assertEqual(kw, dict(axis=AXIS, across=ACROSS, reverse=[False, True, False], tolerance=2e-7))
        self.assertEqual(before, [c.Encode() for c in [rail]+sections])

    def test_unsupported_profile_types(self):
        rail, sections, stations = curves()
        arc = bridge.curve_from_recipe(fixture(parallel=True)[1][0])
        polyline = r3.PolylineCurve([r3.Point3d(0,0,0), r3.Point3d(1,0,0), r3.Point3d(1,1,0)])
        joined = r3.PolyCurve()
        joined.Append(arc)
        joined.Append(r3.LineCurve(arc.PointAtEnd, r3.Point3d(2,2,0)))
        circle = r3.Circle(r3.Point3d(0,0,0), 1).ToNurbsCurve()
        for profile, reason in [(arc, 'type arc'), (polyline, 'type polyline'),
                                (joined, 'type polycurve'), (circle, 'closed/periodic'),
                                (r3.Point(r3.Point3d(0,0,0)), 'valid rhino3dm curve')]:
            with self.subTest(reason=reason), patch.object(sb, 'sweep1_parallel') as backend:
                before = kernel.master_registry_snapshot()
                with self.assertRaisesRegex(kernel.KernelError, reason):
                    bridge.sweep1_parallel(rail, [profile]+sections[1:], stations,
                                           axis=AXIS, across=ACROSS)
                backend.assert_not_called()
                self.assertEqual(kernel.master_registry_snapshot(), before)

    def test_capability_additive_and_missing_symbol(self):
        self.assertEqual(ng.ABI_VERSION, 4)
        self.assertEqual(ng.CAP_SWEEP1_PARALLEL, 1 << 6)
        # All old bits, a claimed bit without an export, and an export without a bit.
        for bits, has_export in [(63, False), (127, False), (63, True), (255, True)]:
            class Library:
                def narwhal_geometry_core_capabilities(self):
                    return bits
            lib = Library()
            if has_export:
                lib.narwhal_geometry_core_sweep1_parallel_v1 = lambda *a: self.fail('unavailable export called')
            binding = type('Binding', (), {'library': lib})()
            with patch.object(ng, '_load_locked', return_value=binding):
                available = bits & 64 != 0 and has_export
                self.assertEqual(ng.sweep1_parallel_available(), available)
                if available:
                    continue
                before = kernel.master_registry_snapshot()
                decline = bridge.last_occt_decline()
                with patch.object(bridge, '_proxy_from_occt') as proxy, patch.object(bridge, '_triangulated_proxy') as mesh:
                    with self.assertRaisesRegex(kernel.KernelError, 'capability unavailable'):
                        produce()
                    proxy.assert_not_called()
                    mesh.assert_not_called()
                self.assertEqual(kernel.master_registry_snapshot(), before)
                self.assertEqual(bridge.last_occt_decline(), decline)
        self._check_worker_environment()

    def _check_worker_environment(self):
        from narwhal3d import narwhal_document as nd
        cp = nd.converter_process
        with tempfile.TemporaryDirectory(prefix='surfacing-worker-env-') as tmp:
            root = Path(tmp)
            (root/'validated').mkdir()
            # Untrusted extracted metadata must never select a loadable library.
            (root/'manifest.json').write_text(
                '{"NARWHAL_GEOMETRY_CORE":"document-controlled-library",'
                '"recipe":{"NARWHAL_GEOMETRY_CORE":"recipe-controlled-library"}}')
            trusted = str(root/'process-selected-library')
            for selected in (None, '', trusted):
                with self.subTest(process_selection=selected):
                    environment = {'PATH': os.defpath, 'UNRELATED_SECRET': 'synthetic-secret',
                                   'PYTHONPATH': 'untrusted-python-path'}
                    if selected is not None:
                        environment['NARWHAL_GEOMETRY_CORE'] = selected
                    result = cp.Result(0, 'NARWHAL-DOCUMENT-WORKER {"count": 0}\n', '')
                    # Exercise real run/_environment filtering. Only the final spawn
                    # is stubbed; the full round-trip below uses the actual worker.
                    with patch.dict(os.environ, environment, clear=True), \
                            patch.object(cp, '_supervisor', return_value=None), \
                            patch.object(cp, '_python_run', return_value=result) as spawn:
                        self.assertEqual(nd._validate_in_worker(root, 0), root/'validated')
                        argv, timeout, forwarded, limit, memory = spawn.call_args.args
                        self.assertEqual(argv, nd._worker_command(root))
                        self.assertEqual((timeout, limit, memory), (600, 65536, 4096))
                        expected = {'PATH': os.defpath}
                        if selected:
                            expected['NARWHAL_GEOMETRY_CORE'] = selected
                        self.assertEqual(forwarded, expected)
                        self.assertNotIn('UNRELATED_SECRET', forwarded)
                        self.assertNotIn('PYTHONPATH', forwarded)
                        self.assertNotIn('document-controlled-library', forwarded.values())
                        self.assertNotIn('recipe-controlled-library', forwarded.values())

    def test_publication_binding(self):
        # Publication seam only: native construction is deliberately stubbed with an
        # existing exact shape AND its matching recipe, not passed off as a sweep result.
        original = ordinary_surface()
        before = snapshot(original)
        result = sb.SweepSurface(before[2], before[1], {})
        with patch.object(sb, 'sweep1_parallel', return_value=result):
            geom = produce()
        self.assertIsNot(geom, original)
        self.assertIs(kernel.master_of(geom), result.shape)
        self.assertEqual(kernel.recipe_of(geom), result.recipe)
        self.assertEqual(geom.GetUserString(kernel.DOCUMENT_FORM_KEY), 'exact')
        self.assertIsNone(kernel.honesty_problem(geom))
        self.assertEqual(snapshot(original)[:3], before[:3])
        self.assertEqual(len(kernel.master_registry_snapshot()), len(before[3])+1)

    def test_failed_publication_has_no_side_effects(self):
        # A transport stub isolates publication failure; it does NOT certify native success.
        geom = ordinary_surface()
        result = sb.SweepSurface(kernel.master_of(geom), kernel.recipe_of(geom), {})
        for stage in ('writer', 'recipe'):
            with self.subTest(stage=stage):
                before = snapshot(geom)
                doc_form, decline = bridge.last_document_form(), bridge.last_occt_decline()
                target = (patch.object(bridge._onbrep(), 'exact_brep', return_value=None)
                          if stage == 'writer' else patch.object(kernel, 'record_recipe', return_value=False))
                with patch.object(sb, 'sweep1_parallel', return_value=result), target, \
                        patch.object(bridge, '_triangulated_proxy') as mesh:
                    with self.assertRaises(kernel.KernelError):
                        produce()
                    mesh.assert_not_called()
                self.assertEqual(snapshot(geom), before)
                self.assertEqual(bridge.last_document_form(), doc_form)
                self.assertEqual(bridge.last_occt_decline(), decline)


class TransformTests(unittest.TestCase):
    def test_open_surface_atomic_transform(self):
        original = ordinary_surface()
        original.SetUserString('test:owner', 'preserved')
        before = snapshot(original)
        moved = bridge.transform_geometry(original, transform(NONUNIFORM))
        self.assertIsNot(moved, original)
        self.assertEqual(snapshot(original)[:3], before[:3])
        self.assertEqual(moved.GetUserString('test:owner'), 'preserved')
        old = BRep_Tool.Surface_s(ob.unique_faces(before[2])[0])
        masters = [kernel.master_of(moved), ob.from_recipe(kernel.recipe_of(moved))]
        for master in masters:
            self.assertTrue(ob.is_valid(master))
            self.assertFalse(ob.is_airtight(master))
            self.assertEqual(master.ShapeType(), before[2].ShapeType())
            surf = BRep_Tool.Surface_s(ob.unique_faces(master)[0])
            u0,u1,v0,v1 = old.Bounds()
            for fu,fv in [(.17,.23),(.62,.47),(.83,.91)]:
                u,v = u0+(u1-u0)*fu, v0+(v1-v0)*fv
                self.assertLess(math.dist(placed(old.Value(u,v).Coord(), NONUNIFORM), surf.Value(u,v).Coord()), 1e-7)
        self.assertTrue(moved.IsValid)
        self.assertFalse(moved.IsSolid)
        for u,v in [(.13,.27),(.73,.45),(.91,.82)]:
            p = original.Faces[0].PointAt(u,v)
            q = moved.Faces[0].PointAt(u,v)
            self.assertLess(math.dist(placed([p.X,p.Y,p.Z], NONUNIFORM), [q.X,q.Y,q.Z]), 1e-7)
        self.assertIsNone(kernel.honesty_problem(moved))

    def test_open_shell_preserves_topology(self):
        sew = BRepBuilderAPI_Sewing(1e-7)
        faces = ob.unique_faces(ob.make_box(2,3,4))
        for face in [faces[0], faces[2]]:
            sew.Add(face)
        sew.Perform()
        shape = sew.SewedShape()
        self.assertEqual(shape.ShapeType().name, 'TopAbs_SHELL')
        geom = bridge._proxy_from_occt(shape)
        recipe = dict(kind='brep_data', data=bridge.brep_data(geom), tolerance=1e-6)
        kernel.record_recipe(geom, recipe)
        kernel.register_master(geom, shape)
        before = snapshot(geom)
        moved = bridge.transform_geometry(geom, transform(NONUNIFORM))
        master = kernel.master_of(moved)
        self.assertEqual(master.ShapeType(), shape.ShapeType())
        self.assertEqual(ob.face_count(master), 2)
        self.assertTrue(ob.is_valid(master))
        self.assertEqual(ob.solid_count(master), 0)
        self.assertEqual(snapshot(geom)[:3], before[:3])
        self.assertTrue(ob.is_valid(ob.from_recipe(kernel.recipe_of(moved))))

    def test_solid_gate_rejects_open_output(self):
        solid = bridge.make_box((0,0,0), (2,3,0), 4)
        kernel.register_master(solid, bridge._occt_operand(solid))
        bad = kernel.master_of(ordinary_surface())
        before = snapshot(solid)
        with patch.object(ob, 'transform', return_value=bad):
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'airtight solid'):
                bridge.transform_geometry(solid, transform(NONUNIFORM))
        self.assertEqual(snapshot(solid), before)
        moved = bridge.transform_geometry(solid, transform(NONUNIFORM))
        self.assertTrue(ob.is_airtight(kernel.master_of(moved)))
        self.assertAlmostEqual(ob.volume(kernel.master_of(moved)), 24*1.5*.75*2, places=8)

    def test_solid_gate_rejects_invalid_solid(self):
        solid = bridge.make_box((0,0,0), (2,3,0), 4)
        kernel.register_master(solid, bridge._occt_operand(solid))
        before = snapshot(solid)
        with patch.object(ob, 'transform', return_value=invalid_solid()):
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'airtight solid'):
                bridge.transform_geometry(solid, transform(NONUNIFORM))
        self.assertEqual(snapshot(solid), before)

    def test_open_gate_rejects_changed_kind(self):
        geom = ordinary_surface()
        before = snapshot(geom)
        for bad in [ob.make_box(2,3,4), ob._unique_edges(before[2])[0]]:
            with patch.object(ob, 'transform', return_value=bad):
                with self.assertRaisesRegex(bridge.NonUniformTransformError, 'topological kind'):
                    bridge.transform_geometry(geom, transform(NONUNIFORM))
            self.assertEqual(snapshot(geom), before)

    def test_open_gate_rejects_invalid_output(self):
        geom = ordinary_surface()
        before = snapshot(geom)
        moved = ob.transform(before[2], NONUNIFORM)
        is_valid = ob.is_valid
        with patch.object(ob, 'transform', return_value=moved), \
                patch.object(ob, 'is_valid', side_effect=lambda s: False if s is moved else is_valid(s)):
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'invalid'):
                bridge.transform_geometry(geom, transform(NONUNIFORM))
        self.assertEqual(snapshot(geom), before)

    def test_invalid_original_is_not_repaired_silently(self):
        geom = ordinary_surface()
        kernel.register_master(geom, invalid_solid())
        before = snapshot(geom)
        with patch.object(ob, 'transform') as called:
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'original shape is invalid'):
                bridge.transform_geometry(geom, transform(NONUNIFORM))
            called.assert_not_called()
        self.assertEqual(snapshot(geom), before)

    def test_bare_face_failure_does_not_register_original(self):
        geom = bridge._proxy_from_occt(kernel.master_of(ordinary_surface()))
        self.assertIsNone(kernel.recipe_of(geom))
        before = snapshot(geom)
        with patch.object(ob, 'transform', side_effect=ValueError('injected transform failure')):
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'injected transform failure'):
                bridge.transform_geometry(geom, transform(NONUNIFORM))
        self.assertEqual(snapshot(geom), before)
        moved = bridge.transform_geometry(geom, transform(NONUNIFORM))
        self.assertTrue(moved.IsValid)
        self.assertIsNotNone(kernel.recipe_of(moved))
        self.assertEqual(snapshot(geom)[:3], before[:3])

    def test_failed_recipe_publication_leaves_registry_unchanged(self):
        geom = ordinary_surface()
        before = snapshot(geom)
        with patch.object(kernel, 'record_recipe', return_value=False):
            with self.assertRaisesRegex(bridge.NonUniformTransformError, 'persist'):
                bridge.transform_geometry(geom, transform(NONUNIFORM))
        self.assertEqual(snapshot(geom), before)


@unittest.skipUnless(HAS_NATIVE, NATIVE_REASON)
class NativeIntegrationTests(unittest.TestCase):
    def test_explicit_producer_audit(self):
        rail, sections, stations = curves()
        before = [c.Encode() for c in [rail]+sections]
        geom = bridge.sweep1_parallel(rail, sections, stations, axis=AXIS, across=ACROSS)
        print('MEASURED bridge section/master/proxy/recipe error', verify_producer(geom))
        self.assertEqual(before, [c.Encode() for c in [rail]+sections])
        self.assertEqual(kernel.recipe_of(geom)['kind'], 'sweep1_parallel')
        forward = [sections[1].PointAt(i/16) for i in range(17)]
        self.assertTrue(sections[1].Reverse())
        self.assertEqual((sections[1].Domain.T0, sections[1].Domain.T1), (-1., 0.))
        with self.assertRaisesRegex(kernel.KernelError, r'domain \[0, 1\]'):
            bridge.sweep1_parallel(rail, sections, stations, axis=AXIS, across=ACROSS,
                                   reverse=[False,True,False])
        # Reverse negates the parameter domain. Explicitly reparameterize this
        # fixture (an exact affine domain change, not a refit); the API stays strict.
        sections[1].Domain = r3.Interval(0., 1.)
        self.assertEqual(list(sections[1].Knots), [0., 0., 1., 1.])
        for i, point in enumerate(reversed(forward)):
            self.assertLess(sections[1].PointAt(i/16).DistanceTo(point), 1e-14)
        reverse = bridge.sweep1_parallel(rail, sections, stations, axis=AXIS, across=ACROSS,
                                         reverse=[False,True,False])
        verify_producer(reverse)
        original = snapshot(geom)
        moved = bridge.transform_geometry(geom.Duplicate(), transform(NONUNIFORM))
        verify_producer(moved, NONUNIFORM)
        verify_producer(geom)
        self.assertEqual(snapshot(geom)[:3], original[:3])

    def test_native_refusal_no_publication(self):
        rail, sections, stations = curves()
        before = [c.Encode() for c in [rail]+sections]
        registry = kernel.master_registry_snapshot()
        with patch.object(bridge._onbrep(), 'exact_brep') as writer:
            with self.assertRaisesRegex(kernel.KernelError, 'order'):
                bridge.sweep1_parallel(rail, sections, [0,.01,1], axis=AXIS, across=ACROSS)
            writer.assert_not_called()
        self.assertEqual(registry, kernel.master_registry_snapshot())
        self.assertEqual(before, [c.Encode() for c in [rail]+sections])

    def _check_proxy_mismatches(self, root, geom):
        from narwhal3d import narwhal_document as nd, native_document
        from narwhal3d.model import NarwhalModel

        master = kernel.master_of(geom)
        recipe = kernel.recipe_of(geom)
        bounds = ob.tight_bbox(master)
        proxy_shape = ob.shape_from_brep_data(bridge.brep_data(geom), sew_tolerance=1e-6)
        self.assertTrue(ob.is_valid(proxy_shape))
        self.assertFalse(ob.is_airtight(proxy_shape))
        proxy_bounds = ob.tight_bbox(proxy_shape)
        for a, b in zip(bounds[0]+bounds[1], proxy_bounds[0]+proxy_bounds[1]):
            self.assertAlmostEqual(a, b, places=9)
        raw = geom.GetBoundingBox()
        hull = ((raw.Min.X, raw.Min.Y, raw.Min.Z), (raw.Max.X, raw.Max.Y, raw.Max.Z))
        self.assertGreater(bounds[0][0]-hull[0][0], 1.)
        center = [(a+b)/2 for a,b in zip(*bounds)]
        translation = [1,0,0,-.5, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        scale = [.8,0,0,.2*center[0], 0,.8,0,.2*center[1],
                 0,0,.8,.2*center[2], 0,0,0,1]

        coherent = NarwhalModel()
        coherent.add_object(geom.Duplicate(), 'coherent surface', 0)
        coherent_path = root/'coherent.narwhal'
        self.assertTrue(nd.save_model(coherent, coherent_path))
        # Also run the positive parent path in this process so process-local guard
        # mutations are exercised, not lost when the fresh-process reader starts.
        candidate = nd.load_model(coherent_path)
        self.assertEqual(candidate.rehydrated, (1, 0))

        def bypass_worker(payload, count):
            # Test the parent's independent defense, not an alternative load route.
            self.assertEqual(count, 1)
            validated = payload/'validated'
            validated.mkdir()
            shutil.copyfile(payload/'document.3dm', validated/'document.3dm')
            records = json.loads((payload/'manifest.json').read_text())['masters']
            for record in records:
                shutil.copyfile(payload/record['member'], validated/(record['object_id']+'.brep'))
            return validated

        for side in ('master', 'proxy'):
            for kind, matrix in [('translated', translation), ('scaled', scale)]:
                with self.subTest(counterpart=side, corruption=kind):
                    candidate = geom.Duplicate()
                    candidate_master, candidate_recipe = master, recipe
                    if side == 'master':
                        candidate_master = ob.transform(master, matrix)
                        candidate_recipe = dict(recipe)
                        candidate_recipe['xform'] = bridge._mat_mul(matrix, recipe['xform'])
                        wrong_bounds = ob.tight_bbox(candidate_master)
                        # Both bad masters fit entirely inside the unchanged proxy's
                        # conservative box: a containment-only replacement MUST fail.
                        for axis in range(3):
                            self.assertGreaterEqual(wrong_bounds[0][axis], hull[0][axis]-1e-7)
                            self.assertLessEqual(wrong_bounds[1][axis], hull[1][axis]+1e-7)
                        self.assertGreater(max(abs(a-b) for a,b in zip(
                            wrong_bounds[0]+wrong_bounds[1], bounds[0]+bounds[1])), .4)
                    else:
                        # Deliberate file-corruption fixture: move ONLY the stored
                        # proxy, retaining the independent original master and recipe.
                        self.assertTrue(candidate.Transform(transform(matrix)))
                    kernel.register_master(candidate, candidate_master)
                    self.assertTrue(kernel.record_recipe(candidate, candidate_recipe))
                    model = NarwhalModel()
                    model.add_object(candidate, 'mismatched surface', 0)
                    target = root/(side+'-'+kind+'.narwhal')
                    self.assertTrue(nd.save_model(model, target))
                    retained = kernel.master_registry_snapshot()
                    payload = root/(side+'-'+kind+'-payload')
                    native_document.unpack(target, payload)
                    # Exercise the REAL worker separately, so a passing parent guard
                    # cannot hide a broken worker guard (and vice versa below).
                    with self.assertRaisesRegex(nd.NarwhalDocumentError, 'proxy bounds'):
                        nd._validate_in_worker(payload, 1)
                    self.assertEqual(kernel.master_registry_snapshot(), retained)
                    with patch.object(nd, '_validate_in_worker', side_effect=bypass_worker):
                        with self.assertRaisesRegex(nd.NarwhalDocumentError, 'proxy bounds'):
                            nd.load_model(target)
                    self.assertEqual(kernel.master_registry_snapshot(), retained)
        verify_producer(geom, COMPOSED)
        print('PASS translated/scaled proxy AND master rejections; bad masters within loose hull')

    def test_transform_and_fresh_process_formats(self):
        original = produce()
        before = snapshot(original)
        moved = bridge.transform_geometry(original.Duplicate(), transform(MATRIX))
        moved = bridge.transform_geometry(moved, transform(NONUNIFORM))
        verify_producer(moved, COMPOSED)
        self.assertEqual(snapshot(original)[:3], before[:3])
        verify_producer(original)
        with tempfile.TemporaryDirectory(prefix='surfacing-integration-') as tmp:
            for extension in ('.narwhal', '.3dm'):
                with self.subTest(format=extension):
                    path = str(Path(tmp)/('sweep'+extension))
                    for mode in ('--write', '--read'):
                        run = subprocess.run([sys.executable, '-B', __file__, mode, path],
                                             text=True, capture_output=True, timeout=90)
                        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                        if run.stdout:
                            print(run.stdout.rstrip())
            self._check_proxy_mismatches(Path(tmp), moved)


def document_process(mode, path):
    from narwhal3d import narwhal_document as nd
    from narwhal3d.model import NarwhalModel
    if mode == '--write':
        geom = bridge.transform_geometry(produce(), transform(MATRIX))
        geom = bridge.transform_geometry(geom, transform(NONUNIFORM))
        verify_producer(geom, COMPOSED)
        model = NarwhalModel()
        model.add_object(geom, 'OURS parallel sections', 0)
        if path.endswith('.narwhal'):
            assert nd.save_model(model, path)
        else:
            assert model.file.Write(path, 8)
    else:
        # Fresh reader, with an unrelated master already registered: no stale-id alias.
        ordinary_surface()
        if path.endswith('.narwhal'):
            # Use the production supervised worker launcher. In this checkout it
            # selects Blender -b --python document_worker.py (no GUI); forcing plain
            # Python instead bypasses its bpy-capable runtime and fails at import.
            model = nd.load_model(path)
            geom = list(model.file.Objects)[0].Geometry
            assert kernel.master_of(geom) is None
            nd.commit_masters(model)
        else:
            doc = r3.File3dm.Read(path)
            geom = list(doc.Objects)[0].Geometry
            assert kernel.master_of(geom) is None
            assert bridge.rehydrate_master(geom)
        verify_producer(geom, COMPOSED)
        print('PASS fresh process', Path(path).suffix, 'exact master/proxy + composed recipe')


def mutate_function(name, old, new, module=bridge):
    fn = getattr(module, name)
    source = inspect.getsource(fn)
    assert old in source, (name, old)
    exec(compile(source.replace(old, new, 1), '<surfacing mutation>', 'exec'), module.__dict__)


def inject_mutation(name):
    if name == 'solid-gate':
        mutate_function('_transform_nonuniform', 'if not occt.is_airtight(moved):', 'if False:')
        return 'TransformTests.test_solid_gate_rejects_open_output'
    if name == 'open-refused':
        mutate_function('_transform_nonuniform', 'elif (not occt.is_valid(moved)',
                        'elif (not occt.is_airtight(moved) or not occt.is_valid(moved)')
        return 'TransformTests.test_open_surface_atomic_transform'
    if name == 'invalid-open':
        mutate_function('_transform_nonuniform', 'not occt.is_valid(moved)', 'False')
        return 'TransformTests.test_open_gate_rejects_invalid_output'
    if name == 'proxy-only':
        bridge.transform_geometry = lambda geom, xf: (geom.Transform(xf), geom)[1]
        return 'TransformTests.test_open_surface_atomic_transform'
    if name == 'recipe-loss':
        mutate_function('_transform_nonuniform', 'updated["xform"] = _mat_mul(matrix, recipe.get("xform") or _IDENTITY16)',
                        'updated.pop("xform", None)')
        return 'TransformTests.test_open_surface_atomic_transform'
    if name == 'publish-before-recipe':
        mutate_function('occt_backed', 'if not k.record_recipe(proxy, recipe):',
                        'k.register_master(proxy, shape)\n    if not k.record_recipe(proxy, recipe):')
        return 'TransformTests.test_failed_recipe_publication_leaves_registry_unchanged'
    if name == 'drop-weights':
        real = bridge.curve_recipe
        def broken(curve):
            recipe = real(curve)
            if recipe and recipe.get('ctype') == 'nurbs':
                recipe['weights'] = [1.] * len(recipe['weights'])
            return recipe
        bridge.curve_recipe = broken
        return 'BridgeContractTests.test_exact_conversion_and_arguments'
    if name == 'publish-original-input':
        mutate_function('_transform_nonuniform', 'data = brep_data(work)',
                        'record_exact_recipe(work)\n                data = brep_data(work)')
        return 'TransformTests.test_bare_face_failure_does_not_register_original'
    if name == 'unregistered-producer':
        mutate_function('sweep1_parallel', 'k.register_master(proxy, result.shape)', 'pass')
        return 'BridgeContractTests.test_publication_binding'
    if name == 'native-bit-ignored':
        ng.sweep1_parallel_available = lambda: True
        return 'BridgeContractTests.test_capability_additive_and_missing_symbol'
    if name in ('worker-selection-loss', 'worker-env-leak'):
        from narwhal3d import narwhal_document as nd
        replacement = 'extra_env=None' if name == 'worker-selection-loss' else 'extra_env=dict(os.environ)'
        mutate_function('_validate_in_worker', 'extra_env=extra_env', replacement, module=nd)
        return 'BridgeContractTests.test_capability_additive_and_missing_symbol'
    if name in ('parent-raw-bounds', 'parent-circular-bounds'):
        from narwhal3d import narwhal_document as nd
        old = ('proxy_bounds = [float(value) for point in\n'
               '                                    occt_backend.tight_bbox(proxy_shape) for value in point]')
        new = ('proxy_bounds = actual_bounds' if name == 'parent-circular-bounds' else
               'proxy_bounds = [value for point in (proxy.GetBoundingBox().Min, '
               'proxy.GetBoundingBox().Max) for value in (point.X, point.Y, point.Z)]')
        mutate_function('load_model', old, new, module=nd)
        return 'NativeIntegrationTests.test_transform_and_fresh_process_formats'
    if name in ('worker-raw-bounds', 'worker-circular-bounds'):
        from narwhal3d import narwhal_document as nd
        worker = Path(nd.__file__).with_name('document_worker.py')
        source = worker.read_text()
        if name == 'worker-raw-bounds':
            old = 'proxy_bounds = proxy_shape_bounds'
            new = ('proxy_bounds = [value for point in (proxy.GetBoundingBox().Min, '
                   'proxy.GetBoundingBox().Max) for value in (point.X, point.Y, point.Z)]')
        else:
            old = '                if any(abs(a - b) > max(1.0e-7, tolerance * 10.0)'
            new = '                proxy_shape_bounds = bounds\n' + old
        assert old in source
        source = source.replace(old, new, 1)
        # Run a mutated in-memory copy with the SAME real background runtime and
        # supervisor. Never edit a shared worker file or bypass worker validation.
        code = f'exec(compile({source!r}, {str(worker)!r}, "exec"), ' + repr(
            {'__name__': '__main__', '__file__': str(worker)}) + ')'
        real_command = nd._worker_command
        def mutated_command(root):
            command = list(real_command(root))
            if '--python' in command:
                index = command.index('--python')
                command[index:index+2] = ['--python-expr', code]
            else:
                command = [command[0], '-c', code, '--', str(root)]
            return tuple(command)
        nd._worker_command = mutated_command
        return 'NativeIntegrationTests.test_transform_and_fresh_process_formats'
    if name == 'shared-original-master':
        real = bridge.transform_geometry
        def corrupt_original(geom, xf):
            shared_key = geom.GetUserString(kernel.MASTER_KEY)
            moved = real(geom, xf)
            # Simulate updating a Duplicate's shared master slot in place.
            kernel._MASTERS[shared_key] = kernel.master_of(moved)
            return moved
        bridge.transform_geometry = corrupt_original
        return 'NativeIntegrationTests.test_explicit_producer_audit'
    if name == 'native-replay-loss':
        sb.replay = lambda recipe: ob.make_box(1, 1, 1)
        return 'NativeIntegrationTests.test_explicit_producer_audit'
    raise ValueError(name)


if __name__ == '__main__':
    if '--write' in sys.argv or '--read' in sys.argv:
        document_process(sys.argv[-2], sys.argv[-1])
    elif '--mutation' in sys.argv:
        case = inject_mutation(sys.argv[-1])
        unittest.main(argv=[__file__, case])
    elif '--mutations' in sys.argv:
        names = ['solid-gate', 'open-refused', 'invalid-open', 'proxy-only', 'recipe-loss',
                 'publish-before-recipe', 'publish-original-input', 'drop-weights',
                 'unregistered-producer', 'native-bit-ignored',
                 'worker-selection-loss', 'worker-env-leak']
        if HAS_NATIVE:
            names += ['shared-original-master', 'native-replay-loss',
                      'parent-raw-bounds', 'parent-circular-bounds',
                      'worker-raw-bounds', 'worker-circular-bounds']
        else:
            print('SKIP native original/replay mutations:', NATIVE_REASON)
        for name in names:
            child = subprocess.run([sys.executable, '-B', __file__, '--mutation', name],
                                   text=True, capture_output=True, timeout=60)
            assert child.returncode != 0 and 'FAILED (' in child.stderr, (name, child.stdout, child.stderr)
            print('RED observed:', name)
    else:
        if '--require-native' in sys.argv:
            sys.argv.remove('--require-native')
            if not HAS_NATIVE:
                raise SystemExit(NATIVE_REASON)
        unittest.main(verbosity=2)
