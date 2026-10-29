"""Executable regression probe: reported PipeShell fit error is NOT section error.

PASS means the independently measured interpolation limitation is reproduced.
This is evidence for NOT shipping this candidate as tolerance-qualified Sweep1.
"""
import math
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules.setdefault('narwhal3d', pkg)
from narwhal3d.kernel import occt_backend as ob
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRep import BRep_Tool
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt, gp_Vec


def fixture(parallel=False):
    # Noncoplanar cubic poles: a genuinely spatial, nonplanar rail.
    rail = dict(ctype='nurbs', degree=3, poles=[[0,0,0], [0,0,3], [2,1,6], [4,-1,9]],
                weights=[1]*4, knots=[0,0,0,1,1,1])
    wire = ob.wire_from_curve(rail)
    curve = BRepAdaptor_Curve(ob._unique_edges(wire)[0])
    sections = []
    for t, radius in zip([0., .45, 1.], [1., 1.7, .8]):
        p, tangent = gp_Pnt(), gp_Vec()
        curve.D1(t, p, tangent)
        x = tangent.Crossed(gp_Vec(0,1,0)).Normalized()
        y = tangent.Normalized().Crossed(x).Normalized()
        if parallel:
            x, y = gp_Vec(1,0,0), gp_Vec(0,1,0)
        # Rational quadratic quarter-circle, start anchored on the rail.
        poles = [p, p.Translated(x.Multiplied(radius)),
                 p.Translated(x.Multiplied(radius).Added(y.Multiplied(radius)))]
        sections.append(dict(ctype='nurbs', degree=2, poles=[list(q.Coord()) for q in poles],
                             weights=[1., math.sqrt(.5), 1.], knots=[0,0,1,1]))
    return rail, sections, [0., .45, 1.]


def probe():
    rail, sections, parameters = fixture()
    spine = ob.wire_from_curve(rail)
    c = BRepAdaptor_Curve(ob._unique_edges(spine)[0])
    ps = BRepOffsetAPI_MakePipeShell(spine)
    ps.SetMode(False)
    ps.SetTolerance(1e-7, 1e-7, 1e-6)
    ps.SetMaxDegree(8)
    ps.SetMaxSegments(64)
    for section, t in zip(sections, parameters):
        ps.Add(ob.wire_from_curve(section), BRepBuilderAPI_MakeVertex(c.Value(t)).Vertex(), False, False)
    ps.Build()
    assert ps.IsDone()
    shape = ps.Shape()
    surf = BRep_Tool.Surface_s(ob.unique_faces(shape)[0])
    print('types', ob.face_types(shape), 'valid', ob.is_valid(shape), 'solid', ob.is_airtight(shape))
    print('error', ps.ErrorOnSurface(), 'degrees', surf.UDegree(), surf.VDegree(),
          'knots', surf.NbUKnots(), surf.NbVKnots(), 'continuity', surf.Continuity())
    maximum = 0.
    for section in sections + [rail]:
        cc = BRepAdaptor_Curve(ob._unique_edges(ob.wire_from_curve(section))[0])
        error = max(GeomAPI_ProjectPointOnSurf(cc.Value(i/64), surf).LowerDistance() for i in range(65))
        print('residual', error)
        maximum = max(maximum, error)
    assert ps.ErrorOnSurface() < 1e-7
    assert maximum > 1e-3, 're-probe: the known middle-section deviation changed'
    print('PASS: exposed section interpolation error hidden by ErrorOnSurface')


if __name__ == '__main__':
    probe()
