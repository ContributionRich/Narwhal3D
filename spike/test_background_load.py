"""Real native load queue, file worker and main-thread candidate publication.

Run in the bundled Blender runtime. Tests never open an owner document.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from narwhal3d import bridge, file_commands as files, narwhal_document as doc
from narwhal3d.model import NarwhalModel
from narwhal3d.state import app_state as app


class BackgroundLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='native-load-tests-')
        cls.source=Path(cls.directory.name)/'exact.narwhal'
        model=NarwhalModel()
        solid=bridge.boolean_brep(bridge.make_box((0,0,0),(10,10,0),10),
                                  bridge.make_cylinder((10,5,0),3,10),'UNION')
        model.add_object(solid,'Exact boolean master')
        assert doc.save_model(model,cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        app.set_model(NarwhalModel())
        app.doc_path=None
        app.mark_saved()
        app.active_command=None
        self.callbacks=[]
        self.host=files._LoadHost(self.callbacks.append,lambda:None)
        self.host_patch=patch.object(files,'_load_host',self.host)
        self.host_patch.start()
        self.draw_patch=patch.object(files,'_rebuild_display',return_value=1)
        self.draw_patch.start()
        self.original=app.model

    def tearDown(self):
        self.host.close()
        deadline=time.monotonic()+15
        while self.host.payloads and time.monotonic()<deadline:
            time.sleep(.01)
        self.draw_patch.stop()
        self.host_patch.stop()
        self.assertFalse(self.host.payloads,'cancelled workers must release private files')

    def pump(self,until,timeout=20):
        ticks=0
        deadline=time.monotonic()+timeout
        while not until():
            self.assertLess(time.monotonic(),deadline,'load did not settle')
            for callback in list(self.callbacks):
                if callback() is None:
                    self.callbacks.remove(callback)
            ticks+=1
            time.sleep(.01)
        return ticks

    def start(self):
        message,done=files.OpenCmd()._open(str(self.source))
        self.assertTrue(done)
        self.assertIsInstance(message,str)
        self.assertIn('Loading',message)
        self.assertIs(app.model,self.original,'Open must not replace the document on request')

    def test_open_pumps_ui_until_exact_candidate_is_ready(self):
        guard=files._load_guard()
        self.assertEqual(guard['model'],app.model._object_registry.native.handle)
        self.assertEqual(guard['context'],app.ctx.undo.native_identity)
        self.start()
        ticks=self.pump(lambda:not self.host.polling)
        self.assertGreater(ticks,10)
        self.assertIsNot(app.model,self.original)
        self.assertEqual(app.model.object_count(),1)
        self.assertEqual(app.doc_path,str(self.source))
        self.assertFalse(app.doc_modified())
        self.assertEqual(app.ctx.undo.undo_names(),[])

    def test_edits_during_load_prevent_replacement(self):
        self.start()
        self.host.poll()  # native Start -> one background worker
        app.ctx.begin_undo('Keep this edit')
        app.ctx.note_change()
        app.model.add_object(bridge.make_circle((0,0,0),3),'Keep this edit')
        app.ctx.end_undo()
        self.pump(lambda:not self.host.polling)
        self.assertIs(app.model,self.original)
        self.assertEqual(app.model.object_count(),1)
        self.assertEqual(app.ctx.undo.undo_names(),['Keep this edit'])
        self.assertIsNone(app.doc_path)

    def test_new_document_during_load_is_never_overwritten(self):
        self.start();self.host.poll()
        replacement=NarwhalModel()
        app.set_model(replacement);app.mark_saved()
        self.pump(lambda:not self.host.polling)
        self.assertIs(app.model,replacement)
        self.assertEqual(app.model.object_count(),0)

    def test_newest_pending_request_wins_with_only_two_host_payloads(self):
        applied=[]
        files.background_load(self.source,lambda model,path:applied.append('first'))
        self.host.poll()
        files.background_load(self.source,lambda model,path:applied.append('second'))
        files.background_load(self.source,lambda model,path:applied.append('third'))
        self.assertEqual(len(self.host.payloads),2)
        self.pump(lambda:not self.host.polling)
        self.assertEqual(applied,['third'])
        self.assertIs(app.model,self.original)

    def test_invalid_file_never_changes_model_or_history(self):
        invalid=Path(self.directory.name)/'invalid.narwhal'
        invalid.write_bytes(b'not a native container')
        files.background_load(invalid,lambda model,path:self.fail('invalid file published'))
        self.pump(lambda:not self.host.polling)
        self.assertIs(app.model,self.original)
        self.assertEqual(app.ctx.undo.undo_names(),[])
        self.assertTrue(any('Could not load' in line for line in app.history))

    def test_hydration_rejects_worker_thread(self):
        prepared=doc.prepare_load(self.source)
        try:
            errors=[]
            worker=threading.Thread(target=lambda:self._hydrate_in_worker(prepared,errors))
            worker.start();worker.join(3)
            self.assertEqual(len(errors),1)
            self.assertIn('main thread',str(errors[0]))
        finally:
            prepared.cleanup()

    def test_edits_during_incremental_hydration_cancel_and_restore_context(self):
        from narwhal3d.kernel import occt_backend
        self.start()
        self.pump(lambda:any(item['hydration'] is not None and item['candidate'] is None
                             for item in self.host.payloads.values()))
        hydration=next(item['hydration'] for item in self.host.payloads.values())
        self.assertIsNone(occt_backend._document_bounds_active.get(), 'no load-local bounds state may leak between UI frames')
        app.ctx.begin_undo('Edit during admission');app.ctx.note_change()
        app.model.add_object(bridge.make_circle((0,0,0),3),'Keep edit')
        app.ctx.end_undo()
        self.pump(lambda:not self.host.polling)
        self.assertIs(app.model,self.original)
        self.assertEqual(app.model.object_count(),1)
        self.assertTrue(hydration.finished)
        self.assertIsNone(occt_backend._document_bounds_active.get())

    def test_source_changed_after_hydration_is_never_committed(self):
        path=Path(self.directory.name)/'changed-after-hydration.narwhal'
        path.write_bytes(self.source.read_bytes())
        files.background_load(path,lambda model,path:self.fail('changed source published'))
        self.pump(lambda:any(item['candidate'] is not None for item in self.host.payloads.values()))
        with path.open('ab') as stream:stream.write(b'changed after admission')
        self.pump(lambda:not self.host.polling)
        self.assertIs(app.model,self.original)
        self.assertTrue(any('source file changed' in line for line in app.history))

    def test_source_changed_before_publication_is_refused(self):
        path=Path(self.directory.name)/'changed.narwhal'
        path.write_bytes(self.source.read_bytes())
        prepared=doc.prepare_load(path)
        try:
            with path.open('ab') as stream:stream.write(b'changed')
            with self.assertRaisesRegex(doc.NarwhalDocumentError,'source file changed'):
                doc.finish_load(prepared)
            self.assertIs(app.model,self.original)
        finally:prepared.cleanup()

    @staticmethod
    def _hydrate_in_worker(prepared,errors):
        try:doc.finish_load(prepared)
        except Exception as exc:errors.append(exc)


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    result=unittest.main(argv=[__file__,*args],exit=False,verbosity=2)
    if not result.result.wasSuccessful():raise SystemExit(1)
