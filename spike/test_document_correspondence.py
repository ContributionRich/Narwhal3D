#!/usr/bin/env python3
"""Independent geometric correspondence attacks, compatibility and mutation witnesses."""
from contextlib import redirect_stdout
import hashlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rhino3dm as r3
from narwhal3d import bridge, document_worker as worker, kernel, narwhal_document as doc
from narwhal3d.kernel import occt_backend as ob


def saddle(reverse=False):
    surface = r3.NurbsSurface.Create(3, False, 2, 2, 2, 2)
    for u in range(2):
        for v in range(2):
            z = (1 if u == v else -1) * (-1 if reverse else 1)
            surface.Points[u, v] = r3.Point4d(4*u, 4*v, z, 1)
    for knots in (surface.KnotsU, surface.KnotsV):
        knots[0], knots[1] = 0.0, 1.0
    return r3.Brep.CreateFromSurface(surface)


def surface_pair():
    geometry, other = saddle(), saddle(True)
    data = bridge.brep_data(geometry)
    shape = ob.shape_from_brep_data(data, sew_tolerance=1e-6)
    kernel.register_master(geometry, shape)
    kernel.record_recipe(geometry, {'kind': 'brep_data', 'data': data, 'tolerance': 1e-6})
    geometry.SetUserString(kernel.DOCUMENT_FORM_KEY, 'exact')
    return geometry, other, shape


def cavity_pair(other_x=7):
    box = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
    def drilled(x):
        return bridge.boolean_brep(box, bridge.make_cylinder((x, 5, -1), 1, 12), 'DIFFERENCE')
    geometry, reflected = drilled(3), drilled(other_x)
    return geometry, reflected, kernel.master_of(geometry)


def stage(root, geometry, shape, tolerance=1e-6):
    """Build independently of Save admission, including valid archive digest bindings."""
    root.mkdir()
    (root / 'masters').mkdir()
    document = r3.File3dm()
    document.Settings.ModelAbsoluteTolerance = tolerance
    attributes = r3.ObjectAttributes()
    attributes.Id = uuid.uuid4()
    data = bytes(ob.shape_to_bytes(shape, triangles=False))
    digest = hashlib.sha256(data).hexdigest()
    geometry.SetUserString(doc.MASTER_DIGEST_KEY, digest)
    object_id = document.Objects.Add(geometry, attributes)
    obj = document.Objects.FindId(str(object_id))
    member = f'masters/{object_id}.brep'
    (root / member).write_bytes(data)
    document.Write(str(root / 'document.3dm'), 8)
    version, encoding = doc._format_contract()
    manifest = {'format': doc.FORMAT, 'schema': 1, 'summary_codec': 'schema1-v1',
                'document_member': 'document.3dm', 'master_encoding': doc.MASTER_ENCODING,
                'occt_version': version, 'bintools_format': encoding,
                'masters': [doc._shape_record(obj, shape, member, digest)]}
    (root / 'manifest.json').write_text(json.dumps(manifest))


def validate(root):
    import resource
    output = io.StringIO()
    start = time.perf_counter()
    with patch.object(sys, 'argv', ['worker', '--', str(root)]), \
            patch.object(resource, 'setrlimit'), \
            patch.object(worker.document_admission, 'read_hints', return_value=()), \
            patch.object(worker.document_admission, 'publish') as publish, redirect_stdout(output):
        try:
            worker.main()
        except SystemExit as exc:
            assert exc.code == 1
            assert not publish.called, 'failed proof published admission receipts'
            return False, output.getvalue(), time.perf_counter()-start
    assert publish.called
    return True, output.getvalue(), time.perf_counter()-start


