#!/usr/bin/env python3
"""Actual Blender/OCCT boundary tests. Pure policy/dependency tests live in Rust.
Run in an owned disposable Blender process with --python-exit-code 1.
"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spike'))
import test_ai_workflow as base
from test_ai_primitives import ui_fixture
from unittest.mock import patch
from types import SimpleNamespace
import unittest
import tempfile
import bpy
from narwhal3d import model, commands, ai_modeling, ai_workspace, kernel
DOC = 'modeling-owned-document'
LIBRARY = ROOT / 'native/automation_core/target/debug/libnarwhal_automation.so'


def step(id, action, publish=True, replace=None):
    return dict(id=id, name=id, action=action, publish=publish, replace=replace)


def transform(source, translation=(0, 0, 0), scale=(1, 1, 1)):
    return dict(op='transform', source=source, translation=translation, scale=scale,
                center=[0, 0, 0], rotation_degrees=0, axis=[0, 0, 1])


class ModelingHost(unittest.TestCase):
    def setUp(self):
        bpy.data.batch_remove(list(bpy.data.objects))
        self.m = model.NarwhalModel()
        self.ctx = commands.NarwhalContext(self.m)
        self.app = SimpleNamespace(model=self.m, ctx=self.ctx, active_command=None)
        self.ui = ui_fixture(self.app)
        self.ui.__enter__()
        self.addCleanup(self.ui.__exit__, None, None, None)
        self.ready = patch.object(ai_workspace.WorkspaceSnapshots, 'ready', lambda _: True)
        self.ready.start(); self.addCleanup(self.ready.stop)
        loader = base.ai._load_workflow_library
        self.loader = patch.object(base.ai, '_load_workflow_library', lambda path=None: loader(LIBRARY))
        self.loader.start(); self.addCleanup(self.loader.stop)
        self.state = base.ai.AssistantState(provider=base.ai.set_provider('openai_compatible', 'deterministic', credential_env='TEST_KEY'))
        self.w = base.ai.WorkflowSession(self.state, DOC, allow_reads=True, library_path=LIBRARY)
        self.addCleanup(self.w.close)
        self.call = 0

    def test_mcp_modeling_reader_preserves_validated_legacy_reads(self):
        from narwhal3d.automation_tools import ReadError
        reader=ai_modeling.ModelingReader(self.app,'test',DOC,lambda:None)
        self.addCleanup(reader.close)
        error=None
        try:
            reader.validate('narwhal.objects',{'offset':0,'limit':32})
        except ReadError as exc:
            error=str(exc)
        self.assertIsNone(error,'legacy read must use its qualified read schema, not modeling-only validation')
        with self.assertRaises(ReadError):
            reader.validate('narwhal.objects',{'offset':0,'limit':32,'shell':'forbidden'})
        self.assertEqual(reader.read('narwhal.objects',{'offset':0,'limit':32})['total_count'],0)

    def stage(self, steps, continuation=False):
        self.call += 1
        prepared = self.w.prepare(DOC, user_text=None if continuation else getattr(self, 'prompt', 'Build the fixture'), continuation=continuation)
        batch = dict(label='Design', steps=steps)
        call_id = 'batch_' + str(self.call)
        result = self.w.finish(base.send(prepared, base.payload('openai_compatible', '', [(call_id, 'narwhal_model', batch)])), DOC)
        self.assertEqual(result['status'], 'tool_calls', result)
        descriptor = self.w.modeling_exchange('stage_modeling', DOC, owner=result['token'],
                    call_id=call_id, context=ai_modeling.context(self.app, DOC))
        return result['token'], descriptor

    def read_call(self,name,arguments,expect_error=False):
        from narwhal3d.automation_tools import ApplicationReader
        self.call+=1
        call_id='read_'+str(self.call)
        prepared=self.w.prepare(DOC,user_text='Inspect the current curve')
        outcome=self.w.finish(base.send(prepared,base.payload('openai_compatible','',[(call_id,name,arguments)])),DOC)
        self.assertEqual(outcome['status'],'tool_calls')
        result=self.w.read_tool_result(outcome['token'],call_id,ApplicationReader(self.app,'test'),DOC)
        self.w.record_results(outcome['token'],[result],DOC)
        self.assertEqual(result['is_error'],expect_error,result)
        return result['output']

    def apply(self, steps, continuation=False, review=False):
        if not review and self.call == 0:
            self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        token, descriptor = self.stage(steps, continuation)
        if review:
            self.assertEqual(descriptor['status'], 'modeling_review_required')
            self.assertEqual(self.m.object_count(), 0)
            with self.assertRaises(base.ai.WorkflowUnavailable):
                ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
            self.w.modeling_exchange('decide_modeling', DOC, ticket=descriptor['ticket'],
                        current=ai_modeling.context(self.app, DOC), allow=True)
        result = ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        self.w.record_results(token, [result], DOC)
        self.assertFalse(result['is_error'], result)
        self.assertEqual(self.ctx.undo.current_serial, 0, 'modeling left an Undo record open')
        with self.assertRaises(base.ai.WorkflowUnavailable):
            ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        return result['output']['objects']

    def test_review_dependent_boolean_and_followup_stable_uuid_undo_roundtrip(self):
        rows = self.apply([
            step('profile', dict(op='circle', center=[0, 0, 0], radius=20), False),
            step('blank', dict(op='extrude', source='profile', distance=12, direction=[0, 0, 1], solid=True), False),
            step('hole', dict(op='cylinder', center=[0, 0, -1], radius=3, height=14), False),
            step('plate', dict(op='boolean', left='blank', right='hole', kind='difference')),
        ], review=True)
        self.assertEqual(self.m.object_count(), 1)
        self.assertTrue(rows[0]['airtight'])
        self.assertEqual(rows[0]['face_types'], {'CYLINDER': 2, 'PLANE': 2})
        self.assertAlmostEqual(rows[0]['volume'], 3.141592653589793 * (400 - 9) * 12, places=6)
        oid = rows[0]['object_id']
        # An actual second provider round edits the stable UUID, not a guessed name.
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        rows = self.apply([step('move_plate', transform(oid, translation=(100, 0, 0)), replace=oid)], continuation=True)
        self.assertEqual(rows[0]['object_id'], oid)
        self.assertAlmostEqual(rows[0]['bounds'][0][0], 80, places=5)
        self.assertEqual(len(self.ctx.undo.undo_names()), 2)
        self.ctx.undo_last()
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app, oid)['bounds'][0][0], -20, places=5)
        self.ctx.redo_last()
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app, oid)['bounds'][0][0], 80, places=5)
        with tempfile.TemporaryDirectory(prefix='ai-modeling-') as folder:
            path = str(Path(folder) / 'design.3dm')
            self.assertTrue(self.m.save(path))
            kernel.forget_masters()
            reopened = model.NarwhalModel.load(path)
            facts = ai_modeling.inspect_geometry(reopened.file.Objects.FindId(oid).Geometry, .001)
            self.assertTrue(facts['airtight'])
            self.assertAlmostEqual(facts['bounds'][0][0], 80, places=5)

    def export_call(self,oid,format,chooser):
        from narwhal3d import filedialog
        self.call+=1
        call_id='export_'+str(self.call)
        prepared=self.w.prepare(DOC,user_text='Export this object to a new local destination')
        outcome=self.w.finish(base.send(prepared,base.payload('openai_compatible','',[(call_id,'narwhal_export',
            dict(object_ids=[oid],format=format))])),DOC)
        self.assertEqual(outcome['status'],'tool_calls',outcome)
        with patch.object(filedialog,'ask_save_path',side_effect=chooser):
            result=ai_modeling.export_with_local_destination(self.app,self.w,outcome['token'],call_id,DOC,lambda:None)
        self.w.record_results(outcome['token'],[result],DOC)
        return result

    def test_local_export_exact_files_no_overwrite_cancel_and_no_document_mutation(self):
        from narwhal3d import filedialog
        import json
        row=self.apply([step('part',dict(op='cylinder',center=[0,0,0],radius=3,height=12))])[0]
        oid=row['object_id']
        encoded=self.m.file.Encode()
        serial=self.ctx.undo.top_serial
        with tempfile.TemporaryDirectory(prefix='ai-local-export-') as folder:
            for format,suffix in [('step','.step'),('three_dm','.3dm')]:
                path=Path(folder)/('part'+suffix)
                result=self.export_call(oid,format,lambda *args:(filedialog.OK,str(path)))
                self.assertFalse(result['is_error'],result)
                self.assertTrue(result['output']['exported'])
                self.assertEqual(result['output']['file_bytes'],path.stat().st_size)
                self.assertNotIn(folder,json.dumps(result))
                if format=='step':
                    self.assertIn('CYLINDRICAL_SURFACE',path.read_text())
                else:
                    reopened=model.NarwhalModel.load(str(path))
                    self.assertEqual(reopened.object_count(),1)
                    facts=ai_modeling.inspect_geometry(reopened.file.Objects[0].Geometry,.001)
                    self.assertTrue(facts['airtight'])
                    self.assertAlmostEqual(facts['volume'],row['volume'],places=6)
            existing=Path(folder)/'existing.step'
            existing.write_text('private existing bytes')
            result=self.export_call(oid,'step',lambda *args:(filedialog.OK,str(existing)))
            self.assertTrue(result['is_error'])
            self.assertEqual(result['output']['outcome'],'existing_destination')
            self.assertEqual(existing.read_text(),'private existing bytes')
            result=self.export_call(oid,'step',lambda *args:(filedialog.CANCEL,None))
            self.assertTrue(result['is_error'])
            self.assertEqual(result['output']['outcome'],'declined')
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()),['existing.step','part.3dm','part.step'])
        self.assertEqual(self.m.file.Encode(),encoded)
        self.assertEqual(self.ctx.undo.top_serial,serial)

    def test_mcp_export_close_and_document_switch_prevent_late_file_writes(self):
        from narwhal3d import filedialog,ui
        self.apply([step('part',dict(op='sphere',center=[0,0,0],radius=3))])
        oid=str(self.m.file.Objects[0].Attributes.Id)
        args=dict(request_id='local_export',export=dict(object_ids=[oid],format='step'))
        with tempfile.TemporaryDirectory(prefix='ai-mcp-export-lifecycle-') as folder, \
                patch.object(ui,'_ai',{},create=True),patch.object(ui,'bpy',bpy,create=True), \
                patch.object(filedialog,'ask_save_path',return_value=(filedialog.OK,str(Path(folder)/'must-not-exist.step'))) as chooser:
            reader=ai_modeling.ModelingReader(self.app,'test',DOC,lambda:None)
            self.addCleanup(reader._workflow.close)
            self.assertEqual(reader.read('narwhal.export',args)['status'],'awaiting_local_destination')
            callback=reader._export_timer
            self.assertTrue(bpy.app.timers.is_registered(callback))
            reader.close()
            self.assertFalse(bpy.app.timers.is_registered(callback))
            callback()  # a late callback must also be harmless, not merely unregistered
            self.assertFalse(chooser.called)
            self.assertFalse(list(Path(folder).iterdir()))
            reader=ai_modeling.ModelingReader(self.app,'test',DOC,lambda:None)
            self.addCleanup(reader.close)
            self.assertEqual(reader.read('narwhal.export',args)['status'],'awaiting_local_destination')
            callback=reader._export_timer
            bpy.app.timers.unregister(callback)
            self.app.model=model.NarwhalModel()
            callback()
            result=reader.read('narwhal.export_result',dict(request_id='local_export'))
            self.assertEqual(result['status'],'finished')
            self.assertEqual(result['result']['output']['outcome'],'stale_context')
            self.assertFalse(chooser.called)
            self.assertFalse(list(Path(folder).iterdir()))

    def test_export_destination_race_cannot_clobber_a_new_external_file(self):
        from narwhal3d import filedialog,export_commands
        row=self.apply([step('part',dict(op='sphere',center=[0,0,0],radius=3))])[0]
        with tempfile.TemporaryDirectory(prefix='ai-export-race-') as folder:
            path=Path(folder)/'raced.step'
            original=export_commands.export_objects
            def concurrent_destination(*args,**kwargs):
                result=original(*args,**kwargs)
                path.write_text('created concurrently; must survive')
                return result
            with patch.object(export_commands,'export_objects',side_effect=concurrent_destination):
                result=self.export_call(row['object_id'],'step',lambda *args:(filedialog.OK,str(path)))
            self.assertTrue(result['is_error'])
            self.assertEqual(result['output']['outcome'],'existing_destination')
            self.assertEqual(path.read_text(),'created concurrently; must survive')
            self.assertEqual(list(Path(folder).iterdir()),[path],'private staging is removed without deleting the external file')

    def test_export_document_switch_during_local_picker_writes_nothing(self):
        from narwhal3d import filedialog
        row=self.apply([step('part',dict(op='sphere',center=[0,0,0],radius=3))])[0]
        with tempfile.TemporaryDirectory(prefix='ai-stale-export-') as folder:
            path=Path(folder)/'not-written.step'
            def choose(*args):
                self.app.model=model.NarwhalModel()
                return filedialog.OK,str(path)
            result=self.export_call(row['object_id'],'step',choose)
            self.assertEqual(result['output']['outcome'],'stale_context')
            self.assertFalse(path.exists())
            self.assertEqual(self.m.object_count(),1)

    def test_revolved_exact_solid_surface_and_followup_roundtrip(self):
        import math
        vertical=transform('profile')
        vertical.update(rotation_degrees=90,axis=[1,0,0])
        rows=self.apply([
            step('profile',dict(op='circle',center=[20,0,0],radius=3),False),
            step('vertical',vertical,False),
            step('ring',dict(op='revolve',source='vertical',axis_start=[0,0,0],axis_end=[0,0,1],
                             start_degrees=0,end_degrees=360,solid=True)),
            step('skin',dict(op='revolve',source='vertical',axis_start=[0,0,0],axis_end=[0,0,1],
                             start_degrees=45,end_degrees=135,solid=False)),
        ])
        ring,skin=rows
        self.assertEqual(self.m.object_count(),2)
        self.assertEqual(ring['face_types'],{'TORUS':1})
        self.assertTrue(ring['airtight'])
        self.assertAlmostEqual(ring['volume'],2*math.pi**2*20*9,places=6)
        self.assertFalse(skin['airtight'])
        self.assertEqual(skin['face_types'],{'TORUS':1})
        # The nonzero start angle must affect actual surface placement.
        self.assertLess(skin['bounds'][0][0],-10)
        self.assertGreater(skin['bounds'][0][1],10)
        oid=ring['object_id']
        moved=self.apply([step('move_ring',transform(oid,translation=(100,0,0)),replace=oid)],continuation=True)[0]
        self.assertEqual(moved['object_id'],oid)
        self.assertAlmostEqual(moved['bounds'][0][0],77,places=5)
        self.ctx.undo_last()
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app,oid)['bounds'][0][0],-23,places=5)
        self.ctx.redo_last()
        with tempfile.TemporaryDirectory(prefix='ai-exact-revolve-') as folder:
            path=str(Path(folder)/'revolutions.3dm')
            self.assertTrue(self.m.save(path))
            kernel.forget_masters()
            reopened=model.NarwhalModel.load(path)
            facts=ai_modeling.inspect_geometry(reopened.file.Objects.FindId(oid).Geometry,.001)
            self.assertTrue(facts['airtight'])
            self.assertEqual(facts['face_types'],{'TORUS':1})
            self.assertAlmostEqual(facts['bounds'][0][0],77,places=5)
            self.assertAlmostEqual(facts['volume'],2*math.pi**2*20*9,places=6)

    def test_curve_construction_rejects_noncurve_sources_with_actionable_reason(self):
        self.w.modeling_exchange('modeling_mode',DOC,mode='build')
        for action in [dict(op='pipe',source='box',radius=2,cap=True),
                       dict(op='revolve',source='box',axis_start=[0,0,0],axis_end=[0,0,1],
                            start_degrees=0,end_degrees=360,solid=True)]:
            token,descriptor=self.stage([step('box',dict(op='box',origin=[10,0,0],size=[3,3,3]),False),step('result',action)])
            result=ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
            self.w.record_results(token,[result],DOC)
            self.assertTrue(result['is_error'])
            self.assertEqual(result['output']['reason'],'curve_source_required')
            self.assertEqual(self.m.object_count(),0)
            self.assertEqual(self.ctx.undo.current_serial,0)

    def test_partial_revolution_refuses_requested_solid_without_publication(self):
        self.w.modeling_exchange('modeling_mode',DOC,mode='build')
        vertical=transform('profile')
        vertical.update(rotation_degrees=90,axis=[1,0,0])
        token,descriptor=self.stage([
            step('profile',dict(op='circle',center=[20,0,0],radius=3),False),
            step('vertical',vertical,False),
            step('skin',dict(op='revolve',source='vertical',axis_start=[0,0,0],axis_end=[0,0,1],
                             start_degrees=0,end_degrees=90,solid=True)),
        ])
        result=ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.w.record_results(token,[result],DOC)
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'],'solid_validation_failed')
        self.assertEqual(self.m.object_count(),0)
        self.assertEqual(self.ctx.undo.current_serial,0)

    def test_closed_pipe_shell_is_not_reported_as_verified_solid(self):
        self.w.modeling_exchange('modeling_mode',DOC,mode='build')
        token,descriptor=self.stage([
            step('rail',dict(op='circle',center=[0,0,0],radius=20),False),
            step('ring',dict(op='pipe',source='rail',radius=3,cap=True)),
        ])
        result=ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.w.record_results(token,[result],DOC)
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'],'solid_validation_failed')
        self.assertEqual(self.m.object_count(),0)
        self.assertEqual(self.ctx.undo.current_serial,0)

    def test_exact_dependent_pipes_followup_undo_and_roundtrip(self):
        import math
        rows=self.apply([
            step('rail',dict(op='nurbs',degree=2,closed=False,
                points=[[20,0,0,1],[20*math.sqrt(.5),20*math.sqrt(.5),0,math.sqrt(.5)],[0,20,0,1]],
                knots=[0,0,1,1]),False),
            step('ring',dict(op='pipe',source='rail',radius=3,cap=True)),
            step('line',dict(op='curve',points=[[50,0,0],[50,0,10],[50,0,20],[50,0,30]],closed=False),False),
            step('tube',dict(op='pipe',source='line',radius=2,cap=True)),
        ])
        self.assertEqual(self.m.object_count(),2)
        ring,tube=rows
        self.assertEqual(ring['face_types'],{'TORUS':1,'PLANE':2})
        self.assertTrue(ring['airtight'])
        self.assertTrue(tube['airtight'])
        self.assertAlmostEqual(ring['volume'],.5*math.pi**2*20*9,places=6)
        self.assertAlmostEqual(tube['volume'],math.pi*4*30,places=6)
        oid=ring['object_id']
        moved=self.apply([step('move_ring',transform(oid,translation=(100,0,0)),replace=oid)],continuation=True)[0]
        self.assertEqual(moved['object_id'],oid)
        self.assertAlmostEqual(moved['bounds'][0][0],ring['bounds'][0][0]+100,places=5)
        self.ctx.undo_last()
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app,oid)['bounds'][0][0],ring['bounds'][0][0],places=5)
        self.ctx.redo_last()
        with tempfile.TemporaryDirectory(prefix='ai-exact-pipe-') as folder:
            path=str(Path(folder)/'pipes.3dm')
            self.assertTrue(self.m.save(path))
            kernel.forget_masters()
            reopened=model.NarwhalModel.load(path)
            facts=ai_modeling.inspect_geometry(reopened.file.Objects.FindId(oid).Geometry,.001)
            self.assertTrue(facts['airtight'])
            self.assertEqual(facts['face_types'],{'TORUS':1,'PLANE':2})
            self.assertAlmostEqual(facts['bounds'][0][0],ring['bounds'][0][0]+100,places=5)
            self.assertAlmostEqual(facts['volume'],.5*math.pi**2*20*9,places=6)

    def test_smooth_outline_and_volumetric_composition(self):
        points = [[-55, 0, 0], [-43, 20, 0], [-12, 27, 0], [22, 18, 0], [42, 7, 0],
                  [58, 23, 0], [56, 1, 0], [62, -18, 0], [42, -7, 0],
                  [14, -17, 0], [5, -32, 0], [-7, -19, 0], [-35, -15, 0]]
        rows = self.apply([step('silhouette', dict(op='curve', points=points, closed=True)),
                           step('eye', dict(op='circle', center=[-37, 8, 0], radius=2))])
        self.assertEqual(self.m.object_count(), 2)
        self.assertEqual(rows[0]['degree'], 3)
        self.assertTrue(rows[0]['closed'] and rows[0]['planar'])
        outline = rows[0]['object_id']
        rows = self.apply([
            step('body_base', dict(op='sphere', center=[0, 0, 0], radius=1), False),
            step('body', transform('body_base', scale=(45, 18, 18))),
            step('tail_base', dict(op='sphere', center=[0, 0, 0], radius=1), False),
            step('tail_left', transform('tail_base', translation=(47, 11, 0), scale=(13, 17, 4))),
            step('tail_right', transform('tail_base', translation=(47, -11, 0), scale=(13, 17, 4))),
            step('fin', transform('tail_base', translation=(-8, -20, -3), scale=(7, 19, 3))),
        ], continuation=True)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['airtight'] and r['solid'] for r in rows))
        self.assertGreater(rows[0]['bounds'][1][0] - rows[0]['bounds'][0][0], 89.99)
        rows = self.apply([step('thick_outline', dict(op='extrude', source=outline, distance=12,
                            direction=[0, 0, 1], solid=True))], continuation=True)
        self.assertTrue(rows[0]['airtight'])
        self.assertAlmostEqual(rows[0]['bounds'][1][2] - rows[0]['bounds'][0][2], 12, places=5)

    def test_bevel_thickness_outline_and_loft_use_exact_existing_producers(self):
        rows = self.apply([
            step('blank', dict(op='box', origin=[0,0,0], size=[20,20,20]), False),
            step('rounded', dict(op='bevel', source='blank', size=2, chamfer=False)),
            step('profile', dict(op='circle', center=[40,0,0], radius=5), False),
            step('plate', dict(op='extrude', source='profile', distance=6, direction=[0,0,2], solid=True)),
            step('loft_a', dict(op='circle', center=[80,0,0], radius=4), False),
            step('loft_b', dict(op='circle', center=[80,0,8], radius=8), False),
            step('loft_c', dict(op='circle', center=[80,0,16], radius=4), False),
            step('skin', dict(op='loft', sections=['loft_a','loft_b','loft_c'])),
        ])
        self.assertEqual(rows[0]['face_types'], {'PLANE':6,'CYLINDER':12,'SPHERE':8})
        self.assertTrue(rows[0]['airtight']); self.assertFalse(rows[2]['solid'])
        oid = rows[1]['object_id']
        self.assertAlmostEqual(rows[1]['bounds'][1][2],6,places=5)
        rows = self.apply([step('thicker',dict(op='thickness',source=oid,distance=12),replace=oid),
                           step('outline',dict(op='outline',source='thicker'))],continuation=True)
        self.assertEqual(rows[0]['object_id'],oid)
        self.assertAlmostEqual(rows[0]['bounds'][1][2],12,places=5)
        self.assertTrue(rows[1]['closed'] and rows[1]['planar'])

    def test_explicit_2d_rejects_relief_and_cancelled_preparation_publishes_nothing(self):
        self.prompt = 'Make an outline in 2D'
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        token, descriptor = self.stage([step('solid',dict(op='sphere',center=[0,0,0],radius=4))])
        self.assertTrue(descriptor['require_planar'])
        result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'],'request_requires_planar_2d_geometry_not_relief_solids')
        self.assertEqual(self.m.object_count(),0); self.assertFalse(self.ctx.undo.can_undo)
        self.w.record_results(token,[result],DOC)
        token, descriptor = self.stage([step('circle',dict(op='circle',center=[0,0,0],radius=4))],True)
        job = ai_modeling.publish_steps(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertEqual(next(job)['phase'],'preparing')
        job.close(); self.w.invalidate()
        self.assertEqual(self.m.object_count(),0); self.assertFalse(self.ctx.undo.can_undo)

    def test_document_switch_callback_rolls_back_only_ai_allocations(self):
        from narwhal3d import bridge, selection_state
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        token, descriptor = self.stage([step('first',dict(op='box',origin=[0,0,0],size=[4,4,4])),
                                         step('second',dict(op='box',origin=[10,0,0],size=[4,4,4]))])
        other = model.NarwhalModel()
        other_ctx = commands.NarwhalContext(other)
        calls = []
        def change_document():
            calls.append(True)
            if len(calls) == 2:
                self.app.model, self.app.ctx = other, other_ctx
                other_ctx.undo.begin('External other-document edit')
                other_ctx.add_geometry(bridge.make_sphere([100,0,0],2), 'External')
                other_ctx.undo.end()
        self.m.before_change = change_document
        result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertTrue(result['is_error'])
        self.assertEqual(self.m.object_count(),0,'AI geometry stranded in old document')
        self.assertEqual(other.object_count(),1,'external document was changed by rollback')
        self.assertEqual(len(bpy.data.objects),1,'AI display allocations leaked or external display was removed')
        self.assertEqual(self.ctx.counters,{})
        self.assertFalse(self.ctx.undo.can_undo,'failed batch retained an Undo record')
        self.assertTrue(other_ctx.undo.can_undo,'external Undo record was discarded')
        self.assertEqual(selection_state.last_created(self.m),[])

    def test_backing_file_switch_is_guarded_before_sdk_insertion(self):
        from narwhal3d import bridge
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        token, descriptor = self.stage([step('first',dict(op='box',origin=[0,0,0],size=[4,4,4])),
                                         step('second',dict(op='box',origin=[10,0,0],size=[4,4,4]))])
        original_file = self.m.file
        other = model.NarwhalModel()
        other_ctx = commands.NarwhalContext(other)
        other_ctx.undo.begin('External other-document edit')
        other_ctx.add_geometry(bridge.make_sphere([100,0,0],2),'External')
        other_ctx.undo.end()
        import rhino3dm as r3
        replacement_file = r3.File3dm.Decode(other.file.Encode())
        calls = []
        def change_backing_file():
            calls.append(True)
            if len(calls) == 2:
                attrs = r3.ObjectAttributes()
                attrs.Name = 'ExternalOld'
                original_file.Objects.Add(r3.Point(r3.Point3d(123,0,0)), attrs)
                self.m.file = replacement_file
        self.m.before_change = change_backing_file
        result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertTrue(result['is_error'])
        self.assertEqual(len(original_file.Objects),1,'rollback discarded an unrelated old-file edit')
        self.assertEqual(original_file.Objects[0].Attributes.Name,'ExternalOld')
        self.assertIs(self.m.file,replacement_file,'regression input never switched backing files')
        self.assertEqual(len(replacement_file.Objects),1,'SDK inserted an AI object into the replacement file')
        self.assertEqual(len(bpy.data.objects),1)
        self.assertFalse(self.ctx.undo.can_undo)
        self.assertTrue(other_ctx.undo.can_undo)

    def test_open_loft_is_not_misrepresented_as_a_solid_boolean_operand(self):
        self.w.modeling_exchange('modeling_mode',DOC,mode='build')
        token,descriptor=self.stage([
            step('a',dict(op='circle',center=[0,0,0],radius=10),False),
            step('b',dict(op='circle',center=[0,0,20],radius=10),False),
            step('skin',dict(op='loft',sections=['a','b']),False),
            step('box',dict(op='box',origin=[-20,-20,-1],size=[40,40,30]),False),
            step('bad_solid',dict(op='boolean',kind='difference',left='box',right='skin'))])
        result=ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.w.record_results(token,[result],DOC)
        self.assertTrue(result['is_error'])
        self.assertEqual(result['output']['reason'],'boolean_requires_airtight_solids')
        self.assertEqual(self.m.object_count(),0)
        self.assertEqual(self.ctx.undo.current_serial,0)

    def test_dense_curve_shape_can_be_studied_remade_and_compared_without_rebuild(self):
        action=dict(op='nurbs',degree=3,closed=False,
            points=[[0,0,0,1],[0,30,0,1],[30,30,0,1],[30,-20,0,1],[60,-20,0,1],[60,0,0,1]],
            knots=[0,0,0,1,2,3,3,3])
        source=self.apply([step('source',action)])[0]['object_id']
        dense=self.m.file.Objects.FindId(source).Geometry.Duplicate()
        for i in range(1,91):
            self.assertTrue(dense.Knots.InsertKnot(3*i/91,1))
        self.ctx.undo.begin('Owned exact knot-refined source fixture')
        self.ctx.note_change()
        self.assertTrue(self.m.replace_geometry(source,dense))
        self.ctx.undo.end()
        self.ctx.rebuild_display()
        self.assertFalse(ai_modeling.inspect_object(self.app,source)['nurbs']['available'])
        encoded=self.m.file.Encode()
        sampled=self.read_call('narwhal_curve_samples',dict(object_id=source,sample_count=24,from_fraction=0,to_fraction=1))
        self.assertEqual(sampled['control_point_count'],96)
        self.assertEqual(sampled['samples'][0][1],[0,0,0])
        self.assertEqual(sampled['samples'][-1][1],[60,0,0])
        self.assertEqual(self.m.file.Encode(),encoded)
        candidate=self.apply([step('new_authored_structure',action)])[0]['object_id']
        comparison=self.read_call('narwhal_compare_curves',dict(left=source,right=candidate,sample_count=24))
        self.assertEqual(comparison['control_point_counts'],[96,6])
        self.assertLess(comparison['maximum_sampled_distance'],1e-6)
        self.assertFalse(comparison['certified_global_maximum'])
        # A visibly wrong newly-authored candidate must not compare as a perfect fit.
        changed=dict(action,points=[[p[0],p[1]+5,p[2],p[3]] for p in action['points']])
        self.apply([step('wrong_shape',changed,replace=candidate)])
        comparison=self.read_call('narwhal_compare_curves',dict(left=source,right=candidate,sample_count=24))
        self.assertGreater(comparison['maximum_sampled_distance'],4.9)
        self.read_call('narwhal_curve_samples',dict(object_id='00000000-0000-0000-0000-000000000001',sample_count=24,from_fraction=0,to_fraction=1),expect_error=True)

    def test_exact_nurbs_inspection_reconstruction_and_control_point_edit(self):
        import math
        rows = self.apply([step('source',dict(op='circle',center=[0,0,0],radius=10))])
        source = rows[0]['object_id']
        data = rows[0]['nurbs']
        self.assertTrue(data['available'])
        self.assertEqual(data['degree'],2)
        action = {key:data[key] for key in ('degree','points','knots','closed')}
        action['op'] = 'nurbs'
        rows = self.apply([step('reconstructed',action,False),
            step('proof',dict(op='extrude',source='reconstructed',direction=[0,0,1],distance=5,solid=True))])
        self.assertTrue(rows[0]['airtight'])
        self.assertAlmostEqual(rows[0]['volume'],math.pi*100*5,places=6)
        action = dict(action,points=[[p[0]*2,p[1],p[2],p[3]] for p in data['points']])
        rows = self.apply([step('edited_outline',action,replace=source)])
        self.assertEqual(rows[0]['object_id'],source)
        self.assertTrue(rows[0]['profile_usable_for_solid'])
        self.assertAlmostEqual(rows[0]['bounds'][1][0]-rows[0]['bounds'][0][0],40,places=6)
        self.ctx.undo_last()
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app,source)['bounds'][1][0],10,places=6)

    def test_explicit_2d_refuses_closed_crossing_profile_then_accepts_usable_outline(self):
        self.prompt = 'Make a smooth closed outline in 2D'
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        points = [[-10,-10,0],[10,10,0],[-10,10,0],[10,-10,0]]
        token, descriptor = self.stage([step('outline',dict(op='curve',points=points,closed=True))])
        result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.w.record_results(token,[result],DOC)
        self.assertTrue(result['is_error'],result)
        self.assertEqual(result['output']['reason'],'modeling_planar_profile_unverified')
        self.assertEqual(self.m.object_count(),0)
        self.assertEqual(self.ctx.undo.current_serial,0)
        rows = self.apply([step('usable',dict(op='curve',points=[[-10,-10,0],[10,-10,0],[10,10,0],[-10,10,0]],closed=True))])
        self.assertTrue(rows[0]['profile_usable_for_solid'])
        self.assertFalse(rows[0]['solid'])

    def test_attribute_edits_preserve_uuid_exact_geometry_undo_and_save(self):
        import rhino3dm as r3
        rows = self.apply([step('detail',dict(op='sphere',center=[10,0,0],radius=3))])
        oid = rows[0]['object_id']
        original = ai_modeling.inspect_object(self.app, oid)
        self.ctx.undo.begin('Owned layer fixture')
        layer_index = self.m.add_layer('Details')
        self.ctx.undo.end()
        layer = str(self.m.file.Layers[layer_index].Id)
        action = dict(op='attributes',source=oid,name='Blue eye',layer_id=layer,color=[20,40,180],visible=False)
        rows = self.apply([step('style',action,replace=oid)])
        facts = rows[0]
        self.assertEqual(facts['object_id'],oid)
        self.assertEqual(facts['name'],'Blue eye')
        self.assertEqual(facts['attributes']['color'],[20,40,180,255])
        self.assertEqual(facts['attributes']['layer_id'],layer)
        self.assertFalse(facts['attributes']['visible'])
        self.assertAlmostEqual(facts['volume'],original['volume'])
        self.assertEqual(self.m.object_count(),1)
        self.ctx.undo_last()
        undone = ai_modeling.inspect_object(self.app,oid)
        self.assertEqual(undone['name'],original['name'])
        self.assertEqual(undone['attributes'],original['attributes'])
        self.ctx.redo_last()
        self.assertEqual(ai_modeling.inspect_object(self.app,oid)['attributes'],facts['attributes'])
        with tempfile.TemporaryDirectory(prefix='ai-attributes-') as folder:
            path = str(Path(folder)/'styled.3dm')
            self.assertTrue(self.m.save(path))
            reopened = model.NarwhalModel.load(path)
            obj = reopened.file.Objects.FindId(oid)
            self.assertEqual(obj.Attributes.Name,'Blue eye')
            self.assertEqual(obj.Attributes.ColorSource,r3.ObjectColorSource.ColorFromObject)
            self.assertEqual(list(obj.Attributes.ObjectColor),[20,40,180,255])
            self.assertFalse(obj.Attributes.Visible)
            self.assertTrue(ai_modeling.inspect_geometry(obj.Geometry,.001)['airtight'])

    def test_attribute_rollback_preserves_original_and_unrelated_callback_edit(self):
        rows = self.apply([step('detail',dict(op='sphere',center=[0,0,0],radius=3))])
        oid = rows[0]['object_id']
        before = ai_modeling.inspect_object(self.app,oid)
        action = dict(op='attributes',source=oid,name='Changed',color=[1,2,3],visible=False,layer_id=None)
        token, descriptor = self.stage([step('style',action,replace=oid)])
        rebuild = self.ctx.rebuild_display
        calls = []
        def fail_once():
            if not calls:
                calls.append(True)
                self.m.file.Objects.FindId(oid).Attributes.Name = 'External name'
                raise RuntimeError('owned publication failure')
            return rebuild()
        with patch.object(self.ctx,'rebuild_display',fail_once):
            result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.w.record_results(token,[result],DOC)
        self.assertTrue(result['is_error'])
        restored = ai_modeling.inspect_object(self.app,oid)
        self.assertEqual(restored['name'],'External name')
        self.assertEqual(restored['attributes'],before['attributes'])
        self.assertAlmostEqual(restored['volume'],before['volume'])

    def assert_box_display(self, oid, minimum_x):
        # Actual Blender display bounds, not a mocked rebuild or receipt.
        from mathutils import Vector
        obj = self.m.file.Objects.FindId(oid)
        self.assertIsNotNone(obj)
        display = bpy.data.objects.get(obj.Attributes.Name)
        self.assertIsNotNone(display)
        bpy.context.view_layer.update()
        self.assertAlmostEqual(min((display.matrix_world @ Vector(p)).x for p in display.bound_box), minimum_x, places=5)
        self.assertAlmostEqual(ai_modeling.inspect_object(self.app,oid)['bounds'][0][0], minimum_x, places=5)

    def failed_replacement(self, fault, external=False):
        import rhino3dm as r3
        row = self.apply([step('original',dict(op='box',origin=[0,0,0],size=[4,4,4]))])[0]
        oid = row['object_id']
        # Retain a real Redo branch as well as Undo before the failed transaction.
        self.apply([step('spare',dict(op='box',origin=[20,0,0],size=[2,2,2]))])
        self.ctx.undo_last()
        obj = self.m.file.Objects.FindId(oid)
        obj.Attributes.SetUserString('private_attribute','preserve me')
        before_facts = ai_modeling.inspect_object(self.app,oid)
        before_strings = obj.Geometry.GetUserStrings()
        # openNURBS archive bytes include userdata copy bookkeeping, so copies
        # are not byte-identical. Compare exact facts/recipe and SDK attributes.
        fields = self.m._SNAPSHOT_ATTRS + ('Id','PlotColor','PlotColorSource','PlotWeight',
            'PlotWeightSource','LinetypeIndex','LinetypeSource','DisplayOrder','WireDensity',
            'Url','ViewportId','CastsShadows','ReceivesShadows','ObjectDecoration','ActiveSpace')
        before_attrs = {field:getattr(obj.Attributes,field) for field in fields}
        before_attr_strings = obj.Attributes.GetUserStrings()
        before_groups = obj.Attributes.GetGroupList2()
        original_batch = obj.Geometry.GetUserString('NarwhalAI.Batch')
        history = (self.ctx.undo.undo_names(),self.ctx.undo.redo_names(),self.ctx.undo.top_serial)
        token, descriptor = self.stage([step('moved',transform(oid,translation=[30,0,0]),replace=oid)])
        table_type = type(self.m.file.Objects)
        method = 'Delete' if fault == 'delete' else 'Add'
        original = getattr(table_type,method)
        hits = []
        def fail_once(table,*args):
            target = (str(args[0]) == oid if method == 'Delete' else
                      str(args[1].Id) == oid and args[0].GetUserString('NarwhalAI.Batch') != original_batch)
            if target and not hits:
                hits.append(True)
                if method == 'Add':
                    self.assertIsNone(table.FindId(oid),'fault must occur AFTER deletion')
                if external:
                    self.m.file.Strings['callback'] = 'unrelated edit survives'
                if fault == 'wrong_uuid':
                    attrs = r3.ObjectAttributes()
                    attrs.Name = args[1].Name
                    return original(table,args[0],attrs)
                raise RuntimeError('owned SDK fault')
            return original(table,*args)
        with patch.object(table_type,method,fail_once):
            result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertEqual(hits,[True])
        self.assertTrue(result['is_error'],result)
        restored = self.m.file.Objects.FindId(oid)
        self.assertIsNotNone(restored,'failed Delete+Add lost original UUID')
        self.assertEqual(ai_modeling.inspect_object(self.app,oid),before_facts)
        self.assertEqual(restored.Geometry.GetUserStrings(),before_strings)
        self.assertEqual({field:getattr(restored.Attributes,field) for field in fields},before_attrs)
        self.assertEqual(restored.Attributes.GetUserStrings(),before_attr_strings)
        self.assertEqual(restored.Attributes.GetGroupList2(),before_groups)
        self.assertEqual(self.m.object_count(),1,'failed add left a duplicate/wrong UUID')
        self.assert_box_display(oid,0)
        self.assertEqual(len(bpy.data.objects),1)
        self.assertEqual(self.ctx.undo.current_serial,0)
        if external:
            self.assertEqual(self.m.file.Strings['callback'],'unrelated edit survives')
            self.assertIsNone(result['output']['applied'])
            self.assertFalse(result['output']['rollback_confirmed'])
            self.assertEqual(self.ctx.undo.undo_names()[:-1],history[0])
        else:
            self.assertIs(result['output']['applied'],False)
            self.assertTrue(result['output']['rollback_confirmed'])
            self.assertEqual((self.ctx.undo.undo_names(),self.ctx.undo.redo_names(),self.ctx.undo.top_serial),history)
            self.ctx.redo_last()
            self.assertEqual(self.m.object_count(),2,'failed attempt destroyed existing Redo')
        self.w.record_results(token,[result],DOC)

    def test_failed_add_after_delete_restores_uuid_attributes_display_and_history(self):
        self.failed_replacement('add')

    def test_failed_delete_preserves_original_and_history(self):
        self.failed_replacement('delete')

    def test_wrong_uuid_add_restores_original_and_removes_owned_stray(self):
        self.failed_replacement('wrong_uuid')

    def test_failed_add_preserves_unrelated_callback_edit(self):
        self.failed_replacement('add',external=True)

    def later_callback_edit(self, delete):
        from narwhal3d import bridge
        row = self.apply([step('original',dict(op='box',origin=[0,0,0],size=[4,4,4]))])[0]
        oid = row['object_id']
        token, descriptor = self.stage([step('moved',transform(oid,translation=[30,0,0]),replace=oid)])
        rebuild = self.ctx.rebuild_display
        calls = []
        def fail_once():
            if not calls:
                calls.append(True)
                attrs = self.m.file.Objects.FindId(oid).Attributes
                self.m.file.Objects.Delete(oid)
                if not delete:
                    self.m.file.Objects.Add(bridge.make_box((80,0,0),(84,4,0),4),attrs)
                raise RuntimeError('external callback changed target')
            return rebuild()
        with patch.object(self.ctx,'rebuild_display',fail_once):
            result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.assertTrue(result['is_error'])
        self.assertFalse(result['output']['rollback_confirmed'])
        self.assertIsNone(result['output']['applied'])
        if delete:
            self.assertIsNone(self.m.file.Objects.FindId(oid),'rollback resurrected an externally deleted target')
            self.assertEqual(len(bpy.data.objects),0)
        else:
            self.assert_box_display(oid,80)
        self.assertEqual(self.ctx.undo.current_serial,0)
        self.w.record_results(token,[result],DOC)

    def test_rollback_preserves_later_callback_target_deletion(self):
        self.later_callback_edit(True)

    def test_rollback_preserves_later_callback_geometry_edit(self):
        self.later_callback_edit(False)

    def committed_fault(self, fault):
        row = self.apply([step('source',dict(op='box',origin=[0,0,0],size=[4,4,4]),False),
                          step('original',transform('source',scale=[1,2,1]))])[0]
        oid = row['object_id']
        history = self.ctx.undo.undo_names()
        token, descriptor = self.stage([step('moved',transform(oid,translation=[30,0,0]),replace=oid)])
        exchange = self.w.modeling_exchange
        calls, failures = [], []
        def exchange_fault(op,*args,**kwargs):
            calls.append(op)
            if op == 'modeling_publication':
                self.assertFalse(kwargs['is_error'],'committed geometry must never become a failure/rollback receipt')
                if fault in ('before','after','unavailable') and (not failures or fault == 'unavailable'):
                    failures.append(op)
                    if fault == 'after':
                        exchange(op,*args,**kwargs)
                    raise base.ai.WorkflowUnavailable('owned acknowledgment fault')
            return exchange(op,*args,**kwargs)
        selections = []
        def selection_fault(*args,**kwargs):
            selections.append(self.ctx.undo.current_serial)
            raise RuntimeError('owned post-commit selection fault')
        end = self.ctx.undo.end
        def end_reply_fault():
            end()
            raise RuntimeError('history end accepted, reply lost')
        with patch.object(self.w,'modeling_exchange',exchange_fault), patch.object(
                self.ctx.undo,'end',end_reply_fault if fault == 'history_reply' else end), patch.object(
                self.ctx,'select_by_names',selection_fault if fault == 'selection' else self.ctx.select_by_names):
            if fault == 'unavailable':
                with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'modeling_committed_acknowledgment_unknown_inspect_no_replay'):
                    ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
                result = None
            else:
                try:
                    result = ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
                except Exception as exc:
                    self.fail('committed transaction did not reconcile: '+str(exc))
        self.assertEqual(calls.count('consume_modeling'),1,'receipt recovery re-executed geometry')
        self.assertEqual(calls.count('modeling_publication'),2 if fault in ('before','unavailable') else 1)
        if fault == 'selection':
            self.assertEqual(selections,[0],'selection fault was not after Undo finalization')
        self.assertEqual(self.m.object_count(),1)
        self.assertIsNotNone(kernel.master_of(self.m.file.Objects.FindId(oid).Geometry),'committed master was retired')
        self.assert_box_display(oid,30)
        self.assertEqual(len(bpy.data.objects),1)
        self.assertEqual(self.ctx.undo.current_serial,0)
        self.assertEqual(self.ctx.undo.undo_names(),history+['AI Design'])
        self.assertFalse(self.ctx.undo.can_redo)
        if result is not None:
            canonical = exchange('modeling_result',DOC,ticket=descriptor['ticket'])['result']
            self.assertEqual(result,canonical)
            self.assertFalse(result['is_error'])
            self.assertTrue(result['output']['applied'])
            self.assertEqual(result['output']['objects'][0]['object_id'],oid)
            self.w.record_results(token,[result],DOC)
        else:
            with self.assertRaisesRegex(base.ai.WorkflowUnavailable,'modeling_result_pending'):
                exchange('modeling_result',DOC,ticket=descriptor['ticket'])
        with self.assertRaises(base.ai.WorkflowUnavailable):
            ai_modeling.publish(self.app,self.w,descriptor,DOC,lambda:None)
        self.ctx.undo_last()
        self.assert_box_display(oid,0)
        self.ctx.redo_last()
        self.assert_box_display(oid,30)
        self.assertTrue(ai_modeling.inspect_object(self.app,oid)['airtight'])

    def test_history_end_lost_reply_is_already_committed(self):
        self.committed_fault('history_reply')

    def test_post_commit_selection_failure_is_best_effort(self):
        self.committed_fault('selection')

    def test_native_ack_failure_before_acceptance_records_receipt_only(self):
        self.committed_fault('before')

    def test_native_ack_failure_after_acceptance_returns_canonical_success(self):
        self.committed_fault('after')

    def test_native_ack_unavailable_retains_committed_geometry_history_and_masters(self):
        self.committed_fault('unavailable')

    def test_clock_feature_inspection_rejects_missing_bore_and_marker(self):
        import math
        rows = self.apply([step('plate',dict(op='cylinder',center=[0,0,0],radius=40,height=12))] + [
            step('marker_'+str(i),dict(op='cylinder',center=[26*math.cos(i*math.pi/6),26*math.sin(i*math.pi/6),12],radius=2,height=2)) for i in range(12)])
        with self.assertRaises(AssertionError):
            clock_geometry_evidence(self.app)
        plate = rows[0]['object_id']
        self.apply([step('cutter',dict(op='cylinder',center=[0,0,-1],radius=2.5,height=14),False),
                    step('bored_plate',dict(op='boolean',kind='difference',left=plate,right='cutter'),replace=plate)])
        self.assertEqual(clock_geometry_evidence(self.app)['markers']['count'],12)
        marker = rows[-1]['object_id']
        self.apply([step('moved_marker',transform(marker,translation=[1,0,0]),replace=marker)])
        with self.assertRaises(AssertionError):
            clock_geometry_evidence(self.app)

    def test_exact_shape_distance_through_real_native_tool_route(self):
        from narwhal3d.automation_tools import ApplicationReader
        rows = self.apply([step('left',dict(op='box',origin=[0,0,0],size=[4,4,4])),
                           step('right',dict(op='box',origin=[10,0,0],size=[4,4,4]))])
        left, right = [row['object_id'] for row in rows]
        prepared = self.w.prepare(DOC,continuation=True)
        outcome = self.w.finish(base.send(prepared,base.payload('openai_compatible','',
            [('measure_parts','narwhal_measure',{'left':left,'right':right})])),DOC)
        self.assertEqual(outcome['status'],'tool_calls')
        result = self.w.read_tool_result(outcome['token'],'measure_parts',ApplicationReader(self.app,'test'),DOC)
        self.assertFalse(result['is_error'])
        self.assertAlmostEqual(result['output']['distance'],6,places=6)
        self.assertFalse(result['output']['within_tolerance'])
        self.assertFalse(result['output']['unified_solid_verified'])
        self.w.record_results(outcome['token'],[result],DOC)
        self.apply([step('touching',transform(right,translation=(-6,0,0)),replace=right)],continuation=True)
        measurement = ai_modeling.measure_objects(self.app,left,right)
        self.assertAlmostEqual(measurement['distance'],0,places=6)
        self.assertTrue(measurement['within_tolerance'])
        self.assertEqual(ai_modeling.measure_objects(self.app,left,'00000000-0000-0000-0000-000000000001')['code'],'object_not_found')

    def test_stale_context_and_failed_operation_publish_nothing(self):
        self.w.modeling_exchange('modeling_mode', DOC, mode='build')
        token, descriptor = self.stage([step('bad', dict(op='extrude', source='00000000-0000-0000-0000-000000000001', distance=12, direction=[0, 0, 1], solid=True))])
        result = ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        self.assertTrue(result['is_error']); self.assertIs(result['output']['applied'], False)
        self.assertEqual(self.m.object_count(), 0); self.assertFalse(self.ctx.undo.can_undo)
        self.w.record_results(token, [result], DOC)
        token, descriptor = self.stage([step('sphere', dict(op='sphere', center=[0, 0, 0], radius=4))], True)
        self.m.file.Strings['outside edit'] = 'must survive'
        with self.assertRaises(base.ai.WorkflowUnavailable):
            ai_modeling.publish(self.app, self.w, descriptor, DOC, lambda: None)
        self.assertEqual(self.m.object_count(), 0)
        self.assertEqual(self.m.file.Strings['outside edit'], 'must survive')


def host_mutations():
    """Re-break real SDK producers in memory; never edit working source files."""
    import inspect
    variants = [
        ('register_replacement_before_sdk_delete','publish_steps',
         [('replaced.append(attempt)','pass')],
         'test_failed_add_after_delete_restores_uuid_attributes_display_and_history'),
        ('restore_missing_failed_replacement','publish_steps',
         [("obj is None and attempt['failed']",'False')],
         'test_failed_add_after_delete_restores_uuid_attributes_display_and_history'),
        ('do_not_resurrect_callback_deletion','publish_steps',
         [("obj is None and attempt['failed']",'obj is None')],
         'test_rollback_preserves_later_callback_target_deletion'),
        ('do_not_overwrite_callback_geometry','publish_steps',
         [("obj is not None and obj.Geometry.Encode() == attempt['encoded']",'obj is not None')],
         'test_rollback_preserves_later_callback_geometry_edit'),
        ('selection_after_irreversible_boundary','publish_steps',
         [('        committed = True',"        ctx.select_by_names([o['name'] for o in output_objects], already_expanded=True)\n        committed = True")],
         'test_post_commit_selection_failure_is_best_effort'),
        ('ack_outside_rollback_boundary','publish_steps',
         [('        committed = True',"        workflow.modeling_exchange('modeling_publication', document_session, ticket=ticket, output=output, is_error=False)\n        committed = True")],
         'test_native_ack_failure_before_acceptance_records_receipt_only'),
        ('ack_query_lost_accepted_reply','publish_steps',
         [("                    result = workflow.modeling_exchange('modeling_result', document_session, ticket=ticket)['result']",
           "                    raise ai_assistant.WorkflowUnavailable('lost_result')")],
         'test_native_ack_failure_after_acceptance_returns_canonical_success'),
        ('ack_retry_pending_receipt_only','publish_steps',
         [("if str(failure) != 'modeling_result_pending':",'if True:')],
         'test_native_ack_failure_before_acceptance_records_receipt_only'),
        ('retain_committed_master_on_ack_failure','publish_steps',
         [("published = {s['id'] for s in batch['steps'] if s['publish']} if committed else set()",'published = set()')],
         'test_native_ack_unavailable_retains_committed_geometry_history_and_masters'),
        ('history_end_reply_not_a_rollback','publish_steps',
         [('if ctx.undo.current_serial != 0 or ctx.undo.top_serial != serial:','if True:')],
         'test_history_end_lost_reply_is_already_committed'),
        ('revolve_curve_precondition','_construct',[("if not isinstance(profile,r3.Curve):",'if False:')],
         'test_curve_construction_rejects_noncurve_sources_with_actionable_reason'),
        ('pipe_curve_precondition','_construct',[("if not isinstance(rail,r3.Curve):",'if False:')],
         'test_curve_construction_rejects_noncurve_sources_with_actionable_reason'),
        ('export_refuses_overwrite_race','export_with_local_destination',
         [('os.link(staged,destination)','os.replace(staged,destination)')],
         'test_export_destination_race_cannot_clobber_a_new_external_file'),
        ('export_rechecks_picker_context','export_with_local_destination',
         [("if app.model is not captured_model or app.model.file is not captured_file or context(app,document_session)!=expected:",
           'if False:')], 'test_export_document_switch_during_local_picker_writes_nothing'),
        ('revolve_actual_sweep','_construct',[("action['start_degrees'], action['end_degrees']","action['start_degrees'], action['start_degrees']+90")],
         'test_revolved_exact_solid_surface_and_followup_roundtrip'),
        ('revolve_actual_start','_construct',[("action['start_degrees'], action['end_degrees']","0, action['end_degrees']-action['start_degrees']")],
         'test_revolved_exact_solid_surface_and_followup_roundtrip'),
        ('pipe_actual_radius','_construct',[("action['radius'], cap=action['cap']","action['radius']/2, cap=action['cap']")],
         'test_exact_dependent_pipes_followup_undo_and_roundtrip'),
        ('pipe_requested_caps','_construct',[("cap=action['cap']","cap=False")],
         'test_exact_dependent_pipes_followup_undo_and_roundtrip'),
        ('reject_open_boolean_operands', '_construct',
         [("if not all(inspect_geometry(g,1e-7)['airtight'] for g in (left,right)):", 'if False:')],
         'test_open_loft_is_not_misrepresented_as_a_solid_boolean_operand'),
        ('sample_actual_curve_shape','sample_curve',[('curve.PointAt(t)','curve.PointAt(domain.T0)')],
         'test_dense_curve_shape_can_be_studied_remade_and_compared_without_rebuild'),
        ('measure_curve_candidate_deviation','compare_curves_approx',[('row.append(distance)','row.append(0.0)')],
         'test_dense_curve_shape_can_be_studied_remade_and_compared_without_rebuild'),
        ('preserve_rational_nurbs_weights', '_construct',
         [('curve.Points[i] = r3.Point4d(*point)', 'curve.Points[i] = r3.Point4d(point[0],point[1],point[2],1.0)')],
         'test_exact_nurbs_inspection_reconstruction_and_control_point_edit'),
        ('planar_profile_failure_not_success', 'inspect_geometry',
         [("facts['profile_usable_for_solid'] = False", "facts['profile_usable_for_solid'] = True"),
                   ("facts['profile_usable_for_solid'] = bool(shape is not None and occt.is_airtight(shape))", "facts['profile_usable_for_solid'] = True")],
         'test_explicit_2d_refuses_closed_crossing_profile_then_accepts_usable_outline'),
        ('exact_distance', 'measure_objects', [('distance = bridge._occt().minimum_distance(*shapes)', 'distance = 0.0')],
         'test_exact_shape_distance_through_real_native_tool_route'),
        ('apply_object_attributes', 'publish_steps', [('setattr(attrs, field, value)', 'pass')],
         'test_attribute_edits_preserve_uuid_exact_geometry_undo_and_save'),
        ('restore_owned_attributes', 'publish_steps',
         [('if obj is not None and getattr(obj.Attributes, field) == owned:', 'if False:')],
         'test_attribute_rollback_preserves_original_and_unrelated_callback_edit'),
        ('master_recipe_coherence', '_construct',
         [('moved = bridge.transform_geometry(geom, ', 'geom.Transform('),
          ('return moved if moved is not None else geom', 'return geom')],
         'test_review_dependent_boolean_and_followup_stable_uuid_undo_roundtrip'),
        ('close_undo_record', 'publish_steps', [('        try:\n            ctx.undo.end()\n        except Exception:', '        try:\n            pass\n        except Exception:')],
         'test_review_dependent_boolean_and_followup_stable_uuid_undo_roundtrip'),
        ('guard_before_sdk_insert', 'publish_steps',
         [("ctx.add_geometry(geom, step['name'], publication_check=check)", "ctx.add_geometry(geom, step['name'])")],
         'test_backing_file_switch_is_guarded_before_sdk_insertion'),
        ('cleanup_owned_allocations', 'publish_steps',
         [('ctx._remove_publication_allocations(allocations)', "ctx._remove_publication_allocations({'objects': [], 'meshes': []})")],
         'test_document_switch_callback_rolls_back_only_ai_allocations'),
        ('old_file_undo_history', 'publish_steps',
         [('if unchanged or model.file is not file:', 'if unchanged:'),
          ('            if model.file is not file:\n                ctx.undo.abandon()\n            else:\n                ctx.undo.end()', '            ctx.undo.end()')],
         'test_backing_file_switch_is_guarded_before_sdk_insertion'),
    ]
    for label, method, changes, test in variants:
        source = inspect.getsource(getattr(ai_modeling, method))
        for old, new in changes:
            assert source.count(old) == 1, (label, 'mutation anchor changed')
            source = source.replace(old, new, 1)
        namespace = dict(ai_modeling.__dict__)
        exec(compile(source, '<actual-host-modeling-mutation>', 'exec'), namespace)
        with patch.object(ai_modeling, method, namespace[method]):
            result = unittest.TestResult()
            ModelingHost(test).run(result)
            assert result.failures and not result.errors, (label, result.failures, result.errors)
        print('ACTUAL_HOST_MUTATION_KILLED', label, flush=True)
    from narwhal3d.kernel import occt_backend
    with patch.object(occt_backend, 'minimum_distance', return_value=0.0):
        result = unittest.TestResult()
        ModelingHost('test_exact_shape_distance_through_real_native_tool_route').run(result)
        assert result.failures and not result.errors, (result.failures, result.errors)
    print('ACTUAL_HOST_MUTATION_KILLED backend_exact_distance', flush=True)
    import textwrap
    for label,method,anchor,replacement,test in [
        ('mcp_legacy_read_delegation','validate','if tool not in self.modeling_tools:','if False:',
         'test_mcp_modeling_reader_preserves_validated_legacy_reads'),
        ('mcp_export_close_retires_authority','close','self._workflow.close()','pass',
         'test_mcp_export_close_and_document_switch_prevent_late_file_writes'),
    ]:
        source=textwrap.dedent(inspect.getsource(getattr(ai_modeling.ModelingReader,method)))
        assert source.count(anchor)==1
        namespace=dict(ai_modeling.__dict__)
        exec(compile(source.replace(anchor,replacement,1),'<mcp-boundary-mutation>','exec'),namespace)
        with patch.object(ai_modeling.ModelingReader,method,namespace[method]):
            result=unittest.TestResult()
            ModelingHost(test).run(result)
            assert result.failures and not result.errors,(label,result.failures,result.errors)
        print('ACTUAL_HOST_MUTATION_KILLED',label,flush=True)


def clock_geometry_evidence(app):
    """Actual OCCT faces and point classification, not names or provider prose."""
    import math
    import itertools
    from narwhal3d import bridge
    from narwhal3d.kernel import occt_backend as occt
    shapes = [bridge._occt_operand(o.Geometry) for o in app.model.file.Objects]
    assert shapes and all(s is not None and occt.is_airtight(s) for s in shapes)
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_IN
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    tol = app.model.absolute_tolerance()
    cylinders = []
    for shape in shapes:
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face(exp.Current())
            surf = BRepAdaptor_Surface(face)
            if surf.GetType() == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                if abs(cyl.Axis().Direction().Z()) > 0.999999:
                    lo, hi = occt.tight_bbox(face)
                    loc = cyl.Location()
                    cylinders.append(dict(x=loc.X(), y=loc.Y(), radius=cyl.Radius(), low=lo[2], high=hi[2]))
            exp.Next()
    def inside(x, y, z):
        return any(BRepClass3d_SolidClassifier(s, gp_Pnt(x,y,z), tol).State() == TopAbs_IN for s in shapes)
    raised = [c for c in cylinders if abs(c['low']-12)<tol and c['high']>12+tol]
    assert 12 <= len(raised) <= 20, ('raised_marker_faces', cylinders)
    ring = None
    for group in itertools.combinations(raised, 12):
        cx = sum(c['x'] for c in group)/12
        cy = sum(c['y'] for c in group)/12
        radii = [math.hypot(c['x']-cx,c['y']-cy) for c in group]
        if min(radii)<tol or max(radii)-min(radii)>tol: continue
        angles = sorted(math.atan2(c['y']-cy,c['x']-cx) for c in group)
        gaps = [(angles[(i+1)%12]-angles[i])%(2*math.pi) for i in range(12)]
        if max(abs(g-math.pi/6) for g in gaps)>0.001: continue
        if not all(inside(c['x'],c['y'],6) for c in group): continue
        ring = dict(center=[cx,cy], radius=sum(radii)/12, count=12)
        break
    assert ring, ('no_supported_12_marker_ring', cylinders)
    cx, cy = ring['center']
    bores = [c for c in cylinders if math.hypot(c['x']-cx,c['y']-cy)<tol and abs(c['low'])<tol and abs(c['high']-12)<tol]
    assert bores and all(not inside(cx,cy,z) for z in [tol*2,6,12-tol*2]), ('missing_through_bore', cylinders)
    return dict(markers=ring, through_bore=bores, plate_thickness=12,
                method='exact_cylindrical_faces_and_solid_point_classification', visual_quality_evaluated=False)


def real_provider_smoke():
    """Opt-in local provider run, distinct from the deterministic tests above."""
    import os, json, time
    from narwhal3d import ai_transport
    from narwhal3d.automation_tools import ApplicationReader
    case = ModelingHost('test_smooth_outline_and_volumetric_composition')
    case.setUp()
    out = Path(tempfile.mkdtemp(prefix='narwhal-real-modeling-'))
    trace = []
    try:
        case.state.provider = base.ai.set_provider('openai_compatible', 'qwen/qwen3.8-27b', 'http://127.0.0.1:1234/v1', credential_env='LM_STUDIO_API_KEY')
        # Rebind and then obtain explicit Build authority in this disposable test.
        case.w._exchange(dict(op='reset', binding=case.w._binding(DOC)))
        case.w.modeling_exchange('modeling_mode', DOC, mode='build')
        design = 'selected' if '--real-selected' in sys.argv else ('3d' if '--real-3d' in sys.argv else ('clock' if '--real-clock' in sys.argv else '2d'))
        if design == 'selected':
            from narwhal3d import bridge
            profile = bridge.make_interpolated_curve([[-30,0,0],[-10,20,0],[20,15,0],[30,-10,0],[-10,-15,0]], degree=3, closed=True)
            case.ctx.undo.begin('Owned selected-outline fixture')
            oid, name = case.ctx.add_geometry(profile, 'Selected outline')
            case.ctx.undo.end()
            case.ctx.select_by_names([name])
        prompt = {'2d':'Make a whale in 2D.', '3d':'Make a 3D whale with a volumetric body, tail flukes, flippers and eyes. A multi-object exact-solid assembly is acceptable; do not claim it is one unified solid.',
                  'clock':'Make a whale-shaped clock face, 12 mm thick, with 12 hour markers and a center hole for the clock spindle.',
                                    'selected':'Turn this selected outline into a 12 mm thick part.'}[design]
        prompt += ' Choose reasonable dimensions in millimeters. Actually build with narwhal_model, inspect the results, and finish. Do not merely describe or plan it.'
        reader = ApplicationReader(case.app, 'test')
        if design == 'selected':
            selected = reader.read('narwhal.selection_summary', {})
            assert any(str(row.get('object_id', row.get('id'))) == str(oid) for row in selected['objects']), selected
        def drive(text, phase):
            for turn in range(24):
                prepared = case.w.prepare(DOC, user_text=text if turn == 0 else None, continuation=turn > 0, timeout_ms=300_000,
                    context={'workspace':reader.read('narwhal.workspace', {}), 'selection':reader.read('narwhal.selection_summary', {})} if turn == 0 else None)
                started = time.monotonic()
                receipt = ai_transport.send_turn(prepared, os.environ)
                outcome = case.w.finish(receipt, DOC)
                trace.append(dict(phase=phase, turn=turn, elapsed=time.monotonic()-started, outcome=outcome))
                (out / 'trace.json').write_text(json.dumps(trace, indent=2))
                print('REAL_PROVIDER_TURN', phase, turn, outcome['status'], 'objects', case.m.object_count(), flush=True)
                if outcome['status'] == 'completed':
                    return
                if outcome['status'] != 'tool_calls':
                    raise AssertionError(outcome)
                results = []
                for call in outcome['response']['tool_calls']:
                    if call['name'] == 'narwhal_model':
                        descriptor = case.w.modeling_exchange('stage_modeling', DOC, owner=outcome['token'], call_id=call['id'], context=ai_modeling.context(case.app, DOC))
                        result = (descriptor['result'] if descriptor['status'] == 'modeling_rejected' else
                                  ai_modeling.publish(case.app, case.w, descriptor, DOC, lambda: None))
                    else:
                        if call['name'] == 'narwhal_capture_view':
                            case.w._exchange(dict(op='decide_visual',token=outcome['token'],binding=case.w._binding(DOC),call_id=call['id'],allow=False))
                        result = case.w.read_tool_result(outcome['token'], call['id'], reader, DOC)
                    results.append(result)
                trace[-1]['results'] = results
                case.w.record_results(outcome['token'], results, DOC)
                (out / 'trace.json').write_text(json.dumps(trace, indent=2))
            raise AssertionError(('provider_budget_exhausted', str(out)))
        drive(prompt, 'build')
        if design == 'selected':
            before = [ai_modeling.inspect_object(case.app, str(o.Attributes.Id)) for o in case.m.file.Objects]
            solids = [m for m in before if m['airtight']]
            assert len(solids)==1 and abs(solids[0]['bounds'][1][2]-solids[0]['bounds'][0][2]-12)<0.01, before
            (out / 'before_edit.json').write_text(json.dumps(before, indent=2))
            target = solids[0]['object_id']
            count = case.m.object_count()
            drive('Make that part 18 mm thick instead. Replace the current solid, preserving its identity; do not make a duplicate.', 'followup')
            after = ai_modeling.inspect_object(case.app, target)
            assert after['airtight'] and abs(after['bounds'][1][2]-after['bounds'][0][2]-18)<0.01, after
            assert case.m.object_count()==count
            drive('Undo that last change.', 'undo')
            undone = ai_modeling.inspect_object(case.app, target)
            assert abs(undone['bounds'][1][2]-undone['bounds'][0][2]-12)<0.01, undone
        measurements = [ai_modeling.inspect_object(case.app, str(o.Attributes.Id)) for o in case.m.file.Objects]
        (out / 'measurements.json').write_text(json.dumps(measurements, indent=2))
        if design == '2d':
            assert any(m.get('closed') and m.get('planar') and m.get('degree', 0) >= 3 for m in measurements), measurements
            assert not any(m['solid'] for m in measurements), measurements
            from narwhal3d import bridge
            import rhino3dm as r3
            outlines = [o.Geometry for o in case.m.file.Objects if isinstance(o.Geometry, r3.Curve) and o.Geometry.IsClosed]
            silhouette = max(outlines, key=lambda c: (c.GetBoundingBox().Max.X-c.GetBoundingBox().Min.X)*(c.GetBoundingBox().Max.Y-c.GetBoundingBox().Min.Y))
            downstream = bridge.extrude_curve(silhouette,1,direction=[0,0,1],solid=True)
            assert ai_modeling.inspect_geometry(downstream,case.m.absolute_tolerance())['airtight'], 'closed outline is not a usable planar manufacturing profile'
        else:
            assert any(m['airtight'] and m.get('volume',0)>0 for m in measurements), measurements
            if design == 'clock':
                evidence = clock_geometry_evidence(case.app)
                (out / 'clock_features.json').write_text(json.dumps(evidence, indent=2))
        assert len(measurements) >= (1 if design == 'clock' else 2), measurements
        assert case.m.save(str(out / 'whale.3dm'))
        print('REAL_PROVIDER_CREATED', design, str(out), 'objects', len(measurements), flush=True)
        print('REAL_PROVIDER_LIMIT: no automated recognizability or visual-quality claim', flush=True)
    finally:
        case.doCleanups()


if __name__ == '__main__':
    if '--real-provider' in sys.argv:
        real_provider_smoke()
        raise SystemExit(0)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ModelingHost)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('actual host modeling failed')
    if '--mutations' in sys.argv:
        host_mutations()
        restored = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModelingHost))
        if not restored.wasSuccessful():
            raise RuntimeError('restored actual host modeling failed')
    print('AI_MODELING_HOST_PASS', flush=True)
