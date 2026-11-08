"""Bug #111: extruding a traced outline (cubic spans, straight runs, corners) took ~13 s.

OCCT's surface-deflection control over-refined the extrusion's side wall: 294k display
vertices for a 3-face solid. The side is straight along its direction, so its edges already
carry the curvature; `occt_backend._premesh_extrusion_faces` meshes it without that control.
This pins the speed, and that the faster mesh is still a closed, faithful display mesh while
the solid itself retains exact extrusion surfaces. New versioned recipes split real
G1 creases; the legacy three-face representation remains covered separately.

Run: tools/blender-patched/blender -b --factory-startup --python spike/test_extrusion_display_mesh.py
"""
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rhino3dm as r3  # noqa: E402

from narwhal3d import bridge, kernel, trace_picture as tp  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

checks, failures = 0, []


def check(ok, label):
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)


def traced_outline(n=44, kinks=True):
    """A TracePicture-style loop: n cubic spans (knots of multiplicity 3) round a wavy
    330 x 490 outline, with a straight run and (optionally) 11 corners."""
    def point(a):
        return ((160 + 15 * math.sin(7 * a)) * math.cos(a),
                (245 + 15 * math.sin(7 * a)) * math.sin(a), 0.0)

    def tangent(a, h=1e-6):
        p, q = point(a - h), point(a + h)
        return tuple((q[i] - p[i]) / (2 * h) for i in range(3))
    step = 2 * math.pi / n
    ctrl = [point(0)]
    for i in range(n):
        a0, a1 = i * step, (i + 1) * step
        p0, p1, d0, d1 = point(a0), point(a1), tangent(a0), tangent(a1)
        ctrl += [tuple(p0[k] + d0[k] * step / 3 for k in range(3)),
                 tuple(p1[k] - d1[k] * step / 3 for k in range(3)), p1]
    ctrl[-1] = ctrl[0]
    # one span made a straight line with handles ON its anchors, as the tracer writes them
    a, b = ctrl[60], ctrl[63]
    ctrl[61], ctrl[62] = a, b
    if kinks:
        for j in range(3, len(ctrl) - 1, 12):      # turn the out-handle 90 degrees: a corner
            a, o = ctrl[j], ctrl[j + 1]
            ctrl[j + 1] = (a[0] + (o[1] - a[1]), a[1] - (o[0] - a[0]), 0.0)
    return tp.nurbs_curve(r3, ctrl)


def closed_mesh(tris):
    """True when every triangle edge is shared by exactly two triangles (no gaps)."""
    count = {}
    for t in tris:
        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            key = (min(e), max(e))
            count[key] = count.get(key, 0) + 1
    return all(c == 2 for c in count.values())


curve = traced_outline()
geom = bridge.extrude_curve(curve, -52.0)
master = kernel.master_of(geom)
print("PUBLIC_LOADED_BRIDGE", bridge.__file__)
print("PUBLIC_LOADED_BACKEND", ob.__file__)
# Independent fixture construction: eleven rotated handles at knots 1,5,...,41,
# plus the straight span's stationary start at knot 20. Its end at 21 is already
# one of the rotated handles. The domain seam is smooth but remains a wire seam.
expected_kinks = set(range(1, 44, 4)) | {20}
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
source_wire = ob.wire_from_curve(bridge.curve_recipe(curve))
source_edge = ob._unique_edges(source_wire)[0]
adaptor = BRepAdaptor_Curve(source_edge)
source_curve = BRep_Tool.Curve_s(source_edge, adaptor.FirstParameter(), adaptor.LastParameter())
check(set(ob.extrusion_kink_parameters(source_curve)) == expected_kinks,
      "all twelve exact trace creases survive stationary span endpoints")
side_faces = len(expected_kinks) + 1
faces = ob.face_types(master)
check(master is not None and ob.is_airtight(master),
      "the extruded traced outline is an exact, airtight solid")
check(faces == {"EXTRUSION": side_faces, "PLANE": 2},
      f"new topology is thirteen exact curved patches and two caps ({faces})")

