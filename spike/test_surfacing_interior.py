"""Independent whole-NURBS correspondence oracle for Freeform persistence tests.

Test-only OCP inspection. These paths preserve the tensor-product basis, so compare
all coefficients rather than mistaking constrained isocurves for the full surface.
"""
import math
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.gp import gp_Pnt, gp_Vec


def support(shape, ob):
    faces=ob.unique_faces(shape)
    assert len(faces)==1,'interior: one face required'
    return BRep_Tool.Surface_s(faces[0])


def assert_interior_equal(actual, expected, ob, matrix=None):
    a,e=support(actual,ob),support(expected,ob)
    def signature(s):
        return (s.UDegree(),s.VDegree(),s.NbUPoles(),s.NbVPoles(),s.IsUPeriodic(),s.IsVPeriodic(),
                tuple(s.UMultiplicity(i) for i in range(1,s.NbUKnots()+1)),
                tuple(s.VMultiplicity(i) for i in range(1,s.NbVKnots()+1)))
    assert signature(a)==signature(e),'interior: NURBS basis changed'
    knots_a=[a.UKnot(i) for i in range(1,a.NbUKnots()+1)]+[a.VKnot(i) for i in range(1,a.NbVKnots()+1)]
    knots_e=[e.UKnot(i) for i in range(1,e.NbUKnots()+1)]+[e.VKnot(i) for i in range(1,e.NbVKnots()+1)]
    assert max(abs(x-y) for x,y in zip(knots_a,knots_e))<1e-12,'interior: knots changed'
    pole_error=weight_error=0.
    for u in range(1,e.NbUPoles()+1):
        for v in range(1,e.NbVPoles()+1):
            p=e.Pole(u,v).Coord()
            if matrix is not None:
                p=[sum(matrix[4*k+j]*p[j] for j in range(3))+matrix[4*k+3] for k in range(3)]
            pole_error=max(pole_error,math.dist(p,a.Pole(u,v).Coord()))
            weight_error=max(weight_error,abs(a.Weight(u,v)/a.Weight(1,1)-e.Weight(u,v)/e.Weight(1,1)))
    assert math.isfinite(pole_error) and pole_error<1e-9,f'interior: pole error {pole_error:.6g}'
    assert math.isfinite(weight_error) and weight_error<1e-12,f'interior: weight error {weight_error:.6g}'
    return dict(pole_error=pole_error,weight_error=weight_error,poles=e.NbUPoles()*e.NbVPoles())


def change_only_interior(shape, ob, stations=(0.,.45,1.)):
    source=support(shape,ob); changed=source.Copy()
    full=[source.VKnot(i) for i in range(1,source.NbVKnots()+1) for _ in range(source.VMultiplicity(i))]
    j=next(j for j in range(2,source.NbVPoles())
           if full[j-1]>stations[0] and full[j+source.VDegree()]<stations[1])
    changed.SetPole(2,j,source.Pole(2,j).Translated(gp_Vec(.04,-.03,.02)),source.Weight(2,j))
    # Entire constrained isocurves are unchanged by disjoint basis support. Samples
    # below additionally verify the constructed negative fixture against API indexing.
    for v in stations:
        for i in range(65):
            assert source.Value(i/64,v).Distance(changed.Value(i/64,v))<1e-12
    for u in (0.,1.):
        for i in range(129):
            assert source.Value(u,i/128).Distance(changed.Value(u,i/128))<1e-12
    witness=max(source.Value(.5,i/256).Distance(changed.Value(.5,i/256)) for i in range(257))
    assert witness>1e-4,'interior corruption needs a meaningful geometric witness'
    face=BRepBuilderAPI_MakeFace(changed,1e-7).Face()
    face.Orientation(shape.Orientation())
    return face,witness


if __name__=='__main__':
    import unittest
    from test_surfacing_freeform import FreeformTests
    suite=unittest.TestSuite([FreeformTests('test_interior_only_corruption'),
                             FreeformTests('test_recipe_transforms_and_sources')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
