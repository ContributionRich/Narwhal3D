#!/usr/bin/env python3
"""Focused Save-summary reuse, native timings, and in-memory source mutation witnesses."""
import hashlib
import inspect
from pathlib import Path
import sys
import time
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from narwhal3d import document_admission, narwhal_document as doc
from narwhal3d.kernel import occt_backend as backend
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere


class SummaryTests(unittest.TestCase):
    def setUp(self):
        doc._shape_summaries.clear()
        self.shape = BRepPrimAPI_MakeSphere(3.0).Shape()
        self.obj = SimpleNamespace(Attributes=SimpleNamespace(Id=uuid.uuid4()))
        self.calls = []
        real = backend.tight_bbox
        self.bounds = patch.object(backend, 'tight_bbox', side_effect=lambda shape: (
            self.calls.append(shape) or real(shape)))
        self.bounds.start()
        self.addCleanup(self.bounds.stop)

    def record(self, shape=None, reuse=True, obj=None):
        shape = self.shape if shape is None else shape
        _, version = doc._format_contract()
        digest = hashlib.sha256(bytes(backend.shape_to_bytes(
            shape, triangles=False, format_version=version))).hexdigest()
        return doc._shape_record(obj or self.obj, shape, 'masters/test.brep', digest,
                                 reuse_save_summary=reuse)

    def test_warm_and_independent(self):
        first = self.record()
        expected = dict(first, bounds=list(first['bounds']), face_types=dict(first['face_types']))
        first['bounds'][0] = 12345
        first['face_types'].clear()
        second_obj = SimpleNamespace(Attributes=SimpleNamespace(Id=uuid.uuid4()))
        second = self.record(obj=second_obj)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second['bounds'], expected['bounds'])
        self.assertEqual(second['face_types'], expected['face_types'])
        self.assertEqual(second['object_id'], str(second_obj.Attributes.Id))
        second['bounds'][0] = 999
        self.assertEqual(self.record()['bounds'], expected['bounds'])

    def test_changed_serialized_master(self):
        first = self.record()
        changed = BRepPrimAPI_MakeSphere(8.0).Shape()
        second = self.record(changed)
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['bounds'], second['bounds'])

    def test_same_wrapper_mutated_location(self):
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location
        first = self.record()
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(20, 0, 0))
        self.shape.Move(TopLoc_Location(transform))
        second = self.record()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['bounds'], second['bounds'])

    def test_default_never_reuses_archive_digest(self):
        first = self.record()
        changed = BRepPrimAPI_MakeSphere(8.0).Shape()
        second = doc._shape_record(self.obj, changed, 'untrusted', first['sha256'])
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first['bounds'], second['bounds'])

    def test_runtime_source_format_invalidate(self):
        self.record()
        contract = doc._shape_summary_contract()
        for index in range(len(contract)):
            changed = list(contract)
            changed[index] = ('different', index)
            with patch.object(doc, '_shape_summary_contract', return_value=tuple(changed)):
                self.record()
        self.assertEqual(len(self.calls), 1 + len(contract))

    def test_contract_tracks_sources_runtime_format(self):
        original = doc._shape_summary_contract()
        for filename in ('narwhal_document.py', 'document_compat.py', 'occt_backend.py'):
            real = Path.read_bytes
            with patch.object(Path, 'read_bytes', lambda path: real(path) + (
                    b'\n# changed' if path.name == filename else b'')):
                self.assertNotEqual(original, doc._shape_summary_contract())
        self.record()
        with patch.object(document_admission, 'runtime_identity', return_value=('other runtime',)):
            self.assertNotEqual(original, doc._shape_summary_contract())
            self.record()
            self.record()
            self.assertEqual(len(self.calls), 2)  # new runtime measures once, then reuses
        with patch.object(doc, '_format_contract', return_value=('other', 99)):
            self.assertNotEqual(original, doc._shape_summary_contract())

    def test_bounded_lru(self):
        with patch.object(doc, 'MAX_SHAPE_SUMMARIES', 2):
            self.record()
            self.record(BRepPrimAPI_MakeSphere(4.0).Shape())
            self.record()  # refresh sphere 3, evict sphere 4 next
            self.record(BRepPrimAPI_MakeSphere(5.0).Shape())
            self.assertEqual(len(doc._shape_summaries), 2)
            self.record()
            self.assertEqual(len(self.calls), 3)
            self.record(BRepPrimAPI_MakeSphere(4.0).Shape())
            self.assertEqual(len(self.calls), 4)

    def test_invalid_not_cached(self):
        with patch.object(backend, 'tight_bbox', return_value=((float('nan'), 0, 0), (1, 1, 1))):
            with self.assertRaises(doc.NarwhalDocumentError):
                self.record()
        self.assertFalse(doc._shape_summaries)
        with patch.object(backend, 'is_valid', return_value=False):
            with self.assertRaises(doc.NarwhalDocumentError):
                self.record()
        self.assertFalse(doc._shape_summaries)


