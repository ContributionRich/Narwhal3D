"""Actual Blender command tolerance, exact closure, refusal and publication faults.
Python is required for Blender/document/history callbacks; geometry stays native.
"""
import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import bpy
import rhino3dm as r3
ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("narwhal3d")
package.__path__ = [str(ROOT / "narwhal3d")]
sys.modules.setdefault("narwhal3d", package)
from narwhal3d import bridge, kernel, commands, model, solid_commands
from narwhal3d.kernel import occt_backend as backend


class CapCommand(unittest.TestCase):
    def context(self, tolerance=.01, nonplanar=False):
        bpy.data.batch_remove(list(bpy.data.objects))
        ctx = commands.NarwhalContext(model.NarwhalModel())
        ctx.model.set_absolute_tolerance(tolerance)
        if nonplanar:
            geometry = bridge.make_srf_pt([(0,0,0),(2,0,0),(2,2,1),(0,2,0)])
        else:
            recipe = dict(kind="cylinder",radius=2,height=3)
            index,face = next((i,f) for i,f in enumerate(backend.unique_faces(backend.from_recipe(recipe)),1)
                              if backend.face_types(f)=={"CYLINDER":1})
            from OCP.BRep import BRep_Builder
            from OCP.TopAbs import TopAbs_VERTEX
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS
            explorer=TopExp_Explorer(face,TopAbs_VERTEX)
            while explorer.More():
                BRep_Builder().UpdateVertex(TopoDS.Vertex(explorer.Current()),.005)
                explorer.Next()
            geometry=bridge.occt_backed(face,backend.native_master_recipe(face))
        ctx.model.add_object(geometry,"Source",0)
        ctx.rebuild_display()
        return ctx

    def run_cap(self,ctx):
        command=solid_commands.CapCmd()
        command.args=[["Source"]]
        ctx.undo.begin("Cap")
        try:
            return command.execute(ctx)
        finally:
            ctx.undo.end()

    def test_document_tolerance_and_exact_undo_redo(self):
        for tolerance in (.01,.02):
            with self.subTest(tolerance=tolerance):
                ctx=self.context(tolerance)
                message,_=self.run_cap(ctx)
                self.assertIn("Capped 1",message)
                obj=list(ctx.model.file.Objects)[0]
                tree=kernel.recipe_of(obj.Geometry)
                self.assertEqual(tree["tolerance"],tolerance)
                self.assertTrue(backend.is_airtight(kernel.master_of(obj.Geometry)))
                self.assertEqual(backend.face_types(kernel.master_of(obj.Geometry)),{"CYLINDER":1,"PLANE":2})
                ctx.undo_last()
                self.assertIsNotNone(ctx.geom_by_name("Source"))
                ctx.redo_last()
                self.assertEqual(ctx.model.object_count(),1)
                self.assertEqual(kernel.recipe_of(list(ctx.model.file.Objects)[0].Geometry)["tolerance"],tolerance)

    def test_tight_tolerance_and_nonplanar_refusals_leave_no_undo(self):
        for tolerance,nonplanar in [(.001,False),(.01,True)]:
            ctx=self.context(tolerance,nonplanar)
            before=ctx.geom_by_name("Source").Encode()
            message,_=self.run_cap(ctx)
            self.assertIn("Unable to cap",message)
            self.assertEqual(ctx.geom_by_name("Source").Encode(),before)
            self.assertEqual(ctx.undo.undo_names(),[])

    def test_display_failure_preserves_source_and_history(self):
        ctx=self.context()
        before=ctx.geom_by_name("Source").Encode()
        with patch.object(ctx,"_make_display_object",side_effect=RuntimeError("owned display fault")):
            try:
                self.run_cap(ctx)
            except RuntimeError:
                pass
        self.assertIsNotNone(ctx.geom_by_name("Source"),"Cap must stage before consuming its source")
        self.assertEqual(ctx.geom_by_name("Source").Encode(),before)
        self.assertEqual(ctx.model.object_count(),1)
        self.assertEqual(ctx.undo.undo_names(),[])

    def test_display_callback_edits_are_preserved_by_scoped_rollback(self):
        for edit_source in (False,True):
            ctx=self.context()
            original=ctx._make_display_object
            def callback(*args,**kwargs):
                value=original(*args,**kwargs)
                if edit_source:
                    ctx.geom_by_name("Source").SetUserString("callback","preserved")
                else:
                    ctx.model.add_object(r3.Point(r3.Point3d(50,0,0)),"External",0)
                raise RuntimeError("owned callback fault")
            with patch.object(ctx,"_make_display_object",side_effect=callback):
                message,_=self.run_cap(ctx)
            self.assertIn("publication failed",message)
            self.assertIsNotNone(ctx.geom_by_name("Source"))
            self.assertFalse(any(o.Attributes.Name.startswith("Capped") for o in ctx.model.file.Objects))
            if edit_source:
                self.assertEqual(ctx.geom_by_name("Source").GetUserString("callback"),"preserved")
            else:
                self.assertIsNotNone(ctx.geom_by_name("External"))
            self.assertEqual(ctx.undo_last(),"External change during Cap")
            self.assertEqual(ctx.model.object_count(),1)
            self.assertIsNotNone(ctx.geom_by_name("Source"))

    def test_command_producer_mutations_red_restored_green(self):
        import inspect
        import textwrap
        source=textwrap.dedent(inspect.getsource(solid_commands.CapCmd.execute))
        old="tolerance=commands._document_tolerance(ctx)"
        self.assertEqual(source.count(old),1)
        namespace=dict(solid_commands.__dict__)
        exec(compile(source.replace(old,"tolerance=1e-6",1),'<cap-command-tolerance-mutation>','exec'),namespace)
        with patch.object(solid_commands.CapCmd,'execute',namespace['execute']):
            red=unittest.TestResult()
            CapCommand('test_document_tolerance_and_exact_undo_redo').run(red)
            self.assertTrue(red.failures,'omitted document tolerance must fail behavioral assertions')
            self.assertFalse(red.errors)
        green=unittest.TestResult()
        CapCommand('test_document_tolerance_and_exact_undo_redo').run(green)
        self.assertTrue(green.wasSuccessful(),str(green.failures+green.errors))
        source=inspect.getsource(solid_commands._replace_cap)
        old='    staged = None'
        self.assertEqual(source.count(old),1)
        namespace=dict(solid_commands.__dict__)
        exec(compile(source.replace(old,old+'\n    file.Objects.Delete(source_id)',1),
                     '<cap-consume-before-stage-mutation>','exec'),namespace)
        with patch.object(solid_commands,'_replace_cap',namespace['_replace_cap']):
            with self.assertRaises(AssertionError):
                self.test_display_failure_preserves_source_and_history()
        self.test_display_failure_preserves_source_and_history()

    def test_generic_bridge_forwards_tolerance_and_never_facets_exact_refusal(self):
        ctx=self.context()
        source=ctx.geom_by_name("Source")
        with patch.object(bridge,"cap_planar_holes_occt",wraps=bridge.cap_planar_holes_occt) as call:
            result=bridge.cap_planar_holes(source,tolerance=.01)
            self.assertTrue(result.brep.IsSolid)
            call.assert_called_once_with(source,tolerance=.01)
        with patch.object(bridge,"cap_planar_holes_occt",return_value=None):
            with self.assertRaisesRegex(ValueError,"no faceted fallback"):
                bridge.cap_planar_holes(source,tolerance=.001)


if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CapCommand))
    if not result.wasSuccessful():
        raise RuntimeError("Cap command checks failed")
