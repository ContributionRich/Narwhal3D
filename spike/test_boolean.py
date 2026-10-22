"""Boolean geometry and display-mesh regression tests.

The checks bound tessellation density, preserve solid extents and curved surfaces,
and verify smooth joins without softening hard edges. Runs inside Blender.
"""
import importlib.util
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "narwhal3d"))

import bpy  # noqa: E402  (clean scene so repeated runs don't accrete objects)
import rhino3dm as r3  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bridge", os.path.join(ROOT, "narwhal3d", "bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

_aspec = importlib.util.spec_from_file_location(
    "analysis", os.path.join(ROOT, "narwhal3d", "analysis.py"))
analysis = importlib.util.module_from_spec(_aspec)
_aspec.loader.exec_module(analysis)

failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def extent(brep):
    bb = brep.GetBoundingBox()
    return (round(bb.Max.X - bb.Min.X, 3), round(bb.Max.Y - bb.Min.Y, 3),
            round(bb.Max.Z - bb.Min.Z, 3))


box_a = bridge.make_box((0, 0, 0), (2, 2, 0), 2.0)   # (0,0,0)-(2,2,2)
box_b = bridge.make_box((1, 1, 0), (3, 3, 0), 2.0)   # (1,1,0)-(3,3,2)

# --- 1. two boxes UNION: tens of faces, not thousands. ----------------------
# A BOUND, not an equality: the EXACT solver's own triangulation of the merged
# shell is not something we specify, so we assert "small" (the bug was 19 456) and
# pin the SHAPE separately below.
u = bridge.boolean_brep(box_a, box_b, "UNION")
check(len(u.Faces) < 100,
      f"UNION has {len(u.Faces)} faces; the adaptive mesh should give tens, not "
      f"thousands (the bug was 19456)")
# --- 2. the SHAPE is unchanged by the optimisation (the regression gate). ----
check(u.IsSolid is True, f"UNION must stay a closed solid, got IsSolid={u.IsSolid}")
check(extent(u) == (3.0, 3.0, 2.0), f"UNION bbox changed: {extent(u)} != (3,3,2)")
print(f"union: {len(u.Faces)} faces, solid={u.IsSolid}, bbox={extent(u)}")

# --- 3. DIFFERENCE: also small, correct shape. ------------------------------
d = bridge.boolean_brep(box_a, box_b, "DIFFERENCE")
check(len(d.Faces) < 100, f"DIFFERENCE has {len(d.Faces)} faces; want tens")
check(d.IsSolid is True and extent(d) == (2.0, 2.0, 2.0),
      f"DIFFERENCE shape wrong: solid={d.IsSolid} bbox={extent(d)}")
print(f"difference: {len(d.Faces)} faces, bbox={extent(d)}")

# --- 4. INTERSECT: the overlap region. --------------------------------------
x = bridge.boolean_brep(box_a, box_b, "INTERSECT")
check(x.IsSolid is True and extent(x) == (1.0, 1.0, 2.0),
      f"INTERSECT shape wrong: solid={x.IsSolid} bbox={extent(x)}")
print(f"intersect: {len(x.Faces)} faces, bbox={extent(x)}")

# --- 5. a CURVED operand must stay ROUND (collapsing planar faces must not
#        collapse curved ones). Sphere minus a half-space box -> hemisphere. ---
sph = bridge.make_sphere((0, 0, 0), 5.0)
halfspace = bridge.make_box((-10, -10, 0), (10, 10, 0), 10.0)   # keeps z <= 0 half
hemi = bridge.boolean_brep(sph, halfspace, "DIFFERENCE")
check(hemi.IsSolid is True, f"sphere-box must be a closed solid, got {hemi.IsSolid}")
# The public build may represent an OCCT result as a faceted display proxy when the
# optional exact .3dm writer is absent. Curvature therefore belongs to the analytic
# master, never to the proxy's triangles.
kernel = bridge._kernel()
occt = bridge._occt()
hemi_master = kernel.master_of(hemi) if kernel is not None else None
hemi_types = occt.face_types(hemi_master) if occt is not None and hemi_master is not None else {}
hemi_bounds = occt.tight_bbox(hemi_master) if occt is not None and hemi_master is not None else None
check(hemi_master is not None and hemi_types.get("SPHERE") == 1
      and hemi_types.get("PLANE") == 1 and occt.is_airtight(hemi_master),
      f"sphere-box analytic master lost its curved sphere face: {hemi_types}")
check(hemi_bounds is not None
      and all(abs(a - b) < 1e-5 for a, b in zip(
          (*hemi_bounds[0], *hemi_bounds[1]), (-5.0, -5.0, -5.0, 5.0, 5.0, 0.0))),
      f"sphere-box analytic bounds changed: {hemi_bounds}")
print(f"curved: sphere-box master faces={hemi_types}, bounds={hemi_bounds}")

# --- 6. a planar-faced solid booleaned with a CURVED one works. The cylinder
#        pokes cleanly through (base at z=-1, top at z=3) -- a cylinder whose base
#        is COPLANAR with the box bottom is a boolean degeneracy both the old and
#        the new mesh route fail, which is a separate, inherent mesh-boolean limit.
cyl = bridge.make_cylinder((1, 1, -1), 1.0, 4.0)
bc = bridge.boolean_brep(box_a, cyl, "UNION")
check(bc is not None and bc.IsSolid is True,
      f"box + cylinder union must be a solid, got {bc}")
