"""Genuine bounded Freeform: exact inputs, independent geometry, production round trips.

Bundled plain Python -B; select the new native library with NARWHAL_GEOMETRY_CORE.
No UI activation or command registration. The .narwhal reader uses the real worker.
"""
import copy
import ctypes
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_surfacing_probe import fixture, ob
from narwhal3d import bridge, kernel, native_geometry as ng, surfacing_backend as sb
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.Geom import Geom_TrimmedCurve
from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve, GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt, gp_Vec
import rhino3dm as r3
from test_surfacing_interior import assert_interior_equal, change_only_interior

MATRIX=[1.2,0,0,13, 0,.8,0,-4, 0,0,1.5,2, 0,0,0,1]


def build(inputs=None, **kw):
    rail,sections,stations=fixture() if inputs is None else inputs
    return sb.sweep1_freeform_approx(rail,sections,stations,**kw)


def exact_curve(recipe):
    return BRepAdaptor_Curve(ob._unique_edges(ob.wire_from_curve(recipe))[0])


def surface(shape):
    assert ob.is_valid(shape) and not ob.is_airtight(shape)
    assert ob.face_types(shape)=={'BSPLINE':1}
    assert len(ob._unique_edges(shape))==4
    s=BRep_Tool.Surface_s(ob.unique_faces(shape)[0])
    assert s.UDegree()==2 and s.VDegree()>=2
    assert s.IsURational() or s.IsVRational()
    assert s.NbUKnots()==2
    assert all(s.VDegree()-s.VMultiplicity(i)>=2 for i in range(2,s.NbVKnots()))
    return s


def placed(p, matrix=None):
    if matrix is None: return p
    xyz=p.Coord()
    return gp_Pnt(*[sum(matrix[4*k+j]*xyz[j] for j in range(3))+matrix[4*k+3] for k in range(3)])


def verify(shape, inputs=None, matrix=None):
    rail,sections,stations=fixture() if inputs is None else inputs
    s=surface(shape)
    worst=0.
    for recipe,t in zip(sections,stations):
        cc=exact_curve(recipe)
        iso=s.VIso(t)
        for i in range(129):
            native=cc.FirstParameter()+(cc.LastParameter()-cc.FirstParameter())*i/128
            expected=placed(cc.Value(native),matrix)
            # Pin correspondence to the requested V iso, not any point elsewhere
            # on the surface. Arc conversion may change U parameterization exactly.
            worst=max(worst,GeomAPI_ProjectPointOnCurve(expected,iso).LowerDistance())
            assert GeomAPI_ProjectPointOnSurf(expected,s).LowerDistance()<2e-7
    rc=exact_curve(rail)
    rail_error=max(placed(rc.Value(rc.FirstParameter()+(rc.LastParameter()-rc.FirstParameter())*i/256),matrix).Distance(
        s.Value(0,rc.FirstParameter()+(rc.LastParameter()-rc.FirstParameter())*i/256)) for i in range(257))
    assert worst<2e-7,('section iso agreement',worst)
    assert rail_error<2e-7,('rail agreement',rail_error)
    return dict(section_iso_error=worst,rail_error=rail_error)


def publish(result):
    # Isolated example of Main's existing exact-publication sequence; no duplicate
    # document/command store and no mesh fallback, even when writer is unavailable.
    geom=bridge._onbrep().exact_brep(result.shape,ob)
    assert geom is not None and geom.IsValid and not geom.IsSolid
    assert geom.SetUserString(kernel.DOCUMENT_FORM_KEY,'exact')
    assert kernel.record_recipe(geom,result.recipe)
    kernel.register_master(geom,result.shape)
    assert kernel.honesty_problem(geom) is None
    return geom


def transform(matrix):
    xf=r3.Transform.Identity()
    for i in range(4):
        for j in range(4): setattr(xf,f'M{i}{j}',matrix[4*i+j])
    return xf


def verify_document(geom,matrix=None):
    assert geom.IsValid and not geom.IsSolid and len(geom.Faces)==1
    assert geom.GetUserString(kernel.DOCUMENT_FORM_KEY)=='exact'
    assert kernel.honesty_problem(geom) is None
    master=kernel.master_of(geom); assert master is not None
    # Expected construction comes from the independent test inputs, never from
    # this object's saved recipe or proxy. Transform its coefficients independently.
    expected=build().shape
    replay=ob.from_recipe(kernel.recipe_of(geom))
    proxy=ob.shape_from_brep_data(bridge.brep_data(geom))
    for shape in (master,replay,proxy):
        verify(shape,matrix=matrix)
        assert_interior_equal(shape,expected,ob,matrix)


