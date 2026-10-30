"""Command-local individual-pick provenance; no fake chronology from a selected set."""
import sys
import types
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
pkg=types.ModuleType('narwhal3d'); pkg.__path__=[str(ROOT/'narwhal3d')]
sys.modules.setdefault('narwhal3d',pkg)
from narwhal3d import curve_inputs as ci


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.doc=object(); self.seq=ci.PickSequence(); self.stamp=[]
        self.a=ci.WholeCurvePick('z-id','a-content','Zeta',self.doc)
        self.b=ci.WholeCurvePick('a-id','b-content','Alpha',self.doc)
        self.c=ci.WholeCurvePick('m-id','c-content','Middle',self.doc)

    def event(self,pick,selected,action,batch=False):
        previous=self.stamp; self.stamp=[]
        self.seq.observe(pick,selected,self.stamp,action,batch,previous)

    def test_actual_chronology_not_name_or_id_sort(self):
        self.event(self.a,['z-id'],'replace')
        self.event(self.c,['m-id','z-id'],'add')
        self.event(self.b,['a-id','m-id','z-id'],'add')
        self.assertEqual(self.seq.accepted(['a-id','z-id','m-id'],self.stamp),[self.a,self.c,self.b])

    def test_remove_reselect_replace_and_clear(self):
        self.event(self.a,['z-id'],'replace'); self.event(self.b,['z-id','a-id'],'add')
        self.event(self.a,['a-id'],'remove')
        changed=ci.WholeCurvePick('z-id','new-content','Renamed',self.doc)
        self.event(changed,['z-id','a-id'],'add')
        self.assertEqual(self.seq.accepted(['z-id','a-id'],self.stamp),[self.b,changed])
        self.event(self.b,['a-id'],'replace')
        self.assertEqual(self.seq.accepted(['a-id'],self.stamp),[self.b])
        self.event(None,[],'replace'); self.assertEqual(self.seq.accepted([],self.stamp),[])

    def test_repeat_pick_does_not_refresh_stale_content(self):
        self.event(self.a,['z-id'],'replace')
        changed=ci.WholeCurvePick('z-id','changed','Zeta',self.doc)
        self.event(changed,['z-id'],'add')
        self.assertEqual(self.seq.accepted(['z-id'],self.stamp),[self.a])

    def test_batches_and_restored_same_membership_refuse(self):
        self.event(self.a,['z-id','a-id'],'replace',batch=True)
        with self.assertRaisesRegex(ci.CurveInputError,'ambiguous'): self.seq.accepted(['z-id','a-id'],self.stamp)
        self.event(self.a,['z-id'],'replace')
        with self.assertRaisesRegex(ci.CurveInputError,'replaced or restored'): self.seq.accepted(['z-id'],[])
        self.stamp=[]  # External same-membership selection-state replacement.
        self.event(self.b,['z-id','a-id'],'add')
        with self.assertRaisesRegex(ci.CurveInputError,'ambiguous'): self.seq.accepted(['z-id','a-id'],self.stamp)

    def test_untracked_membership_refuses(self):
        self.event(self.a,['z-id'],'replace')
        with self.assertRaisesRegex(ci.CurveInputError,'ambiguous'): self.seq.accepted(['z-id','a-id'],self.stamp)


class IdentityTests(unittest.TestCase):
    def test_uuid_mapping_rename_replacement_and_content(self):
        import rhino3dm as r3
        from narwhal3d.model import NarwhalModel
        model=NarwhalModel()
        model.add_object(r3.LineCurve(r3.Point3d(0,0,0),r3.Point3d(1,0,0)),'same',0)
        model.add_object(r3.LineCurve(r3.Point3d(0,0,0),r3.Point3d(2,0,0)),'same',0)
        owners=list(model.file.Objects); a,b=[str(o.Attributes.Id) for o in owners]
        model.register_blender_object(a,'display-A'); model.register_blender_object(b,'display-B')
        pick=ci.capture_display_pick(model,'display-B')
        self.assertEqual(pick.object_id,b)
        ctx=type('Context',(),{'model':model})()
        owners[1].Attributes.Name='renamed'
        self.assertAlmostEqual(ci.resolve(ctx,pick).PointAtEnd.X,2.)
        replacement=owners[1].Geometry.Duplicate(); replacement.Reverse()
        model.replace_geometry(b,replacement)
        with self.assertRaisesRegex(ci.CurveInputError,'geometry changed'): ci.resolve(ctx,pick)
        model.file.Objects.Delete(b)
        model.add_object(r3.LineCurve(r3.Point3d(0,0,0),r3.Point3d(2,0,0)),'same',0)
        with self.assertRaisesRegex(ci.CurveInputError,'UUID is missing'): ci.resolve(ctx,pick)
        ctx.model=NarwhalModel()
        with self.assertRaisesRegex(ci.CurveInputError,'picked document'): ci.resolve(ctx,pick)


