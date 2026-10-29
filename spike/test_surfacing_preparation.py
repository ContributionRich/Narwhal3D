"""Real-curve station fidelity and immutable preparation; no command/UI activation."""
import copy
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

from test_surfacing_freeform import bridge, kernel, ob, sb, ng, r3, surface, exact_curve, fixture
from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
from OCP.gp import gp_Pnt
from narwhal3d.model import NarwhalModel


def sources(kind='arc', reverse_rail=False, reverse_middle=False, update=0):
    if kind=='arc':
        rail=bridge.curve_from_recipe(dict(ctype='nurbs',degree=2,
            poles=[[10,0,0],[10,10,0],[0,10,0]],weights=[1,math.sqrt(.5),1],knots=[-2,-2,7,7]))
    elif kind=='crossing':
        rail=bridge.curve_from_recipe(dict(ctype='nurbs',degree=3,
            poles=[[.1875,-.09375,0],[-7/48,13/96,0],[-7/48,-13/96,0],[.1875,.09375,0]],
            weights=[1]*4,knots=[0,0,0,1,1,1]))
    else:
        recipe=fixture()[0]; recipe['knots']=[-4,-4,-4,6,6,6]
        rail=bridge.curve_from_recipe(recipe)
    if reverse_rail: assert rail.Reverse()
    fractions=[0.,.25 if kind=='crossing' else .27,1.]
    parameters=[rail.Domain.T0+(rail.Domain.T1-rail.Domain.T0)*f for f in fractions]
    points=[rail.PointAt(t) for t in parameters]
    sections=[]
    for t,p,radius in zip(parameters,points,[1.,1.4,.8]):
        radius += update*.01
        tangent=rail.TangentAt(t)
        if kind=='arc':
            x=[p.X/10,p.Y/10,0.]; y=[0.,0.,1.]
        else:
            v=[tangent.X,tangent.Y,tangent.Z]
            reference=[0.,0.,1.] if kind=='crossing' else [0.,1.,0.]
            x=[v[1]*reference[2]-v[2]*reference[1],v[2]*reference[0]-v[0]*reference[2],v[0]*reference[1]-v[1]*reference[0]]
            norm=math.sqrt(sum(q*q for q in x)); x=[q/norm for q in x]
            y=[v[1]*x[2]-v[2]*x[1],v[2]*x[0]-v[0]*x[2],v[0]*x[1]-v[1]*x[0]]
        a=[p.X,p.Y,p.Z]
        sections.append(bridge.curve_from_recipe(dict(ctype='nurbs',degree=2,
            poles=[a,[a[k]+radius*x[k] for k in range(3)],
                   [a[k]+radius*(x[k]+y[k]) for k in range(3)]],
            weights=[1,math.sqrt(.5),1],knots=[-9,-9,-3,-3])))
    if reverse_middle: assert sections[1].Reverse()
    model=NarwhalModel()
    for i,curve in enumerate([rail]+sections): model.add_object(curve,'source'+str(i),0)
    ids=tuple(str(obj.Attributes.Id) for obj in model.file.Objects)
    def resolve():
        by_id={str(obj.Attributes.Id):obj.Geometry for obj in model.file.Objects}
        return ids,[by_id[key] for key in ids]
    return model,ids,resolve,parameters,points,[False,reverse_middle,False]


def prepare(case):
    model,ids,resolve,_,points,reverse=case
    _,curves=resolve()
    return sb.prepare_freeform(curves[0],curves[1:],source_ids=ids,station_points=points,reverse=reverse)


def assert_physical(prepared,points):
    native=exact_curve(json.loads(prepared._inputs_json)[0])
    errors=[native.Value(t).Distance(gp_Pnt(p.X,p.Y,p.Z)) for t,p in zip(prepared.parameters,points)]
    assert max(errors)<1e-10,f'physical station mapping error {max(errors):.9g}'
    return max(errors)


def assert_sections(result,prepared,curves):
    surf=surface(result.shape)
    error=0.
    for curve,t in zip(curves[1:],prepared.parameters):
        iso=surf.VIso(t)
        for i in range(65):
            p=curve.PointAt(curve.Domain.T0+(curve.Domain.T1-curve.Domain.T0)*i/64)
            error=max(error,GeomAPI_ProjectPointOnCurve(gp_Pnt(p.X,p.Y,p.Z),iso).LowerDistance())
    assert error<1e-7,f'prepared source-section error {error:.9g}'
    return error


