"""Actual SDK glue qualification; numeric validation is tested in Rust."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ai_modeling_host import ModelingHost, step, ai_modeling, kernel, model
import unittest
import tempfile

class SurfaceHost(ModelingHost):
    def test_surface_exact_net_edit_roundtrip(self):
        from math import sqrt
        from narwhal3d.kernel import occt_backend as occt
        w=sqrt(0.5)
        action=dict(op='nurbs_surface',degree_u=2,degree_v=1,
            points=[[[1,0,0,1],[1,0,3,1]],[[w,w,0,w],[w,w,3*w,w]],[[0,1,0,1],[0,1,3,1]]],
            knots_u=[0,0,0,1,1,1],knots_v=[0,0,1,1])
        patch=dict(op='nurbs_surface',degree_u=2,degree_v=2,
            points=[[[u,v,(u-1)*(v-1),1] for v in range(3)] for u in range(3)],
            knots_u=[0,0,0,1,1,1],knots_v=[0,0,0,1,1,1])
        patch_rows=self.apply([step('curved_patch',patch)])
        self.assertFalse(patch_rows[0]['solid'])
        self.assertEqual(patch_rows[0]['surface_data']['degree_v'],2)
        rows=self.apply([step('quarter',action)])
        oid=rows[0]['object_id']
        self.assertFalse(rows[0]['solid'])
        self.assertFalse(rows[0]['airtight'])
        data=ai_modeling.inspect_object(self.app,oid)['surface_data']
        self.assertTrue(data['available'])
        self.assertEqual(data['points'],action['points'])
        surface=self.m.file.Objects.FindId(oid).Geometry.Surfaces[0]
        # Independent constants at the middle of the exact rational quarter circle.
        p=surface.PointAt(0.5,0.5)
        self.assertAlmostEqual(p.X,0.7071067811865476,places=12)
        self.assertAlmostEqual(p.Y,0.7071067811865476,places=12)
        self.assertAlmostEqual(p.Z,1.5,places=12)
        edit={k:data[k] for k in ('degree_u','degree_v','points','knots_u','knots_v')}
        edit['op']='nurbs_surface'
        edit['points'][1][1][2]+=w
        rows=self.apply([step('edit_patch',edit,replace=oid)])
        self.assertEqual(rows[0]['object_id'],oid)
        z=self.m.file.Objects.FindId(oid).Geometry.Surfaces[0].PointAt(.5,.5).Z
        self.assertGreater(z,1.6)
        self.ctx.undo_last()
        self.assertAlmostEqual(self.m.file.Objects.FindId(oid).Geometry.Surfaces[0].PointAt(.5,.5).Z,1.5)
        self.ctx.redo_last()
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'surface.3dm')
            self.assertTrue(self.m.save(path))
            kernel.forget_masters()
            reopened=model.NarwhalModel.load(path)
            geom=reopened.file.Objects.FindId(oid).Geometry
            self.assertAlmostEqual(geom.Surfaces[0].PointAt(.5,.5).Z,z)
            self.assertFalse(ai_modeling.inspect_geometry(geom,.001)['solid'])
            shape=kernel.master_of(geom) or occt.from_recipe(kernel.recipe_of(geom))
            dest=str(Path(folder)/'surface.step')
            self.assertTrue(occt.export_step(shape,dest))
            recovered=occt.import_step(dest)['shells'][0]
            self.assertFalse(occt.is_airtight(recovered))
            self.assertEqual(sum(occt.face_types(recovered).values()),1)

if __name__=='__main__':
    suite=unittest.TestSuite([SurfaceHost('test_surface_exact_net_edit_roundtrip')])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