class CorrespondenceTests(unittest.TestCase):
    def test_reflected_cavity_recipe(self):
        geometry, reflected, shape = cavity_pair()
        reflected_shape = kernel.master_of(reflected)
        self.assertEqual(ob.face_types(shape), ob.face_types(reflected_shape))
        self.assertEqual(ob.tight_bbox(shape), ob.tight_bbox(reflected_shape))
        self.assertAlmostEqual(ob.document_schema1_volume(shape),
                               ob.document_schema1_volume(reflected_shape), places=9)
        self.assertGreater(min(ob.document_exclusive_volume_pair(shape, reflected_shape)), 30)
        kernel.record_recipe(geometry, kernel.recipe_of(reflected))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'reflected'
            stage(root, geometry, shape)
            accepted, message, elapsed = validate(root)
            print('REFLECTED RECIPE', elapsed, message.strip(), flush=True)
            self.assertFalse(accepted, 'same-summary reflected cavity recipe was admitted')
            self.assertIn('recipe', message)

    def test_same_bbox_distinct_open_proxy(self):
        geometry, wrong_proxy, shape = surface_pair()
        wrong_shape = ob.shape_from_brep_data(bridge.brep_data(wrong_proxy), sew_tolerance=1e-6)
        self.assertFalse(ob.is_airtight(shape))
        self.assertFalse(ob.is_airtight(wrong_shape))
        self.assertEqual(ob.face_types(shape), ob.face_types(wrong_shape))
        self.assertEqual(ob.tight_bbox(shape), ob.tight_bbox(wrong_shape))
        for key, value in geometry.GetUserStrings():
            wrong_proxy.SetUserString(key, value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'surface'
            stage(root, wrong_proxy, shape)
            accepted, message, elapsed = validate(root)
            print('OPEN SURFACE ATTACK', elapsed, message.strip(), flush=True)
            self.assertFalse(accepted, 'different same-bounds open proxy was admitted')
            self.assertIn('proxy', message)

    def test_small_cavity_displacement_recipe(self):
        geometry, shifted, shape = cavity_pair(3 + 1e-5)
        other = kernel.master_of(shifted)
        self.assertEqual(ob.tight_bbox(shape), ob.tight_bbox(other))
        self.assertAlmostEqual(ob.document_schema1_volume(shape), ob.document_schema1_volume(other), places=9)
        kernel.record_recipe(geometry, kernel.recipe_of(shifted))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'shifted'
            stage(root, geometry, shape, tolerance=0.01)
            accepted, message, _ = validate(root)
            self.assertFalse(accepted, message)
            self.assertIn('recipe', message)

    def test_changed_open_recipe(self):
        geometry, reflected, shape = surface_pair()
        kernel.record_recipe(geometry, {'kind': 'brep_data', 'data': bridge.brep_data(reflected),
                                        'tolerance': 1e-6})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'open-recipe'
            stage(root, geometry, shape)
            accepted, message, _ = validate(root)
            self.assertFalse(accepted, message)
            self.assertIn('recipe', message)

    def test_native_open_edges_and_trimmed_surfaces(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
        from OCP.Geom import Geom_BezierCurve
        from OCP.collections import Array1_gp_Pnt as TColgp_Array1OfPnt
        from OCP.gp import gp_Pnt, gp_Pln
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        def edge(x):
            points = TColgp_Array1OfPnt(1, 3)
            for index, point in enumerate(((0,0,0), (x,4,0), (4,0,0)), 1):
                points.SetValue(index, gp_Pnt(*point))
            return BRepBuilderAPI_MakeEdge(Geom_BezierCurve(points)).Edge()
        a, b = edge(1), edge(3)
        for pa, pb in zip(ob.tight_bbox(a), ob.tight_bbox(b)):
            for va, vb in zip(pa, pb):
                self.assertAlmostEqual(va, vb, places=8)
        self.assertFalse(ob.document_same_open_support(a, b))
        copy = ob.shape_from_bytes(bytes(ob.shape_to_bytes(a, triangles=False)))
        self.assertTrue(ob.document_same_open_support(a, copy))
        full = BRepBuilderAPI_MakeFace(gp_Pln(), 0, 4, 0, 4).Face()
        builder = BRep_Builder()
        split = TopoDS_Compound()
        builder.MakeCompound(split)
        for low, high in ((0, 2), (2, 4)):
            builder.Add(split, BRepBuilderAPI_MakeFace(gp_Pln(), low, high, 0, 4).Face())
        self.assertTrue(ob.document_same_open_support(full, split))
        half = BRepBuilderAPI_MakeFace(gp_Pln(), 0, 2, 0, 4).Face()
        self.assertFalse(ob.document_same_open_support(full, half))
        self.assertFalse(ob.document_same_open_support(half, full))
        side = next(face for face in ob.unique_faces(ob.make_cylinder(2, 4))
                    if ob.face_types(face) == {'CYLINDER': 1})
        self.assertTrue(ob.document_same_open_support(side, ob._nurbs_convert(side)))

    def test_record_local_exact_byte_reuse_and_nonfinite_rejection(self):
        a, b = ob.make_box(2, 3, 4), ob.make_box(2, 3, 5)
        proofs = {}
        with patch.object(worker, '_exclusive_volume_pair', return_value=(0.0, 0.0)) as native:
            self.assertTrue(worker._corresponds(a, b, True, 24, proofs))
            reread = ob.shape_from_bytes(bytes(ob.shape_to_bytes(a, triangles=False)))
            self.assertTrue(worker._corresponds(b, reread, True, 24, proofs))
            self.assertEqual(native.call_count, 1)
            worker._corresponds(a, b, True, 24, {})
            self.assertEqual(native.call_count, 2)  # no reuse across records/Open
        with patch.object(worker, '_exclusive_volume_pair', side_effect=AssertionError('must not compute')):
            self.assertTrue(worker._corresponds(a, reread, True, 24, {}))
        for pair in ((0.0, 1.0), (1.0, 0.0), (0.0, float('nan')),
                     (float('inf'), 0.0), (-1.0, 0.0), (0.0,)):
            with patch.object(worker, '_exclusive_volume_pair', return_value=pair):
                self.assertFalse(worker._corresponds(a, b, True, 24, {}), pair)
        with patch.object(worker, '_exclusive_volume_pair', return_value=(0.0, 0.0)) as native:
            changed = ob.make_box(2, 3, 6)
            worker._corresponds(a, changed, True, 24, proofs)
            self.assertEqual(native.call_count, 1)
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(10, 0, 0))
        a.Move(TopLoc_Location(move))
        with patch.object(worker, '_exclusive_volume_pair', return_value=(1.0, 1.0)) as native:
            self.assertFalse(worker._corresponds(a, b, True, 24, proofs))
            self.assertEqual(native.call_count, 1)  # mutable wrapper identity is not a witness
        with patch.object(worker, '_exclusive_volume_pair', return_value=(1e-9, 1e-9)):
            self.assertTrue(worker._corresponds(a, b, True, 24, {}))
        with patch.object(worker, '_exclusive_volume_pair', return_value=(3e-8, 0)):
            self.assertFalse(worker._corresponds(a, b, True, 24, {}))

    def test_native_failure_never_publishes_receipts(self):
        geometry, reflected, shape = cavity_pair()
        kernel.record_recipe(geometry, kernel.recipe_of(reflected))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'failed-proof'
            stage(root, geometry, shape)
            original = worker._corresponds
            comparisons = []
            def fail_recipe(*args):
                # Count proof boundaries, not surface types: the proxy proof now
                # deliberately receives the original analytic master too.
                comparisons.append(args)
                if len(comparisons) == 2:
                    raise RuntimeError('native failure')
                return original(*args)
            with patch.object(worker, '_corresponds', side_effect=fail_recipe):
                accepted, message, _ = validate(root)
            self.assertFalse(accepted)
            self.assertIn('recipe correspondence cannot be established', message)

    def test_invalid_native_difference_is_not_a_proof(self):
        with patch.object(ob, 'is_valid', return_value=False):
            with self.assertRaisesRegex(ob.KernelError, 'NURBS conversion failed'):
                ob.document_exclusive_volume_pair(ob.make_box(2, 3, 4), ob.make_box(2, 3, 5))

    def test_empty_results_are_not_invalid_nonempty_geometry(self):
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.gp import gp_Pnt
        empty = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(empty)
        self.assertTrue(ob._document_empty_result(empty))
        vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Vertex()
        builder.Add(empty, vertex)
        self.assertFalse(ob._document_empty_result(empty))
        a = ob.make_box(2, 3, 4)
        b = ob.shape_from_bytes(bytes(ob.shape_to_bytes(a, triangles=False)))
        # A runtime/checker rejecting an empty compound cannot turn no material
        # into an invalid nonempty result. No other invalid topology is exempted.
        with patch.object(ob, 'is_valid', return_value=False):
            self.assertEqual(ob.document_exclusive_volume_pair(a, b), (0.0, 0.0))

    def test_retry_proves_both_directions_on_exact_scratch_copies(self):
        a = ob.make_box(2, 3, 4)
        b = ob.shape_from_bytes(bytes(ob.shape_to_bytes(a, triangles=False)))
        originals = tuple(bytes(ob.shape_to_bytes(s, triangles=False)) for s in (a,b))
        native = ob._document_difference_pair
        attempts = []
        def fast_invalid(first, second, **kwargs):
            attempts.append(kwargs)
            if kwargs.get('shared', True):
                raise ob._DocumentBooleanInvalidResult('synthetic invalid fast result')
            return native(first, second, **kwargs)
        with patch.object(ob, '_document_difference_pair', side_effect=fast_invalid), \
                patch.object(ob, '_nurbs_convert', wraps=ob._nurbs_convert) as convert:
            self.assertEqual(ob.document_exclusive_volume_pair(a, b), (0.0, 0.0))
            self.assertEqual(convert.call_count, 2)
            self.assertEqual(attempts, [{}, {'shared': False}])
        self.assertEqual(originals, tuple(bytes(ob.shape_to_bytes(s, triangles=False)) for s in (a,b)))
        # A retry remains a proof, not a waiver: one direction alone is empty here.
        with patch.object(ob, '_document_difference_pair', side_effect=fast_invalid):
            self.assertFalse(worker._corresponds(a, ob.make_box(2, 3, 5), True, 24, {}))
            geometry, wrong, shape = surface_pair()
            wrong_shape = ob.shape_from_brep_data(bridge.brep_data(wrong), sew_tolerance=1e-6)
            self.assertFalse(ob.document_same_open_support(shape, wrong_shape))

    def test_worker_uses_original_master_for_geometry_and_normalized_volume(self):
        geometry = bridge.make_cylinder((0, 0, 0), 2, 4)
        master = ob.make_cylinder(2, 4)
        kernel.register_master(geometry, master)
        geometry.SetUserString(kernel.DOCUMENT_FORM_KEY, 'exact')
        decoded, converted, measured, comparisons = [], [], [], []
        read_shape, convert_shape = ob.shape_from_bytes, ob._nurbs_convert
        measure_volume, prove = worker.schema1_volume, worker._corresponds
        def read(data, wrapper_type=None):
            shape = read_shape(data, wrapper_type)
            decoded.append(shape)
            return shape
        def convert(shape):
            normalized = convert_shape(shape)
            converted.append((shape, normalized))
            return normalized
        def volume(shape):
            measured.append(shape)
            return measure_volume(shape)
        def correspondence(shape, other, *args):
            comparisons.append(shape)
            self.assertIs(shape, decoded[0], 'geometry proof must receive the original decoded master')
            self.assertEqual(ob.face_types(shape), {'CYLINDER': 1, 'PLANE': 2})
            normalized = next(result for source, result in converted if source is decoded[0])
            self.assertIsNot(normalized, shape)
            self.assertTrue(any(item is normalized for item in measured),
                            'normalized volume must still be independently integrated before geometry proof')
            before = bytes(ob.shape_to_bytes(shape, triangles=False))
            result = prove(shape, other, *args)
            self.assertEqual(before, bytes(ob.shape_to_bytes(shape, triangles=False)))
            return result
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)/'original-master'
            stage(payload, geometry, master)
            with patch.object(ob, 'shape_from_bytes', side_effect=read), \
                    patch.object(ob, '_nurbs_convert', side_effect=convert), \
                    patch.object(worker, 'schema1_volume', side_effect=volume), \
                    patch.object(worker, '_corresponds', side_effect=correspondence):
                accepted, message, _ = validate(payload)
            self.assertTrue(accepted, message)
        self.assertEqual(len(comparisons), 2)  # real proxy and real recipe proofs

    @unittest.skipUnless('--archive' in sys.argv, 'supply --archive for the unchanged private regression archive')
    def test_archive_readability_and_native_retry(self):
        from narwhal3d import native_document
        source = Path(sys.argv[sys.argv.index('--archive') + 1])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        ob.document_validation_threads()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'payload'
            native_document.unpack(source, root)
            manifest = json.loads((root / 'manifest.json').read_text())
            payload_hashes = {row['member']: hashlib.sha256((root / row['member']).read_bytes()).hexdigest()
                              for row in manifest['masters']}
            self.assertEqual(payload_hashes, {row['member']: row['sha256'] for row in manifest['masters']})
            raw = r3.File3dm.Read(str(root / 'document.3dm'))
            item = next(obj for obj in raw.Objects if obj.Attributes.Name == 'Polysurface31')
            record = next(row for row in manifest['masters'] if row['object_id'] == str(item.Attributes.Id))
            master = ob.shape_from_bytes((root / record['member']).read_bytes(), record['wrapper_type'])
            recipe = ob.from_recipe(kernel.recipe_of(item.Geometry))
            originals = tuple(bytes(ob.shape_to_bytes(s, triangles=False)) for s in (master, recipe))
            with self.assertRaises(ob._DocumentBooleanInvalidResult):
                ob._document_difference_pair(master, recipe)
            converted = (ob._nurbs_convert(master), ob._nurbs_convert(recipe))
            with self.assertRaises(ob._DocumentBooleanInvalidResult):
                ob._document_difference_pair(*converted)
            start = time.perf_counter()
            results = ob._document_correspondence_differences(master, recipe)
            self.assertTrue(all(ob._document_empty_result(result) for result in results))
            print('POLYSURFACE31 VALID EMPTY RETRY', time.perf_counter()-start, flush=True)
            self.assertEqual(originals, tuple(bytes(ob.shape_to_bytes(s, triangles=False)) for s in (master, recipe)))
        start = time.perf_counter()
        candidate = doc.load_model(source)
        open_seconds = time.perf_counter()-start
        self.assertEqual(len(candidate._native_master_bindings), len(manifest['masters']))
        for (_, master), record in zip(candidate._native_master_bindings, manifest['masters']):
            self.assertEqual(ob.face_types(master), record['face_types'])
            self.assertEqual(ob.is_airtight(master), record['airtight'])
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'after-open'
            native_document.unpack(source, root)
            after_hashes = {member: hashlib.sha256((root/member).read_bytes()).hexdigest()
                            for member in payload_hashes}
        self.assertEqual(payload_hashes, after_hashes)
        print('UNCHANGED MASTER PAYLOAD HASHES', len(after_hashes), json.dumps(after_hashes, sort_keys=True), flush=True)
        print('UNCHANGED ARCHIVE FULL OPEN', open_seconds, 'masters',
              len(candidate._native_master_bindings), 'SHA256', before, flush=True)

    def test_legitimate_open_surface(self):
        geometry, _, shape = surface_pair()
        original = bytes(ob.shape_to_bytes(shape, triangles=False))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'surface'
            stage(root, geometry, shape)
            accepted, message, elapsed = validate(root)
            self.assertTrue(accepted, message)
            print('LEGITIMATE OPEN SURFACE', elapsed, flush=True)
        self.assertEqual(original, bytes(ob.shape_to_bytes(shape, triangles=False)))

    def test_real_open_rejects_attacks_without_publication(self):
        from narwhal3d import native_document
        cavity, reflected, master = cavity_pair()
        kernel.record_recipe(cavity, kernel.recipe_of(reflected))
        surface, wrong_proxy, surface_master = surface_pair()
        for key, value in surface.GetUserStrings():
            wrong_proxy.SetUserString(key, value)
        with tempfile.TemporaryDirectory() as directory:
            for name, geometry, shape, expected in (
                    ('recipe', cavity, master, 'recipe'),
                    ('proxy', wrong_proxy, surface_master, 'proxy')):
                root = Path(directory) / name
                stage(root, geometry, shape)
                archive = Path(directory) / (name + '.narwhal')
                native_document.pack(root, archive)
                with self.assertRaisesRegex(doc.NarwhalDocumentError, expected):
                    doc.load_model(archive)
        self.assertIs(kernel.master_of(cavity), master)
        self.assertIs(kernel.master_of(surface), surface_master)

    def test_legitimate_cavity(self):
        geometry, _, shape = cavity_pair()
        original = bytes(ob.shape_to_bytes(shape, triangles=False))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'solid'
            stage(root, geometry, shape)
            accepted, message, elapsed = validate(root)
            self.assertTrue(accepted, message)
            print('LEGITIMATE CAVITY', elapsed, flush=True)
        self.assertEqual(original, bytes(ob.shape_to_bytes(shape, triangles=False)))


