"""Exact G1 extrusion topology, legacy compatibility, and snap-only crease tests.

Uses OCCT/openNURBS, never a mesh as the asserted geometry. In-memory negative
controls: CREASE_MUTATION=split or legacy or crease. No shared source mutation.
All curves and hollow prisms are generated; no private artwork is required.
"""
import copy
import hashlib
import importlib
import math
import os
from pathlib import Path
import runpy
import tempfile

ROOT = Path(__file__).resolve().parents[1]
n = runpy.run_path(str(ROOT / 'spike/test_report121_124.py'))
b, k, r = n['bridge'], n['kernel'], n['r3']
oc = b._occt()
print('PUBLIC_LOADED_BRIDGE', b.__file__)
print('PUBLIC_LOADED_BACKEND', oc.__file__)


def stationary_tangent_tests():
    from OCP.collections import Array1_gp_Pnt, Array1_double, Array1_int
    from OCP.Geom import Geom_BSplineCurve
    from OCP.gp import gp_Pnt
    import inspect
    import textwrap

    tangent = oc.extrusion_kink_parameters
    mutation = os.environ.get('TANGENT_MUTATION', '')
    if mutation in ('first-only', 'left-sign'):
        source = textwrap.dedent(inspect.getsource(tangent))
        old, new = (('range(1, curve.Degree() + 1)', 'range(1, 2)') if mutation == 'first-only'
                    else ('if left_side and order % 2 == 0:', 'if False:'))
        assert source.count(old) == 1, 'stationary mutation must actually alter the helper'
        scope = dict(vars(oc))
        exec(source.replace(old, new), scope)
        tangent = scope['extrusion_kink_parameters']
    elif mutation == 'skip':
        tangent = lambda curve: []

    def native(points, degree, domain, multiplicity):
        poles = Array1_gp_Pnt(1, len(points))
        weights = Array1_double(1, len(points))
        for i, point in enumerate(points, 1):
            poles.SetValue(i, gp_Pnt(*point)); weights.SetValue(i, 1.0)
        knots, mult = Array1_double(1, 3), Array1_int(1, 3)
        for i, value in enumerate(domain, 1):
            knots.SetValue(i, value)
            mult.SetValue(i, multiplicity if i == 2 else degree + 1)
        return Geom_BSplineCurve(poles, weights, knots, mult, degree, False)

    z = (0, 0, 0)
    left2 = [(-2, 0, 0), (-1, 0, 0), z, z]
    left3 = [(-2, 0, 0), z, z, z]
    right2 = [z, (1, 0, 0), (2, 0, 0)]
    right3 = [z, z, (2, 0, 0)]
    cases = [('smooth-order2', left2 + right2, []),
             ('kink-order2', left2 + [z, (0, 1, 0), (0, 2, 0)], 'kink'),
             ('cusp-order2', [(2, 0, 0), (1, 0, 0), z, z] + right2, 'kink'),
             ('smooth-order3', left3 + right3, []),
             ('kink-order3', left3 + [z, z, (0, 2, 0)], 'kink'),
             ('smooth-mixed-orders', left2 + right3, [])]
    checks = 0
    for domain in ((2.0, 5.0, 8.0), (-8.0, -5.0, -2.0)):
        for label, points, expected in cases:
            curve = native(points, 3, domain, 3)
            assert curve.LocalDN(domain[1], 1, 2, 1).Magnitude() == 0
            assert curve.LocalDN(domain[1], 2, 3, 1).Magnitude() == 0
            try:
                actual = tangent(curve)
            except oc.KernelError as error:
                raise AssertionError(f'valid stationary {label} {domain} must have a one-sided tangent: {error}') from error
            want = [domain[1]] if expected == 'kink' else []
            assert actual == want, (label, domain, actual, want)
            checks += 1
        # C1 parametric continuity does NOT guarantee G1 when the derivative is
        # zero: x(t)=t^2 reverses direction at the simple interior knot.
        cusp = native([(1, 0, 0), z, z, (1, 0, 0)], 2, domain, 1)
        assert tangent(cusp) == [domain[1]], 'a stationary C1 cusp must not be skipped by knot multiplicity'
        # Conversely, an odd-order stationary parametrization stays smooth.
        smooth = native([(-1, 0, 0), z, z, z, (1, 0, 0)], 3, domain, 1)
        assert tangent(smooth) == [], 'a stationary C2 odd-order curve is G1 smooth'
        checks += 2
        degenerate = native([z, z, z, z, z, (1, 0, 0), (2, 0, 0)], 3, domain, 3)
        try:
            tangent(degenerate)
        except oc.KernelError as error:
            assert 'undefined tangent' in str(error)
        else:
            raise AssertionError('a truly constant one-sided span must still decline')
        checks += 1
    print('STATIONARY_TANGENTS', checks, 'exact native one-sided cases PASS')


