"""Isolated executable backend + existing bridge persistence evidence, no GUI.

Run with NARWHAL_GEOMETRY_CORE pointing at the newly built ABI-4 library.
--mutations runs deliberately broken behavior in fresh child processes.
"""
import copy
import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_surfacing_probe import fixture, ob, ROOT
from narwhal3d import bridge, kernel, native_geometry as ng, surfacing_backend as sb
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt, gp_Vec
import rhino3dm as r3

AXIS = [0., 0., 1.]
ACROSS = [math.sqrt(.5), math.sqrt(.5), 0.]


def build(rail=None, sections=None, parameters=None, **kw):
    r, s, p = fixture(parallel=True)
    return sb.sweep1_parallel(r if rail is None else rail, s if sections is None else sections,
                             p if parameters is None else parameters,
                             axis=kw.pop('axis', AXIS), across=kw.pop('across', ACROSS), **kw)


def surface(shape):
    assert ob.is_valid(shape)
    assert not ob.is_airtight(shape)
    assert ob.face_types(shape) == {'BSPLINE': 1}
    assert len(ob._unique_edges(shape)) == 4
    s = BRep_Tool.Surface_s(ob.unique_faces(shape)[0])
    assert (s.UDegree(), s.VDegree(), s.NbUKnots(), s.NbVKnots()) == (2, 3, 2, 2)
    # OCCT names rationality for the iso-curve family, not the varying coordinate.
    # MEASURED IsURational=False, IsVRational=True for these U-varying weights.
    assert s.IsURational() or s.IsVRational()
    assert abs(s.Weight(2,1)-math.sqrt(.5))<1e-14
    return s


def curve_value(recipe, t):
    # Independent closed-form Bernstein evaluation, not native implementation data.
    n = recipe['degree']
    coefficients = [math.comb(n,i) * t**i * (1-t)**(n-i) * w
                    for i,w in enumerate(recipe['weights'])]
    return [sum(b*p[k] for b,p in zip(coefficients, recipe['poles'])) / sum(coefficients)
            for k in range(3)]


def verify(shape, matrix=None):
    s = surface(shape)
    rail, sections, stations = fixture(parallel=True)
    def placed(p):
        if matrix is None:
            return p
        return [sum(matrix[4*k+i]*p[i] for i in range(3))+matrix[4*k+3] for k in range(3)]
    section_error = 0.
    projected_error = 0.
    for section,t in zip(sections, stations):
        for i in range(129):
            p = placed(curve_value(section,i/128))
            section_error = max(section_error, math.dist(p, s.Value(i/128,t).Coord()))
            projected_error = max(projected_error, GeomAPI_ProjectPointOnSurf(gp_Pnt(*p),s).LowerDistance())
    rail_error = max(math.dist(placed(curve_value(rail,i/256)),s.Value(0,i/256).Coord())
                     for i in range(257))
    assert section_error < 1e-7, ('section agreement', section_error)
    assert projected_error < 1e-7, ('independent projection', projected_error)
    assert rail_error < 1e-7, ('rail agreement', rail_error)
    return dict(section_error=section_error, projection_error=projected_error, rail_error=rail_error)


def document_geometry(result):
    # Existing integration only. No new document store and no triangle fallback here.
    geom = bridge._proxy_from_occt(result.shape)
    assert geom.GetUserString(kernel.DOCUMENT_FORM_KEY) == 'exact', bridge.last_document_form()
    kernel.register_master(geom,result.shape)
    kernel.record_recipe(geom,result.recipe)
    assert kernel.honesty_problem(geom) is None
    return geom


MATRIX = [0,-1,0,11, 1,0,0,-7, 0,0,1,3, 0,0,0,1]
NONUNIFORM = [1.5,0,0,11, 0,.75,0,-7, 0,0,2,3, 0,0,0,1]


def write_document(path, nonuniform=False):
    result=build()
    if nonuniform:
        recipe=copy.deepcopy(result.recipe); recipe['xform']=NONUNIFORM
        result=sb.SweepSurface(ob.transform(result.shape,NONUNIFORM),recipe,result.report)
        geom=document_geometry(result)
        verify(kernel.master_of(geom),NONUNIFORM)
        doc=r3.File3dm(); doc.Objects.AddBrep(geom,r3.ObjectAttributes())
        assert doc.Write(path,8)
        return
    geom = document_geometry(result)
    xf = r3.Transform.Identity()
    for i in range(4):
        for j in range(4):
            setattr(xf, f'M{i}{j}', MATRIX[4*i+j])
    assert bridge.transform_geometry(geom,xf)
    verify(kernel.master_of(geom), MATRIX)
    doc = r3.File3dm()
    doc.Objects.AddBrep(geom,r3.ObjectAttributes())
    assert doc.Write(path,8)