class PreparationTests(unittest.TestCase):
    def test_non_affine_arc_point_fidelity(self):
        case=sources(); model,ids,resolve,parameters,points,_=case
        curves=resolve()[1]; before=[curve.Encode() for curve in curves]
        self.assertIsInstance(curves[0],r3.NurbsCurve)
        self.assertTrue(curves[0].IsRational)
        self.assertEqual(bridge.curve_recipe(curves[0])['ctype'],'arc')
        prepared=prepare(case)
        point_error=assert_physical(prepared,points)
        native=exact_curve(json.loads(prepared._inputs_json)[0])
        wrong=prepared.native_domain[0]+.27*(prepared.native_domain[1]-prepared.native_domain[0])
        wrong_error=native.Value(wrong).Distance(gp_Pnt(points[1].X,points[1].Y,points[1].Z))
        self.assertGreater(wrong_error,.01)  # Endpoint-only/affine fixtures cannot prove this.
        result=sb.build_prepared_freeform(prepared,resolve_sources=resolve)
        section_error=assert_sections(result,prepared,curves)
        self.assertEqual([c.Encode() for c in resolve()[1]],before)
        self.assertEqual(prepared.source_ids,ids)
        self.assertEqual(result.recipe['source_provenance']['ids'],list(ids))
        self.assertEqual(prepared.source_domains[0],(-2.,7.))
        self.assertLess(prepared.max_station_error,1e-10)
        self.assertEqual(model.object_count(),4)  # No publication hidden in preparation/build.
        print('MEASURED arc source/native domains',prepared.source_domains[0],prepared.native_domain,
              'physical error',point_error,'wrong affine error',wrong_error,'section error',section_error)

    def test_reversed_rail_and_explicit_section_reversal(self):
        case=sources(reverse_rail=True,reverse_middle=True)
        prepared=prepare(case)
        assert_physical(prepared,case[4])
        result=sb.build_prepared_freeform(prepared,resolve_sources=case[2])
        assert_sections(result,prepared,case[2]()[1])
        curves=case[2]()[1]
        with self.assertRaisesRegex(kernel.KernelError,'anchor/normal'):
            sb.prepare_freeform(curves[0],curves[1:],source_ids=case[1],station_points=case[4])

    def test_nonplanar_real_sources_and_default_anchors(self):
        case=sources('nonplanar'); curves=case[2]()[1]
        prepared=sb.prepare_freeform(curves[0],curves[1:],source_ids=case[1])
        assert_physical(prepared,case[4])
        result=sb.build_prepared_freeform(prepared,resolve_sources=case[2])
        assert_sections(result,prepared,curves)
        self.assertEqual(prepared.source_domains[0],(-4.,6.))

    def test_ambiguous_and_invalid_placements(self):
        case=sources('crossing')
        with self.assertRaisesRegex(kernel.KernelError,'ambiguous physical station'): prepare(case)
        case=sources(); curves=case[2]()[1]
        points=list(case[4]); p=points[1]; points[1]=r3.Point3d(p.X,p.Y,p.Z+.01)
        with self.assertRaisesRegex(kernel.KernelError,'not on the rail'):
            sb.prepare_freeform(curves[0],curves[1:],source_ids=case[1],station_points=points)
        points=list(reversed(case[4])); sections=list(reversed(curves[1:]))
        ids=(case[1][0],)+tuple(reversed(case[1][1:]))
        with self.assertRaisesRegex(kernel.KernelError,'endpoints in order'):
            sb.prepare_freeform(curves[0],sections,source_ids=ids,station_points=points)
        closed=r3.Circle(3.).ToNurbsCurve()
        with self.assertRaisesRegex(kernel.KernelError,'closed/periodic'):
            sb.prepare_freeform(closed,curves[1:],source_ids=case[1],station_points=case[4])

    def test_snapshot_copy_matches_content_fingerprint(self):
        case=sources(); curves=case[2]()[1]
        before=[c.Encode() for c in curves]
        duplicate=r3.NurbsCurve.Duplicate
        def changed_copy(curve):
            result=duplicate(curve)
            # Deliberate capture corruption: copies all move together, so geometric
            # placement would otherwise pass while provenance still claimed originals.
            result.Transform(r3.Transform.Translation(3.,0.,0.))
            return result
        with patch.object(r3.NurbsCurve,'Duplicate',changed_copy):
            with self.assertRaisesRegex(kernel.KernelError,'snapshot copy differs'):
                sb.prepare_freeform(curves[0],curves[1:],source_ids=case[1])
        self.assertEqual([c.Encode() for c in case[2]()[1]],before)

    def test_stale_source_identity_and_content(self):
        case=sources(); prepared=prepare(case); model,ids,resolve=case[:3]
        with self.assertRaisesRegex(kernel.KernelError,'source IDs/order'):
            sb.validate_prepared_sources(prepared,tuple(reversed(ids)),resolve()[1])
        replacement=resolve()[1][1].Duplicate(); replacement.Reverse()
        model.replace_geometry(ids[1],replacement)
        with self.assertRaisesRegex(kernel.KernelError,'source geometry changed'):
            sb.build_prepared_freeform(prepared,resolve_sources=resolve)
        self.assertEqual(model.object_count(),4)

    def test_change_during_build_and_cancel_leave_no_publication(self):
        case=sources(); prepared=prepare(case); model,ids,resolve=case[:3]
        before=kernel.master_registry_snapshot(); real=sb.sweep1_freeform_approx
        def change(*args,**kw):
            result=real(*args,**kw)
            replacement=resolve()[1][2].Duplicate(); replacement.Reverse()
            model.replace_geometry(ids[2],replacement)
            return result
        with patch.object(sb,'sweep1_freeform_approx',change):
            with self.assertRaisesRegex(kernel.KernelError,'stale Freeform'):
                sb.build_prepared_freeform(prepared,resolve_sources=resolve)
        self.assertEqual(kernel.master_registry_snapshot(),before)
        self.assertEqual(model.object_count(),4)
        def cancel(): raise RuntimeError('command cancelled')
        with self.assertRaisesRegex(kernel.KernelError,'cancelled'):
            sb.build_prepared_freeform(prepared,resolve_sources=cancel)

    def test_prepared_provenance_fresh_worker_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix='prepared-sweep-') as tmp:
            path=str(Path(tmp)/'prepared.narwhal')
            for mode in ('--write','--read'):
                run=subprocess.run([sys.executable,'-B',__file__,mode,path],text=True,capture_output=True,timeout=120)
                self.assertEqual(run.returncode,0,run.stderr[-4000:])
                if run.stdout: print(run.stdout.strip())

    def test_old_library_refuses_preparation(self):
        class Old:
            def narwhal_geometry_core_capabilities(self): return 255
        with patch.object(ng,'_load_locked',return_value=type('Binding',(),{'library':Old()})()):
            self.assertFalse(ng.prepare_freeform_available())
            with self.assertRaisesRegex(kernel.KernelError,'preparation capability unavailable'):
                prepare(sources())
        old=os.environ.get('NARWHAL_GEOMETRY_OLD_CORE')
        if old:
            with patch.dict(os.environ,{'NARWHAL_GEOMETRY_CORE':old}):
                self.assertTrue(ng.sweep1_freeform_available())
                self.assertFalse(ng.prepare_freeform_available())
                with self.assertRaisesRegex(kernel.KernelError,'preparation capability unavailable'):
                    prepare(sources())


