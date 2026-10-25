"""Every point, edge and face of every kind of SURFACE must move: exactly, validly, quickly.

The movability catalog covers closed solids. Open surfaces and polysurfaces need the
same guarantee. MEASURED before this round: the native move refused every input that was
not an airtight solid or a lone FACE, so a planar surface, a loft, an EdgeSrf, a Sweep1,
an open extrusion or a partial revolve could not have a single point dragged. Revolve
also had no exact kernel shape at all (it was a rhino3dm-only surface).

Each surface is moved part by part, +-X, +-Y, +-Z. Every move must succeed, stay valid,
keep the same number of faces and free (boundary) edges, keep every rebuilt B-spline
face unfolded, and finish in under four seconds. Revolve is also checked for exactness:
a revolved rectangle is the closed ring {PLANE:2, CYLINDER:2} with volume pi(R^2-r^2)h,
and its recipe replays to the same shape.
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
    print(f"SKIP: surface movability needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)
from narwhal3d import native_geometry  # noqa: E402

sweep = load("narwhal3d.sweep", "narwhal3d/sweep.py")

from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.BRepClass import BRepClass_FaceClassifier  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN  # noqa: E402
from OCP.TopExp import TopExp, TopExp_Explorer  # noqa: E402
from OCP.collections import (  # noqa: E402
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as AncestorMap)
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # noqa: E402

P = r3.Point3d


def polyline(points, closed=True):
    points = [P(*p) for p in points]
    return r3.PolylineCurve(r3.Polyline(points + ([points[0]] if closed else [])))


def arc(a, b, c):
    return r3.Arc(P(*a), P(*b), P(*c)).ToNurbsCurve()


def ring_profile():
    return polyline([(5, 0, 0), (10, 0, 0), (10, 0, 10), (5, 0, 10)])


def first(geometry):
    return geometry[0] if isinstance(geometry, list) else geometry


# (name, producer). Every entry is an OPEN surface or polysurface except "revolved_ring",
# the closed solid a full-turn closed profile must become.
SURFACES = [
    ("revolve_quarter", lambda: bridge.make_revolve(ring_profile(), (0, 0, 0), (0, 0, 1), 0, 90)),
    ("revolve_open_profile", lambda: bridge.make_revolve(
        polyline([(5, 0, 0), (10, 0, 0), (8, 0, 10), (5, 0, 10)], closed=False),
        (0, 0, 0), (0, 0, 1), 0, 360)),
    ("revolve_arc", lambda: bridge.make_revolve(
        arc((5, 0, 0), (9, 0, 4), (5, 0, 8)), (0, 0, 0), (0, 0, 1), 0, 360)),
    ("revolved_ring", lambda: bridge.make_revolve(ring_profile(), (0, 0, 0), (0, 0, 1), 0, 360)),
    ("planar", lambda: bridge.make_planar_srf(
        [polyline([(0, 0, 0), (30, 0, 0), (30, 20, 0), (0, 20, 0)])])),
    ("planar_with_hole", lambda: bridge.make_planar_srf(
        [polyline([(0, 0, 0), (30, 0, 0), (30, 20, 0), (0, 20, 0)]),
         bridge.make_circle((15, 10, 0), 4)])),
    ("extruded_open_L", lambda: bridge.extrude_curve(
        polyline([(0, 0, 0), (30, 0, 0), (30, 20, 0)], closed=False), 10,
        direction=(0, 0, 1), solid=False)),
    ("extruded_arc", lambda: bridge.extrude_curve(
        arc((0, 0, 0), (10, 5, 0), (20, 0, 0)), 10, direction=(0, 0, 1), solid=False)),
    ("loft", lambda: bridge.loft_brep(
        [r3.LineCurve(P(0, 0, 0), P(20, 0, 0)), arc((0, 10, 3), (10, 14, 6), (20, 10, 3))])),
    ("edge_srf", lambda: bridge.make_edge_srf(
        [r3.LineCurve(P(0, 0, 0), P(20, 0, 0)), r3.LineCurve(P(20, 0, 0), P(20, 20, 5)),
         r3.LineCurve(P(20, 20, 5), P(0, 20, 0)), r3.LineCurve(P(0, 20, 0), P(0, 0, 0))])),
    ("sweep1", lambda: sweep.sweep1(
        arc((0, 0, 0), (20, 10, 0), (40, 0, 0)),
        polyline([(0, -2, -2), (0, 2, -2), (0, 2, 2), (0, -2, 2)]))),
    ("sweep2", lambda: sweep.sweep2(
        r3.LineCurve(P(0, 0, 0), P(30, 0, 0)), arc((0, 20, 0), (15, 25, 0), (30, 20, 0)),
        arc((0, 0, 0), (0, 10, 5), (0, 20, 0)))),
    ("pipe_open", lambda: sweep.pipe(arc((0, 0, 0), (20, 10, 0), (40, 0, 0)), 2.0)),
    ("extruded_circle_open", lambda: bridge.extrude_curve(
        bridge.make_circle((0, 0, 0), 5), 10, direction=(0, 0, 1), solid=False)),
    ("open_cone", lambda: bridge.make_cone((0, 0, 0), 10, 20, solid=False)),
]
STEP = 2.0


def free_edge_count(shape):
    ancestors = AncestorMap()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    count = 0
    for index in range(1, ancestors.Size() + 1):
        edge = TopoDS.Edge(ancestors.FindKey(index))
        if not BRep_Tool.Degenerated_s(edge) and len(list(ancestors.FindFromIndex(index))) == 1:
            count += 1
    return count


def has_degenerate_edge(face):
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        if BRep_Tool.Degenerated_s(TopoDS.Edge(explorer.Current())):
            return True
        explorer.Next()
    return False


def worst_jacobian_ratio(shape, grid=12):
    worst = 1.0
    for face in occt.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face, True)
        if adaptor.GetType().name != "GeomAbs_BSplineSurface" or has_degenerate_edge(face):
            continue
        u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
        v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
        tolerance = BRep_Tool.Tolerance_s(face)
        values = []
        for i in range(grid):
            for j in range(grid):
                u = u0 + (u1 - u0) * (i + 0.5) / grid
                v = v0 + (v1 - v0) * (j + 0.5) / grid
                if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance).State() != TopAbs_IN:
                    continue
                point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
                adaptor.D1(u, v, point, du, dv)
                values.append(du.Crossed(dv).Magnitude())
        if values and max(values) > 0:
            worst = min(worst, min(values) / max(values))
    return worst


failures = []

# Revolve is exact, honest and replayable.
ring = bridge.make_revolve(ring_profile(), (0, 0, 0), (0, 0, 1), 0, 360)
ring_shape = kernel.master_of(ring)
assert ring_shape is not None, "Revolve produced no exact kernel shape"
assert kernel.honesty_problem(ring) is None, kernel.honesty_problem(ring)
assert occt.face_types(ring_shape) == {"PLANE": 2, "CYLINDER": 2}, occt.face_types(ring_shape)
assert occt.is_airtight(ring_shape), "a full-turn closed profile must revolve to a solid"
want = math.pi * (10 ** 2 - 5 ** 2) * 10
assert abs(occt.volume(ring_shape) - want) < 1e-6 * want, (occt.volume(ring_shape), want)
recipe = kernel.recipe_of(ring)
assert recipe and recipe["kind"] == "revolve", recipe
replayed = occt.from_recipe(recipe)
assert abs(occt.volume(replayed) - want) < 1e-6 * want, "revolve recipe does not replay"
cone = kernel.master_of(bridge.make_cone((1, 2, 3), 10, 20, solid=False))
assert cone is not None and occt.face_types(cone) == {"CONE": 1}, "open cone is not exact"
assert not occt.is_airtight(cone), "Solid=No must leave the cone open"
quarter = kernel.master_of(bridge.make_revolve(ring_profile(), (0, 0, 0), (0, 0, 1), 0, 90))
assert quarter is not None and not occt.is_airtight(quarter), "a 90-degree revolve stays open"
started_rotated = kernel.master_of(
    bridge.make_revolve(ring_profile(), (0, 0, 0), (0, 0, 1), 90, 180))
box = Bnd_Box()
# Exact bounds: Add() may pad by a coarse triangulation or bounding polygon.
BRepBndLib.AddOptimal_s(started_rotated, box, False, False)
x0, y0, x1, y1 = box.CornerMin().X(), box.CornerMin().Y(), box.CornerMax().X(), box.CornerMax().Y()
assert x1 < 1e-3 and y0 > -1e-3 and x0 < -9.99, ("start angle ignored", (x0, y0, x1, y1))


def distance_to(shape, point):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    return BRepExtrema_DistShapeShape(
        BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex(), shape).Value()


def moved_rim(shape, point, vector):
    """Move the closed rim edge that passes through `point`."""
    for index, edge in enumerate(occt._unique_edges(shape), 1):
        if distance_to(edge, point) < 1e-6 and BRep_Tool.IsClosed_s(edge):
            result = native_geometry.move_component(
                shape, lambda: occt.shape_to_bytes(shape, triangles=False), 2, index,
                list(vector), live=False)
            assert result.ok, f"rim through {point} refused: {result.detail}"
            return occt.shape_from_bytes(result.data)
    raise AssertionError(f"no closed rim through {point}")


# A curved wall must keep its curvature when one rim moves: the far side of the wall is
# untouched, not replaced by a straight ruled surface between the rims. MEASURED before
# the rim stretch: moving the top rim of the revolved arc collapsed its 9 mm bulge to a
# 5 mm cylinder, and moving one end of the bent pipe straightened the whole tube.
bulge = kernel.master_of(bridge.make_revolve(
    arc((5, 0, 0), (9, 0, 4), (5, 0, 8)), (0, 0, 0), (0, 0, 1), 0, 360))
stretched = moved_rim(bulge, (5, 0, 8), (0, 0, 1.0))
assert distance_to(stretched, (9, 0, 4)) < 0.6, (
    "revolved arc lost its bulge", distance_to(stretched, (9, 0, 4)))
bent = kernel.master_of(first(sweep.pipe(arc((0, 0, 0), (20, 10, 0), (40, 0, 0)), 2.0))) \
    or bridge._occt_operand(first(sweep.pipe(arc((0, 0, 0), (20, 10, 0), (40, 0, 0)), 2.0)))
end_point = next(point for point in ((40, 0, 2), (40, 0, -2), (40, 2, 0), (40, -2, 0))
                 if distance_to(bent, point) < 1e-6)
for vector in ((0, 0, -0.5), (1.0, 0, 0)):
    tube = moved_rim(bent, end_point, vector)
    middle = distance_to(tube, (20, 10, 0))
    assert abs(middle - 2.0) < 0.3, ("bent pipe straightened", vector, middle)

ONLY = {name for name in os.environ.get("NARWHAL_CATALOG_ONLY", "").split(",") if name}
moves, slowest = 0, 0.0
started = time.perf_counter()
for name, producer in [entry for entry in SURFACES if not ONLY or entry[0] in ONLY]:
    geometry = first(producer())
    shape = kernel.master_of(geometry) or bridge._occt_operand(geometry)
    if shape is None:
        failures.append(f"{name}: no exact OCCT shape")
        continue
    data = occt.shape_to_bytes(shape, triangles=False)
    face_count = len(occt.unique_faces(shape))
    free_edges = free_edge_count(shape)
    closed = occt.is_airtight(shape)
    parts = {"vertex": occt._unique_vertices(shape), "edge": occt._unique_edges(shape),
             "face": occt.unique_faces(shape)}
    for kind, code in (("vertex", 3), ("edge", 2), ("face", 1)):
        for index, part in enumerate(parts[kind], 1):
            if kind == "edge" and BRep_Tool.Degenerated_s(part):
                continue
            for axis in range(3):
                for sign in (1.0, -1.0):
                    vector = [0.0, 0.0, 0.0]
                    vector[axis] = sign * STEP
                    key = (name, kind, index, tuple(vector))
                    t = time.perf_counter()
                    result = native_geometry.move_component(
                        shape, lambda: data, code, index, vector, live=False)
                    elapsed = time.perf_counter() - t
                    slowest = max(slowest, elapsed)
                    moves += 1
                    if not result.ok:
                        failures.append(f"{key}: {result.detail}")
                        continue
                    moved = occt.shape_from_bytes(result.data)
                    if not occt.is_valid(moved):
                        failures.append(f"{key}: invalid result")
                    elif len(occt.unique_faces(moved)) != face_count:
                        failures.append(f"{key}: face count changed")
                    elif free_edge_count(moved) != free_edges or occt.is_airtight(moved) != closed:
                        failures.append(f"{key}: boundary changed")
                    elif worst_jacobian_ratio(moved) < 0.02:
                        failures.append(f"{key}: a rebuilt face folds")
                    elif elapsed > 4.0:
                        failures.append(f"{key}: took {elapsed:.2f}s")
assert not failures, f"{len(failures)} of {moves} moves failed:\n" + "\n".join(failures[:30])
print(f"SURFACE MOVABILITY PASSED ({len(SURFACES)} surfaces, {moves} moves, "
      f"slowest {slowest:.2f}s, {time.perf_counter() - started:.0f}s total)")