def run_tests(names=None):
    suite = (unittest.TestSuite(CorrespondenceTests(name) for name in names) if names else
             unittest.defaultTestLoader.loadTestsFromTestCase(CorrespondenceTests))
    return unittest.TextTestRunner(verbosity=2).run(suite)


def mutations():
    main_source = inspect.getsource(worker.main)
    original_main = worker.main
    for label, old, new, names in (
        ('restore converted geometry operand',
         'if not _corresponds(shape, proxy_shape, True,',
         'if not _corresponds(correlation_shape, proxy_shape, True,',
         ['test_worker_uses_original_master_for_geometry_and_normalized_volume']),
        ('remove recipe geometric proof',
         'if not _corresponds(shape, recipe_shape, airtight, volume, proofs):',
         'if False:', ['test_reflected_cavity_recipe', 'test_changed_open_recipe',
                       'test_small_cavity_displacement_recipe']),
        ('remove open proxy proof',
         'elif not _corresponds(shape, proxy_shape, False, None, proofs):',
         'elif False:', ['test_same_bbox_distinct_open_proxy'])):
        assert old in main_source
        exec(compile(main_source.replace(old, new), '<worker-mutant>', 'exec'), worker.__dict__)
        try:
            assert not run_tests(names).wasSuccessful(), 'SURVIVED: ' + label
            print('MUTATION RED:', label, flush=True)
        finally:
            worker.main = original_main
    source = inspect.getsource(worker._corresponds)
    original = worker._corresponds
    for label, old, new in (
        ('trust one direction only', 'for value in pair)', 'for value in pair[:1])'),
        ('unsafe summary-only reuse', 'key = (airtight, exclusive_tolerance, witnesses)',
         'key = (airtight, exclusive_tolerance, None)'),
        ('accept every byte witness', 'if witnesses[0] == witnesses[1]:', 'if True:'),
        ('ignore nonfinite result', 'math.isfinite(value) and 0.0 <= value <= exclusive_tolerance',
         'not (value > exclusive_tolerance)')):
        assert old in source
        exec(compile(source.replace(old, new), '<proof-mutant>', 'exec'), worker.__dict__)
        try:
            assert not run_tests(['test_record_local_exact_byte_reuse_and_nonfinite_rejection']).wasSuccessful(), label
            print('MUTATION RED:', label, flush=True)
        finally:
            worker._corresponds = original
    original_proof = worker._corresponds
    source = inspect.getsource(original_proof)
    old = 'exclusive_tolerance = abs(volume) * 1.0e-9 if airtight else None'
    assert old in source
    exec(compile(source.replace(old,
        'exclusive_tolerance = max(abs(volume) * 1.0e-9, 0.001) if airtight else None'),
        '<cubic-tolerance-mutant>', 'exec'), worker.__dict__)
    try:
        assert not run_tests(['test_small_cavity_displacement_recipe']).wasSuccessful()
        print('MUTATION RED: cubic model-tolerance material allowance', flush=True)
    finally:
        worker._corresponds = original_proof
    original = ob._document_difference_pair
    source = inspect.getsource(original)
    old = 'if not result.IsNull() and not is_valid(result):'
    assert old in source
    exec(compile(source.replace(old, 'if False:'), '<invalid-result-mutant>', 'exec'), ob.__dict__)
    try:
        assert not run_tests(['test_invalid_native_difference_is_not_a_proof']).wasSuccessful()
        print('MUTATION RED: invalid native result accepted', flush=True)
    finally:
        ob._document_difference_pair = original
    original = ob.document_same_open_support
    source = inspect.getsource(original)
    old = '_document_correspondence_differences(shape_a, shape_b):'
    assert old in source
    exec(compile(source.replace(old, '_document_correspondence_differences(shape_a, shape_b)[:1]:'),
                 '<surface-direction-mutant>', 'exec'), ob.__dict__)
    try:
        assert not run_tests(['test_native_open_edges_and_trimmed_surfaces']).wasSuccessful()
        print('MUTATION RED: one-sided open support', flush=True)
    finally:
        ob.document_same_open_support = original
    original = ob._document_correspondence_differences
    source = inspect.getsource(original)
    for label, old, new, name in (
        ('remove exact retry', 'return _document_difference_pair(*converted, shared=False)',
         'raise _DocumentBooleanInvalidResult("retry removed")',
         'test_retry_proves_both_directions_on_exact_scratch_copies'),
        ('reuse broken intersection filler', 'shared=False', 'shared=True',
         ('test_archive_readability_and_native_retry' if '--archive' in sys.argv else
          'test_retry_proves_both_directions_on_exact_scratch_copies')),
        ('skip exact conversion', 'converted = tuple(_nurbs_convert(shape) for shape in (shape_a, shape_b))',
         'converted = (shape_a, shape_b)', 'test_retry_proves_both_directions_on_exact_scratch_copies')):
        assert old in source
        exec(compile(source.replace(old, new), '<retry-mutant>', 'exec'), ob.__dict__)
        try:
            assert not run_tests([name]).wasSuccessful(), 'SURVIVED: ' + label
            print('MUTATION RED:', label, flush=True)
        finally:
            ob._document_correspondence_differences = original
    original = ob._document_empty_result
    source = inspect.getsource(original)
    old = 'and not TopoDS_Iterator(shape).More()'
    assert old in source
    exec(compile(source.replace(old, ''), '<empty-mutant>', 'exec'), ob.__dict__)
    try:
        assert not run_tests(['test_empty_results_are_not_invalid_nonempty_geometry']).wasSuccessful()
        print('MUTATION RED: nonempty compound called empty', flush=True)
    finally:
        ob._document_empty_result = original
    assert run_tests().wasSuccessful()
    print('RESTORED GREEN', flush=True)


