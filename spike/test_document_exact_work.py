#!/usr/bin/env python3
"""Native exact Open comparison profiling and focused regression checks."""
from pathlib import Path
import hashlib
import json
import inspect

import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import bridge, kernel, native_document
from narwhal3d.kernel import occt_backend as ob
import rhino3dm as r3


def profile_pair(a, b, use_obb):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BOPAlgo import BOPAlgo_PaveFiller
    from OCP.collections import List_TopoDS_Shape
    args = List_TopoDS_Shape()
    args.Append(a)
    args.Append(b)
    filler = BOPAlgo_PaveFiller()
    filler.SetArguments(args)
    filler.SetNonDestructive(True)
    filler.SetRunParallel(True)
    filler.SetUseOBB(use_obb)
    start = time.perf_counter()
    filler.Perform()
    assert not filler.HasErrors()
    phases = {'intersections': time.perf_counter() - start}
    values = []
    for index, (source, tool) in enumerate(((a, b), (b, a))):
        sources, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        sources.Append(source)
        tools.Append(tool)
        start = time.perf_counter()
        cut = BRepAlgoAPI_Cut(filler)
        cut.SetArguments(sources)
        cut.SetTools(tools)
        cut.SetNonDestructive(True)
        cut.SetRunParallel(True)
        cut.Build()
        assert cut.IsDone()
        phases['cut' + str(index)] = time.perf_counter() - start
        result = cut.Shape()
        start = time.perf_counter()
        values.append(0.0 if result.IsNull() else abs(ob.document_schema1_volume(result)))
        phases['volume' + str(index)] = time.perf_counter() - start
    return values, phases


def profile_archive(source):
    ob.document_validation_threads()
    before = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='exact-open-profile-') as directory:
        root = Path(directory) / 'payload'
        native_document.unpack(source, root)
        manifest = json.loads((root / 'manifest.json').read_text())
        document = r3.File3dm.Read(str(root / 'document.3dm'))
        objects = {str(obj.Attributes.Id): obj for obj in document.Objects}
        for record in manifest['masters']:
            obj = objects[record['object_id']]
            if obj.Attributes.Name not in ('Extrusion1', 'Extrusion3'):
                continue
            master = ob.shape_from_bytes((root / record['member']).read_bytes(), record['wrapper_type'])
            proxy = ob.shape_from_brep_data(bridge.brep_data(obj.Geometry),
                                          sew_tolerance=document.Settings.ModelAbsoluteTolerance)
            correlation = ob._nurbs_convert(master)
            print('PROFILE OBJECT', obj.Attributes.Name, ob.face_types(master), flush=True)
            for use_obb in (() if '--bounds-only' in sys.argv else (True, False)):
                values, phases = profile_pair(correlation, proxy, use_obb)
                print('PAIR', obj.Attributes.Name, 'OBB', use_obb, values, phases, flush=True)
            for label, shape in (('master', master), ('proxy', proxy),
                                 ('recipe', ob.from_recipe(kernel.recipe_of(obj.Geometry)))):
                encoded = bytes(ob.shape_to_bytes(shape, triangles=False))
                print('EXACT BYTES', obj.Attributes.Name, label, len(encoded),
                      hashlib.sha256(encoded).hexdigest(), flush=True)
                start = time.perf_counter()
                box = ob.tight_bbox(shape)
                print('BOUNDS', obj.Attributes.Name, label, time.perf_counter()-start, box, flush=True)
                if label == 'master':
                    start = time.perf_counter()
                    candidate = ob.tight_bbox(shape, parallel=True)
                    print('BOUNDS PARALLEL', obj.Attributes.Name,
                          time.perf_counter()-start, candidate, flush=True)
                    assert all(abs(a-b) < 1e-10 for x,y in zip(box,candidate) for a,b in zip(x,y))
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before


def profile_open(source):
    from narwhal3d import narwhal_document as doc
    start = time.perf_counter()
    model = doc.load_model(source)
    print('OPEN FULL', source, time.perf_counter()-start,
          'masters', len(model._native_master_bindings), flush=True)