stationary_tangent_tests()

MUTATION = os.environ.get('CREASE_MUTATION', '')
if MUTATION == 'split':
    oc.extrusion_kink_parameters = lambda curve, **kw: []
if MUTATION == 'crease':
    oc.extrusion_crease_curves = lambda shape: []
if MUTATION == 'legacy':
    original_wire_builder = oc.wire_from_curve
    oc.wire_from_curve = lambda profile: original_wire_builder(b._version_extrusion_profiles(profile))


def face_count(shape):
    return len(oc.unique_faces(shape))


def digest(shape):
    return hashlib.sha256(oc.shape_to_bytes(shape, triangles=False)).hexdigest()


def profile():
    curve = r.NurbsCurve(3, False, 3, 9)
    points = ((0, 0), (1, -.25), (2, 0), (2.25, 1), (2, 2),
              (1, 2.25), (0, 2), (-.25, 1), (0, 0))
    for i, (x, y) in enumerate(points):
        curve.Points[i] = r.Point4d(x, y, 0, 1)
    for i, value in enumerate((0, 0, 1, 1, 2, 2, 3, 3, 4, 4)):
        curve.Knots[i] = value
    assert curve.IsClosed and not curve.IsPolyline()
    return curve


crv = profile()
legacy = {'kind': 'extrusion', 'profile': b.curve_recipe(crv), 'dir': [0, 0, 3], 'solid': True}
old = oc.from_recipe(legacy)
old_hash = digest(old)
assert face_count(old) == 3, 'legacy one wrapped side remains unchanged'
new_recipe = b._version_extrusion_profiles(legacy)

assert 'extrusion_kinks' not in legacy['profile'], 'absent field must retain old recipe interpretation'
new = oc.from_recipe(new_recipe)
assert face_count(new) == 6, 'four exact curved sides plus two caps, not one creased face'
assert oc.is_airtight(new)
assert abs(oc.volume(new) - 14) < 1e-7, 'independent exact quadratic Green-area * height = 14'
assert face_count(old) == 3 and digest(old) == old_hash, 'new construction cannot alter legacy master bytes'
assert digest(oc.from_recipe(legacy)) == old_hash, 'legacy replay keeps identical native bytes'
zero_version = copy.deepcopy(legacy)
zero_version['profile']['extrusion_kinks'] = 0
assert digest(oc.from_recipe(zero_version)) == old_hash, 'explicit zero retains legacy semantics'
bad_version = copy.deepcopy(legacy)
bad_version['profile']['extrusion_kinks'] = 2
try:
    oc.from_recipe(bad_version)
except oc.KernelError as error:
    assert 'unsupported extrusion kink recipe version' in str(error)
else:
    raise AssertionError('unknown kink recipe version must be refused')