class LegacyRouteTests(unittest.TestCase):
    def _hybrid(self, typed_rail, reuse):
        import rhino3dm as r3
        from unittest.mock import patch
        from narwhal3d import solid_commands as sc, state, bridge
        from narwhal3d.model import NarwhalModel
        model = NarwhalModel()
        geometries = [r3.LineCurve(r3.Point3d(0,0,0), r3.Point3d(10,0,0)),
                      r3.LineCurve(r3.Point3d(0,0,0), r3.Point3d(0,1,0))]
        names = ['RailA', 'SectionB']
        ids = [model.add_object(g, name, 0)[0] for g, name in zip(geometries, names)]
        for oid, name in zip(ids, names):
            model.register_blender_object(oid, name)
        def named(name):
            return next((o.Geometry for o in model.file.Objects if o.Attributes.Name == name), None)
        ctx = types.SimpleNamespace(model=model, geom_by_name=named)
        cmd = sc.Sweep1Cmd()
        app = types.SimpleNamespace(model=model, ctx=ctx, active_command=cmd, doc_path=None)
        refs = [names[0] if typed_rail else ci.capture_display_pick(model, names[0]),
                ci.capture_display_pick(model, names[1]) if typed_rail else names[1]]
        expected = [bridge._freeform_source_hash(g) for g in geometries]
        seen = []
        def build(rails, section):
            seen.append([bridge._freeform_source_hash(rails[0]), bridge._freeform_source_hash(section)])
            self.assertEqual(seen[-1], expected)
            rails[0].Reverse(); section.Reverse()
            self.assertEqual([bridge._freeform_source_hash(model.file.Objects.FindId(oid).Geometry)
                              for oid in ids], expected, 'legacy builders receive detached curves')
            raise RuntimeError('fixture stops after verifying construction inputs')
        with patch.object(state, 'app_state', app):
            for ref in refs:
                cmd._capture_source(ctx, ref)
            cmd.args = [refs[0], [refs[1]]]
            cmd._mouse_used = True
            changed = 0 if typed_rail else 1
            model.file.Objects.FindId(ids[changed]).Attributes.Name = 'Renamed original'
            if reuse:
                model.add_object(r3.LineCurve(r3.Point3d(100,0,0), r3.Point3d(100,20,0)), names[changed], 0)
            with patch.object(cmd, '_build', build):
                cmd.execute(ctx)
        self.assertEqual(len(seen), 1)

    def test_both_hybrid_directions_keep_validated_uuid_curves(self):
        for typed_rail in (True, False):
            for reuse in (True, False):
                with self.subTest(typed_rail=typed_rail, reuse=reuse):
                    self._hybrid(typed_rail, reuse)

    def test_component_mode_preserves_legacy_without_weakening_new_sequence(self):
        from narwhal3d.solid_commands import Sweep1Cmd
        cmd = Sweep1Cmd()
        self.assertTrue(cmd.mouse_selection_active())
        self.assertTrue(cmd.begin_component_pick())
        self.assertFalse(cmd.mouse_selection_active())
        other = Sweep1Cmd(); other._mouse_used = True
        self.assertFalse(other.begin_component_pick())
        self.assertTrue(other.mouse_selection_active())
        empty = Sweep1Cmd(); empty._mouse_used = True
        result = empty.execute(types.SimpleNamespace())
        self.assertIn('no cross-section', result[0])

    def test_mutated_name_reresolution_is_detected(self):
        import inspect, textwrap
        from unittest.mock import patch
        from narwhal3d.solid_commands import _SweepCmd
        original = _SweepCmd.execute
        source = textwrap.dedent(inspect.getsource(original))
        anchor = 'got = self._curves(ctx) if resolved is None else resolved'
        self.assertEqual(source.count(anchor), 1)
        namespace = {}
        exec(source.replace(anchor, 'got = self._curves(ctx)'), original.__globals__, namespace)
        with patch.object(_SweepCmd, 'execute', namespace['execute']):
            with self.assertRaises(AssertionError):
                self._hybrid(True, True)
            with self.assertRaises(AssertionError):
                self._hybrid(False, True)
        self._hybrid(True, True)
        self._hybrid(False, True)
        with patch.object(ci, '_detached', lambda curve: curve):
            with self.assertRaises(AssertionError):
                self._hybrid(True, False)
        self._hybrid(True, False)


if __name__=='__main__': unittest.main(verbosity=2)