class ExactBoundsTests(unittest.TestCase):
    def test_native_results_and_mutation(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
        from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
        from OCP.gp import gp_GTrsf, gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location
        ob.document_validation_threads()
        matrix = gp_GTrsf()
        for index, scale in enumerate((2.0, 3.0, 4.0), 1):
            matrix.SetValue(index, index, scale)
        shape = BRepBuilderAPI_GTransform(BRepPrimAPI_MakeSphere(1.0).Shape(), matrix, True).Shape()
        serial = ob.tight_bbox(shape)
        parallel = ob.tight_bbox(shape, parallel=True)
        for actual, expected in zip((*parallel[0], *parallel[1]), (-2, -3, -4, 2, 3, 4)):
            self.assertAlmostEqual(actual, expected, places=6)
        for a, b in zip((*serial[0], *serial[1]), (*parallel[0], *parallel[1])):
            self.assertAlmostEqual(a, b, places=10)
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(10, -20, 30))
        shape.Move(TopLoc_Location(move))
        moved = ob.tight_bbox(shape, parallel=True)
        for point, original in zip(moved, parallel):
            for actual, expected, delta in zip(point, original, (10, -20, 30)):
                self.assertAlmostEqual(actual, expected + delta, places=8)

    def test_one_source_six_independent_planes(self):
        import OCP.BRepExtrema as extrema
        real = extrema.BRepExtrema_DistShapeShape
        instances, events = [], []
        class Distance:
            def __init__(self, *args):
                self.query = real(*args)
                instances.append(self)
            def SetMultiThread(self, enabled):
                events.append(('parallel', enabled))
                self.query.SetMultiThread(enabled)
            def LoadS1(self, shape):
                events.append(('source', shape))
                self.query.LoadS1(shape)
            def LoadS2(self, face):
                events.append(('plane', face))
                self.query.LoadS2(face)
            def Perform(self):
                events.append(('solve', None))
                return self.query.Perform()
            def IsDone(self):
                return self.query.IsDone()
            def Value(self):
                return self.query.Value()
        with patch.object(extrema, 'BRepExtrema_DistShapeShape', Distance):
            shape = ob.make_sphere(2)
            result = ob._exact_bounds(shape, [-2]*3, [2]*3, parallel=True)
        self.assertEqual(len(instances), 1)
        self.assertEqual([v for k,v in events if k == 'parallel'], [True])
        self.assertEqual(len([1 for k,v in events if k == 'source']), 1)
        planes = [v for k,v in events if k == 'plane']
        self.assertEqual(len(planes), 6)
        self.assertEqual(len([1 for k,v in events if k == 'solve']), 6)
        for i, face in enumerate(planes):
            self.assertTrue(all(not face.IsSame(other) for other in planes[:i]))
        for point, expected in zip(result, (-2.0, 2.0)):
            for actual in point:
                self.assertAlmostEqual(actual, expected, places=9)

    def test_bbox_forwards_explicit_parallel_only(self):
        shape = ob._nurbs_convert(ob.make_sphere(2))
        with patch.object(ob, '_exact_bounds', return_value='native bounds') as exact:
            self.assertEqual(ob.tight_bbox(shape, parallel=True), 'native bounds')
            self.assertEqual(exact.call_args.kwargs, {'parallel': True})
            self.assertEqual(ob.tight_bbox(shape), 'native bounds')
            self.assertEqual(exact.call_args.kwargs, {'parallel': False})

    def test_serial_default_unchanged(self):
        import OCP.BRepExtrema as extrema
        real = extrema.BRepExtrema_DistShapeShape
        calls = []
        def construct(*args):
            calls.append(args)
            return real(*args)
        with patch.object(extrema, 'BRepExtrema_DistShapeShape', construct):
            ob._exact_bounds(ob.make_sphere(2), [-2]*3, [2]*3)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(len(args) == 2 for args in calls))

    def test_worker_full_checks_use_bounded_parallelism(self):
        from narwhal3d import document_worker as worker, narwhal_document as doc
        from narwhal3d.model import NarwhalModel
        import resource
        model = NarwhalModel()
        geometry = bridge.make_box((0,0,0), (2,3,0), 4)
        kernel.register_master(geometry, ob.make_box(2, 3, 4))
        geometry.SetUserString(kernel.DOCUMENT_FORM_KEY, 'exact')
        model.add_object(geometry, 'Exact', 0)
        with tempfile.TemporaryDirectory(prefix='exact-worker-test-') as directory:
            archive = Path(directory) / 'input.narwhal'
            doc.save_model(model, archive)
            root = Path(directory) / 'payload'
            native_document.unpack(archive, root)
            calls = []
            original = ob.tight_bbox
            def bounds(shape, **kwargs):
                calls.append(kwargs)
                return original(shape, **kwargs)
            with patch.object(sys, 'argv', ['worker', '--', str(root)]), \
                    patch.object(worker.document_admission, 'read_hints', return_value=()), \
                    patch.object(resource, 'setrlimit'), patch.object(ob, 'tight_bbox', bounds):
                worker.main()
            self.assertEqual(len(calls), 3)  # independent master, proxy, recipe
            self.assertTrue(all(call == {'parallel': True} for call in calls))
            from OCP.OSD import OSD_ThreadPool
            self.assertEqual(OSD_ThreadPool.DefaultPool_s().NbThreads(), 2)


