#!/usr/bin/env python3
"""Prototype gate controls; --geometry requires actual Blender, never a fake publisher."""
import argparse
from copy import deepcopy
from contextlib import contextmanager
import importlib
import math
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_ai_sustained_lifecycle as lifecycle


@contextmanager
def ui_fixture(app):
    # patch.dict(sys.modules) would discard newly imported OCP extension modules
    # on exit, leaving its cached native backend with an invalid import state.
    name='narwhal3d.ui';missing=object();previous=sys.modules.get(name,missing)
    sys.modules[name]=types.SimpleNamespace(app_state=app,invalidate_snap_cache=lambda:None)
    try: yield
    finally:
        if previous is missing: sys.modules.pop(name,None)
        else: sys.modules[name]=previous


def arguments(operation='sphere'):
    args=dict(center=[11,23,37],radius=4)
    if operation!='sphere': args['height']=7
    return dict(operation=operation,arguments=args)


def context():
    return dict(append=lifecycle.box_context(),undo_top_serial=0,undo_next_serial=1)


class PrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.ctx=lifecycle.session('openai_responses');self.state,self.w=self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__,None,None,None)
        self.p=self.w.prepare(lifecycle.DOC,user_text='prototype only, no network')

    def stage(self, args=None, current=None):
        out=self.w.stage_primitive_prototype(self.p.data['token'],lifecycle.DOC,args or arguments(),current or context())
        self.assertFalse(out['live_enabled']);self.assertTrue(out['prototype_only'])
        return out['proposal']

    def test_live_catalog_unchanged_and_one_use_approval(self):
        catalog=self.w.operation_catalog()
        self.assertNotIn('narwhal_create_sphere',{t['name'] for t in catalog['tools']})
        disabled=catalog['operations']['disabled_prototypes']
        self.assertEqual({row['operation'] for row in disabled},{'sphere','cylinder','cone'})
        self.assertTrue(all(row['prototype_implemented'] and not row['execution_available'] and not row['proposal_available'] for row in disabled))
        proposal=self.stage()
        with self.assertRaisesRegex(lifecycle.base.ai.WorkflowUnavailable,'primitive_approval_required'):
            self.w.take_primitive_prototype(proposal,lifecycle.DOC,context())
        self.w.decide_primitive_prototype(proposal,lifecycle.DOC,context(),accepted=True)
        out=self.w.take_primitive_prototype(proposal,lifecycle.DOC,context())
        self.assertEqual(out['status'],'primitive_prototype_consumed')
        self.assertFalse(out['live_enabled'])
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
            self.w.take_primitive_prototype(proposal,lifecycle.DOC,context())
        with self.assertRaisesRegex(lifecycle.base.ai.WorkflowUnavailable,'primitive_request_replay'):
            self.stage()

    def test_deny_changed_context_and_cancel_cannot_publish(self):
        proposal=self.stage()
        self.w.decide_primitive_prototype(proposal,lifecycle.DOC,context(),accepted=False)
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
            self.w.take_primitive_prototype(proposal,lifecycle.DOC,context())
        self.w.invalidate()
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
            self.w.decide_primitive_prototype(proposal,lifecycle.DOC,context(),accepted=True)

    def test_dimensions_unknown_fields_and_boolean_numbers(self):
        for value in (0,-1,True,1e7,.001):
            data=arguments();data['arguments']['radius']=value
            with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)
        data=arguments();data['arguments']['mesh']=True
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)
        data=arguments();data['arguments']['center']=[999999,0,0]
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)

    def test_undo_and_document_context_are_exact(self):
        for field in ('undo_top_serial','undo_next_serial'):
            with self.subTest(field=field), lifecycle.session('openai_responses') as (_state,w):
                p=w.prepare(lifecycle.DOC,user_text='prototype')
                proposal=w.stage_primitive_prototype(p.data['token'],lifecycle.DOC,arguments(),context())['proposal']
                changed=context();changed[field]+=1
                with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                    w.decide_primitive_prototype(proposal,lifecycle.DOC,changed,accepted=True)
        proposal=self.stage();changed=context();changed['append']['units']='Inches'
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
            self.w.decide_primitive_prototype(proposal,lifecycle.DOC,changed,accepted=True)

    def test_new_user_turn_cannot_retain_prototype_approval(self):
        old=self.stage()
        self.w.decide_primitive_prototype(old,lifecycle.DOC,context(),accepted=True)
        self.assertEqual(lifecycle.finish(self.w,self.p,'openai_responses')['status'],'completed')
        self.p=self.w.prepare(lifecycle.DOC,user_text='a new explicit request')
        fresh=self.stage()
        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
            self.w.take_primitive_prototype(old,lifecycle.DOC,context())
        self.w.decide_primitive_prototype(fresh,lifecycle.DOC,context(),accepted=True)
        self.assertEqual(self.w.take_primitive_prototype(fresh,lifecycle.DOC,context())['status'],'primitive_prototype_consumed')

    def test_approval_context_changes_retire_each_prototype(self):
        changes=[('append','document_session','other-document'),('append','units','Inches'),
                 ('append','tolerance',.02),('append','layer_id','00000000-0000-0000-0000-000000000003'),
                 ('append','layer_available',False),('append','command_idle',False),('append','undo_ready',False),
                 (None,'undo_top_serial',1),(None,'undo_next_serial',2)]
        for operation in ('sphere','cylinder','cone'):
            for parent,field,value in changes:
                with self.subTest(operation=operation,field=field), lifecycle.session('openai_responses') as (_state,w):
                    p=w.prepare(lifecycle.DOC,user_text='prototype')
                    proposal=w.stage_primitive_prototype(p.data['token'],lifecycle.DOC,arguments(operation),context())['proposal']
                    changed=context();(changed[parent] if parent else changed)[field]=value
                    with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                        w.decide_primitive_prototype(proposal,lifecycle.DOC,changed,accepted=True)
                    with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                        w.decide_primitive_prototype(proposal,lifecycle.DOC,context(),accepted=True)

    def test_approval_expiry_provider_and_explicit_cancel_for_all_shapes(self):
        import time
        for operation in ('sphere','cylinder','cone'):
            for mode in ('expiry','provider','cancel'):
                with self.subTest(operation=operation,mode=mode), lifecycle.session('openai_responses') as (state,w):
                    p=w.prepare(lifecycle.DOC,user_text='prototype',timeout_ms=50 if mode=='expiry' else 30000)
                    proposal=w.stage_primitive_prototype(p.data['token'],lifecycle.DOC,arguments(operation),context())['proposal']
                    w.decide_primitive_prototype(proposal,lifecycle.DOC,context(),accepted=True)
                    if mode=='expiry': time.sleep(.06)
                    elif mode=='provider': state.provider=lifecycle.base.ai.set_provider('anthropic','fixture',credential_env='TEST_KEY')
                    else: w.invalidate()
                    with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                        w.take_primitive_prototype(proposal,lifecycle.DOC,context())

    def test_shared_validation_for_all_prototypes(self):
        for operation in ('sphere','cylinder','cone'):
            for field in ('radius',)+(() if operation=='sphere' else ('height',)):
                for value in (0,-1,True,False,1e7,.001):
                    data=arguments(operation);data['arguments'][field]=value
                    with self.subTest(operation=operation,field=field,value=value), self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)
                for value in (float('nan'),float('inf'),-float('inf')):
                    data=arguments(operation);data['arguments'][field]=value
                    with self.assertRaises(ValueError): self.stage(data)
            for value in ([True,0,0],[1e7,0,0],[0,0],[0,0,0,0]):
                data=arguments(operation);data['arguments']['center']=value
                with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)
            for field,value in (('plane','Front'),('mode','Mesh'),('solid',False),('target_id','selection')):
                data=arguments(operation);data['arguments'][field]=value
                with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)

    def test_cone_requires_base_cap_exact_recipe_and_conical_face(self):
        self.assertNotIn('narwhal_create_cone',{t['name'] for t in self.w.operation_catalog()['tools']})
        self.stage(arguments('cone'))
        facts=dict(proxy_solid=True,airtight=True,face_types={'CONE':1,'PLANE':1},volume=math.pi*4**2*7/3,
                   bounds=[[7,19,37],[15,27,44]],recipe_kind='cone')
        self.w.check_primitive_geometry(arguments('cone'),facts)
        for faces in ({'CONE':1},{'PLANE':128},{'CYLINDER':1,'PLANE':2}):
            with self.assertRaisesRegex(lifecycle.base.ai.WorkflowUnavailable,'primitive_geometry_verification_failed'):
                self.w.check_primitive_geometry(arguments('cone'),dict(facts,face_types=faces))

    def test_cylinder_requires_positive_height_and_both_caps(self):
        self.assertNotIn('narwhal_create_cylinder',{t['name'] for t in self.w.operation_catalog()['tools']})
        for height in (0,-1,True,1e7,.001):
            data=arguments('cylinder');data['arguments']['height']=height
            with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable): self.stage(data)
        self.stage(arguments('cylinder'))
        facts=dict(proxy_solid=True,airtight=True,face_types={'CYLINDER':1,'PLANE':2},volume=math.pi*4**2*7,
                   bounds=[[7,19,37],[15,27,44]],recipe_kind='cylinder')
        self.w.check_primitive_geometry(arguments('cylinder'),facts)
        for faces in ({'CYLINDER':1},{'CYLINDER':1,'PLANE':1},{'PLANE':128}):
            with self.assertRaisesRegex(lifecycle.base.ai.WorkflowUnavailable,'primitive_geometry_verification_failed'):
                self.w.check_primitive_geometry(arguments('cylinder'),dict(facts,face_types=faces))

    def test_rust_geometry_acceptance_checks_real_invariants(self):
        facts=dict(proxy_solid=True,airtight=True,face_types={'SPHERE':1},volume=4/3*math.pi*4**3,
                   bounds=[[7,19,33],[15,27,41]],recipe_kind='sphere')
        self.w.check_primitive_geometry(arguments(),facts)
        for field,value in [('airtight',False),('proxy_solid',False),('face_types',{'PLANE':128}),
                            ('volume',3),('bounds',[[0,0,0],[1,1,1]]),('recipe_kind','mesh')]:
            with self.subTest(field=field):
                bad=dict(facts,**{field:value})
                with self.assertRaisesRegex(lifecycle.base.ai.WorkflowUnavailable,'primitive_geometry_verification_failed'):
                    self.w.check_primitive_geometry(arguments(),bad)


