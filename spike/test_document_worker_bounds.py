#!/usr/bin/env python3
"""Worker-completed, exact-witness bounds reuse. Run in headless Blender.

--mutations observes deliberate safety/performance regressions RED in memory,
then runs the restored on-disk suite GREEN. --archive adds the original archive
Open and hash-preservation measurement; no archive-supplied fact is trusted.
"""
from contextlib import contextmanager, redirect_stdout
import hashlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rhino3dm as r3
from narwhal3d import bridge, document_worker as worker, kernel
from narwhal3d import narwhal_document as doc, native_document
from narwhal3d.kernel import occt_backend as ob
from narwhal3d.model import NarwhalModel


def encoded(body):
    return json.dumps(body, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


def bundle(output, shapes):
    """Unit fixture simulating an otherwise completed trusted worker's output."""
    output.mkdir()
    (output/'document.3dm').write_bytes(b'test metadata output')
    facts = ob.DocumentBoundsFacts(ob._document_bounds_identity())
    members = ['document.3dm']
    for index, shape in enumerate(shapes, 1):
        member = str(uuid.UUID(int=index)) + '.brep'
        (output/member).write_bytes(bytes(ob.shape_to_bytes(shape, triangles=False)))
        members.append(member)
        ob.document_measure_bounds(shape, facts)
    completion = ob.document_publish_bounds(output, facts, members)
    assert completion is not None
    return completion


def rewrite(output, completion, change, resign=True):
    path = output/ob._DOCUMENT_BOUNDS_FILE
    body = json.loads(path.read_bytes())
    change(body)
    data = encoded(body)
    path.write_bytes(data)
    result = dict(completion)
    if resign:
        result.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    return result


def exact_box(width=4):
    geometry = bridge.make_box((0,0,0), (width,4,0), 4)
    kernel.register_master(geometry, ob.make_box(width,4,4))
    geometry.SetUserString(kernel.DOCUMENT_FORM_KEY, 'exact')
    return geometry


class WorkerBoundsTests(unittest.TestCase):
    def test_thread_runtime_images_are_digest_qualified_before_loading(self):
        import os
        from narwhal3d import document_admission
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'libtbb-fixture.so'
            image.write_bytes(b'qualified fixture, never executed')
            digest=hashlib.sha256(image.read_bytes()).hexdigest()
            original=list(worker._runtime_images)
            try:
                with patch.dict(os.environ,{document_admission.WORKER_IMAGES_ENV:json.dumps([[str(image),digest]])}), patch('ctypes.CDLL',return_value=object()) as load:
                    worker._match_parent_runtime_images()
                    load.assert_called_once_with(str(image))
                for value in ([[str(image),'0'*64]],[[str(image.with_name('unrelated.so')),digest]],{},[[str(image),digest]]*17):
                    with self.subTest(value=str(value)[:80]), patch.dict(os.environ,{document_admission.WORKER_IMAGES_ENV:json.dumps(value)}), patch('ctypes.CDLL') as load:
                        with self.assertRaises(SystemExit):worker._match_parent_runtime_images()
                        load.assert_not_called()
            finally:worker._runtime_images[:]=original

    def test_fresh_witness_hit_independent_returns_and_lifetime(self):
        shape = ob._nurbs_convert(ob.make_sphere(2))
        native = ob.tight_bbox(shape)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'validated'
            completion = bundle(output, [shape])
            with ob.document_bounds_context() as state:
                self.assertTrue(ob.document_accept_bounds(state, output, completion))
                with patch.object(ob, '_exact_bounds', side_effect=AssertionError('cache miss')):
                    first = ob.tight_bbox(shape)
                    second = ob.tight_bbox(shape)
                self.assertEqual(first, native)
                self.assertEqual(second, native)
                self.assertIsNot(first, second)
                self.assertIsNot(first[0], second[0])
                self.assertEqual(state['hits'], 2)
            self.assertFalse(state['entries'])
            with ob.document_bounds_context() as other:
                self.assertEqual(ob.tight_bbox(shape), native)
                self.assertEqual(other['hits'], 0)
        self.assertIsNone(ob._document_bounds_active.get())

    def test_nested_context_and_exception_cleanup(self):
        shape=ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'validated'
            completion=bundle(output,[shape])
            with self.assertRaisesRegex(RuntimeError,'parent failed'):
                with ob.document_bounds_context() as outer:
                    self.assertTrue(ob.document_accept_bounds(outer,output,completion))
                    with self.assertRaisesRegex(ValueError,'nested failed'):
                        with ob.document_bounds_context() as inner:
                            self.assertEqual(ob.tight_bbox(shape),((0.,0.,0.),(4.,4.,4.)))
                            self.assertEqual(inner['hits'],0)
                            raise ValueError('nested failed')
                    self.assertIs(ob._document_bounds_active.get(),outer)
                    ob.tight_bbox(shape)
                    self.assertEqual(outer['hits'],1)
                    raise RuntimeError('parent failed')
            self.assertFalse(outer['entries'])
            self.assertFalse(inner['entries'])
            self.assertIsNone(ob._document_bounds_active.get())

    def test_worker_measurements_never_consume_parent_cache(self):
        shape=ob._nurbs_convert(ob.make_sphere(2))
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'validated'
            completion=bundle(output,[shape])
            with ob.document_bounds_context() as state:
                self.assertTrue(ob.document_accept_bounds(state,output,completion))
                fresh=ob.DocumentBoundsFacts(ob._document_bounds_identity())
                with patch.object(ob,'_exact_bounds',wraps=ob._exact_bounds) as native:
                    ob.document_measure_bounds(shape,fresh)
                    native.assert_called_once()
                self.assertEqual(state['hits'],0)
                self.assertEqual(len(fresh.rows),1)

    def test_archive_fake_facts_are_not_completion(self):
        shape = ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'archive-controlled'
            completion = bundle(output, [shape])
            rewrite(output, completion, lambda body: body['facts'][0]['bounds'][1].__setitem__(0, 9.0))
            with ob.document_bounds_context() as state:
                self.assertFalse(ob.document_accept_bounds(state, output, None))
                self.assertEqual(ob.tight_bbox(shape), ((0.,0.,0.), (4.,4.,4.)))
                self.assertEqual(state['hits'], 0)

    def test_completion_digest_mismatch_is_native_fallback(self):
        shape = ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'validated'
            completion = bundle(output, [shape])
            original_size = completion['bytes']
            rewrite(output, completion, lambda body: body['facts'][0]['bounds'][1].__setitem__(0, 9.0), resign=False)
            self.assertEqual((output/ob._DOCUMENT_BOUNDS_FILE).stat().st_size, original_size)
            with ob.document_bounds_context() as state:
                self.assertFalse(ob.document_accept_bounds(state, output, completion))
                self.assertEqual(ob.tight_bbox(shape), ((0.,0.,0.), (4.,4.,4.)))
                self.assertEqual(state['hits'], 0)

    def test_changed_shape_same_bbox_and_mutable_wrapper_miss(self):
        shape = ob.make_box(4,4,4)
        cylinder = ob.transform(ob.make_cylinder(2,4),
                                [1,0,0,2, 0,1,0,2, 0,0,1,0, 0,0,0,1])
        self.assertEqual(ob.tight_bbox(shape), ob.tight_bbox(cylinder))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'validated'
            completion = bundle(output, [shape])
            with ob.document_bounds_context() as state:
                self.assertTrue(ob.document_accept_bounds(state, output, completion))
                self.assertEqual(ob.tight_bbox(cylinder), ((0.,0.,0.), (4.,4.,4.)))
                self.assertEqual(state['hits'], 0, 'same bounds are not a shape witness')
                from OCP.gp import gp_Trsf, gp_Vec
                from OCP.TopLoc import TopLoc_Location
                move = gp_Trsf()
                move.SetTranslation(gp_Vec(10,0,0))
                shape.Move(TopLoc_Location(move))
                self.assertEqual(ob.tight_bbox(shape), ((10.,0.,0.), (14.,4.,4.)))
                self.assertEqual(state['hits'], 0)

    def test_runtime_mismatch_and_mid_context_change_fall_back(self):
        shape = ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'validated'
            completion = bundle(output, [shape])
            with ob.document_bounds_context() as state:
                with patch.object(ob, '_document_bounds_identity', return_value='different runtime/source'):
                    self.assertFalse(ob.document_accept_bounds(state, output, completion))
                self.assertTrue(ob.document_accept_bounds(state, output, completion))
                with patch.object(ob, '_document_bounds_identity', return_value='changed after admission'):
                    self.assertEqual(ob.tight_bbox(shape), ((0.,0.,0.), (4.,4.,4.)))
                self.assertEqual(state['hits'], 0)
                self.assertFalse(state['entries'])

    def test_output_member_tampering_and_no_ambient_authority(self):
        import os
        shape = ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'validated'
            completion = bundle(output, [shape])
            (output/'document.3dm').write_bytes(b'changed metadata output')
            with ob.document_bounds_context() as state:
                with patch.dict(os.environ, {'NARWHAL_DOCUMENT_ADMISSION_HINTS': str(output/ob._DOCUMENT_BOUNDS_FILE)}):
                    self.assertFalse(ob.document_accept_bounds(state, output, completion))
                    self.assertEqual(ob.tight_bbox(shape), ((0.,0.,0.), (4.,4.,4.)))
                    self.assertEqual(state['hits'], 0)

    def test_malformed_bounded_schema_is_optional(self):
        shape = ob.make_box(4,4,4)
        changes = {
            'algorithm': lambda b: b.__setitem__('algorithm', 'unknown'),
            'encoding': lambda b: b.__setitem__('encoding', b['encoding']+1),
            'wrapper': lambda b: b['facts'][0]['key'].__setitem__(0, 'InventedShape'),
            'bool enum': lambda b: b['facts'][0]['key'].__setitem__(1, True),
            'oversize witness': lambda b: b['facts'][0]['key'].__setitem__(2, ob.MAX_DOCUMENT_BOUNDS_SHAPE+1),
            'digest': lambda b: b['facts'][0]['key'].__setitem__(3, 'not a digest'),
            'bounds': lambda b: b['facts'][0].__setitem__('bounds', [[0,0,0],[True,4,4]]),
            'reversed': lambda b: b['facts'][0].__setitem__('bounds', [[9,0,0],[4,4,4]]),
            'duplicate facts': lambda b: b['facts'].append(b['facts'][0]),
            'too many facts': lambda b: b.__setitem__('facts', b['facts']*(ob.MAX_DOCUMENT_BOUNDS_FACTS+1)),
            'path traversal': lambda b: b['outputs'].__setitem__('../escape.brep', '0'*64),
        }
        for label, change in changes.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'validated'
                completion=bundle(output,[shape])
                completion=rewrite(output,completion,change)
                with ob.document_bounds_context() as state:
                    self.assertFalse(ob.document_accept_bounds(state,output,completion))
                    self.assertEqual(ob.tight_bbox(shape),((0.,0.,0.),(4.,4.,4.)))
                    self.assertEqual(state['hits'],0)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'validated'
            completion=bundle(output,[shape])
            path=output/ob._DOCUMENT_BOUNDS_FILE
            for data in (b'{bad json', b'{"algorithm":1,"algorithm":2}',
                         b' '*(ob.MAX_DOCUMENT_BOUNDS_JSON+1)):
                path.write_bytes(data)
                report=dict(completion,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
                with ob.document_bounds_context() as state:
                    self.assertFalse(ob.document_accept_bounds(state,output,report))
                    self.assertEqual(ob.tight_bbox(shape),((0.,0.,0.),(4.,4.,4.)))

    def test_fact_file_symlinks_and_nonregular_files_are_misses(self):
        shape=ob.make_box(4,4,4)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'validated'
            completion=bundle(output,[shape])
            path=output/ob._DOCUMENT_BOUNDS_FILE
            other=Path(directory)/'elsewhere.json'
            path.rename(other)
            path.symlink_to(other)
            with ob.document_bounds_context() as state:
                self.assertFalse(ob.document_accept_bounds(state,output,completion))
                self.assertEqual(ob.tight_bbox(shape),((0.,0.,0.),(4.,4.,4.)))
            path.unlink()
            path.mkdir()
            with ob.document_bounds_context() as state:
                self.assertFalse(ob.document_accept_bounds(state,output,completion))
                self.assertEqual(ob.tight_bbox(shape),((0.,0.,0.),(4.,4.,4.)))

    def test_producer_count_limit_and_missing_old_worker_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'validated'
            with patch.object(ob,'MAX_DOCUMENT_BOUNDS_FACTS',1):
                completion=bundle(output,[ob.make_box(2,3,4),ob.make_box(3,3,4)])
                self.assertEqual(len(json.loads((output/ob._DOCUMENT_BOUNDS_FILE).read_bytes())['facts']),1)
                with ob.document_bounds_context() as state:
                    self.assertTrue(ob.document_accept_bounds(state,output,completion))
                    self.assertEqual(len(state['entries']),1)
            with ob.document_bounds_context() as state:
                self.assertFalse(ob.document_accept_bounds(state,output,None))
                self.assertFalse(state['entries'])

    def test_failed_worker_never_activates_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            completion=bundle(root/'validated',[ob.make_box(4,4,4)])
            result=SimpleNamespace(returncode=1,timed_out=False,error=None,stderr='injected failed worker',
                stdout='NARWHAL-DOCUMENT-WORKER '+json.dumps({'count':1,'bounds_facts':completion})+'\n')
            with ob.document_bounds_context() as state:
                with patch.object(doc.converter_process,'run',return_value=result):
                    with self.assertRaises(doc.NarwhalDocumentError):
                        doc._validate_in_worker(root,1,bounds_scope=state)
                self.assertFalse(state['entries'])
                self.assertEqual(state['hits'],0)

    def test_worker_emits_nothing_before_all_proofs_complete(self):
        import resource
        model=NarwhalModel()
        model.add_object(exact_box(4),'first',0)
        model.add_object(exact_box(5),'second',0)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'valid.narwhal'
            self.assertTrue(doc.save_model(model,target))
            payload=Path(directory)/'payload'
            native_document.unpack(target,payload)
            self.assertFalse(any(p.name==ob._DOCUMENT_BOUNDS_FILE for p in payload.rglob('*')))
            original=worker._corresponds
            calls=[]
            def fail_last(*args):
                calls.append(args)
                if len(calls)==4:
                    raise RuntimeError('last proof failed')
                return original(*args)
            with patch.object(sys,'argv',['worker','--',str(payload)]), \
                    patch.object(resource,'setrlimit'), \
                    patch.object(worker.document_admission,'read_hints',return_value=()), \
                    patch.object(worker,'_corresponds',side_effect=fail_last), \
                    patch.object(ob,'document_publish_bounds',wraps=ob.document_publish_bounds) as publish, \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    worker.main()
                self.assertEqual(caught.exception.code,1)
                publish.assert_not_called()
            self.assertTrue(list((payload/'validated').glob('*.brep')))
            self.assertFalse((payload/'validated'/ob._DOCUMENT_BOUNDS_FILE).exists())

    def test_real_open_uses_fresh_facts_ignores_archive_claims_and_clears_scope(self):
        model=NarwhalModel()
        model.add_object(exact_box(),'exact',0)
        scopes=[]
        original=ob.document_bounds_context
        @contextmanager
        def observed():
            with original() as state:
                scopes.append(state)
                yield state
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            original_archive=root/'original.narwhal'
            self.assertTrue(doc.save_model(model,original_archive))
            payload=root/'payload'
            native_document.unpack(original_archive,payload)
            manifest=json.loads((payload/'manifest.json').read_text())
            manifest['bounds_facts']={'bounds': [[999]*3,[1000]*3], 'identity': 'archive claim'}
            (payload/'manifest.json').write_text(json.dumps(manifest))
            archive=root/'forged-optional-metadata.narwhal'
            native_document.pack(payload,archive)
            before=hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(ob,'document_bounds_context',observed):
                loaded=doc.load_model(archive)
            self.assertEqual(loaded.rehydrated,(1,0))
            self.assertGreater(scopes[-1]['hits'],0)
            self.assertFalse(scopes[-1]['entries'])
            self.assertIsNone(ob._document_bounds_active.get())
            self.assertEqual(ob.tight_bbox(loaded._native_master_bindings[0][1]),((0.,0.,0.),(4.,4.,4.)))
            self.assertEqual(before,hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_real_open_falls_back_when_completed_worker_facts_are_missing_or_mismatched(self):
        model=NarwhalModel()
        model.add_object(exact_box(),'exact',0)
        original_context=ob.document_bounds_context
        original_run=doc.converter_process.run
        scopes=[]
        @contextmanager
        def observed():
            with original_context() as state:
                scopes.append(state)
                yield state
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'valid.narwhal'
            self.assertTrue(doc.save_model(model,archive))
            for mode in ('old-worker-without-facts','completion-digest-mismatch'):
                def completed(*args,**kwargs):
                    result=original_run(*args,**kwargs)
                    if 'NARWHAL-DOCUMENT-WORKER ' not in result.stdout:
                        return result
                    lines=result.stdout.splitlines()
                    for index,line in enumerate(lines):
                        if not line.startswith('NARWHAL-DOCUMENT-WORKER '):
                            continue
                        report=json.loads(line.split(' ',1)[1])
                        self.assertEqual(report.get('reused'),0)
                        if mode=='old-worker-without-facts':
                            report.pop('bounds_facts',None)
                        else:
                            report['bounds_facts']['sha256']='0'*64
                        lines[index]='NARWHAL-DOCUMENT-WORKER '+json.dumps(report)
                    return SimpleNamespace(returncode=result.returncode,timed_out=result.timed_out,
                        error=result.error,stderr=result.stderr,stdout='\n'.join(lines)+'\n')
                with self.subTest(mode=mode), patch.object(ob,'document_bounds_context',observed), \
                        patch.object(doc.converter_process,'run',side_effect=completed):
                    candidate=doc.load_model(archive)
                    self.assertEqual(candidate.rehydrated,(1,0))
                    self.assertEqual(scopes[-1]['hits'],0)
                    self.assertFalse(scopes[-1]['entries'])
                    self.assertEqual(ob.tight_bbox(candidate._native_master_bindings[0][1]),
                                     ((0.,0.,0.),(4.,4.,4.)))

    @unittest.skipUnless('--archive' in sys.argv,'supply original archive for full Open timing')
    def test_original_archive_timing_and_hashes(self):
        archive=Path(sys.argv[sys.argv.index('--archive')+1])
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        scopes=[]
        original=ob.document_bounds_context
        @contextmanager
        def observed():
            with original() as state:
                scopes.append(state)
                yield state
        with tempfile.TemporaryDirectory() as directory:
            before=Path(directory)/'before'
            native_document.unpack(archive,before)
            masters={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (before/'masters').glob('*.brep')}
            start=time.perf_counter()
            with patch.object(ob,'document_bounds_context',observed):
                candidate=doc.load_model(archive)
            elapsed=time.perf_counter()-start
            self.assertEqual(len(candidate._native_master_bindings),9)
            self.assertGreaterEqual(scopes[-1]['hits'],4)
            self.assertFalse(scopes[-1]['entries'])
            after=Path(directory)/'after'
            native_document.unpack(archive,after)
            self.assertEqual(masters,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (after/'masters').glob('*.brep')})
        self.assertEqual(digest,hashlib.sha256(archive.read_bytes()).hexdigest())
        print('PUBLIC FULL OPEN',elapsed,'masters',9,'bounds_hits',scopes[-1]['hits'],
              'bounds_misses',scopes[-1]['misses'],'master_hashes_unchanged',len(masters),'archive_sha256',digest,flush=True)


def run_tests(names=None):
    suite=(unittest.TestSuite(WorkerBoundsTests(name) for name in names) if names else
           unittest.defaultTestLoader.loadTestsFromTestCase(WorkerBoundsTests))
    return unittest.TextTestRunner(verbosity=2).run(suite)


def mutations():
    cases=[
        (ob,'document_accept_bounds','archive fake facts',
         '    import hashlib\n',
         '    import hashlib\n    if completion is None:\n        data = (output / _DOCUMENT_BOUNDS_FILE).read_bytes()\n        completion = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "identity": _document_bounds_identity()}\n',
         'test_archive_fake_facts_are_not_completion'),
        (ob,'document_accept_bounds','completion mismatch',
         'or hashlib.sha256(data).hexdigest() != completion["sha256"]','or False',
         'test_completion_digest_mismatch_is_native_fallback'),
        (ob,'_document_bounds_key','changed shape same bounds',
         'return (wrapper, enum, len(data), hashlib.sha256(data).hexdigest())',
         'return (wrapper, enum, 1, "0"*64)',
         'test_changed_shape_same_bbox_and_mutable_wrapper_miss'),
        (ob,'document_accept_bounds','runtime mismatch',
         'or body["identity"] != identity or completion["identity"] != identity','or False',
         'test_runtime_mismatch_and_mid_context_change_fall_back'),
        (doc,'_validate_in_worker','failed worker',
         'if result.returncode != 0 or result.timed_out or result.error or marker is None:',
         'if False:', 'test_failed_worker_never_activates_facts'),
        (ob,'_document_bounds_lookup','cache off',
         'state = _document_bounds_active.get()','state = None',
         'test_fresh_witness_hit_independent_returns_and_lifetime'),
        (ob,'document_bounds_context','facts survive invocation',
         'state["entries"].clear()', 'pass',
         'test_nested_context_and_exception_cleanup'),
        (ob,'document_measure_bounds','worker facts must be fresh',
         'token = _document_bounds_active.set(None)',
         'token = _document_bounds_active.set(_document_bounds_active.get())',
         'test_worker_measurements_never_consume_parent_cache'),
        (ob,'_document_bounds_lookup','runtime changed after admission',
         'if state["identity"] != _document_bounds_identity():','if False:',
         'test_runtime_mismatch_and_mid_context_change_fall_back'),
    ]
    for module,name,label,old,new,test in cases:
        original=getattr(module,name)
        source=inspect.getsource(original)
        assert old in source,(name,old)
        exec(compile(source.replace(old,new,1),'<bounds mutation>','exec'),module.__dict__)
        try:
            assert not run_tests([test]).wasSuccessful(),'SURVIVED: '+label
            print('MUTATION RED:',label,flush=True)
        finally:
            setattr(module,name,original)
    assert run_tests().wasSuccessful()
    print('RESTORED GREEN',flush=True)


if __name__=='__main__':
    if '--mutations' in sys.argv:
        mutations()
    elif not run_tests().wasSuccessful():
        raise SystemExit(1)