start = time.perf_counter()
verts, tris, edges = bridge.display_tessellation(master)
elapsed = time.perf_counter() - start
check(elapsed < 2.0, f"its display mesh builds quickly ({elapsed:.2f} s; was ~13 s)")
check(len(verts) < 40000, f"the display mesh is not over-refined ({len(verts)} vertices; was ~294k)")
check(closed_mesh(tris), "the display mesh is closed: caps and side wall stay welded")

# The top cap's outline (its boundary edges) follows the exact curve, and its extent
# matches the curve's: sample the exact curve and measure to the nearest outline segment.
dom = curve.Domain
samples = [curve.PointAt(dom.T0 + (dom.T1 - dom.T0) * k / 600) for k in range(600)]
top = [t for t in tris if all(abs(verts[i][2]) < 1e-6 for i in t)]
count = {}
for t in top:
    for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
        key = (min(e), max(e))
        count[key] = count.get(key, 0) + 1
outline = [(verts[a], verts[b]) for (a, b), c in count.items() if c == 1]


def gap(p, seg):
    (ax, ay, _), (bx, by, _) = seg
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p.X - ax) * dx + (p.Y - ay) * dy) / max(dx * dx + dy * dy, 1e-30)))
    return math.hypot(ax + t * dx - p.X, ay + t * dy - p.Y)


worst = max(min(gap(p, seg) for seg in outline) for p in samples)
check(outline and worst < 0.05,
      f"the top cap's outline follows the exact curve within display tolerance (worst {worst:.4f})")
xs = [v[0] for v in verts]
check(abs(min(xs) - min(p.X for p in samples)) < 0.05 and abs(max(xs) - max(p.X for p in samples)) < 0.05,
      "the display mesh spans the exact profile's extent")

# Bug #112: sub-object picking (Gumball extrude ghost, selection highlight) meshes the
# master through its own route; it must not over-refine either.
start = time.perf_counter()
parts = bridge.subobject_parts(geom)
elapsed = time.perf_counter() - start
check(parts is not None and len(parts["faces"]) == side_faces + 2
      and len(parts["edges"]) == 3 * side_faces,
      "sub-object parts are the exact new topology: 15 faces, 39 edges")
check(elapsed < 2.0, f"sub-object parts build quickly ({elapsed:.2f} s; was ~17 s)")

# Keep the original bug #111 workload too: old recipes are not reinterpreted
# into new topology, and their single creased side must still mesh efficiently.
legacy_recipe = {"kind": "extrusion", "profile": bridge.curve_recipe(curve),
                 "dir": [0, 0, -52.0], "solid": True}
legacy_master = ob.from_recipe(legacy_recipe)
legacy_geom = bridge.occt_backed(legacy_master, legacy_recipe)
check(ob.face_types(legacy_master) == {"EXTRUSION": 1, "PLANE": 2}
      and ob.is_airtight(legacy_master), "legacy traced extrusion remains exact, airtight and three-faced")
legacy_side = next(face for face in ob.unique_faces(legacy_master)
                   if hasattr(BRepAdaptor_Surface(face).Surface().Surface(), "BasisCurve"))
legacy_basis = BRepAdaptor_Surface(legacy_side).Surface().Surface().BasisCurve()
check(set(ob.extrusion_kink_parameters(legacy_basis)) == expected_kinks,
      "valid stationary legacy surface retains all twelve exact G1 break parameters")
start = time.perf_counter()
legacy_verts, legacy_tris, _legacy_edges = bridge.display_tessellation(legacy_master)
elapsed = time.perf_counter() - start
check(elapsed < 2.0, f"legacy three-face display mesh still builds quickly ({elapsed:.2f} s)")
check(len(legacy_verts) < 40000, f"legacy mesh is not over-refined ({len(legacy_verts)} vertices)")
check(closed_mesh(legacy_tris), "legacy cap and side meshes remain welded closed")
start = time.perf_counter()
legacy_parts = bridge.subobject_parts(legacy_geom)
elapsed = time.perf_counter() - start
check(legacy_parts is not None and len(legacy_parts["faces"]) == 3
      and len(legacy_parts["edges"]) == 3, "legacy sub-object topology stays three faces and three edges")
check(elapsed < 2.0, f"legacy sub-object parts still build quickly ({elapsed:.2f} s)")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    sys.exit(1)
print("EXTRUSION DISPLAY MESH (#111) PASSED")
