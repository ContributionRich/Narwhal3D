"""Reproduce OCCT's rational-flag defect with consistent station parameters.

PASS means the uncorrected NSections demonstrably lost rational section weights.
Production uses the private native compatibility subclass, not this probe route.
"""
import math
from test_surfacing_probe import fixture, ob
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAdaptor import GeomAdaptor_Curve
from OCP.GeomFill import (GeomFill_CorrectedFrenet, GeomFill_CurveAndTrihedron,
                         GeomFill_NSections, GeomFill_Sweep, GeomFill_ApproxStyle)
from OCP.GeomAbs import GeomAbs_C2
from OCP.Geom import Geom_BSplineSurface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.collections import (Sequence_Geom_Curve, Sequence_double, Array2_gp_Pnt,
                             Array2_double, Array1_double, Array1_int)
from OCP.gp import gp_Mat, gp_Vec, gp_Pnt, gp_Trsf


def build(tol=1e-8):
    rail,sections,params=fixture()
    c=BRepAdaptor_Curve(ob._unique_edges(ob.wire_from_curve(rail))[0]).BSpline()
    loc=GeomFill_CurveAndTrihedron(GeomFill_CorrectedFrenet())
    loc.SetCurve(GeomAdaptor_Curve(c)); loc.SetInterval(0.,1.)
    local=[]; curves=Sequence_Geom_Curve(); ts=Sequence_double()
    for sec,t in zip(sections,params):
        m,v=gp_Mat(),gp_Vec(); assert loc.D0(t,m,v)
        inv=m.Inverted()
        poles=[]
        for p in sec['poles']:
            q=gp_Vec(*p).Subtracted(v)
            xyz=q.XYZ().Multiplied(inv); poles.append(list(xyz.Coord()))
        local.append(poles)
        recipe=dict(sec,poles=poles)
        curves.Append(BRepAdaptor_Curve(ob._unique_edges(ob.wire_from_curve(recipe))[0]).BSpline())
        ts.Append(t)
    pa=Array2_gp_Pnt(1,3,1,3); wa=Array2_double(1,3,1,3)
    a=params[1]
    for u in range(3):
        control=[local[0][u],[(local[1][u][k]-(1-a)**2*local[0][u][k]-a*a*local[2][u][k])/(2*a*(1-a)) for k in range(3)],local[2][u]]
        for v in range(3):
            pa.SetValue(u+1,v+1,gp_Pnt(*control[v])); wa.SetValue(u+1,v+1,sections[0]['weights'][u])
    ka=Array1_double(1,2); ka.SetValue(1,0.); ka.SetValue(2,1.)
    ma=Array1_int(1,2); ma.SetValue(1,3); ma.SetValue(2,3)
    ref=Geom_BSplineSurface(pa,wa,ka,ka,ma,ma,2,2,False,False)
    section=GeomFill_NSections(curves,ts,0.,1.,0.,1.)
    section.SetSurface(ref); section.ComputeSurface()
    loc.SetInterval(0.,1.)
    sweep=GeomFill_Sweep(loc,False); sweep.SetDomain(0.,1.,0.,1.)
    sweep.SetTolerance(tol,tol,1e-6,1e-6)
    sweep.SetForceApproxC1(False)
    sweep.Build(section,GeomFill_ApproxStyle.GeomFill_Section,GeomAbs_C2,8,64)
    assert sweep.IsDone()
    s=sweep.Surface()
    print('reported fit',sweep.ErrorOnSurface(),'degree',s.UDegree(),s.VDegree(),'continuity',s.Continuity())
    for sec,t in zip(sections,params):
        cc=BRepAdaptor_Curve(ob._unique_edges(ob.wire_from_curve(sec))[0])
        direct=max(cc.Value(i/64).Distance(s.Value(i/64,t)) for i in range(65))
        projected=max(GeomAPI_ProjectPointOnSurf(cc.Value(i/64),s).LowerDistance() for i in range(65))
        print('section',t,'direct',direct,'projection',projected)
        assert direct>1e-3 and projected>1e-3
    print('rail',max(c.Value(i/256).Distance(s.Value(0,i/256)) for i in range(257)))
    assert not section.IsRational() and ref.IsVRational()
    assert all(s.Weight(i,j)==1. for i in range(1,s.NbUPoles()+1) for j in range(1,s.NbVPoles()+1))
    print('PASS: uncorrected NSections dropped rational weights despite small reported fit')
    return s


if __name__=='__main__': build()