print(f"mixed: box+cylinder union faces={len(bc.Faces)}, solid={bc.IsSolid}")

# --- 7. operation names: the three valid ones work; a wrong one refuses by name.
raised = False
msg = ""
try:
    bridge.boolean_brep(box_a, box_b, "INTERSECTION")   # the classic wrong guess
except ValueError as e:
    raised = True
    msg = str(e)
check(raised and "INTERSECT" in msg,
      f"a bad operation must raise ValueError naming the valid set (raised={raised}, "
      f"msg={msg!r})")

# --- 8. analysis_density tracks PLANARITY, not face count. A box
#        union is now ~40 PLANAR faces, so the old faces>64 heuristic misclassified
#        it and Volume ran 64x64 (slow). All-planar -> density 1 (exact + fast); a
#        curved operand keeps the full grid.
check(analysis.analysis_density(u) == 1,
      f"box UNION (all planar) should be density 1, got {analysis.analysis_density(u)}")
check(analysis.analysis_density(box_a) == 1,
      f"a box (all planar) should be density 1, got {analysis.analysis_density(box_a)}")
check(analysis.analysis_density(sph) != 1,
      f"a sphere (curved) must keep the full grid, got {analysis.analysis_density(sph)}")
print(f"density: box-union={analysis.analysis_density(u)}, "
      f"sphere={analysis.analysis_density(sph)}")

# --- 9. A mesh-derived display must shade smooth where it is smooth and remain hard
#        where it creases. Weld smooth faces and re-split only sharp edges.
import bmesh  # noqa: E402


def _vf(bm):
    return len(bm.verts), len(bm.faces)


# (a) Two overlapping cylinders. The display mesh must
#     SHARE vertices across faces (welded), so smooth shading can average normals.
cyl_a = bridge.make_cylinder((0, 0, 0), 3.0, 4.0)
cyl_b = bridge.make_cylinder((3, 0, 0), 3.0, 4.0)
cu = bridge.boolean_brep(cyl_a, cyl_b, "UNION")
cbm = bridge.geometry_to_bmesh(cu)
cv, cf = _vf(cbm)
# DIAGNOSIS recorded in the suite (the gate asks for the vertex-vs-face count): an
# UNWELDED mesh has ~4 verts/face (lone quads), a welded one well under 1.
check(cf > 64, f"cylinder union should be mesh-derived (got {cf} faces)")
check(cv / cf < 1.5,
      f"cylinder union display mesh is {cv}/{cf} = {cv / cf:.2f} verts/face; an "
      f"unwelded mesh is ~4.0 -- faces are not sharing vertices, so smooth shading "
      f"cannot average normals")
print(f"cylinder union DISPLAY: {cv} verts / {cf} faces = {cv / cf:.2f} per face "
      f"(welded; was ~4.0)")
# The geometry is unchanged; this is a shading-only operation.
_cbb = cu.GetBoundingBox()
check(cu.IsSolid is True,
      "welding the display mesh must not change the brep (still a solid)")

# (b) HARD edges survive: a box built as isolated per-face quads (as brep_to_bmesh
#     makes a mesh-derived brep) must NOT be welded smooth across its 90 deg edges.
#     After _weld_shading every sharp edge is re-split, so the six faces stay
#     isolated (24 verts) -- a box does not get rounded corners.
def iso_box_bmesh():
    bm = bridge._new_bmesh()
    c = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
         (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    quads = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    for q in quads:
        bm.faces.new([bm.verts.new(c[i]) for i in q])
    return bm


bx = iso_box_bmesh()
check(len(bx.verts) == 24, f"iso box fixture should start at 24 verts, got {len(bx.verts)}")
bridge._weld_shading(bx)
check(len(bx.verts) == 24,
      f"a box's 90-degree edges were welded smooth ({len(bx.verts)} verts, not 24) -- "
      f"hard edges would render rounded")
bx.free()
print("hard edges: box stays 24 verts (every 90-degree edge re-split)")

# (c) SMOOTH joins weld: two quads meeting at a shallow 10 deg fold, built as isolated
#     islands (8 verts), must weld to 6 -- the shared edge is below the sharp
#     threshold, so smooth shading averages across it.
def roof_bmesh(fold_deg):
    bm = bridge._new_bmesh()
    a = math.radians(fold_deg)
    q1 = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    q2 = [(0, 1, 0), (1, 1, 0),
          (1, 1 + math.cos(a), math.sin(a)), (0, 1 + math.cos(a), math.sin(a))]
    for q in (q1, q2):
        bm.faces.new([bm.verts.new(p) for p in q])
    return bm


rf = roof_bmesh(10.0)
check(len(rf.verts) == 8, f"roof fixture should start at 8 verts, got {len(rf.verts)}")
bridge._weld_shading(rf)
check(len(rf.verts) == 6,
      f"a shallow (10 deg) join was not welded ({len(rf.verts)} verts, not 6) -- "
      f"a smooth surface would still render faceted")
rf.free()
print("smooth joins: 10-degree fold welds 8 verts -> 6")

# --- report ------------------------------------------------------------------
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("BOOLEAN TESTS PASSED")
