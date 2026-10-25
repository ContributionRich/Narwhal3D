"""Bug #109: sequential point edits on a six-edge cap must stay smooth, exact and fast.

After bug #108 the FIRST lift of a cap corner produced a smooth bicubic cap, but every
later edit of the same cap went wrong (MEASURED on the captured model, which this test
rebuilds exactly):

  * a second vertex of the now-curved cap could not be moved at all ("no valid exact
    local-move candidate");
  * moving the first vertex back down folded the cap (Jacobian ratio 0.004, 143-degree
    normal flip) and took 7 s; moving a third vertex took 15 s;
  * once that was routed through the height field, one edit spent 139 s inside OCCT's
    self-interference check, because the fitted cap crossed its shared curved edge back
    and forth (57 intersection fragments);
  * every edit loosened the next edit's tolerance (1.2e-4 -> 5.6e-4 -> 2e-3 mm);
  * a file saved before a rebuild change could not replay its later recipe steps.
"""
import importlib.util
import math
import os
import sys
import time
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relative))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kernel = load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py")
bridge = load("narwhal3d.bridge", "narwhal3d/bridge.py")
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

if not occt.available():
    print(f"SKIP: bug #109 needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)

from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d, BRepAdaptor_Surface  # noqa: E402
from OCP.BRepClass import BRepClass_FaceClassifier  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GeomAbs import GeomAbs_BSplineSurface  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # noqa: E402

P = r3.Point3d
TOP = 60.98363105980431
Y0, Y1 = 27.192013178990976, 79.19396211049502
X_RIGHT, X_LEFT = 59.13986296002858, -65.80825076615129
LOBE_START, LOBE_END = -34.506508389794206, 26.840992725596244
LOBE = [(LOBE_START, Y0), (-37.71640343692346, 23.166652612828834),
        (-51.174893262019, 2.7102911468990163), (-51.13679486911738, -27.561601156379723),
        (6.149856511277747, -48.954101066615664), (37.86417150340613, -3.9854711529905282),
        (29.918259242830715, 21.522836117669193), (LOBE_END, Y0)]
LOBE_KNOTS = [-6.261722392999202] * 3 + [-6.0, -5.0, -4.0, -3.0] + [-2.6686419209164414] * 3

profile = r3.PolyCurve()
polyline = r3.Polyline([P(LOBE_END, Y0, 0), P(X_RIGHT, Y0, 0), P(X_RIGHT, Y1, 0),
                        P(X_LEFT, Y1, 0), P(X_LEFT, Y0, 0), P(LOBE_START, Y0, 0)])
assert profile.Append(r3.PolylineCurve(polyline))
lobe = r3.NurbsCurve(3, False, 4, len(LOBE))
for index, (x, y) in enumerate(LOBE):
    lobe.Points[index] = r3.Point4d(x, y, 0.0, 1.0)
for index, knot in enumerate(LOBE_KNOTS):
    lobe.Knots[index] = knot
assert lobe.IsValid and profile.Append(lobe) and profile.IsClosed
extrusion = bridge.make_extrusion(profile, TOP)


def shape_of(geometry):
    return kernel.master_of(geometry) or bridge._occt_operand(geometry)


def topology(shape):
    return (len(occt.unique_faces(shape)), len(occt._unique_edges(shape)),
            len(occt._unique_vertices(shape)))


def vertex_index(shape, point):
    points = [occt._local_point(vertex) for vertex in occt._unique_vertices(shape)]
    index = min(range(len(points)), key=lambda k: occt._local_distance(points[k], point))
    assert occt._local_distance(points[index], point) < 1e-6, (point, points[index])
    return index + 1


def cap_of(shape):
    caps = []
    for face in occt.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face, True)
        if adaptor.GetType() == GeomAbs_BSplineSurface:
            surface = adaptor.BSpline()
            if surface.UDegree() == 3 and surface.VDegree() == 3:
                caps.append(face)
    assert len(caps) == 1, f"expected one bicubic cap, found {len(caps)}"
    return caps[0]


def cap_quality(face, grid=25):
    adaptor = BRepAdaptor_Surface(face, True)
    u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
    tolerance = BRep_Tool.Tolerance_s(face)
    jacobians, normals = [], {}
    for i in range(grid):
        for j in range(grid):
            u = u0 + (u1 - u0) * (i + 0.5) / grid
            v = v0 + (v1 - v0) * (j + 0.5) / grid
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance).State() != TopAbs_IN:
                continue
            point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
            adaptor.D1(u, v, point, du, dv)
            cross = du.Crossed(dv)
            jacobians.append(cross.Magnitude())
            if cross.Magnitude() > 0:
                normals[(i, j)] = cross.Divided(cross.Magnitude())
    turn = 0.0
    for (i, j), normal in normals.items():
        for step in ((1, 0), (0, 1)):
            other = normals.get((i + step[0], j + step[1]))
            if other is not None:
                turn = max(turn, math.degrees(normal.Angle(other)))
    assert len(jacobians) > 100
    return min(jacobians) / max(jacobians), turn


