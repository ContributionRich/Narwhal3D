#!/usr/bin/env python3
"""Real Blender interaction safety plus unchanged command/publication regressions.

--launch [--mutations] reuses the existing command harness with a smaller 1 GiB
cap. No assertion or command machinery is replaced. Pointer acceptance itself is
certified separately by test_sweep1_interaction_gui.py, not these API-driven guards.
"""
from pathlib import Path
import json
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
if '--launch' in sys.argv:
    source=(ROOT/'spike/test_sweep1_command_integration.py').read_text().split("if '--launch' in sys.argv:")[0]
    source=source.replace('MemoryMax=2048M','MemoryMax=1024M').replace('6*1024*1024','5*1024*1024')
    namespace={'__file__':__file__,'__name__':'interaction_launcher'}
    exec(compile(source,'<existing bounded command launcher>','exec'),namespace)
    namespace['SOURCES'] += ('narwhal3d/ui.py','narwhal3d/curve_inputs.py','spike/test_sweep1_interaction_checks.py')
    raise SystemExit(namespace['launch']())

sys.path.insert(0,str(ROOT/'spike'))
import unittest
from unittest.mock import patch
import bpy
import test_sweep1_command_integration as legacy
from narwhal3d import ui, state, commands, solid_commands, curve_inputs
from test_surfacing_freeform import bridge, kernel, r3
from narwhal3d.model import NarwhalModel


class InteractionChecks(legacy.CommandIntegrationTests):
    def mouse_sources(self):
        self.state.feed_line('Sweep1'); self.cmd=self.state.active_command
        ui._select_object_hit('source0'); self.state.feed_line('')
        for i in (1,2,3): ui._select_object_hit('source'+str(i),shift=i>1)
        self.state.feed_line('')
        self.assertTrue(self.cmd._aligning)

    def test_alignment_selection_replacement_preserves_prior_history(self):
        self.setup_case(); self.saved_history_fixture()
        prior=(self.ctx.undo.undo_names(),self.ctx.undo.redo_names())
        self.mouse_sources()
        registry=kernel.master_registry_snapshot()
        self.ctx.select_by_names([])  # Same membership, distinct set application.
        self.assertIn('selection changed or was restored',self.finish())
        self.assertEqual((self.ctx.undo.undo_names(),self.ctx.undo.redo_names()),prior)
        self.assertEqual(self.ctx.model.object_count(),4)
        self.assertEqual(kernel.master_registry_snapshot(),registry)
        self.assertFalse(self.state.doc_modified())

    def test_alignment_clear_callback_keeps_external_edit_dirty_undoable(self):
        self.setup_case(); self.saved_history_fixture()
        self.state.feed_line('Sweep1'); self.cmd=self.state.active_command
        ui._select_object_hit('source0'); self.state.feed_line('')
        for i in (1,2,3): ui._select_object_hit('source'+str(i),shift=i>1)
        oid=self.case[1][2]
        original=self.ctx.model.file.Objects.FindId(oid).Geometry.Encode()
        real=self.ctx.select_by_names
        def changed(names,*a,**kw):
            result=real(names,*a,**kw)
            if not names and self.cmd.step_index==2:
                curve=self.ctx.model.file.Objects.FindId(oid).Geometry.Duplicate(); curve.Reverse()
                self.ctx.model.replace_geometry(oid,curve)
            return result
        with patch.object(self.ctx,'select_by_names',changed):
            self.assertIn('geometry changed',self.finish())
        self.assertEqual(self.ctx.model.object_count(),4)
        self.assertEqual(self.ctx.undo.undo_names()[0],'External change during Sweep1')
        self.assertFalse(self.ctx.undo.can_redo)
        self.assertTrue(self.state.doc_modified())
        self.assertNotEqual(self.ctx.model.file.Objects.FindId(oid).Geometry.Encode(),original)
        self.assertEqual(self.ctx.undo_last(),'External change during Sweep1')
        self.assertEqual(self.ctx.model.file.Objects.FindId(oid).Geometry.Encode(),original)

    def test_mouse_uuid_name_reuse_and_same_id_changes_refuse(self):
        for replace_id in (False,True):
            self.setup_case(); self.mouse_sources()
            oid=self.case[1][2]; owner=self.ctx.model.file.Objects.FindId(oid)
            curve=owner.Geometry.Duplicate()
            if replace_id:
                self.ctx.note_change('External edit')
                self.ctx.model.file.Objects.Delete(oid)
                self.ctx.model.add_object(curve,'source2',0)
            else:
                curve.Reverse(); self.ctx.model.replace_geometry(oid,curve)
            registry=kernel.master_registry_snapshot()
            text=self.finish()
            self.assertIn('stale Freeform source',text)
            self.assertEqual(self.ctx.model.object_count(),4)
            self.assertNotIn('Sweep1',self.ctx.undo.undo_names())
            self.assertEqual(kernel.master_registry_snapshot(),registry)

    def test_explicit_typed_rail_ignores_preexisting_component_selection(self):
        self.setup_case()
        ui._selected_subobjects.add(('unrelated','Edge',1))
        self.enter_sources(); self.finish()
        self.assertEqual(self.ctx.model.object_count(),5)
        legacy.verify_document(self.ctx.model)

    def test_single_section_mouse_and_typed_circle_keep_legacy_routes(self):
        for style in ('Freeform','Roadlike'):
            for mouse in (False,True):
                bridge.remove_display_objects(list(bpy.data.objects))
                model=NarwhalModel()
                model.add_object(r3.LineCurve(r3.Point3d(0,0,0),r3.Point3d(0,0,5)),'rail',0)
                model.add_object(r3.Circle(1.).ToNurbsCurve(),'circle',0)
                state.app_state.set_model(model); ctx=state.app_state.ctx; ctx.rebuild_display(); ctx.undo.clear()
                state.app_state.feed_line('Sweep1'); state.app_state.feed_line(style)
                if mouse:
                    ui._select_object_hit('rail'); state.app_state.feed_line('')
                    ui._select_object_hit('circle'); state.app_state.feed_line('')
                else:
                    state.app_state.feed_line('rail'); state.app_state.feed_line('circle'); state.app_state.feed_line('')
                self.assertEqual(model.object_count(),3,(style,mouse,state.app_state.history[-3:]))
                self.assertEqual(ctx.undo.undo_names(),['Sweep1'])