def profile_archive(source):
    from narwhal3d import native_document
    ob.document_validation_threads()
    with tempfile.TemporaryDirectory(prefix='correspondence-profile-') as directory:
        root = Path(directory) / 'payload'
        native_document.unpack(source, root)
        manifest = json.loads((root / 'manifest.json').read_text())
        raw = r3.File3dm.Read(str(root / 'document.3dm'))
        objects = {str(obj.Attributes.Id): obj for obj in raw.Objects}
        for index, record in enumerate(manifest['masters']):
            obj = objects[record['object_id']]
            shape = ob.shape_from_bytes((root / record['member']).read_bytes(), record['wrapper_type'])
            case = Path(directory) / str(index)
            stage(case, obj.Geometry.Duplicate(), shape,
                  tolerance=raw.Settings.ModelAbsoluteTolerance)
            original = worker._corresponds
            timings = []
            def measured(*args):
                start = time.perf_counter()
                try:
                    result = original(*args)
                    timings.append((time.perf_counter()-start, result))
                    return result
                except Exception as exc:
                    timings.append((time.perf_counter()-start, str(exc)))
                    raise
            with patch.object(worker, '_corresponds', side_effect=measured):
                accepted, message, elapsed = validate(case)
            print('PROFILE RECORD', obj.Attributes.Name, 'accepted', accepted, 'worker_s', elapsed,
                  'proofs', timings, message.strip(), flush=True)