def arc_inputs():
    rail=dict(ctype='arc',center=[0,0,0],normal=[0,0,1],xaxis=[1,0,0],radius=10.,start=.2,end=1.2)
    stations=[.2,.65,1.2]; sections=[]
    for t,radius in zip(stations,[1.,1.4,.8]):
        radial=[math.cos(t),math.sin(t),0.]
        anchor=[10*x for x in radial]
        # In the normal plane, quarter arc starts at rail, tangent radially out.
        sections.append(dict(ctype='arc',center=[anchor[0],anchor[1],radius],
                             normal=[-math.sin(t),math.cos(t),0.],xaxis=[0,0,-1],
                             radius=radius,start=0.,end=math.pi/2))
    return rail,sections,stations


def count_inputs(count, update=0):
    rail,_,_=fixture(); curve=exact_curve(rail)
    stations=[i/(count-1) for i in range(count)]; sections=[]
    for t in stations:
        p,tan=gp_Pnt(),gp_Vec(); curve.D1(t,p,tan)
        x=tan.Crossed(gp_Vec(0,1,0)).Normalized(); y=tan.Normalized().Crossed(x)
        radius=1.+.3*t+update*.01*(1.+t)
        poles=[p,p.Translated(x.Multiplied(radius)),p.Translated(x.Added(y).Multiplied(radius))]
        sections.append(dict(ctype='nurbs',degree=2,knots=[0,0,1,1],weights=[1,math.sqrt(.5),1],
                             poles=[list(q.Coord()) for q in poles]))
    return rail,sections,stations


def benchmark(count):
    import resource
    times=[]; reports=[]
    for i in range(6):
        inputs=count_inputs(count,i)
        start=time.perf_counter(); result=build(inputs); times.append(time.perf_counter()-start)
        reports.append(result.report)
        verify(result.shape,inputs)
    print(json.dumps(dict(sections=count,cold_seconds=times[0],distinct_warm_seconds=times[1:],
                          peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          patches=[r['patches'] for r in reports],
                          proof_cells=[r['proof_cells'] for r in reports])))


