"""Bug #110: every point, edge and face of an extruded multi-arc profile must move.

The captured model is an extrusion of an eight-segment profile: a circle split into two
arcs (meeting tangentially), straight sides and a cubic lobe. Its caps therefore have
EIGHT edges. MEASURED before the fix: every move with a component across the cap plane
was refused -- 96 of the 300 checked moves -- because only caps of up to six edges were
rebuilt. Once eight-edge caps were admitted, three more defects surfaced, each of which
this matrix exposes:

  * the two arcs meet tangentially at the split vertex; lifting that vertex gave the arcs
    opposite slopes there, a crease along a smooth outline that no smooth cap can follow
    (kept tangents fix it);
  * a ramped rational arc's height flips curvature at its double knot (d2z/ds2 -4.6e-4
    -> +4.6e-4 per mm), so the cap missed it by 2.2e-3 mm (C2-smoothed ramp fixes it);
  * a translated arc stored as a rational B-spline failed its own identity test because
    the fixed-order length integral was off by 6e-6 (adaptive arc length fixes it).

The test runs the matrix -- all 50 parts, +-X/+-Y/+-Z at 5 mm and +-Z at 25 mm -- and
requires every move to succeed, stay valid/airtight/unfolded and finish quickly.
"""
import importlib.util
import os
import sys
import time
import types

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
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

if not occt.available():
    print(f"SKIP: bug #110 needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)
from narwhal3d import native_geometry  # noqa: E402

from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepClass import BRepClass_FaceClassifier  # noqa: E402
from OCP.TopAbs import TopAbs_IN  # noqa: E402
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec  # noqa: E402

CENTER = [91.60841830762207, 104.26252117287306, 0.0]
RADIUS = 52.36656075662252
RECIPE = {
    "kind": "extrusion", "both": False, "solid": True, "dir": [0.0, 0.0, 44.98903160016964],
    "profile": {"ctype": "polycurve", "segments": [
        {"ctype": "arc", "center": CENTER, "radius": RADIUS, "normal": [0.0, 0.0, 1.0],
         "xaxis": [-0.6200245897089471, -0.7845823781836115, 0.0],
         "start": 0.0, "end": 2.2395703707527166},
        {"ctype": "arc", "center": CENTER, "radius": RADIUS, "normal": [0.0, 0.0, 1.0],
         "xaxis": [1.0, 0.0, 0.0], "start": 0.0, "end": 3.6407810232491795},
        {"ctype": "polyline", "points": [[45.632076217226285, 79.19396211049506, 0.0],
                                         [-65.80825076615129, 79.19396211049502, 0.0],
                                         [-65.80825076615129, 27.192013178990976, 0.0]]},
        {"ctype": "polyline", "points": [[-65.80825076615129, 27.192013178990976, 0.0],
                                         [-34.506508389794206, 27.192013178990976, 0.0]]},
        {"ctype": "nurbs", "degree": 3, "periodic": False, "weights": [1.0] * 8,
         "knots": [-6.261722392999202] * 3 + [-6.0, -5.0, -4.0, -3.0]
                  + [-2.6686419209164414] * 3,
         "poles": [[-34.506508389794206, 27.192013178990976, 0.0],
                   [-37.71640343692346, 23.166652612828834, 0.0],
                   [-51.174893262019, 2.7102911468990163, 0.0],
                   [-51.13679486911738, -27.561601156379723, 0.0],
                   [6.149856511277747, -48.954101066615664, 0.0],
                   [37.86417150340613, -3.9854711529905282, 0.0],
                   [29.918259242830715, 21.522836117669193, 0.0],
                   [26.840992725596244, 27.192013178990976, 0.0]]},
        {"ctype": "polyline", "points": [[26.840992725596244, 27.192013178990976, 0.0],
                                         [59.13986296002858, 27.192013178990976, 0.0],
                                         [59.13986296002858, 63.17664039714556, 0.0]]},
    ]},
}


def worst_jacobian_ratio(shape, grid=15):
    worst = 1.0
    for face in occt.unique_faces(shape):
        adaptor = BRepAdaptor_Surface(face, True)
        if adaptor.GetType().name != "GeomAbs_BSplineSurface":
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


shape = occt.from_recipe(RECIPE)
counts = {"face": len(occt.unique_faces(shape)), "edge": len(occt._unique_edges(shape)),
          "vertex": len(occt._unique_vertices(shape))}
assert counts == {"face": 10, "edge": 24, "vertex": 16}, counts
data = occt.shape_to_bytes(shape, triangles=False)
failures, slowest, moves = [], 0.0, 0
started = time.perf_counter()
# Every direction at 5 mm; across the cap plane (where every refusal was) also at 25 mm.
for step, axes in ((5.0, (0, 1, 2)), (25.0, (2,))):
    for kind, code in (("vertex", 3), ("edge", 2), ("face", 1)):
        for index in range(1, counts[kind] + 1):
            for axis in axes:
                for sign in (1.0, -1.0):
                    vector = [0.0, 0.0, 0.0]
                    vector[axis] = sign * step
                    t = time.perf_counter()
                    result = native_geometry.move_component(
                        shape, lambda: data, code, index, vector, live=False)
                    elapsed = time.perf_counter() - t
                    slowest = max(slowest, elapsed)
                    moves += 1
                    label = f"{kind} {index} {vector}"
                    if not result.ok:
                        failures.append(f"{label}: {result.detail}")
                        continue
                    moved = occt.shape_from_bytes(result.data)
                    if not (occt.is_valid(moved) and occt.is_airtight(moved)):
                        failures.append(f"{label}: invalid or open result")
                    elif worst_jacobian_ratio(moved) < 0.05:
                        failures.append(f"{label}: a rebuilt face folds")
                    elif elapsed > 4.0:
                        failures.append(f"{label}: took {elapsed:.2f}s")
assert not failures, f"{len(failures)} of {moves} moves failed:\n" + "\n".join(failures[:20])
print(f"BUG 110 MULTI-ARC EXTRUSION FULLY MOVABLE PASSED "
      f"({moves} moves, slowest {slowest:.2f}s, {time.perf_counter() - started:.0f}s total)")