class GeometryTests(unittest.TestCase):
    def test_actual_sphere_publication_master_recipe_and_single_undo(self):
        self.publication('sphere')

    def test_actual_cylinder_publication_master_recipe_and_single_undo(self):
        self.publication('cylinder')

    def test_actual_cone_publication_master_recipe_and_single_undo(self):
        self.publication('cone')

    def test_scoped_publication_rollback(self):
        import bpy
        from narwhal3d import model, commands, ai_primitives, ai_workspace, kernel, bridge
        loader=lifecycle.base.ai._load_workflow_library
        for operation in ('sphere','cylinder','cone'):
            for failure in ('display','collision','external_edit'):
                with self.subTest(operation=operation,failure=failure):
                    bpy.data.batch_remove(list(bpy.data.objects))
                    m=model.NarwhalModel();ctx=commands.NarwhalContext(m)
                    app=types.SimpleNamespace(model=m,ctx=ctx,active_command=None)
                    retained=[];created=[]
                    if failure=='collision':
                        oid,_=m.add_object(bridge.make_sphere([1,2,3],2),operation.title()+'1')
                        retained.append(oid)
                    producer_name='make_'+operation;producer=getattr(bridge,producer_name)
                    def produce(*args,**kwargs):
                        geom=producer(*args,**kwargs);created.append(geom);return geom
                    def fail_display(*_a,**_kw):
                        if failure=='external_edit':
                            oid,_=m.add_object(bridge.make_sphere([1,2,3],2),'External edit')
                            retained.append(oid)
                        raise RuntimeError('injected display failure')
                    with ui_fixture(app), patch.object(ai_workspace.WorkspaceSnapshots,'ready',lambda _self:True), \
                         patch.object(lifecycle.base.ai,'_load_workflow_library',lambda path=None:loader(lifecycle.LIBRARY)), \
                         patch.object(bridge,producer_name,produce), patch.object(ctx,'_make_display_object',fail_display), \
                         lifecycle.session('openai_responses') as (_state,w):
                        p=w.prepare(lifecycle.DOC,user_text='isolated rollback test')
                        current=ai_primitives.context(app,lifecycle.DOC)
                        proposal=w.stage_primitive_prototype(p.data['token'],lifecycle.DOC,arguments(operation),current)['proposal']
                        w.decide_primitive_prototype(proposal,lifecycle.DOC,current,accepted=True)
                        with self.assertRaises(RuntimeError):
                            ai_primitives.publish_prototype(app,w,proposal,lifecycle.DOC,lambda:None)
                        self.assertEqual({str(o.Attributes.Id) for o in m.file.Objects},set(retained))
                        self.assertEqual(len(bpy.data.objects),0)
                        self.assertIsNone(kernel.master_of(created[0]),'failed action leaked its master binding')
                        self.assertEqual(ctx.undo.current_serial,0)
                        self.assertEqual(ctx.undo.can_undo,failure=='external_edit')
                        if failure=='external_edit':
                            self.assertEqual(m.object_count(),1)
                            self.assertNotEqual(ctx.undo.top_serial,0,'external edit must remain distinct from the saved/initial state')
                        with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                            ai_primitives.publish_prototype(app,w,proposal,lifecycle.DOC,lambda:None)

    def publication(self, operation):
        import bpy
        from narwhal3d import model, commands, ai_primitives, ai_workspace, kernel
        from narwhal3d.kernel import occt_backend as occt
        self.assertIsNotNone(bpy.context.scene)
        bpy.data.batch_remove(list(bpy.data.objects))  # This disposable test process owns the whole scene.
        faces={'SPHERE':1} if operation=='sphere' else {'CYLINDER':1,'PLANE':2}
        volume=4/3*math.pi*4**3 if operation=='sphere' else math.pi*4**2*7
        if operation=='cone':
            faces={'CONE':1,'PLANE':1};volume/=3
        undo_name='AI '+operation.title()+' prototype'
        m=model.NarwhalModel();ctx=commands.NarwhalContext(m)
        app=types.SimpleNamespace(model=m,ctx=ctx,active_command=None)
        loader=lifecycle.base.ai._load_workflow_library
        # Only the UI-ready seam is simulated in background Blender. Geometry,
        # producer, publication, native layer calculator and Undo are all real.
        with ui_fixture(app), \
             patch.object(ai_workspace.WorkspaceSnapshots,'ready',lambda _self:True), \
             patch.object(lifecycle.base.ai,'_load_workflow_library',lambda path=None:loader(lifecycle.LIBRARY)), \
             lifecycle.session('openai_responses') as (_state,w):
            p=w.prepare(lifecycle.DOC,user_text='isolated sphere prototype')
            current=ai_primitives.context(app,lifecycle.DOC)
            proposal=w.stage_primitive_prototype(p.data['token'],lifecycle.DOC,arguments(operation),current)['proposal']
            with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                ai_primitives.publish_prototype(app,w,proposal,lifecycle.DOC,lambda:None)
            self.assertEqual(m.object_count(),0)
            w.decide_primitive_prototype(proposal,lifecycle.DOC,current,accepted=True)
            result=ai_primitives.publish_prototype(app,w,proposal,lifecycle.DOC,lambda:None)
            self.assertTrue(result['created']);self.assertFalse(result['live_enabled'])
            self.assertEqual(m.object_count(),1)
            obj=m.file.Objects[0];geom=obj.Geometry;master=kernel.master_of(geom)
            self.assertIsNotNone(master)
            self.assertEqual(occt.face_types(master),faces)
            self.assertTrue(occt.is_airtight(master))
            self.assertAlmostEqual(occt.volume(master),volume,places=8)
            self.assertEqual(kernel.recipe_of(geom)['kind'],operation)
            self.assertEqual(ctx.undo.current_serial,0)
            self.assertEqual(ctx.undo.undo_names(),[undo_name])
            before=m.object_count()
            with self.assertRaises(lifecycle.base.ai.WorkflowUnavailable):
                ai_primitives.publish_prototype(app,w,proposal,lifecycle.DOC,lambda:None)
            self.assertEqual(m.object_count(),before)
            self.assertEqual(ctx.undo_last(),undo_name)
            self.assertEqual(m.object_count(),0)
            self.assertFalse(ctx.undo.can_undo)
            self.assertEqual(ctx.redo_last(),undo_name)
            self.assertEqual(m.object_count(),1)
            restored=m.file.Objects[0].Geometry
            self.assertTrue(occt.is_airtight(kernel.master_of(restored)))
            self.assertEqual(kernel.recipe_of(restored)['kind'],operation)
            import tempfile
            saved_recipe=deepcopy(kernel.recipe_of(restored))
            with tempfile.TemporaryDirectory(prefix='narwhal-ai-primitive-roundtrip-') as folder:
                path=str(Path(folder)/'prototype.3dm')
                self.assertTrue(m.save(path))
                self.assertEqual(m.last_save_warnings,[])
                kernel.forget_masters()  # Force reconstruction, not process-local registry reuse.
                reopened=model.NarwhalModel.load(path)
                self.assertEqual(reopened.object_count(),1)
                reopened_geom=reopened.file.Objects[0].Geometry
                reopened_master=kernel.master_of(reopened_geom)
                self.assertIsNotNone(reopened_master,'save/reopen lost the analytic master')
                self.assertEqual(kernel.recipe_of(reopened_geom),saved_recipe)
                self.assertEqual(occt.face_types(reopened_master),faces)
                self.assertTrue(occt.is_airtight(reopened_master))
                self.assertAlmostEqual(occt.volume(reopened_master),volume,places=8)
                self.assertEqual(str(reopened.file.Objects[0].Attributes.Id),result['object_id'])