def face_area(face):
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def assert_tolerances_bound_true_gaps(shape, limit):
    for face in occt.unique_faces(shape):
        surface = BRep_Tool.Surface_s(face)
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            explorer.Next()
            curve, pcurve = BRepAdaptor_Curve(edge), BRepAdaptor_Curve2d(edge, face)
            first, last = curve.FirstParameter(), curve.LastParameter()
            gap = 0.0
            for sample in range(1001):
                fraction = 0.5 - 0.5 * math.cos(math.pi * sample / 1000)
                parameter = first + (last - first) * fraction
                uv = pcurve.Value(parameter)
                gap = max(gap, curve.Value(parameter).Distance(surface.Value(uv.X(), uv.Y())))
            tolerance = BRep_Tool.Tolerance_s(edge)
            assert gap <= tolerance, f"edge tolerance {tolerance:.3e} hides a {gap:.3e} gap"
            assert tolerance <= limit, f"edge tolerance {tolerance:.3e} crept past {limit:.1e}"


def move(geometry, point, dz, *, smooth=0.4):
    shape = shape_of(geometry)
    started = time.perf_counter()
    moved = bridge.move_subobject(geometry, "vertex", vertex_index(shape, point), (0.0, 0.0, dz))
    elapsed = time.perf_counter() - started
    result = shape_of(moved)
    assert elapsed < 5.0, f"moving {point} by {dz} blocked for {elapsed:.2f}s"
    assert topology(result) == (8, 18, 12)
    assert occt.is_valid(result) and occt.is_airtight(result)
    assert kernel.honesty_problem(moved) is None
    assert occt.face_types(result) == {"PLANE": 6, "BSPLINE": 2}, occt.face_types(result)
    cap = cap_of(result)
    # The five straight cap edges stay analytic LINES (not B-spline copies), so the
    # surrounding signature -- and STEP export -- does not change type on every edit.
    explorer, lines = TopExp_Explorer(cap, TopAbs_EDGE), 0
    while explorer.More():
        lines += BRepAdaptor_Curve(TopoDS.Edge(explorer.Current())).GetType().name == "GeomAbs_Line"
        explorer.Next()
    assert lines == 5, f"cap keeps {lines} of its 5 straight edges as lines"
    jacobian_ratio, turn = cap_quality(cap)
    assert jacobian_ratio > smooth, f"cap folds: Jacobian ratio {jacobian_ratio:.4f}"
    assert turn < 60.0, f"cap creases: normals turn {turn:.1f} deg"
    # 5e-6 of the part: no tolerance creep across edits (the bug reached 1.2e-5 after two).
    assert_tolerances_bound_true_gaps(result, 5e-6 * 170.0)
    return moved, elapsed, face_area(cap)


timings = []
corner = (LOBE_END, Y0, TOP)
first, elapsed, _ = move(extrusion, corner, 34.46427764943129)
timings.append(elapsed)
lifted = (LOBE_END, Y0, TOP + 34.46427764943129)

# The other lobe end, both ways: refused outright before, then 139 s downward.
other = (LOBE_START, Y0, TOP)
for dz in (-15.0, 15.0):
    _, elapsed, _ = move(first, other, dz)
    timings.append(elapsed)

# A rectangle corner of the curved cap: 15 s through the folded composite before.
_, elapsed, _ = move(first, (X_LEFT, Y1, TOP), 24.593)
timings.append(elapsed)

# Back down part of the way: folded before. The cap is rebuilt from its boundary alone,
# so +34.46 then -16.17 must match one +18.29 lift.
second, elapsed, area_two_steps = move(first, lifted, -16.17106587454181, smooth=0.6)
timings.append(elapsed)
_, _, area_one_step = move(extrusion, corner, 34.46427764943129 - 16.17106587454181, smooth=0.6)
assert abs(area_two_steps - area_one_step) <= 1e-3 * area_one_step, (area_two_steps, area_one_step)

third, elapsed, _ = move(second, (X_LEFT, Y1, TOP), 24.593)
timings.append(elapsed)

recipe = kernel.recipe_of(third)
replay = occt.from_recipe(recipe)
assert topology(replay) == (8, 18, 12) and occt.is_airtight(replay)
assert abs(occt.volume(replay) - occt.volume(shape_of(third))) <= 1e-6 * occt.volume(replay)

# Legacy replay: a stored vertex target whose neighbourhood TYPES no longer match (the
# kernel rebuilt its curves differently since the file was saved) still resolves by its
# exact position -- and only uniquely.
base = shape_of(first)
index = vertex_index(base, lifted)
target = occt.local_subobject_signature(base, "vertex", index)
target["adjacency"] = dict(target["adjacency"], edge_types=["CIRCLE", "CIRCLE", "CIRCLE"])
tolerances = occt._local_tolerances(base)
assert occt._local_resolve_target(base, "vertex", index, target, tolerances) == index - 1
target["centroid"] = [value + 1.0 for value in target["centroid"]]
try:
    occt._local_resolve_target(base, "vertex", index, target, tolerances)
except occt.KernelError:
    pass
else:
    raise AssertionError("a vertex target matching no position must still be refused")

print(f"BUG 109 SEQUENTIAL CAP EDITS PASSED (slowest {max(timings):.2f}s)")
