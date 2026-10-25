"""Bug #108: lifting one vertex of a six-edge extrusion cap must give the reference smooth cap.

A user moved the corner vertex of an extruded rectangle-with-a-curved-lobe straight up
by 20 mm. The reference application (MEASURED from the captured .3dm, reproduced here
as a fixture):

  * keeps every edge exact, but RAMPS the curved top edge -- its interior control points
    rise in proportion to their Greville abscissae -- instead of dragging only its end
    control point (which kinked the edge and folded the cap into a 180-degree spike);
  * rebuilds the cap as a smooth degree-3x3 height field over the old cap plane:
    area 11613 mm^2, Jacobian ratio 0.81, never ballooning far past the lifted corner.

Our first fix (a free OCCT plate) ballooned to 3.6x the lift and silently carried a
1 mm surface-to-edge gap behind a 1.4e-4 "G0 error". So this test asks for the shape,
for edge tolerances that really bound the gap, and for a move that does not freeze.
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
    print(f"SKIP: bug #108 needs OCCT ({occt.unavailable_reason()})")
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
LOBE = [(-45.225, 27.192), (-48.435, 23.167), (-61.893, 2.710), (-61.855, -27.562),
        (-4.568, -48.954), (27.146, -3.985), (19.200, 21.523), (16.123, 27.192)]
LOBE_KNOTS = [4.251, 4.251, 4.251, 4.512, 5.512, 6.512, 7.512, 7.844, 7.844, 7.844]
# MEASURED: the reference's lifted lobe edge, control-point heights above the cap for a 20 mm lift.
REFERENCE_LOBE_RISE = [0.0, 0.485, 2.826, 7.023, 12.589, 16.915, 19.385, 20.0]
REFERENCE_CAP_AREA = 11613.0


def lobe_curve():
    curve = r3.NurbsCurve(3, False, 4, len(LOBE))
    for index, (x, y) in enumerate(LOBE):
        curve.Points[index] = r3.Point4d(x, y, 0.0, 1.0)
    for index, knot in enumerate(LOBE_KNOTS):
        curve.Knots[index] = knot
    assert curve.IsValid
    return curve


profile = r3.PolyCurve()
for start, end in [((16.123, 27.192), (48.422, 27.192)), ((48.422, 27.192), (48.422, 79.194)),
                   ((48.422, 79.194), (-76.526, 79.194)), ((-76.526, 79.194), (-76.526, 27.192)),
                   ((-76.526, 27.192), (-45.225, 27.192))]:
    assert profile.Append(r3.LineCurve(P(*start, 0), P(*end, 0)))
assert profile.Append(lobe_curve())
assert profile.IsClosed and profile.SegmentCount == 6

extrusion = bridge.make_extrusion(profile, TOP)


def shape_of(geometry):
    return kernel.master_of(geometry) or bridge._occt_operand(geometry)


def topology(shape):
    return (len(occt.unique_faces(shape)), len(occt._unique_edges(shape)),
            len(occt._unique_vertices(shape)))


def edges_of(shape):
    out = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        out.append(TopoDS.Edge(explorer.Current()))
        explorer.Next()
    return out


def cap_of(shape):
    caps = []
    for face in occt.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face, True)
        if adaptor.GetType() == GeomAbs_BSplineSurface:
            surface = adaptor.BSpline()
            if surface.UDegree() == 3 and surface.VDegree() == 3:
                caps.append((face, surface))
    assert len(caps) == 1, f"expected one bicubic cap, found {len(caps)}"
    return caps[0]


def cap_quality(face, grid=25):
    """Smallest/largest surface Jacobian and worst neighbour normal turn inside the trim."""
    adaptor = BRepAdaptor_Surface(face, True)
    u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
    tolerance = BRep_Tool.Tolerance_s(face)
    jacobians, normals, heights = [], {}, []
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
            heights.append(point.Z())
            if cross.Magnitude() > 0:
                normals[(i, j)] = cross.Divided(cross.Magnitude())
    worst_turn = 0.0
    for (i, j), normal in normals.items():
        for step in ((1, 0), (0, 1)):
            other = normals.get((i + step[0], j + step[1]))
            if other is not None:
                worst_turn = max(worst_turn, math.degrees(normal.Angle(other)))
    assert len(jacobians) > 100
    return min(jacobians) / max(jacobians), worst_turn, min(heights), max(heights)


def face_area(face):
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def assert_tolerances_bound_true_gaps(shape):
    for face in occt.unique_faces(shape):
        surface = BRep_Tool.Surface_s(face)
        for edge in edges_of(face):
            if BRep_Tool.Degenerated_s(edge):
                continue
            curve, pcurve = BRepAdaptor_Curve(edge), BRepAdaptor_Curve2d(edge, face)
            first, last = curve.FirstParameter(), curve.LastParameter()
            gap = 0.0
            for sample in range(2001):
                fraction = 0.5 - 0.5 * math.cos(math.pi * sample / 2000)
                parameter = first + (last - first) * fraction
                uv = pcurve.Value(parameter)
                gap = max(gap, curve.Value(parameter).Distance(surface.Value(uv.X(), uv.Y())))
            tolerance = BRep_Tool.Tolerance_s(edge)
            assert gap <= tolerance, f"edge tolerance {tolerance:.3e} hides a {gap:.3e} gap"
            assert tolerance <= 1e-3, f"edge tolerance {tolerance:.3e} is not manufacturable"


source = shape_of(extrusion)
assert topology(source) == (8, 18, 12)
points = [occt._local_point(vertex) for vertex in occt._unique_vertices(source)]
selected = 1 + min(range(len(points)), key=lambda index: occt._local_distance(
    points[index], (16.123, 27.192, TOP)))
assert occt._local_distance(points[selected - 1], (16.123, 27.192, TOP)) < 1e-6


def lift(dz):
    started = time.perf_counter()
    moved = bridge.move_subobject(extrusion, "vertex", selected, (0.0, 0.0, dz))
    elapsed = time.perf_counter() - started
    shape = shape_of(moved)
    assert topology(shape) == topology(source)
    assert occt.is_valid(shape) and occt.is_airtight(shape)
    assert kernel.honesty_problem(moved) is None
    assert occt.face_types(shape) == {"PLANE": 6, "BSPLINE": 2}, occt.face_types(shape)
    assert elapsed < 5.0, f"lifting the cap corner by {dz} blocked for {elapsed:.2f}s"
    return moved, shape, elapsed


timings = []
moved, shape, elapsed = lift(20.0)
timings.append(elapsed)
cap, cap_surface = cap_of(shape)
jacobian_ratio, normal_turn, low, high = cap_quality(cap)
# The folded cap measured a Jacobian ratio of 4e-3 and a 139-degree normal flip.
assert jacobian_ratio > 0.5, f"cap is nearly singular: Jacobian ratio {jacobian_ratio:.4f}"
assert normal_turn < 45.0, f"cap creases: neighbouring normals turn {normal_turn:.1f} deg"
# The reference overshoots the lifted corner by 18%; the free plate overshot by 260%.
assert high < TOP + 20.0 * 1.4, f"cap balloons to {high:.2f}"
assert low > TOP - 20.0 * 0.1, f"cap sags to {low:.2f}"
area = face_area(cap)
assert abs(area - REFERENCE_CAP_AREA) / REFERENCE_CAP_AREA < 0.02, f"cap area {area:.0f} vs reference {REFERENCE_CAP_AREA:.0f}"

lobe_edges = []
for edge in occt._unique_edges(shape):
    curve = BRepAdaptor_Curve(edge)
    if curve.GetType().name == "GeomAbs_BSplineCurve":
        bspline = curve.BSpline()
        heights = [bspline.Pole(index).Z() for index in range(1, bspline.NbPoles() + 1)]
        if max(heights) > TOP + 1.0:
            lobe_edges.append(sorted(height - TOP for height in heights))
assert len(lobe_edges) == 1, lobe_edges
for ours, expected in zip(lobe_edges[0], REFERENCE_LOBE_RISE):
    # 0.01: the fixture's knots are the reference's rounded to 3 decimals.
    assert abs(ours - expected) < 0.01, f"lobe edge rise {lobe_edges[0]} vs reference {REFERENCE_LOBE_RISE}"
assert_tolerances_bound_true_gaps(shape)

recipe = kernel.recipe_of(moved)
assert recipe["kind"] == "local_move" and recipe["a"] == kernel.recipe_of(extrusion)
replay = occt.from_recipe(recipe)
assert topology(replay) == topology(source)
assert occt.is_valid(replay) and occt.is_airtight(replay)
assert abs(occt.volume(replay) - occt.volume(shape)) <= 1e-6 * occt.volume(shape)

# Downward: MEASURED 9.4 s inside the exact self-interference check (and 100 s for a
# 40 mm lift) while the fitted cap crossed its shared curved edge back and forth; the
# fit now keeps that gap one-signed (bug #109).
moved_down, shape_down, elapsed = lift(-20.0)
timings.append(elapsed)
cap, _ = cap_of(shape_down)
jacobian_ratio, normal_turn, low, high = cap_quality(cap)
assert jacobian_ratio > 0.5 and normal_turn < 45.0, (jacobian_ratio, normal_turn)
assert low > TOP - 20.0 * 1.4 and high < TOP + 20.0 * 0.1, (low, high)
assert_tolerances_bound_true_gaps(shape_down)

print("BUG 108 SMOOTH SIX-EDGE CAP PASSED "
      f"(area {area:.0f} vs reference {REFERENCE_CAP_AREA:.0f}; {max(timings):.2f}s)")
