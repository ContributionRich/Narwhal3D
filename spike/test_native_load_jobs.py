"""ctypes admission tests for Rust-owned load jobs; no Blender or geometry needed."""
import ctypes as c
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from narwhal3d.native_document import LoadJobs,SaveJobs,DocumentCoreError

GUARD=dict(model=1,context=2,serial=3,state=4,generation=None,path=None)


class NativeLoadTests(unittest.TestCase):
    def setUp(self):
        self.queue=LoadJobs()
        self.addCleanup(lambda:self.queue.request('close'))

    def test_host_policy_is_native_and_source_guards_refuse_commit(self):
        job=self.queue.request('request',guard=GUARD,strict=True)['job']
        self.assertEqual(self.queue.request('tick',guard=GUARD,paused=False)['action'],'start')
        self.queue.request('complete',job=job,success=True)
        guard=dict(GUARD,state=99)
        self.assertEqual(self.queue.request('tick',guard=guard,paused=False)['action'],'cancel')

    def test_success_requires_hydration_and_a_final_native_guard_check(self):
        job=self.queue.request('request',guard=GUARD,strict=True)['job']
        self.queue.request('tick',guard=GUARD,paused=False)
        self.queue.request('complete',job=job,success=True)
        with self.assertRaises(DocumentCoreError):self.queue.request('hydrated',job=job,success=True)
        self.assertEqual(self.queue.request('tick',guard=GUARD,paused=False)['action'],'hydrate')
        self.queue.request('hydrated',job=job,success=True)
        self.assertEqual(self.queue.request('tick',guard=GUARD,paused=False)['action'],'commit')

    def test_undersized_output_never_executes_create(self):
        payload=b'{"op":"create"}'
        source=(c.c_ubyte*len(payload)).from_buffer_copy(payload)
        output=(c.c_ubyte*1)()
        required=self.queue.call(source,len(payload),output,len(output))
        self.assertGreater(required,1)
        other=LoadJobs()
        try:self.assertEqual(other.queue,self.queue.queue+1)
        finally:other.request('close')

    def test_invalid_requests_fail_without_changing_pending_job(self):
        job=self.queue.request('request',guard=GUARD,strict=True)['job']
        with self.assertRaises(DocumentCoreError):
            self.queue.request('request',guard=dict(GUARD,path='x'*8193),strict=True)
        with self.assertRaises(DocumentCoreError):
            self.queue.request('complete',job=job,success=True)
        self.assertEqual(self.queue.request('tick',guard=GUARD,paused=False)['job'],job)
        self.queue.request('complete',job=job,success=False)
        self.assertEqual(self.queue.request('tick',guard=GUARD,paused=False)['action'],'error')

    def test_malformed_native_values_are_not_accepted_or_replayed(self):
        import json
        reply=dict(status='ok',action='idle',queue=self.queue.queue,job=None,replaced=None)
        for changes in ({'job':True},{'queue':0},{'action':'invented'}):
            data=json.dumps(dict(reply,**changes)).encode()
            calls=[]
            def bad_call(source,length,output,capacity):
                calls.append(1);c.memmove(output,data,len(data));return len(data)
            with patch.object(self.queue,'call',side_effect=bad_call):
                with self.assertRaises(DocumentCoreError):self.queue.request('info')
            self.assertEqual(calls,[1])

    def test_package_import_keeps_worker_free_of_blender_ui(self):
        import subprocess
        code="import sys;sys.path.insert(0,"+repr(str(ROOT))+");import narwhal3d;assert 'narwhal3d.ui' not in sys.modules;assert 'bpy' not in sys.modules"
        subprocess.run([sys.executable,'-P','-c',code],check=True,timeout=15)

    def test_missing_exports_fail_explicitly(self):
        with patch('narwhal3d.native_document_session.load_library',return_value=SimpleNamespace()):
            with self.assertRaisesRegex(DocumentCoreError,'matching application-core'):
                LoadJobs()


class NativeSaveTests(unittest.TestCase):
    def test_running_save_is_retained_and_only_pending_save_is_replaced(self):
        queue=SaveJobs()
        try:
            first=queue.request('enqueue',autosave=False)
            second=queue.request('enqueue',autosave=False)
            newest=queue.request('enqueue',autosave=False)
            self.assertEqual(first['action'],'start')
            self.assertEqual(newest['replaced'],second['job'])
            self.assertEqual(queue.request('info')['job'],first['job'])
            self.assertEqual(queue.request('intended')['job'],newest['job'])
            with self.assertRaises(DocumentCoreError):queue.request('close')
            queue.request('finish',job=first['job'])
            self.assertEqual(queue.request('next')['job'],newest['job'])
            queue.request('finish',job=newest['job'])
        finally:queue.request('close')

    def test_busy_autosave_cannot_replace_a_user_save(self):
        queue=SaveJobs()
        try:
            first=queue.request('enqueue',autosave=False)
            self.assertEqual(queue.request('enqueue',autosave=True)['action'],'declined')
            self.assertEqual(queue.request('info')['job'],first['job'])
            queue.request('finish',job=first['job'])
            self.assertEqual(queue.request('info')['action'],'idle')
        finally:queue.request('close')


if __name__=='__main__':unittest.main()