class FreeformTests(unittest.TestCase):
    def test_section_count_scaling(self):
        for count in [2,4]:
            inputs=count_inputs(count)
            verify(build(inputs).shape,inputs)
    def test_nonplanar_accuracy(self):
        before=copy.deepcopy(fixture()); result=build(before)
        evidence=verify(result.shape,before)
        self.assertEqual(before,fixture())
        self.assertGreater(result.report['min_cell_determinant'],0.)
        self.assertGreater(result.report['proof_cells'],0)
        self.assertEqual(result.report['continuity'],2)
        self.assertLess(result.report['fit_error'],1e-7)
        self.assertLess(result.report['section_error'],1e-7)
        self.assertLess(result.report['law_error'],1e-7)
        print('MEASURED Freeform',json.dumps(dict(evidence,**result.report)))

    def test_true_moving_frame_and_c2(self):
        s=surface(build().shape); rail=exact_curve(fixture()[0])
        error=0.; jump=0.
        for j in range(65):
            t=j/64; p,tan=gp_Pnt(),gp_Vec(); rail.D1(t,p,tan); tan.Normalize()
            for i in range(17):
                error=max(error,abs(gp_Vec(p,s.Value(i/16,t)).Dot(tan)))
        for k in range(2,s.NbVKnots()):
            t=s.VKnot(k); delta=min(t-s.VKnot(k-1),s.VKnot(k+1)-t)*1e-7
            for i in range(17):
                normals=[]
                for v in [t-delta,t+delta]:
                    p,du,dv=gp_Pnt(),gp_Vec(),gp_Vec(); s.D1(i/16,v,p,du,dv)
                    self.assertGreater(du.Crossed(dv).Magnitude(),1e-6)
                    normals.append(du.Crossed(dv))
                jump=max(jump,normals[0].Angle(normals[1]))
        self.assertLess(error,1e-7)
        self.assertLess(jump,1e-5)
        print('MEASURED normal-plane error / knot normal jump',error,jump)

    def test_exact_arcs_and_domains(self):
        inputs=arc_inputs(); verify(build(inputs).shape,inputs)
        # The existing serializer is allowed to recognize exact arcs as arcs.
        rail,secs,ts=fixture()
        secs=[bridge.curve_recipe(bridge.curve_from_recipe(s)) for s in secs]
        self.assertTrue(all(s['ctype']=='arc' for s in secs))
        verify(build((rail,secs,ts)).shape,(rail,secs,ts))
        rail,secs,ts=fixture()
        rail['knots']=[2,2,2,7,7,7]; ts=[2+5*t for t in ts]
        for sec in secs: sec['knots']=[-9,-9,-3,-3]
        verify(build((rail,secs,ts)).shape,(rail,secs,ts))

    def test_reversal_and_order(self):
        rail,sections,ts=fixture()
        original=copy.deepcopy(sections)
        sections[1]['poles'].reverse(); sections[1]['knots']=[-1,-1,0,0]
        result=build((rail,sections,ts),reverse=[False,True,False])
        verify(result.shape,(rail,original,ts))
        with self.assertRaisesRegex(kernel.KernelError,'placement'): build((rail,sections,ts))
        with self.assertRaisesRegex(kernel.KernelError,'order'): build((rail,original,list(reversed(ts))))
        with self.assertRaisesRegex(kernel.KernelError,'order'): build((rail,original,[0,.01,1]))

    def test_named_refusals_and_no_mutation(self):
        rail,sections,ts=fixture()
        q=copy.deepcopy(sections); q[1]['poles'][0][0]+=.1
        before=kernel.master_registry_snapshot()
        with self.assertRaisesRegex(kernel.KernelError,'placement'): build((rail,q,ts))
        self.assertEqual(kernel.master_registry_snapshot(),before)
        with self.assertRaisesRegex(kernel.KernelError,'domain'): build((rail,sections,[0,.45,.9]))
        closed=dict(rail,closed=True)
        with self.assertRaisesRegex(kernel.KernelError,'seam'): build((closed,sections,ts))
        poly=dict(ctype='polyline',points=[[0,0,0],[0,0,4],[4,0,9]])
        with self.assertRaisesRegex(kernel.KernelError,'curve'): build((poly,sections,ts))
        degenerate=dict(rail,poles=[[0,0,0]]*4)
        with self.assertRaises(kernel.KernelError): build((degenerate,sections,ts))
        with self.assertRaisesRegex(kernel.KernelError,'tolerance'): build(tolerance=float('nan'))
        q=copy.deepcopy(sections); q[1]['weights'][1]=.5
        with self.assertRaisesRegex(kernel.KernelError,'weights'): build((rail,q,ts))

    def test_fold_refusal(self):
        rail,sections,ts=fixture()
        # 180-degree reversal about the rail at the middle section: same anchor,
        # same normal plane, but interpolation collapses/folds between stations.
        anchor=sections[1]['poles'][0]
        sections[1]['poles']=[[2*anchor[k]-p[k] for k in range(3)] for p in sections[1]['poles']]
        with self.assertRaisesRegex(kernel.KernelError,'folding'): build((rail,sections,ts))

    def test_interior_only_corruption(self):
        result=build()
        changed,witness=change_only_interior(result.shape,ob)
        verify(changed)  # The old section/rail-only oracle deliberately passes.
        with self.assertRaisesRegex(AssertionError,'interior: pole error'):
            assert_interior_equal(changed,result.shape,ob)
        corrupted=publish(sb.SweepSurface(changed,result.recipe,result.report))
        with self.assertRaisesRegex(AssertionError,'interior: pole error'):
            verify_document(corrupted)
        verify_document(publish(result))
        print('RED interior-only corruption / restored GREEN; displacement witness',witness)

    def test_recipe_transforms_and_sources(self):
        result=build(); geom=publish(result)
        before=geom.Encode(),kernel.recipe_of(geom),kernel.master_of(geom)
        moved=bridge.transform_geometry(geom.Duplicate(),transform(MATRIX))
        verify_document(moved,MATRIX)
        self.assertEqual((geom.Encode(),kernel.recipe_of(geom),kernel.master_of(geom)),before)
        verify_document(geom)
        rigid=[0,-1,0,5,1,0,0,-2,0,0,1,7,0,0,0,1]
        verify_document(bridge.transform_geometry(geom.Duplicate(),transform(rigid)),rigid)
        # Transform exact inputs before construction, not merely the result.
        r,secs,ts=fixture()
        for c in [r]+secs:
            c['poles']=[list(placed(gp_Pnt(*p),rigid).Coord()) for p in c['poles']]
        verify(build((r,secs,ts)).shape,matrix=rigid)

    def test_nested_operations_and_step(self):
        result=build()
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
        for folded_direction in ([0,0,.2],[.13,.27,.09]):
            with self.assertRaisesRegex(kernel.KernelError,'self-intersecting'):
                ob.make_prism(result.shape,folded_direction)
        direction=[-.13,.27,.09]
        prism=ob.make_prism(result.shape,direction)
        self.assertTrue(ob.is_valid(prism)); self.assertTrue(ob.is_airtight(prism))
        self.assertTrue(BRepAlgoAPI_Check(prism,True,True).IsValid())
        self.assertGreater(ob.volume(prism),0.)
        recipe=dict(kind='prism',a=result.recipe,dir=direction,solid=True)
        self.assertAlmostEqual(ob.volume(ob.from_recipe(recipe)),ob.volume(prism),places=8)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'freeform.step'; ob.export_step(prism,str(target))
            self.assertIn('B_SPLINE_SURFACE',target.read_text())

    def test_one_undoable_operation(self):
        from narwhal3d.model import NarwhalModel
        from narwhal3d.undo import UndoStack
        model=NarwhalModel(); stack=UndoStack()
        rail,secs,_=fixture()
        for i,c in enumerate([rail]+secs): model.add_object(bridge.curve_from_recipe(c),'source'+str(i),0)
        sources=[obj.Geometry.Encode() for obj in model.file.Objects]
        stack.begin('Sweep1'); stack.capture(model.snapshot())
        model.add_object(publish(build()),'Freeform sweep',0)
        self.assertTrue(stack.end()); self.assertEqual(stack.undo_names(),['Sweep1'])
        self.assertEqual(model.object_count(),5)
        record=stack.undo(model.snapshot()); model.restore(record.before)
        self.assertEqual(model.object_count(),4)
        self.assertEqual([o.Geometry.Encode() for o in model.file.Objects],sources)
        record=stack.redo(model.snapshot()); model.restore(record.after)
        self.assertEqual(model.object_count(),5)
        geom=list(model.file.Objects)[-1].Geometry
        if kernel.master_of(geom) is None: self.assertTrue(bridge.rehydrate_master(geom))
        verify_document(geom)

    def test_real_fresh_process_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            for extension in ['.narwhal','.3dm']:
                path=str(Path(tmp)/('freeform'+extension))
                for mode in ['--write','--read']:
                    run=subprocess.run([sys.executable,'-B',__file__,mode,path],capture_output=True,text=True,timeout=120)
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    if run.stdout: print(run.stdout.strip())

    def test_older_library_and_buffer_contract(self):
        class Old:
            def narwhal_geometry_core_capabilities(self): return 127
        with patch.object(ng,'_load_locked',return_value=type('Binding',(),{'library':Old()})()):
            self.assertFalse(ng.sweep1_freeform_available())
            with self.assertRaisesRegex(kernel.KernelError,'capability unavailable'): build()
        r,s,p=fixture(); data=ob.freeform_inputs_bytes(r,s)
        ng.sweep1_freeform_approx(data,p,[False]*3,1e-7)
        fn=ng._load_locked().library.narwhal_geometry_core_sweep1_freeform_approx_v1
        source=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
        stations=(ctypes.c_double*6)(0,0,.45,0,1,0); out=(ctypes.c_ubyte*8)(*[93]*8); report=ng.FreeformReport()
        needed=fn(source,len(data),stations,3,1e-7,out,8,ctypes.byref(report))
        self.assertGreater(needed,8); self.assertEqual(list(out),[93]*8)
        stations[2]=float('nan')
        self.assertLess(fn(source,len(data),stations,3,1e-7,out,8,ctypes.byref(report)),0)
        self.assertEqual(list(out),[93]*8)


