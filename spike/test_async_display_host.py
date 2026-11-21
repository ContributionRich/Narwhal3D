"""Actual OCP/Blender glue qualification; Rust supervises all worker lifetimes.
Run with the rebuilt NARWHAL_CONVERTER_SUPERVISOR and --python-exit-code 1.
Python assertions are required here for live SDK masters, bmeshes and Undo state.
"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'spike'))
import time
import os
import unittest
from unittest.mock import patch
import test_ai_modeling_host as host
from narwhal3d import ai_modeling as ai, bridge, kernel, display_prepare as dp


def fixture(radius=500):
    original = ai._construct(dict(op='sphere', center=[0,0,0], radius=radius), None)
    ai.inspect_geometry(original, .001)
    geom = ai._construct(host.transform('base', scale=(1.35,.62,.5)), lambda _: original)
    ai.inspect_geometry(geom, .001)
    return geom, kernel.master_of(geom)


def move_live_master(master):
    # Actual-host fault injection: OCP shapes are mutable even with stable identity.
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Trsf, gp_Vec
    original = master.Location()
    translation = gp_Trsf()
    translation.SetTranslation(gp_Vec(100, 0, 0))
    master.Location(TopLoc_Location(translation))
    return original


def wait(job):
    polls, slowest = 0, 0
    while True:
        start = time.monotonic()
        mesh = job.poll()
        slowest = max(slowest, time.monotonic()-start)
        polls += 1
        if mesh is not None:
            return mesh, polls, slowest
        time.sleep(.01)


class WorkerHost(unittest.TestCase):
    def test_standalone_probe_bridge_keeps_its_display_entry_point(self):
        import importlib.util
        with patch.object(sys, 'path', [str(ROOT/'narwhal3d'), *sys.path]):
            spec = importlib.util.spec_from_file_location('display_probe_bridge', ROOT/'narwhal3d/bridge.py')
            standalone = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(standalone)
            ob = standalone._occt()
            geom = standalone.occt_backed(ob.make_sphere(5), dict(kind='sphere', radius=5))
            bm = standalone.geometry_to_bmesh(geom)
            self.assertGreater(len(bm.faces), 0)
            bm.free()

    def test_real_producer_replay_exact_master_smooth_display_and_reuse(self):
        geom, master = fixture()
        ob = bridge._occt()
        encoded = geom.Encode()
        exact = ob.shape_to_bytes(master, triangles=False)
        recipe = kernel.recipe_of(geom)
        volume = ob.volume(master)
        job = dp.DisplayJob(master)
        try:
            mesh, polls, slowest = wait(job)
            self.assertGreater(polls, 20, 'host must keep getting execution while OCCT meshes')
            self.assertLess(slowest, .5, 'poll must not enter native meshing')
            self.assertGreater(len(mesh.triangles)//3, 100000, 'do not substitute a coarse proxy')
            self.assertEqual(exact, ob.shape_to_bytes(master, triangles=False))
            self.assertEqual(encoded, geom.Encode())
            self.assertEqual(recipe, kernel.recipe_of(geom))
            self.assertEqual({'BSPLINE':1}, ob.face_types(master))
            self.assertTrue(ob.is_airtight(master))
            self.assertEqual(volume, ob.volume(master))
            # Sample the recorded ellipsoid's display nodes against its exact equation.
            v = mesh.vertices
            for i in range(0, len(v)//3, max(1,len(v)//3//128)):
                x,y,z = v[i*3:i*3+3]
                self.assertAlmostEqual((x/675)**2+(y/310)**2+(z/250)**2, 1, places=6)
            with patch.object(bridge, 'display_tessellation', side_effect=AssertionError('synchronous remesh')):
                for _ in range(2):
                    bm = bridge.geometry_to_bmesh(geom, prepared_display=mesh)
                    self.assertTrue(all(f.smooth for f in bm.faces))
                    bm.free()
            print('ASYNC_REPLAY', polls, 'polls; max poll', slowest, 'bytes', len(mesh.data), flush=True)
        finally:
            job.close()

    def test_real_mesher_timeout_and_memory_limit_fail_closed(self):
        geom, master = fixture()
        exact = bridge._occt().shape_to_bytes(master, triangles=False)
        for options, expected in ((dict(timeout=2), 'display_timeout'),
                                   (dict(memory_mib=32), 'display_worker_failed')):
            job = dp.DisplayJob(master, **options)
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(dp.DisplayPreparationError, expected):
                    wait(job)
                self.assertLess(time.monotonic()-started, 5)
                self.assertIsNotNone(job.process.poll())
                self.assertEqual(exact, bridge._occt().shape_to_bytes(master, triangles=False))
            finally:
                job.close()

    def test_cancel_during_real_meshing_reaps_worker_and_preserves_master(self):
        geom, master = fixture()
        exact = bridge._occt().shape_to_bytes(master, triangles=False)
        job = dp.DisplayJob(master)
        folder = Path(job.folder.name)
        try:
            deadline = time.monotonic()+5
            while not (folder/'meshing').exists():
                self.assertIsNone(job.poll())
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.01)
            worker_pid = int((folder/'meshing').read_text())
            time.sleep(.2)
            self.assertIsNone(job.process.poll())
            started = time.monotonic()
            job.close()
            self.assertLess(time.monotonic()-started, 1)
            self.assertIsNotNone(job.process.poll())
            self.assertFalse(folder.exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(worker_pid, 0)
            self.assertEqual(exact, bridge._occt().shape_to_bytes(master, triangles=False))
        finally:
            job.close()

    def test_same_identity_master_mutated_during_job_is_refused(self):
        geom, master = fixture(5)
        ob = bridge._occt()
        exact = ob.shape_to_bytes(master, triangles=False)
        encoded = geom.Encode()
        job = dp.DisplayJob(master)
        location = master.Location()
        try:
            self.assertIsNone(job.process.poll())
            move_live_master(master)
            mutated = ob.shape_to_bytes(master, triangles=False)
            self.assertNotEqual(exact, mutated, "the input must expose the stale mesh")
            self.assertIs(job.master, master)
            with self.assertRaisesRegex(dp.DisplayPreparationError, "display_master_changed"):
                wait(job)
            self.assertEqual(mutated, ob.shape_to_bytes(master, triangles=False))
            self.assertEqual(encoded, geom.Encode())
        finally:
            master.Location(location)
            job.close()

    def test_same_identity_master_mutated_after_collection_is_refused_on_every_use(self):
        geom, master = fixture(5)
        ob = bridge._occt()
        job = dp.DisplayJob(master)
        location = master.Location()
        try:
            mesh, _, _ = wait(job)
            self.assertLess(max(mesh.vertices[0::3]), 100)
            bm = bridge.geometry_to_bmesh(geom, prepared_display=mesh)
            bm.free()
            move_live_master(master)
            mutated = ob.shape_to_bytes(master, triangles=False)
            self.assertIs(mesh.master, master)
            with patch.object(bridge, "_tessellation_bmesh", side_effect=AssertionError("stale mesh used")), patch.object(bridge, "brep_to_bmesh", side_effect=AssertionError("fallback")):
                for _ in range(2):
                    with self.assertRaisesRegex(dp.DisplayPreparationError, "display_master_changed"):
                        bridge.geometry_to_bmesh(geom, prepared_display=mesh)
            self.assertEqual(mutated, ob.shape_to_bytes(master, triangles=False))
        finally:
            master.Location(location)
            job.close()

    def test_prepared_binding_errors_never_fall_back(self):
        geom, master = fixture(5)
        job = dp.DisplayJob(master)
        try:
            mesh, _, _ = wait(job)
            other, _ = fixture(6)
            with patch.object(bridge, 'brep_to_bmesh', side_effect=AssertionError('fallback')):
                with self.assertRaisesRegex(dp.DisplayPreparationError, 'display_master_changed'):
                    bridge.geometry_to_bmesh(other, prepared_display=mesh)
            with patch.object(bridge, '_master_bmesh', side_effect=dp.DisplayPreparationError('display_timeout')):
                with self.assertRaisesRegex(dp.DisplayPreparationError, 'display_timeout'):
                    bridge.geometry_to_bmesh(geom)
        finally:
            job.close()


class PublicationHost(unittest.TestCase):
    setUp = host.ModelingHost.setUp
    stage = host.ModelingHost.stage

    def start(self, radius=500, historical_tag=False):
        self.w.modeling_exchange('modeling_mode',host.DOC,mode='build')
        finish = self.w.finish
        def finish_with_historical_tag(*args, **kwargs):
            outcome = finish(*args, **kwargs)
            if historical_tag:
                # Install the persisted prior-session collision before admission
                # captures the real document fingerprint. Native conversation IDs
                # are process-global, so never assume this test owns conversation 1.
                import rhino3dm as r3
                previous = r3.Point(r3.Point3d(20,30,40))
                previous.SetUserString('NarwhalAI.Batch', str(outcome['token']['conversation'])+':1')
                attrs = r3.ObjectAttributes()
                attrs.Name = 'Historical ticket collision'
                self.m.file.Objects.Add(previous, attrs)
            return outcome
        with patch.object(self.w, 'finish', side_effect=finish_with_historical_tag):
            _, descriptor = self.stage([
                host.step('base',dict(op='sphere',center=[0,0,0],radius=radius),False),
                host.step('body',host.transform('base',scale=(1.35,.62,.5)))])
        if historical_tag:
            expected_tag = str(descriptor['ticket']['owner']['conversation'])+':'+str(descriptor['ticket']['serial'])
            self.assertEqual(expected_tag, next(iter(self.m.file.Objects)).Geometry.GetUserString('NarwhalAI.Batch'))
        self.before = self.m.file.Encode()
        self.history = self.ctx.undo.undo_names()
        job = ai.publish_steps(self.app,self.w,descriptor,host.DOC,lambda:None)
        self.addCleanup(job.close)
        return job

    def waiting(self, job):
        deadline=time.monotonic()+5
        while True:
            row=next(job)
            self.assertEqual(self.ctx.undo.current_serial,0)
            self.assertEqual(self.before,self.m.file.Encode())
            if row['phase']=='display_preparing':return
            self.assertLess(time.monotonic(),deadline)

    def test_success_uses_prepared_display_for_add_and_rebuild_without_native_remesh(self):
        job=self.start(radius=5)
        with patch.object(bridge,'_master_bmesh',side_effect=dp.DisplayPreparationError('inline_mesh_forbidden')):
            while True:
                try:next(job)
                except StopIteration as done:
                    result=done.value;break
                time.sleep(.01)
        self.assertFalse(result['is_error'],result)
        self.assertEqual(1,self.m.object_count())
        self.assertEqual(0,self.ctx.undo.current_serial)
        self.assertEqual(1,len(self.ctx.undo.undo_names()))
        self.assertTrue(result['output']['objects'][0]['airtight'])

    def test_generator_close_cancels_before_any_document_or_undo_mutation(self):
        job=self.start()
        self.waiting(job)
        started=time.monotonic()
        job.close()
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(self.before,self.m.file.Encode())
        self.assertEqual(self.history,self.ctx.undo.undo_names())
        self.assertEqual(0,self.ctx.undo.current_serial)
        self.assertEqual(0,self.m.object_count())

    def test_timeout_is_reported_before_document_or_undo_mutation(self):
        with patch.object(dp,'TIMEOUT',2):
            job=self.start(historical_tag=True)
            self.waiting(job)
            while True:
                try:next(job)
                except StopIteration as done:
                    result=done.value;break
                time.sleep(.01)
        self.assertTrue(result['is_error'],result)
        self.assertEqual('modeling_display_preparation_failed',result['output']['code'])
        self.assertEqual('display_timeout',result['output']['reason'])
        self.assertIs(result['output']['applied'],False)
        self.assertEqual(self.before,self.m.file.Encode())
        self.assertEqual(self.history,self.ctx.undo.undo_names())
        self.assertEqual(0,self.ctx.undo.current_serial)

    def check_mutated_display_publication(self, after_collection):
        jobs, locations = [], []
        initialize, poll = dp.DisplayJob.__init__, dp.DisplayJob.poll
        def observe(job, master, **kwargs):
            initialize(job, master, **kwargs)
            jobs.append(job)
        def mutate_collected(job):
            mesh = poll(job)
            if mesh is not None and not locations:
                locations.append((job.master, move_live_master(job.master)))
            return mesh
        try:
            with patch.object(dp.DisplayJob, "__init__", observe), patch.object(dp.DisplayJob, "poll", mutate_collected if after_collection else poll), patch.object(bridge, "_tessellation_bmesh", side_effect=AssertionError("stale mesh used")), patch.object(bridge, "brep_to_bmesh", side_effect=AssertionError("fallback")), patch.object(self.ctx.undo, "begin", wraps=self.ctx.undo.begin) as begin:
                publication = self.start(radius=5)
                self.waiting(publication)
                if not after_collection:
                    locations.append((jobs[-1].master, move_live_master(jobs[-1].master)))
                while True:
                    try:next(publication)
                    except StopIteration as done:
                        result = done.value
                        break
                    time.sleep(.01)
                self.assertTrue(result["is_error"], result)
                self.assertEqual("display_master_changed", result["output"]["reason"])
                self.assertIs(result["output"]["applied"], False)
                self.assertEqual(self.before, self.m.file.Encode())
                self.assertEqual(self.history, self.ctx.undo.undo_names())
                self.assertEqual(0, self.ctx.undo.current_serial)
                self.assertEqual(0, self.m.object_count())
                if not after_collection:
                    self.assertEqual(0, begin.call_count, "changed job input must be refused before Undo begins")
        finally:
            for master, location in locations:master.Location(location)

    def test_mutation_during_display_job_leaves_document_and_undo_unchanged(self):
        self.check_mutated_display_publication(after_collection=False)

    def test_mutation_after_display_collection_leaves_document_and_undo_unchanged(self):
        self.check_mutated_display_publication(after_collection=True)

    def test_stale_context_while_meshing_cancels_before_publication(self):
        job=self.start()
        self.waiting(job)
        with patch.object(ai,'context',return_value={'stale':True}):
            with self.assertRaises(StopIteration) as done:next(job)
        result=done.exception.value
        self.assertTrue(result['is_error'],result)
        self.assertIs(result['output']['applied'],False)
        self.assertEqual(self.before,self.m.file.Encode())
        self.assertEqual(self.history,self.ctx.undo.undo_names())
        self.assertEqual(0,self.ctx.undo.current_serial)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():raise SystemExit(1)