def read_document(path, nonuniform=False):
    doc = r3.File3dm.Read(path)
    geom = list(doc.Objects)[0].Geometry
    assert kernel.master_of(geom) is None
    assert bridge.rehydrate_master(geom)
    assert kernel.honesty_problem(geom) is None
    verify(kernel.master_of(geom),NONUNIFORM if nonuniform else MATRIX)
    assert len(geom.Faces)==1 and not geom.IsSolid
    print('PASS fresh-process exact document + transformed recipe reload')


class SweepTests(unittest.TestCase):
    def test_geometry(self):
        start = time.perf_counter()
        result = build()
        elapsed = time.perf_counter()-start
        evidence = verify(result.shape)
        self.assertLess(result.report['section_control_error_bound'],1e-10)
        self.assertGreater(result.report['min_axial_increment'],0)
        self.assertGreater(result.report['min_transverse_increment'],0)
        self.assertGreater(result.report['projected_jacobian_lower_bound'],0)
        self.assertLess(evidence['section_error'],result.report['section_control_error_bound'])
        print('MEASURED',json.dumps(dict(evidence, elapsed_seconds=elapsed, **result.report)))

    def test_continuity(self):
        s = surface(build().shape)
        min_jacobian = math.inf
        max_normal_jump = 0.
        for i in range(33):
            u = i/32
            for j in range(33):
                p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
                s.D1(u,j/32,p,du,dv)
                min_jacobian = min(min_jacobian,du.Crossed(dv).Magnitude())
            normals=[]
            for t in [.45-1e-8,.45+1e-8]:
                p,du,dv=gp_Pnt(),gp_Vec(),gp_Vec()
                s.D1(u,t,p,du,dv)
                normals.append(du.Crossed(dv).Normalized())
            max_normal_jump=max(max_normal_jump,normals[0].Angle(normals[1]))
        self.assertGreater(min_jacobian,1.)
        self.assertLess(max_normal_jump,1e-6)
        print('MEASURED continuity',min_jacobian,max_normal_jump,
              '(33x33 Jacobian grid; 33 two-sided normals; not certified sampled bounds)')

    def test_orientations_and_order(self):
        r,s,p=fixture(parallel=True)
        s[1]['poles'].reverse()
        verify(build(sections=s,reverse=[False,True,False]).shape)
        with self.assertRaisesRegex(kernel.KernelError,'anchor'):
            build(sections=s)
        with self.assertRaisesRegex(kernel.KernelError,'order'):
            build(sections=list(reversed(s)),parameters=list(reversed(p)))
        with self.assertRaisesRegex(kernel.KernelError,'order'):
            build(parameters=[0.,.01,1.])
        with self.assertRaisesRegex(kernel.KernelError,'order'):
            build(parameters=[0.,0.,1.])

    def test_invalid_inputs(self):
        r,s,p=fixture(parallel=True)
        cases=[]
        for target,value in [('anchor',.1),('plane',.1),('nan',math.nan),('degenerate',0.)]:
            q=copy.deepcopy(s)
            if target=='anchor': q[1]['poles'][0][0]+=value
            elif target=='plane': q[1]['poles'][1][2]+=value
            elif target=='nan': q[1]['poles'][1][0]=value
            else: q[1]['poles']=[q[1]['poles'][0]]*3
            cases.append(q)
        for q in cases:
            original=repr(q)
            with self.assertRaises(kernel.KernelError): build(sections=q)
            self.assertEqual(repr(q),original)
        closed=copy.deepcopy(s); closed[1]['closed']=True
        with self.assertRaisesRegex(kernel.KernelError,'seam'): build(sections=closed)
        rail=copy.deepcopy(r); rail['poles'][-1]=rail['poles'][0]
        with self.assertRaisesRegex(kernel.KernelError,'rail'): build(rail=rail)
        with self.assertRaisesRegex(kernel.KernelError,'orthonormal'): build(axis=[0,0,2])
        with self.assertRaises(kernel.KernelError): build(tolerance=0)
        with self.assertRaises(kernel.KernelError): build(tolerance=math.nan)
        weights=copy.deepcopy(s); weights[1]['weights'][1]=.5
        with self.assertRaisesRegex(kernel.KernelError,'common'): build(sections=weights)
        # No intentional within-document-tolerance snapping/fitting of placements.
        misplaced=copy.deepcopy(s); misplaced[1]['poles'][0][0]+=1e-8
        with self.assertRaisesRegex(kernel.KernelError,'anchor'): build(sections=misplaced)
        verify(build(axis=[0,0,1+4e-13]).shape)

    def test_two_and_four_sections(self):
        r,s,p=fixture(parallel=True)
        two=build(sections=[s[0],s[-1]],parameters=[0.,1.])
        surface(two.shape)
        r,s,p=fixture(parallel=True)
        # Four unequal linear-radius sections, independently constructed.
        sections=[]
        for t in [0.,.3,.7,1.]:
            anchor=curve_value(r,t); radius=1.+t
            q=copy.deepcopy(s[0])
            q['poles']=[anchor,[anchor[0]+radius,anchor[1],anchor[2]],
                       [anchor[0]+radius,anchor[1]+radius,anchor[2]]]
            sections.append(q)
        out=build(sections=sections,parameters=[0.,.3,.7,1.])
        surf=surface(out.shape)
        for section,t in zip(sections,[0.,.3,.7,1.]):
            self.assertLess(math.dist(curve_value(section,.37),surf.Value(.37,t).Coord()),1e-10)

    def test_native_buffer_contract(self):
        packed=[]
        real=ng.sweep1_parallel
        def capture(values): packed.extend(values); return real(values)
        with patch.object(ng,'sweep1_parallel',capture): build()
        fn=ng._load_locked().library.narwhal_geometry_core_sweep1_parallel_v1
        source=(ctypes.c_double*len(packed))(*packed); report=ng.SweepReport()
        out=(ctypes.c_ubyte*8)(*[91]*8)
        required=fn(source,len(packed),out,len(out),ctypes.byref(report))
        self.assertGreater(required,8); self.assertEqual(list(out),[91]*8)
        source[0]=math.nan
        self.assertLess(fn(source,len(packed),out,len(out),ctypes.byref(report)),0)
        self.assertEqual(list(out),[91]*8)
        self.assertEqual(ctypes.sizeof(report),48)

    def test_recipe_and_subsequent_exact_operations(self):
        result=build()
        recipe=json.loads(json.dumps(result.recipe))
        verify(ob.from_recipe(recipe))
        # The old Z extrusion folds this curved face despite positive signed
        # volume and closure. Keep it as a refusal, not a manufacturing fixture.
        with self.assertRaisesRegex(kernel.KernelError,'self-intersecting'):
            ob.make_prism(result.shape,[0.,0.,.2])
        direction=[-.13,.27,.09]
        prism=ob.make_prism(result.shape,direction)
        self.assertTrue(ob.is_valid(prism)); self.assertTrue(ob.is_airtight(prism))
        self.assertGreater(ob.volume(prism),0.)
        rebuilt=ob.from_recipe(dict(kind='prism',a=recipe,dir=direction,solid=True))
        self.assertAlmostEqual(ob.volume(prism),ob.volume(rebuilt),places=9)
        self.assertGreater(ob.face_types(prism).get('BSPLINE',0),0)
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'exact.step'
            ob.export_step(prism,str(dest))
            self.assertIn('B_SPLINE_SURFACE',dest.read_text())
        invalid=copy.deepcopy(recipe); invalid['version']=2
        with self.assertRaisesRegex(kernel.KernelError,'version'): ob.from_recipe(invalid)

    def test_transform(self):
        geom=document_geometry(build())
        xf=r3.Transform.Identity()
        for i in range(4):
            for j in range(4): setattr(xf,f'M{i}{j}',MATRIX[4*i+j])
        self.assertTrue(bridge.transform_geometry(geom,xf))
        verify(kernel.master_of(geom),MATRIX)
        verify(ob.from_recipe(kernel.recipe_of(geom)),MATRIX)
        # Also rotate the construction inputs and the declared frame itself.
        r,s,p=fixture(parallel=True)
        for curve in [r]+s:
            curve['poles']=[[-q[1]+3,q[0]-2,q[2]+5] for q in curve['poles']]
        rotated=build(rail=r,sections=s,across=[-ACROSS[1],ACROSS[0],0.])
        verify(rotated.shape,[0,-1,0,3,1,0,0,-2,0,0,1,5,0,0,0,1])

    def test_nonuniform_backend_and_bridge(self):
        result=build()
        scaled=ob.transform(result.shape,NONUNIFORM)
        verify(scaled,NONUNIFORM)
        recipe=copy.deepcopy(result.recipe); recipe['xform']=NONUNIFORM
        verify(ob.from_recipe(recipe),NONUNIFORM)
        geom=document_geometry(result)
        before=kernel.recipe_of(geom)
        xf=r3.Transform.Identity()
        for i in range(4):
            for j in range(4): setattr(xf,f'M{i}{j}',NONUNIFORM[4*i+j])
        original_master=kernel.master_of(geom)
        original_proxy=geom.Encode()
        moved=bridge.transform_geometry(geom,xf)
        self.assertIsNot(moved,geom)
        verify(kernel.master_of(moved),NONUNIFORM)
        verify(ob.from_recipe(kernel.recipe_of(moved)),NONUNIFORM)
        self.assertEqual(len(moved.Faces),1)
        self.assertFalse(moved.IsSolid)
        self.assertTrue(moved.IsValid)
        for u,v in [(.13,.27),(.73,.45),(.91,.82)]:
            p=geom.Faces[0].PointAt(u,v)
            expected=[sum(NONUNIFORM[4*k+i]*[p.X,p.Y,p.Z][i] for i in range(3))
                      +NONUNIFORM[4*k+3] for k in range(3)]
            q=moved.Faces[0].PointAt(u,v)
            self.assertLess(math.dist(expected,[q.X,q.Y,q.Z]),1e-7)
        self.assertEqual(kernel.recipe_of(geom),before)
        self.assertEqual(geom.Encode(),original_proxy)
        self.assertIs(kernel.master_of(geom),original_master)
        verify(kernel.master_of(geom))

    def test_fresh_process_save_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'sweep.3dm')
            for mode in ['--write','--read','--write-scaled','--read-scaled']:
                child=subprocess.run([sys.executable,'-B',__file__,mode,path],text=True,capture_output=True,timeout=60)
                self.assertEqual(child.returncode,0,child.stdout+child.stderr)

    def test_older_native_library(self):
        class Old:
            @staticmethod
            def narwhal_geometry_core_capabilities(): return ng.CAP_EDIT_SESSIONS
        class Binding: library=Old()
        with patch.object(ng,'_load_locked',return_value=Binding()):
            self.assertFalse(ng.sweep1_parallel_available())
            with self.assertRaisesRegex(kernel.KernelError,'unavailable'): build()

    def test_single_section_route_unchanged(self):
        r,s,p=fixture(parallel=True)
        original=ob.make_sweep1(r,s[0])
        self.assertTrue(ob.is_valid(original))
        self.assertTrue(ob.is_valid(ob.from_recipe(dict(kind='sweep1',rail=r,section=s[0]))))