assert face_count(oc.shape_from_bytes(oc.shape_to_bytes(new, triangles=False))) == 6
new_geom = b.extrude_curve(crv, 3, direction=(0, 0, 1))
assert k.recipe_of(new_geom)['profile']['extrusion_kinks'] == 1
assert face_count(k.master_of(new_geom)) == 6
native_geom = b.make_extrusion(crv, 3)
assert len(native_geom.Faces) == 6, 'plain command producer splits real openNURBS kinks too'
assert face_count(oc.from_recipe(k.recipe_of(native_geom))) == 6
regions = b.make_planar_srf([crv, b.make_circle((1, 1, 0), .2)])
assert len(regions) == 1
region = b.make_extrude_srf(regions[0], 3, direction=(0, 0, 1), split_profile_kinks=True)
assert face_count(k.master_of(region)) == 7, 'multi-profile prism splits outer kinks and keeps analytic hole'
assert oc.is_airtight(k.master_of(region))
assert abs(oc.volume(k.master_of(region)) - (14 - math.pi * .04 * 3)) < 1e-7
assert face_count(oc.from_recipe(k.recipe_of(region))) == 7
circle = b.extrude_curve(r.NurbsCurve.CreateFromCircle(r.Circle(3)), 4, direction=(0, 0, 1))
assert face_count(k.master_of(circle)) == 3, 'smooth cylinder remains one curved side and two caps'
assert abs(oc.volume(k.master_of(circle)) - math.pi * 9 * 4) < 1e-8
# Rational circle represented GENERICALLY: repeated C0 knots are G1-smooth,
# and must NOT be split merely because their multiplicity equals the degree.
ci = r.NurbsCurve.CreateFromCircle(r.Circle(3))
generic = {'ctype': 'nurbs', 'degree': ci.Degree,
           'poles': [[p.X / p.W, p.Y / p.W, p.Z / p.W] for p in ci.Points],
           'weights': [p.W for p in ci.Points], 'knots': list(ci.Knots), 'periodic': False,
           'extrusion_kinks': 1}
assert len(oc._unique_edges(oc.wire_from_curve(generic))) == 1, 'C0 but G1 circle knots are not creases'
# New recipe survives a native 3dm save/reload without changing the old one.
with tempfile.TemporaryDirectory(prefix='crease-roundtrip-') as directory:
    file = r.File3dm()
    file.Objects.AddBrep(new_geom, r.ObjectAttributes())
    path = str(Path(directory) / 'new.3dm')
    assert file.Write(path, 8)
    loaded = r.File3dm.Read(path).Objects[0].Geometry
    assert b.rehydrate_master(loaded)
    assert k.recipe_of(loaded) == k.recipe_of(new_geom)
    assert face_count(k.master_of(loaded)) == 6
    assert abs(oc.volume(k.master_of(loaded)) - 14) < 1e-7

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Pnt