def document_process(mode,path):
    from narwhal3d import narwhal_document as nd
    from narwhal3d.model import NarwhalModel
    if mode=='--write':
        geom=bridge.transform_geometry(publish(build()),transform(MATRIX))
        verify_document(geom,MATRIX)
        model=NarwhalModel(); model.add_object(geom,'Freeform',0)
        if path.endswith('.narwhal'): assert nd.save_model(model,path)
        else: assert model.file.Write(path,8)
    else:
        bridge.occt_backed(ob.make_box(1,2,3),dict(kind='box',dx=1,dy=2,dz=3))
        if path.endswith('.narwhal'):
            model=nd.load_model(path); geom=list(model.file.Objects)[0].Geometry
            assert kernel.master_of(geom) is None; nd.commit_masters(model)
        else:
            doc=r3.File3dm.Read(path); geom=list(doc.Objects)[0].Geometry
            assert kernel.master_of(geom) is None; assert bridge.rehydrate_master(geom)
        verify_document(geom,MATRIX)
        print('PASS fresh-process Freeform',Path(path).suffix,'real master/proxy/recipe')


if __name__=='__main__':
    if '--benchmark' in sys.argv: benchmark(int(sys.argv[-1]))
    elif '--write' in sys.argv or '--read' in sys.argv: document_process(sys.argv[-2],sys.argv[-1])
    else: unittest.main(verbosity=2)