def inject_mutation(name):
    if name in ('ignore-middle','lose-weights','reorder'):
        real=ng.sweep1_parallel
        def broken(a):
            a=list(a); n=int(a[1]); start=21+2*n
            if name=='lose-weights': a[2]=1.
            elif name=='reorder':
                a[start:start+9],a[start+9:start+18]=a[start+9:start+18],a[start:start+9]
            else:
                # Keep the middle anchor but secretly shrink its rational arc.
                anchor=a[start+9:start+12]
                for j in [1,2]:
                    for k in range(3):
                        index=start+9+3*j+k
                        a[index]=anchor[k]+.5*(a[index]-anchor[k])
            return real(a)
        ng.sweep1_parallel=broken
        return 'test_geometry'
    if name=='planar-substitution':
        real=sb.sweep1_parallel
        def planar(*args,**kw):
            result=real(*args,**kw)
            return sb.SweepSurface(ob.unique_faces(ob.make_box(1,1,1))[0],result.recipe,result.report)
        sb.sweep1_parallel=planar
        return 'test_geometry'
    if name=='wrong-master':
        bridge.transform_geometry=lambda geom,xf: geom.Transform(xf)
        return 'test_transform'
    if name=='replay-loss':
        sb.replay=lambda recipe: ob.make_box(1,1,1)
        return 'test_recipe_and_subsequent_exact_operations'
    raise ValueError(name)


if __name__=='__main__':
    if '--write' in sys.argv: write_document(sys.argv[-1])
    elif '--read' in sys.argv: read_document(sys.argv[-1])
    elif '--write-scaled' in sys.argv: write_document(sys.argv[-1],nonuniform=True)
    elif '--read-scaled' in sys.argv: read_document(sys.argv[-1],nonuniform=True)
    elif '--mutations' in sys.argv:
        for name in ['ignore-middle','lose-weights','reorder','planar-substitution','wrong-master','replay-loss']:
            run=subprocess.run([sys.executable,'-B',__file__,'--mutation',name],text=True,capture_output=True,timeout=60)
            assert run.returncode!=0 and 'FAILED (' in run.stderr, (name,run.stdout,run.stderr)
            print('RED observed:',name)
    elif '--mutation' in sys.argv:
        case=inject_mutation(sys.argv[-1]); unittest.main(argv=[__file__,'SweepTests.'+case])
    else: unittest.main(verbosity=2)