for number, origin in (('synthetic-origin', (0, 0, -1.2)),
                       ('synthetic-translated', (17, -11, 4))):
    geom, joined, expected_volume = n['synthetic_legacy_prism'](origin)
    assert joined.SegmentCount == 7
    fresh_joined = b.make_extrusion(joined, -.5)
    assert face_count(k.master_of(fresh_joined)) == 9
    assert face_count(oc.from_recipe(k.recipe_of(fresh_joined))) == 9
    assert oc.is_airtight(k.master_of(fresh_joined))
    recipe_before = copy.deepcopy(k.recipe_of(geom))
    assert b.rehydrate_master(geom)
    master = k.master_of(geom)
    before = digest(master)
    assert face_count(master) == 10 and len(oc._unique_edges(master)) == 24
    old_volume = oc.volume(master)
    records = oc.extrusion_crease_curves(master)
    assert len(records) == 24, 'synthetic outer side has 24 genuine missing G1 crease isocurves'
    assert all('index' not in rec and rec['snap_feature'] == 'crease-isocurve' for rec in records)
    sources = b.object_snap_sources(geom)
    targets = [t for src, edge, extent in sources for t in b.snap_source_targets(src, edge)]
    creases = [t for t in targets if t.curve.GetUserString('Narwhal3D:SnapFeature') == 'crease-isocurve']
    assert len(creases) == 24 and len(targets) == 48
    # Prove exact membership against native master surfaces, independently of
    # the edge serializer and display tessellation. Each vertical line is .5 high.
    for target in creases:
        assert abs(math.dist(*target.vertices) - .5) < 1e-7
        for p in target.vertices:
            vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
            distance = BRepExtrema_DistShapeShape(vertex, master)
            distance.Perform()
            assert distance.IsDone() and distance.Value() < 1e-7
    assert digest(master) == before and face_count(master) == 10
    assert k.recipe_of(geom) == recipe_before and oc.volume(master) == old_volume
    replay = oc.from_recipe(recipe_before)
    assert digest(replay) == before, 'synthetic legacy native master fixture must replay unchanged'
    upgraded = oc.from_recipe(b._version_extrusion_profiles(recipe_before))
    assert face_count(upgraded) == 34, 'new hollow prism exposes all 24 internal kinks as real edges'
    assert oc.is_airtight(upgraded)
    assert abs(oc.volume(upgraded) - expected_volume) < 1e-7, 'independent exact Green-area of both loops * height'
    assert not oc.extrusion_crease_curves(upgraded), 'new faces have no hidden G1 creases'
    assert digest(master) == before, 'upgrading a new recipe must not mutate the old master'
    with tempfile.TemporaryDirectory(prefix='legacy-crease-roundtrip-') as directory:
        saved = r.File3dm()
        saved.Objects.AddBrep(geom, r.ObjectAttributes())
        filename = str(Path(directory) / 'legacy.3dm')
        assert saved.Write(filename, 8)
        reopened = r.File3dm.Read(filename).Objects[0].Geometry
        assert k.recipe_of(reopened) == recipe_before
        assert b.rehydrate_master(reopened)
        assert digest(k.master_of(reopened)) == before
        assert face_count(k.master_of(reopened)) == 10
    print(number, 'legacy faces=10 edges=24; snap-only creases=24; native bytes unchanged')
# The actual public display-profile generator has D1=0 at knot 20. Native
# edge/face Common used to fail there and abort every snap source for the object.
import ast
import importlib
source = Path(__file__).with_name('test_extrusion_display_mesh.py')
node = next(item for item in ast.parse(source.read_text()).body
            if isinstance(item, ast.FunctionDef) and item.name == 'traced_outline')
namespace = dict(math=math, tp=importlib.import_module('narwhal3d.trace_picture'), r3=r)
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
trace = namespace['traced_outline']()
trace_recipe = dict(kind='extrusion', profile=b.curve_recipe(trace),
                    dir=[0, 0, -52.0], solid=True)
trace_master = oc.from_recipe(trace_recipe)
trace_hash = digest(trace_master)
trace_records = oc.extrusion_crease_curves(trace_master)
assert len(trace_records) == 12, 'all actual traced-profile creases survive stationary knot 20'
assert all('index' not in rec for rec in trace_records)
assert digest(trace_master) == trace_hash and face_count(trace_master) == 3
print('LEGACY_TRACE_CLIP 12 exact creases; stationary u=20 included; master unchanged')

# A genuine nonrectangular trim and a hole crossing u=1. These are native
# pcurve wires on an exact extrusion surface, not polygons approximating geometry.
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.Geom import Geom_SurfaceOfLinearExtrusion
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Pnt2d, gp_Dir2d, gp_Trsf, gp_Vec
surface = next(BRepAdaptor_Surface(f).Surface().Surface()
               for f in oc.unique_faces(old)
               if isinstance(BRepAdaptor_Surface(f).Surface().Surface(), Geom_SurfaceOfLinearExtrusion))