def run_tests():
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(InteractionChecks)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    return result.testsRun


if __name__=='__main__':
    count=run_tests()
    rows=[]
    if '--mutations' in sys.argv:
        rows=legacy.mutations()
        import inspect,io,textwrap
        for label,method,old,new,test in [
            ('alignment-selection-stamp','_resolve_accepted_sources',
             'if self._alignment_selection_stamp is not None and curve_inputs.selection_stamp(model) is not self._alignment_selection_stamp:',
             'if False:','test_alignment_selection_replacement_preserves_prior_history'),
            ('single-circle-eligibility','_resolve_accepted_sources',
             'if require_freeform and ref in self._multi_source_errors:',
             'if ref in self._multi_source_errors:','test_single_section_mouse_and_typed_circle_keep_legacy_routes'),
            ('typed-over-component-priority','_feed_objects_step',
             'if not text and self._mouse_used and self._selected_subobjects(ctx):',
             'if self._selected_subobjects(ctx):','test_explicit_typed_rail_ignores_preexisting_component_selection'),
        ]:
            original=getattr(solid_commands.Sweep1Cmd,method)
            source=textwrap.dedent(inspect.getsource(original))
            assert old in source
            namespace={}
            exec(compile(source.replace(old,new),'<interaction guard mutation>','exec'),original.__globals__,namespace)
            with patch.object(solid_commands.Sweep1Cmd,method,namespace[original.__name__]):
                stream=io.StringIO()
                result=unittest.TextTestRunner(stream=stream).run(InteractionChecks(test))
            assert result.failures and not result.errors,stream.getvalue()[-3000:]
            rows.append(dict(mutation=label,red=True))
            print('MUTATION RED',label,flush=True)
        count=run_tests()
    (Path(os.environ['SWEEP1_COMMAND_OUT'])/'passed.json').write_text(json.dumps(dict(tests=count,mutations=rows),indent=2))