def document_process(mode,path):
    from narwhal3d import narwhal_document as nd
    from test_surfacing_freeform import publish, assert_interior_equal
    if mode=='--write':
        case=sources(reverse_rail=True,reverse_middle=True); prepared=prepare(case)
        result=sb.build_prepared_freeform(prepared,resolve_sources=case[2])
        case[0].add_object(publish(result),'prepared Freeform',0)
        assert nd.save_model(case[0],path)
    else:
        model=nd.load_model(path); nd.commit_masters(model)
        objects=list(model.file.Objects); geom=objects[-1].Geometry
        recipe=kernel.recipe_of(geom); provenance=recipe['source_provenance']
        ids=tuple(provenance['ids'])
        by_id={str(obj.Attributes.Id):obj.Geometry for obj in objects[:-1]}
        def resolve(): return ids,[by_id[key] for key in ids]
        curves=resolve()[1]
        prepared=sb.prepare_freeform(curves[0],curves[1:],source_ids=ids,
            station_points=provenance['station_points'],reverse=recipe['reverse'],tolerance=recipe['tolerance'])
        assert list(prepared.source_hashes)==provenance['geometry_sha256']
        expected=sb.build_prepared_freeform(prepared,resolve_sources=resolve)
        assert_interior_equal(kernel.master_of(geom),expected.shape,ob)
        assert_interior_equal(ob.shape_from_brep_data(bridge.brep_data(geom)),expected.shape,ob)
        print('PASS prepared reversed-arc provenance + entire interior through real .narwhal worker')


def benchmark():
    import resource
    rows=[]
    for i in range(6):
        case=sources('nonplanar',update=i)
        start=time.perf_counter(); prepared=prepare(case); preparation=time.perf_counter()-start
        start=time.perf_counter(); result=sb.build_prepared_freeform(prepared,resolve_sources=case[2]); construction=time.perf_counter()-start
        assert_physical(prepared,case[4]); assert_sections(result,prepared,case[2]()[1])
        rows.append(dict(preparation_seconds=preparation,construction_seconds=construction))
    print(json.dumps(dict(distinct_inputs=rows,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)))


if __name__=='__main__':
    if '--write' in sys.argv or '--read' in sys.argv: document_process(sys.argv[-2],sys.argv[-1])
    elif '--benchmark' in sys.argv: benchmark()
    else: unittest.main(verbosity=2)