def diagnose_archive(source):
    from narwhal3d import native_document
    ob.document_validation_threads()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / 'payload'
        native_document.unpack(source, root)
        manifest = json.loads((root / 'manifest.json').read_text())
        raw = r3.File3dm.Read(str(root / 'document.3dm'))
        objects = {str(obj.Attributes.Id): obj for obj in raw.Objects}
        for record in manifest['masters']:
            obj = objects[record['object_id']]
            master = ob.shape_from_bytes((root / record['member']).read_bytes(), record['wrapper_type'])
            recipe = ob.from_recipe(kernel.recipe_of(obj.Geometry))
            if '--object' in sys.argv and obj.Attributes.Name != sys.argv[sys.argv.index('--object') + 1]:
                continue
            print('DIAGNOSE', obj.Attributes.Name, record['object_id'],
                  ob.face_types(master), ob.face_types(recipe), flush=True)
            from OCP.BRep import BRep_Builder
            from OCP.TopoDS import TopoDS_Compound, TopoDS_Iterator
            from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
            from OCP.TopExp import TopExp_Explorer
            empty = TopoDS_Compound()
            BRep_Builder().MakeCompound(empty)
            print('EMPTY VALIDITY', ob.is_valid(empty), 'has children', TopoDS_Iterator(empty).More(), flush=True)
            converted = (ob._nurbs_convert(master), ob._nurbs_convert(recipe))
            # Diagnostic-only raw results, never used for admission or receipts.
            namespace = dict(ob.__dict__)
            code = inspect.getsource(ob._document_difference_pair).replace(
                'if not result.IsNull() and not is_valid(result):', 'if False:')
            exec(compile(code, '<diagnostic-only>', 'exec'), namespace)
            for label, a, b, shared in (
                    ('native-shared', master, recipe, True),
                    ('nurbs-shared', *converted, True),
                    ('nurbs-independent', *converted, False)):
                start = time.perf_counter()
                results = namespace['_document_difference_pair'](a, b, shared=shared)
                print('DIAGNOSE (RAW, NOT A PROOF)', label,
                      'empty', [ob._document_empty_result(r) for r in results],
                      'valid', [ob.is_valid(r) for r in results],
                      'volumes', [ob.document_schema1_volume(r) for r in results],
                      'face/edge/vertex', [[ob._count(r, kind) for kind in
                                           (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)] for r in results],
                      'seconds', time.perf_counter()-start, flush=True)



if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    elif '--profile' in sys.argv:
        profile_archive(sys.argv[sys.argv.index('--profile') + 1])
    elif '--diagnose' in sys.argv:
        diagnose_archive(sys.argv[sys.argv.index('--diagnose') + 1])
    elif not run_tests().wasSuccessful():
        raise SystemExit(1)