def run_tests():
    return unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SummaryTests))


def mutations():
    # Compile only our function into a temporary globals dictionary: no shared files
    # are ever broken while other agents are running.
    source = inspect.getsource(doc._shape_record)
    cases = [
        ('disable reuse', 'cached = _shape_summaries.get(key)', 'cached = None'),
        ('omit serialized SHA', 'digest, wrapper_type,', '"constant", wrapper_type,'),
        ('omit contract', '_shape_summary_contract(), digest', 'None, digest'),
        ('trust arbitrary callers', 'if reuse_save_summary:', 'if True:'),
        ('alias returned summaries', 'deepcopy(cached)', 'cached'),
        ('remove bound', 'while len(_shape_summaries) > MAX_SHAPE_SUMMARIES:',
         'while False:'),
        ('remove LRU refresh', '_shape_summaries.move_to_end(key)', 'pass'),
        ('accept invalid shape', 'not occt_backend.is_valid(shape)', 'False'),
        ('accept nonfinite bounds', 'if not all(math.isfinite(value) for value in bounds):',
         'if False:'),
    ]
    original = doc._shape_record
    for label, old, new in cases:
        assert old in source, label
        exec(compile(source.replace(old, new), '<summary-mutant>', 'exec'), doc.__dict__)
        try:
            result = run_tests()
            assert not result.wasSuccessful(), f'SURVIVED: {label}'
            print(f'MUTATION RED: {label}', flush=True)
        finally:
            doc._shape_record = original
            doc._shape_summaries.clear()
    contract_source = inspect.getsource(doc._shape_summary_contract)
    original_contract = doc._shape_summary_contract
    contract_cases = [
        ('ignore summary sources', 'source.digest()', 'b"constant"'),
        ('ignore runtime identity', 'document_admission.runtime_identity()', 'None'),
        ('ignore wire format', '_format_contract()', 'None'),
    ]
    for label, old, new in contract_cases:
        assert old in contract_source
        exec(compile(contract_source.replace(old, new), '<contract-mutant>', 'exec'), doc.__dict__)
        try:
            assert not run_tests().wasSuccessful(), f'SURVIVED: {label}'
            print(f'MUTATION RED: {label}', flush=True)
        finally:
            doc._shape_summary_contract = original_contract
            doc._shape_summaries.clear()
    assert run_tests().wasSuccessful(), 'restored suite must be GREEN'
    print('RESTORED GREEN', flush=True)


def timing():
    shape = BRepPrimAPI_MakeSphere(7.0).Shape()
    obj = SimpleNamespace(Attributes=SimpleNamespace(Id=uuid.uuid4()))
    _, version = doc._format_contract()
    doc._shape_summaries.clear()
    for label in ('cold', 'warm'):
        start = time.perf_counter()
        data = bytes(backend.shape_to_bytes(shape, triangles=False, format_version=version))
        digest = hashlib.sha256(data).hexdigest()
        serialized = time.perf_counter()
        doc._shape_record(obj, shape, 'masters/timing.brep', digest, reuse_save_summary=True)
        end = time.perf_counter()
        print(f'TIMING sphere {label}: serialize+SHA={serialized-start:.6f}s '
              f'summary={end-serialized:.6f}s total={end-start:.6f}s', flush=True)


def fixture_timing(source):
    from narwhal3d.model import NarwhalModel
    source = Path(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    model = NarwhalModel.load(str(source))
    doc._shape_summaries.clear()
    original = doc._shape_record
    real_bounds = backend.tight_bbox
    with tempfile.TemporaryDirectory(prefix='summary-profile-') as directory:
        for label in ('cold', 'warm', 'uncached'):
            costs = []
            bounds = []
            def measured(*args, **kwargs):
                if label == 'uncached':
                    kwargs['reuse_save_summary'] = False
                start = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    costs.append(time.perf_counter() - start)
            def bbox(shape):
                start = time.perf_counter()
                try:
                    return real_bounds(shape)
                finally:
                    bounds.append(time.perf_counter() - start)
            start = time.perf_counter()
            with patch.object(doc, '_shape_record', measured), patch.object(backend, 'tight_bbox', bbox):
                doc.save_model(model, Path(directory) / (label + '.narwhal'))
            print(f'FIXTURE {source.name} {label}: Save={time.perf_counter()-start:.6f}s '
                  f'summaries={sum(costs):.6f}s records={len(costs)} '
                  f'host_bounds={sum(bounds):.6f}s calls={len(bounds)}', flush=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


if __name__ == '__main__':
    if '--mutations' in sys.argv:
        mutations()
    else:
        if not run_tests().wasSuccessful():
            raise SystemExit(1)
        timing()
        if '--fixture' in sys.argv:
            fixture_timing(sys.argv[sys.argv.index('--fixture') + 1])