def run_tests(names=None):
    suite = (unittest.TestSuite(ExactBoundsTests(name) for name in names) if names else
             unittest.defaultTestLoader.loadTestsFromTestCase(ExactBoundsTests))
    return unittest.TextTestRunner(verbosity=2).run(suite)


def mutations():
    original = ob._exact_bounds
    source = inspect.getsource(original)
    cases = [
        ('disable native parallel', 'distance.SetMultiThread(True)', 'distance.SetMultiThread(False)',
         'test_one_source_six_independent_planes'),
        ('reload source six times', 'distance.LoadS2(face)', 'distance.LoadS1(shape)\n                distance.LoadS2(face)',
         'test_one_source_six_independent_planes'),
        ('skip one side', 'for sign in (-1.0, 1.0):', 'for sign in (-1.0,):',
         'test_one_source_six_independent_planes'),
        ('reverse side distance', 'base + ext.Value()', 'base - ext.Value()',
         'test_native_results_and_mutation'),
        ('parallel by default', 'parallel=False', 'parallel=True', 'test_serial_default_unchanged'),
    ]
    for label, old, new, name in cases:
        assert old in source
        exec(compile(source.replace(old, new), '<bounds-mutant>', 'exec'), ob.__dict__)
        try:
            assert not run_tests([name]).wasSuccessful(), 'SURVIVED: ' + label
            print('MUTATION RED:', label, flush=True)
        finally:
            ob._exact_bounds = original
    original_bbox = ob.tight_bbox
    source = inspect.getsource(original_bbox)
    assert 'parallel=parallel' in source
    exec(compile(source.replace('parallel=parallel', 'parallel=False'), '<dispatch-mutant>', 'exec'), ob.__dict__)
    try:
        assert not run_tests(['test_bbox_forwards_explicit_parallel_only']).wasSuccessful()
        print('MUTATION RED: missing bounds dispatch', flush=True)
    finally:
        ob.tight_bbox = original_bbox
    from narwhal3d import document_worker as worker
    original_main = worker.main
    source = inspect.getsource(original_main)
    assert 'parallel=True' in source
    exec(compile(source.replace('parallel=True', 'parallel=False'), '<worker-mutant>', 'exec'), worker.__dict__)
    try:
        assert not run_tests(['test_worker_full_checks_use_bounded_parallelism']).wasSuccessful()
        print('MUTATION RED: worker opt-in missing', flush=True)
    finally:
        worker.main = original_main
    assert run_tests().wasSuccessful()
    print('RESTORED GREEN', flush=True)


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    elif '--profile' not in sys.argv and '--open-profile' not in sys.argv:
        if not run_tests().wasSuccessful():
            raise SystemExit(1)
    if '--open-profile' in sys.argv:
        profile_open(sys.argv[sys.argv.index('--open-profile') + 1])
    if '--profile' in sys.argv:
        profile_archive(sys.argv[sys.argv.index('--profile') + 1])
