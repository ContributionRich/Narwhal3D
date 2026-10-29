"""M1 STEP oracle for a derived solid; never cap or modify the product sweep.

Run with bundled plain Python -B and the Freeform native library selected (see
../docs/surfacing/STEP_ROUNDTRIP.md). Python here is test-only OCP API inspection,
not a second product geometry implementation. Missing native support is an error.
--mutation {origin,scale,units,interior} deliberately exits RED without source edits.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import tempfile
import unittest

from test_surfacing_freeform import MATRIX, build, fixture, ob, surface
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_Shell, BRepCheck_NoError
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_OUT, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec


# OURS: this direction passes OCCT's self-intersection check before/after STEP.
# Both the original Z direction and positive-X variant fold this curved face.
DIRECTION = [-.13, .27, .09]
DISTANCE_TOL = 2e-8  # mm, serialization agreement, not a sweep fitting tolerance
BASIS_TOL = 2e-11
EXPECTED_TYPES = {'BSPLINE': 6}  # exact nonuniform transform converts extrusions


def require(ok, label):
    if not ok:
        raise AssertionError(label)


def explored(shape, kind):
    exp = TopExp_Explorer(shape, kind)
    result = []
    while exp.More():
        result.append(exp.Current())
        exp.Next()
    return result


def mass_properties(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, 1e-13)
    return props.Mass(), props.CentreOfMass().Coord()


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return (*box.CornerMin().Coord(), *box.CornerMax().Coord())


def max_error(a, b):
    require(len(a) == len(b), 'correspondence: array size')
    require(all(math.isfinite(x) for x in (*a, *b)), 'correspondence: nonfinite data')
    return max((abs(x-y) for x, y in zip(a, b)), default=0.)


def topology(shape):
    # Do not rely on export text or the producer's validity/solid claims.
    require(BRepCheck_Analyzer(shape).IsValid(), 'validity: OCCT analyzer')
    require(BRepAlgoAPI_Check(shape, True, True).IsValid(), 'validity: no self-intersections')
    require(len(explored(shape, TopAbs_SOLID)) == 1, 'airtightness: single solid')
    shells = explored(shape, TopAbs_SHELL)
    require(len(shells) == 1, 'airtightness: single shell')
    require(BRepCheck_Shell(TopoDS.Shell(shells[0])).Closed() == BRepCheck_NoError,
            'airtightness: closed shell')
    classifier = BRepClass3d_SolidClassifier(shape)
    classifier.PerformInfinitePoint(1e-7)
    require(classifier.State() == TopAbs_OUT, 'orientation: infinity must be outside')
    require(ob.face_types(shape) == EXPECTED_TYPES, 'topology: six spline faces, no facets')
    require(len(ob._unique_edges(shape)) == 12, 'topology: twelve unique edges')
    for face in ob.unique_faces(shape):
        require(len(explored(face, TopAbs_WIRE)) == 1, 'topology: one wire per face')
        require(len(ob._unique_edges(face)) == 4, 'topology: four edges per face')


def spline_data(shape):
    """World-space support data and trim intervals, read from this shape alone."""
    faces = []
    for face in ob.unique_faces(shape):
        s = BRep_Tool.Surface_s(face)
        # STEP rounds near-constant weights (~1e-13), changing Is*Rational flags.
        # Compare EVERY weight below, not those roundoff-sensitive cached flags.
        signature = (s.UDegree(), s.VDegree(), s.NbUPoles(), s.NbVPoles(),
                     s.IsUPeriodic(), s.IsVPeriodic(),
                     tuple(s.UMultiplicity(i) for i in range(1, s.NbUKnots()+1)),
                     tuple(s.VMultiplicity(i) for i in range(1, s.NbVKnots()+1)))
        basis = ([s.UKnot(i) for i in range(1, s.NbUKnots()+1)]
                 + [s.VKnot(i) for i in range(1, s.NbVKnots()+1)]
                 + [s.Weight(i, j) for i in range(1, s.NbUPoles()+1)
                    for j in range(1, s.NbVPoles()+1)]
                 + list(BRepTools.UVBounds_s(face)))
        poles = [c for i in range(1, s.NbUPoles()+1) for j in range(1, s.NbVPoles()+1)
                 for c in s.Pole(i, j).Coord()]
        faces.append((signature, basis, poles))
    edges = []
    for edge in ob._unique_edges(shape):
        adaptor = BRepAdaptor_Curve(edge)
        # This fixture preserves exact spline curves too, including straight sides.
        c = adaptor.BSpline()
        signature = (c.Degree(), c.NbPoles(), c.IsPeriodic(),
                     tuple(c.Multiplicity(i) for i in range(1, c.NbKnots()+1)))
        basis = ([c.Knot(i) for i in range(1, c.NbKnots()+1)]
                 + [c.Weight(i) for i in range(1, c.NbPoles()+1)]
                 + [adaptor.FirstParameter(), adaptor.LastParameter()])
        poles = [x for i in range(1, c.NbPoles()+1) for x in c.Pole(i).Coord()]
        edges.append((signature, basis, poles))
    return faces, edges


def match_splines(expected, actual, factor, label):
    """Bijective match, independent of STEP entity/explorer ordering; no refitting."""
    require(len(expected) == len(actual), f'correspondence: {label} count')
    unused = set(range(len(actual)))
    mapping = []
    worst_pole = worst_basis = 0.
    for signature, basis, poles in expected:
        candidates = [j for j in unused if actual[j][0] == signature]
        require(bool(candidates), f'correspondence: {label} degree/multiplicity/periodicity')
        scaled = [x*factor for x in poles]
        j = min(candidates, key=lambda k: max_error(scaled, actual[k][2]))
        pole_error = max_error(scaled, actual[j][2])
        basis_error = max_error(basis, actual[j][1])
        require(pole_error < DISTANCE_TOL,
                f'correspondence: {label} poles {pole_error:.9g} mm')
        require(basis_error < BASIS_TOL,
                f'correspondence: {label} knots/weights/trims {basis_error:.9g}')
        worst_pole = max(worst_pole, pole_error)
        worst_basis = max(worst_basis, basis_error)
        unused.remove(j)
        mapping.append(j)
    return mapping, worst_pole, worst_basis


def correspondence(original, imported, factor=1.):
    before_faces, before_edges = spline_data(original)
    after_faces, after_edges = spline_data(imported)
    fm, fp, fb = match_splines(before_faces, after_faces, factor, 'surface')
    em, ep, eb = match_splines(before_edges, after_edges, factor, 'edge')
    # Equal supports alone would not detect rewiring the trims onto other faces.
    old_edges, new_edges = ob._unique_edges(original), ob._unique_edges(imported)
    old_faces, new_faces = ob.unique_faces(original), ob.unique_faces(imported)
    for i, j in enumerate(fm):
        before = Counter(em[k] for e in ob._unique_edges(old_faces[i])
                         for k, edge in enumerate(old_edges) if e.IsSame(edge))
        after = Counter(k for e in ob._unique_edges(new_faces[j])
                        for k, edge in enumerate(new_edges) if e.IsSame(edge))
        require(before == after, 'correspondence: face/edge incidence')
    return dict(surface_pole_error_mm=fp, edge_pole_error_mm=ep,
                basis_error=max(fb, eb), faces=len(fm), edges=len(em))


def position(original, imported, factor=1.):
    error = max_error([x*factor for x in bounds(original)], bounds(imported))
    # AddOptimal still pads extrema by OCCT Confusion=1e-7 even with
    # useShapeTolerance=False and GetGap()==0. Budget that padding in both units;
    # the actual poles, anchor and centroid retain the tighter DISTANCE_TOL.
    bbox_budget = DISTANCE_TOL + (factor+1)*1e-7
    require(error < bbox_budget, f'position/scale: bounding box error {error:.9g} mm')
    # Independent physical landmark: original rail starts at (0,0,0), so its
    # transformed prism corner MUST be (13,-4,2), not an origin-centered copy.
    anchor = gp_Pnt(13*factor, -4*factor, 2*factor)
    anchor_error = min(anchor.Distance(BRep_Tool.Pnt_s(TopoDS.Vertex(v)))
                       for v in explored(imported, TopAbs_VERTEX))
    require(anchor_error < DISTANCE_TOL, f'position: anchor error {anchor_error:.9g} mm')
    return error


def verify_roundtrip(original, imported, file_units, unit='MM'):
    expected_units, factor = {'MM': (['millimetre'], 1.), 'INCH': (['INCH'], 25.4)}[unit]
    require(file_units == expected_units, f'units: {file_units!r} != {expected_units!r}')
    topology(imported)
    pos_error = position(original, imported, factor)
    evidence = correspondence(original, imported, factor)
    source_volume, source_center = mass_properties(original)
    volume, center = mass_properties(imported)
    require(source_volume > 0 and volume > 0, 'volume: positive signed mass')
    relative_error = abs(volume/(source_volume*factor**3)-1.)
    require(relative_error < 2e-10, f'volume: relative error {relative_error:.9g}')
    center_error = max_error([x*factor for x in source_center], center)
    require(center_error < DISTANCE_TOL, f'position: centroid error {center_error:.9g} mm')
    return dict(evidence, units=file_units, volume_mm3=volume,
                volume_relative_error=relative_error, bbox_error_mm=pos_error,
                centroid_error_mm=center_error, bounds_mm=bounds(imported))


def roundtrip(shape, unit='MM'):
    with tempfile.TemporaryDirectory(prefix='surfacing-step-') as tmp:
        path = Path(tmp)/'freeform-prism.step'
        size = ob.export_step(shape, str(path), unit=unit)
        require(size == path.stat().st_size and size > 0, 'STEP export: nonempty file')
        loaded = ob.import_step(str(path), unit='MM')
        require(len(loaded['solids']) == 1, 'STEP import: one solid')
        require(not any(loaded[k] for k in ('shells', 'faces', 'edges')),
                'STEP import: no loose components')
        return loaded['solids'][0], loaded['file_units']


def derived_prism(open_face):
    return ob.transform(ob.make_prism(open_face, DIRECTION), MATRIX)


def interior_mutant(open_face):
    """Change an interior coefficient with support clear of all constrained sections."""
    original = surface(open_face)
    changed = original.Copy()
    knots = [original.VKnot(i) for i in range(1, original.NbVKnots()+1)
             for _ in range(original.VMultiplicity(i))]
    stations = fixture()[2]
    j = next(j for j in range(2, original.NbVPoles())
             if knots[j-1] > stations[0] and knots[j+original.VDegree()] < stations[1])
    changed.SetPole(2, j, changed.Pole(2, j).Translated(gp_Vec(.04, -.03, .02)),
                    original.Weight(2, j))
    for v in stations:
        for i in range(33):
            require(original.Value(i/32, v).Distance(changed.Value(i/32, v)) < 1e-12,
                    'mutation fixture: constrained section changed')
    for u in (0., 1.):
        for i in range(129):
            require(original.Value(u, i/128).Distance(changed.Value(u, i/128)) < 1e-12,
                    'mutation fixture: boundary/rail changed')
    deviation = max(original.Value(.5, i/256).Distance(changed.Value(.5, i/256))
                    for i in range(257))
    require(deviation > 1e-4, 'mutation fixture: no meaningful interior change')
    face = BRepBuilderAPI_MakeFace(changed, 1e-7).Face()
    face.Orientation(open_face.Orientation())
    print(f'MEASURED interior corruption: {deviation:.9g} model units; constraints unchanged')
    return face


def mutated_export(original, open_face, mutation):
    if mutation == 'interior':
        bad = derived_prism(interior_mutant(open_face))
    elif mutation == 'origin':
        bad = ob.transform(original, [1,0,0,-13, 0,1,0,4, 0,0,1,-2, 0,0,0,1])
    elif mutation == 'scale':
        bad = ob.transform(original, [25.4,0,0,0, 0,25.4,0,0, 0,0,25.4,0, 0,0,0,1])
    else:
        require(mutation == 'units', 'unknown mutation')
        bad = original
    imported, units = roundtrip(bad, 'INCH' if mutation == 'units' else 'MM')
    topology(imported)  # Corruption must be geometric, not merely an invalid solid.
    return imported, units


class StepRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.open_face = build().shape
        cls.prism = derived_prism(cls.open_face)
        cls.source_data = spline_data(cls.prism)
        surface(cls.open_face)  # Still one OPEN rational face, not an invented solid.
        topology(cls.prism)

    def test_roundtrip_units_and_geometry(self):
        for unit in ('MM', 'INCH'):
            with self.subTest(unit=unit):
                imported, units = roundtrip(self.prism, unit)
                report = verify_roundtrip(self.prism, imported, units, unit)
                print('MEASURED STEP', json.dumps(report, sort_keys=True))
        self.assertEqual(spline_data(self.prism), self.source_data)
        surface(self.open_face)

    def test_corruptions_red_then_restored_green(self):
        for mutation in ('origin', 'scale', 'units', 'interior'):
            with self.subTest(mutation=mutation):
                bad, units = mutated_export(self.prism, self.open_face, mutation)
                if mutation == 'interior':
                    # Isolate the interior guard: do not let volume/bbox catch it first.
                    with self.assertRaisesRegex(AssertionError, 'correspondence: surface poles') as caught:
                        correspondence(self.prism, bad)
                else:
                    guard = 'units:' if mutation == 'units' else 'position/scale:'
                    with self.assertRaisesRegex(AssertionError, guard) as caught:
                        verify_roundtrip(self.prism, bad, units)
                print(f'RED {mutation}: {caught.exception}')
                restored, units = roundtrip(self.prism)
                verify_roundtrip(self.prism, restored, units)
                self.assertEqual(spline_data(self.prism), self.source_data)
                print(f'GREEN restored after {mutation}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutation', choices=('origin', 'scale', 'units', 'interior'))
    args, rest = parser.parse_known_args()
    if args.mutation:
        face = build().shape
        prism = derived_prism(face)
        topology(prism)
        imported, units = mutated_export(prism, face, args.mutation)
        if args.mutation == 'interior':
            correspondence(prism, imported)
        else:
            verify_roundtrip(prism, imported, units)
        print('UNEXPECTED GREEN: corruption survived the intended guard')
        raise SystemExit(0)
    unittest.main(argv=[__file__, *rest], verbosity=2)