def uv_wire(points):
    wire = BRepBuilderAPI_MakeWire()
    for start, end in zip(points, points[1:] + points[:1]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        line = Geom2d_Line(gp_Pnt2d(*start), gp_Dir2d(dx, dy))
        wire.Add(BRepBuilderAPI_MakeEdge(line, surface, 0, math.hypot(dx, dy)).Edge())
    return wire.Wire()

# CCW outer trapezoid; clockwise hole. At u=1, the top is v=2.5,
# while the hole removes .8 <= v <= 1.4. Bounds-only clipping is wrong.
outer = uv_wire([(0, 0), (2, 0), (2, 2), (0, 3)])
hole = uv_wire([(.75, .8), (.75, 1.4), (1.25, 1.4), (1.25, .8)])
maker = BRepBuilderAPI_MakeFace(surface, outer, True)
maker.Add(hole)
trimmed = maker.Face()
BRepLib.BuildCurves3d_s(trimmed)
assert BRepCheck_Analyzer(trimmed).IsValid(), 'hole witness must be a valid native trimmed face'
for moved in (False, True):
    witness = trimmed
    offset = (17, -11, 4) if moved else (0, 0, 0)
    if moved:
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(*offset))
        from OCP.TopLoc import TopLoc_Location
        witness = trimmed.Moved(TopLoc_Location(transform))
    before = digest(witness)
    records = oc.extrusion_crease_curves(witness)
    assert len(records) == 2, 'hole splits crease into two exact inside intervals'
    intervals = []
    for record in records:
        assert 'index' not in record and record['snap_feature'] == 'crease-isocurve'
        assert record['degree'] == 1 and len(record['poles']) == 2
        poles = record['poles']
        intervals.append(tuple(sorted((p[2] - offset[2] - surface.Value(1, 0).Z()) / surface.Direction().Z() for p in poles)))
        for point in poles:
            vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
            distance = BRepExtrema_DistShapeShape(vertex, witness)
            distance.Perform()
            assert distance.IsDone() and distance.Value() < 1e-7
    intervals.sort()
    assert all(abs(a - e) < 1e-8 for pair, expected in zip(intervals, [(0, .8), (1.4, 2.5)])
               for a, e in zip(pair, expected)), intervals
    # Independently show that the missing portion really is outside the face.
    point = surface.Value(1, 1.1)
    point.Translate(gp_Vec(*offset))
    distance = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(point).Vertex(), witness)
    distance.Perform()
    assert distance.Value() > .1, 'no crease may continue through the real trim hole'
    assert digest(witness) == before
print('TRIM_HOLE_CLIP valid native trapezoid + hole; located and unlocated; exact intervals PASS')

# This is the full lifetime fixture's wrapped cubic, not a polygon substitute.
# Support knot 9 lies outside active domain [2,8]; splitting there is invalid.
wrapped = b.make_polyline([(0, 14, 0), (3, 14, 0), (4, 16, 0),
                           (3, 18, 0), (0, 18, 0), (-1, 16, 0)],
                          degree=3, closed=True)
wrapped_recipe = b.curve_recipe(wrapped)
for shift in (0, -20, 20):
    legacy_profile = copy.deepcopy(wrapped_recipe)
    legacy_profile['knots'] = [u + shift for u in legacy_profile['knots']]
    versioned = dict(legacy_profile, extrusion_kinks=1)
    wire = oc.wire_from_curve(versioned)
    assert len(oc._unique_edges(wire)) == 1, 'smooth wrapped cubic must remain a single edge'
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    native_curve = BRepAdaptor_Curve(oc._unique_edges(wire)[0]).BSpline()
    assert abs(native_curve.FirstParameter() - (2 + shift)) < 1e-10
    assert abs(native_curve.LastParameter() - (8 + shift)) < 1e-10
    assert oc.extrusion_kink_parameters(native_curve) == [], 'support knots are not profile joins'
    recipe = dict(kind='extrusion', profile=versioned, dir=[0, 0, 3], solid=True)
    solid = oc.from_recipe(recipe)
    previous = oc.from_recipe(dict(recipe, profile=legacy_profile))
    assert oc.is_valid(solid) and oc.is_airtight(solid) and face_count(solid) == 3
    assert abs(oc.volume(solid) - oc.volume(previous)) < 1e-9
    assert digest(solid) == digest(previous), 'smooth wrapped profile retains exact legacy topology'
print('WRAPPED_LIFETIME_PROFILE positive/negative domains; support knots excluded PASS')

print('EXTRUSION_CREASES PASS')
