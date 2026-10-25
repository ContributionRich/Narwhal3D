"""Every point, edge and face of every kind of solid must move -- exactly and quickly.

Bug #110 asked for "every single possible combination of objects" to have movable
points, curves and surfaces. This is the matrix that answers it: one solid from each
producer (primitives, extrusions of straight, curved, closed and mixed profiles,
booleans, holes, fillets), every pickable part, +-X, +-Y, +-Z. MEASURED before this
round: cylinders, cones, tubes, extruded circles/ellipses, spheres, ellipsoids and tori
refused nearly every move (seams, closed rims, apexes and poles were unsupported), and
a box with a hole refused all 32 parts (no face with an inner wire could be rebuilt).

Each move must succeed, stay valid and airtight, keep every rebuilt B-spline face
unfolded, and finish in under four seconds. The only tolerated refusals are listed in
KNOWN with the measured reason; anything else -- or a listed refusal that starts to
work -- fails, so the list cannot go stale.
"""
import importlib.util
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
    print(f"SKIP: movability catalog needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)
from narwhal3d import native_geometry  # noqa: E402

from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepClass import BRepClass_FaceClassifier  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # noqa: E402

P = r3.Point3d


def polyline(points):
    return r3.PolylineCurve(r3.Polyline([P(*p) for p in points] + [P(*points[0])]))


def keyhole():
    curve = r3.PolyCurve()
    curve.Append(r3.LineCurve(P(0, 0, 0), P(40, 0, 0)))
    curve.Append(r3.Arc(P(40, 0, 0), P(60, 20, 0), P(40, 40, 0)).ToNurbsCurve())
    curve.Append(r3.LineCurve(P(40, 40, 0), P(0, 40, 0)))
    curve.Append(r3.LineCurve(P(0, 40, 0), P(0, 0, 0)))
    return curve


def slot():
    curve = r3.PolyCurve()
    curve.Append(r3.LineCurve(P(0, 0, 0), P(40, 0, 0)))
    curve.Append(r3.Arc(P(40, 0, 0), P(50, 10, 0), P(40, 20, 0)).ToNurbsCurve())
    curve.Append(r3.LineCurve(P(40, 20, 0), P(0, 20, 0)))
    curve.Append(r3.Arc(P(0, 20, 0), P(-10, 10, 0), P(0, 0, 0)).ToNurbsCurve())
    return curve


def box():
    return bridge.make_box((0, 0, 0), (30, 20, 0), 10)


# (name, producer, step): a step below the smallest feature, so a move cannot
# legitimately fold a face over itself (box_union_cyl has 2 mm wide faces).
CATALOG = [
    ("box", box, 3.0),
    ("cylinder", lambda: bridge.make_cylinder((0, 0, 0), 10, 20), 3.0),
    ("cone", lambda: bridge.make_cone((0, 0, 0), 10, 20), 3.0),
    ("truncated_cone", lambda: bridge.make_truncated_cone((0, 0, 0), 10, 5, 20), 3.0),
    ("tube", lambda: bridge.make_tube((0, 0, 0), 10, 6, 20), 2.0),
    ("sphere", lambda: bridge.make_sphere((0, 0, 0), 10), 3.0),
    ("ellipsoid", lambda: bridge.make_ellipsoid((0, 0, 0), 12, 8, 6), 3.0),
    ("torus", lambda: bridge.make_torus((0, 0, 0), 20, 5), 3.0),
    ("pyramid", lambda: bridge.make_pyramid((0, 0, 0), 10, 5, 15), 3.0),
    ("extruded_triangle", lambda: bridge.make_extrusion(
        polyline([(0, 0, 0), (30, 0, 0), (10, 20, 0)]), 10), 3.0),
    ("extruded_L", lambda: bridge.make_extrusion(polyline(
        [(0, 0, 0), (30, 0, 0), (30, 10, 0), (10, 10, 0), (10, 30, 0), (0, 30, 0)]), 10), 3.0),
    ("extruded_circle", lambda: bridge.make_extrusion(bridge.make_circle((0, 0, 0), 10), 10), 3.0),
    ("extruded_ellipse", lambda: bridge.make_extrusion(
        bridge.make_ellipse((0, 0, 0), (15, 0, 0), (0, 8, 0)), 10), 3.0),
    ("extruded_keyhole", lambda: bridge.make_extrusion(keyhole(), 10), 3.0),
    ("extruded_slot", lambda: bridge.make_extrusion(slot(), 10), 3.0),
    ("box_union_cylinder", lambda: bridge.boolean_brep(
        box(), bridge.make_cylinder((30, 10, 0), 8, 10), "UNION"), 1.0),
    ("box_with_hole", lambda: bridge.boolean_brep(
        box(), bridge.make_cylinder((15, 10, -1), 4, 12), "DIFFERENCE"), 3.0),
    ("box_with_fillet", lambda: bridge.bevel_edges_occt(box(), 2.0, edge_indices=[1]), 3.0),
]

# MEASURED limitation, kept honest: OCCT's self-interference check reports a translated
# copy of this B-spline ellipsoid as self-intersecting although it accepts the original,
# so a sideways pole stretch cannot be certified and is refused (an exact rigid move of
# the whole ellipsoid is certified from the source instead).
KNOWN = {
    ("ellipsoid", "vertex", 1, (3.0, 0.0, 0.0)), ("ellipsoid", "vertex", 1, (-3.0, 0.0, 0.0)),
    ("ellipsoid", "vertex", 1, (0.0, 3.0, 0.0)), ("ellipsoid", "vertex", 2, (3.0, 0.0, 0.0)),
    ("ellipsoid", "vertex", 2, (-3.0, 0.0, 0.0)), ("ellipsoid", "vertex", 2, (0.0, -3.0, 0.0)),
}


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
        # Apexes and poles have a vanishing Jacobian by construction.
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


# NARWHAL_CATALOG_ONLY=name1,name2 restricts the run (for focused mutation checks).
ONLY = {name for name in os.environ.get("NARWHAL_CATALOG_ONLY", "").split(",") if name}
CATALOG = [entry for entry in CATALOG if not ONLY or entry[0] in ONLY]
KNOWN = {key for key in KNOWN if not ONLY or key[0] in ONLY}
failures, surprises, moves, slowest = [], [], 0, 0.0
started = time.perf_counter()
for name, producer, step in CATALOG:
    geometry = producer()
    shape = kernel.master_of(geometry) or bridge._occt_operand(geometry)
    assert shape is not None, f"{name} has no exact OCCT shape"
    data = occt.shape_to_bytes(shape, triangles=False)
    parts = {"vertex": occt._unique_vertices(shape), "edge": occt._unique_edges(shape),
             "face": occt.unique_faces(shape)}
    for kind, code in (("vertex", 3), ("edge", 2), ("face", 1)):
        for index, part in enumerate(parts[kind], 1):
            if kind == "edge" and BRep_Tool.Degenerated_s(part):
                continue  # a zero-length apex/pole edge is not a pickable part
            for axis in range(3):
                for sign in (1.0, -1.0):
                    vector = [0.0, 0.0, 0.0]
                    vector[axis] = sign * step
                    key = (name, kind, index, tuple(vector))
                    t = time.perf_counter()
                    result = native_geometry.move_component(
                        shape, lambda: data, code, index, vector, live=False)
                    elapsed = time.perf_counter() - t
                    slowest = max(slowest, elapsed)
                    moves += 1
                    if not result.ok:
                        if key not in KNOWN:
                            failures.append(f"{key}: {result.detail}")
                        continue
                    if key in KNOWN:
                        surprises.append(f"{key} now succeeds: remove it from KNOWN")
                    moved = occt.shape_from_bytes(result.data)
                    if not (occt.is_valid(moved) and occt.is_airtight(moved)):
                        failures.append(f"{key}: invalid or open result")
                    elif worst_jacobian_ratio(moved) < 0.02:
                        failures.append(f"{key}: a rebuilt face folds")
                    elif elapsed > 4.0:
                        failures.append(f"{key}: took {elapsed:.2f}s")
assert not failures and not surprises, (
    f"{len(failures)} of {moves} moves failed:\n" + "\n".join((failures + surprises)[:30]))
print(f"MOVABILITY CATALOG PASSED ({len(CATALOG)} solids, {moves} moves, "
      f"{len(KNOWN)} known refusals, slowest {slowest:.2f}s, "
      f"{time.perf_counter() - started:.0f}s total)")