def publisher_mutations():
    from narwhal3d import ai_primitives
    source=Path(ai_primitives.__file__).read_text()
    variants=[('master_cleanup','kernel.discard_master_binding(key, shape)','pass'),
              ('native_consumption','workflow.take_primitive_prototype(proposal, document_session, current)',
               "{'status':'primitive_prototype_consumed','action':proposal['arguments'],'context':proposal['expected']}"),
              ('undo_close','ctx.undo.end()','pass')]
    for label,old,new in variants:
        assert old in source
        module=types.ModuleType('narwhal3d._prototype_mutation');module.__package__='narwhal3d'
        exec(compile(source.replace(old,new,1),'<prototype-mutation>','exec'),module.__dict__)
        with patch.object(ai_primitives,'publish_prototype',module.publish_prototype):
            result=unittest.TestResult()
            case='test_scoped_publication_rollback' if label=='master_cleanup' else 'test_actual_sphere_publication_master_recipe_and_single_undo'
            GeometryTests(case).run(result)
            expected_failures=3 if label=='master_cleanup' else 1
            if result.errors or len(result.failures)!=expected_failures: raise AssertionError((label,'unexpected mutation failures',result.errors,len(result.failures)))
        print('Actual Blender publisher RED confirmed:',label,flush=True)
    from narwhal3d import bridge
    with patch.object(bridge,'rehydrate_document',lambda *_a,**_kw:(0,[])):
        result=unittest.TestResult()
        GeometryTests('test_actual_sphere_publication_master_recipe_and_single_undo').run(result)
        if result.errors or len(result.failures)!=1: raise AssertionError(('roundtrip mutation',result.errors,result.failures))
    print('Actual Blender RED confirmed: roundtrip reconstruction',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--geometry',action='store_true')
    p.add_argument('--publisher-mutations',action='store_true')
    p.add_argument('--test')
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    lifecycle.LIBRARY=args.library.resolve()
    cls=GeometryTests if args.geometry else PrimitiveTests
    suite=unittest.TestSuite([cls(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(cls)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful() and args.publisher_mutations:
        publisher_mutations()
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GeometryTests))
    raise SystemExit(not result.wasSuccessful())
