"""Blender display-bridge and tessellation regression tests.

A one-object retessellation must preserve every Blender object and mesh identity while
updating only the changed object's vertices. Additional gates cover trims, adaptive
curve and surface density, shading, visibility, seams, caps, and view-dependent detail.
"""
import importlib.util
import math
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# The narwhal3d dir on the path lets bridge's `import tessellate` fallback
# resolve when bridge is loaded standalone (below), and lets this test import the
# pure tessellate module directly.
sys.path.insert(0, os.path.join(ROOT, "narwhal3d"))

import bpy  # noqa: E402
import rhino3dm as r3  # noqa: E402
import tessellate  # noqa: E402  (pure module under narwhal3d/)

# Load bridge.py standalone. Its only module-level imports are `math` and
# `rhino3dm`; the functions that use `from . import ...` are never called here,
# so it does not need to be part of the narwhal3d package.
_spec = importlib.util.spec_from_file_location(
    "bridge", os.path.join(ROOT, "narwhal3d", "bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def box_brep(x0, y0, z0, x1, y1, z1):
    """A rhino3dm box brep spanning the given corners."""
    bbox = r3.BoundingBox(r3.Point3d(x0, y0, z0), r3.Point3d(x1, y1, z1))
    return r3.Brep.CreateFromBox(r3.Box(bbox))


def vert_bounds(ob):
    xs = [v.co.x for v in ob.data.vertices]
    ys = [v.co.y for v in ob.data.vertices]
    zs = [v.co.z for v in ob.data.vertices]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


# --- clean scene ------------------------------------------------------------
for _ob in list(bpy.data.objects):
    bpy.data.objects.remove(_ob, do_unlink=True)

# --- build a small "document": several display objects ----------------------
geoms = {
    "Box1": box_brep(0, 0, 0, 1, 1, 1),
    "Box2": box_brep(5, 0, 0, 6, 2, 3),
    "Box3": box_brep(-4, -4, 0, -2, -1, 1),
}
objs = {}
meshes = {}
for name, geom in geoms.items():
    bm = bridge.geometry_to_bmesh(geom)
    ob = bridge.bmesh_to_object(name, bm)
    objs[name] = ob
    meshes[name] = ob.data

# Invariant the whole display relies on: position lives in the mesh, not the
# matrix. Confirm the objects start at the origin so we can prove we keep it.
for name, ob in objs.items():
    t = ob.matrix_world.translation
    check(abs(t.x) < 1e-9 and abs(t.y) < 1e-9 and abs(t.z) < 1e-9,
          f"{name} did not start at matrix_world origin: {tuple(t)}")

before_bounds = {name: vert_bounds(ob) for name, ob in objs.items()}
n_objects_before = len(bpy.data.objects)
n_meshes_before = len(bpy.data.meshes)

# --- the change: replace Box2 with a much bigger brep, in place -------------
new_geom = box_brep(5, 0, 0, 12, 9, 7)   # same corner, far larger extent
ok = bridge.rebuild_object_mesh("Box2", new_geom)
check(ok is True, f"rebuild_object_mesh returned {ok!r}, expected True")

# 1. No object was created or destroyed.
check(len(bpy.data.objects) == n_objects_before,
      f"object count changed: {n_objects_before} -> {len(bpy.data.objects)}")
check(len(bpy.data.meshes) == n_meshes_before,
      f"mesh datablock count changed: {n_meshes_before} -> {len(bpy.data.meshes)}")

# 2. Every object -- INCLUDING the changed one -- is the SAME Python object,
#    and its mesh datablock is the SAME datablock. This is what keeps a held
#    reference (the dragged object) from going stale.
for name in geoms:
    live = bpy.data.objects.get(name)
    check(live is objs[name], f"{name}: object reference went stale (not `is`)")
    check(live is not None and live.data is meshes[name],
          f"{name}: mesh datablock was replaced (not `is`)")

# 3. The UNCHANGED objects still have their original vertex data.
for name in ("Box1", "Box3"):
    check(vert_bounds(objs[name]) == before_bounds[name],
          f"{name} vertex data changed but should not have")

# 4. The CHANGED object really changed -- its bounds now match the new geom.
after = vert_bounds(objs["Box2"])
check(after != before_bounds["Box2"], "Box2 vertex data did not change")
check(abs(after[3] - 12.0) < 1e-6 and abs(after[4] - 9.0) < 1e-6
      and abs(after[5] - 7.0) < 1e-6,
      f"Box2 new extent wrong: max corner {after[3:]}, expected (12, 9, 7)")

# 5. The position invariant held: matrix_world untouched, still at the origin.
t = objs["Box2"].matrix_world.translation
check(abs(t.x) < 1e-9 and abs(t.y) < 1e-9 and abs(t.z) < 1e-9,
      f"rebuild_object_mesh moved matrix_world off the origin: {tuple(t)}")

# 6. A name that does not exist returns False and touches nothing.
n_before = len(bpy.data.objects)
missing = bridge.rebuild_object_mesh("NoSuchObject", new_geom)
check(missing is False,
      f"rebuild_object_mesh on a missing name returned {missing!r}, expected False")
check(len(bpy.data.objects) == n_before,
      "rebuild_object_mesh on a missing name changed the object count")

# --- measurement: in-place update vs a full recreate -----------------------
# The full path (rebuild_display) deletes and recreates every object; approximate
# its cost by recreating all display objects from their geoms. The in-place path
# updates only the one that changed. Print both so the speed claim carries a
# number for diagnostic comparison.
ITER = 20

t0 = time.perf_counter()
for _ in range(ITER):
    bridge.rebuild_object_mesh("Box2", new_geom)
inplace = (time.perf_counter() - t0) / ITER

t0 = time.perf_counter()
for _ in range(ITER):
    for name in list(objs):
        old = bpy.data.objects.get(name)
        if old is not None:
            data = old.data
            bpy.data.objects.remove(old, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
        bm = bridge.geometry_to_bmesh(geoms[name] if name != "Box2" else new_geom)
        objs[name] = bridge.bmesh_to_object(name, bm)
full = (time.perf_counter() - t0) / ITER

print(f"in-place timing: 1 object = {inplace * 1e3:.3f} ms/frame; "
      f"full recreate of {len(geoms)} objects = {full * 1e3:.3f} ms/frame; "
      f"speedup x{full / inplace:.1f}")

# ===========================================================================
# Honour trimmed faces such as square caps.
# ===========================================================================

# --- pure tessellate.ear_clip on hand-built polygons ------------------------
def poly_area_from_tris(poly, tris):
    """Sum unsigned triangle areas for a 2-D polygon triangulation."""
    total = 0.0
    for i, j, k in tris:
        (ax, ay), (bx, by), (cx, cy) = poly[i], poly[j], poly[k]
        total += abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2.0
    return total


# a unit square -> 2 triangles, area 1. Convex caps take the linear fan path.
square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
tris = tessellate.ear_clip(square)
check(len(tris) == len(square) - 2, f"square: {len(tris)} tris, expected 2")
check(abs(poly_area_from_tris(square, tris) - 1.0) < 1e-12,
      f"square area {poly_area_from_tris(square, tris)}, expected 1")
# The fast path must not invoke the O(n^2) interior-point scan. This is also
# a mutation gate: deleting the convex-fan return makes this fixture RED.
_original_point_test = tessellate._point_in_triangle
def _unexpected_point_test(*args):
    raise AssertionError("convex fast path fell back to ear clipping")
tessellate._point_in_triangle = _unexpected_point_test
try:
    check(len(tessellate.ear_clip(square)) == 2,
          "convex square did not use the fan fast path")
finally:
    tessellate._point_in_triangle = _original_point_test

# A representative large convex cap is linear in vertex count rather than the
# old quadratic interior scan. Keep this as a printed probe, not a wall-clock
# assertion: the predicate guard above is the deterministic performance gate.
_perf_poly = [(math.cos(2.0 * math.pi * i / 2048),
               math.sin(2.0 * math.pi * i / 2048)) for i in range(2048)]
_perf_t0 = time.perf_counter()
_perf_tris = tessellate.ear_clip(_perf_poly)
print(f"convex cap: {len(_perf_poly)} vertices -> {len(_perf_tris)} triangles "
      f"in {(time.perf_counter() - _perf_t0) * 1e3:.3f} ms")
check(len(_perf_tris) == len(_perf_poly) - 2,
      "large convex cap did not produce n-2 triangles")

# Reverse winding must preserve a CCW triangle winding in the result.
clockwise = list(reversed(square))
clockwise_tris = tessellate.ear_clip(clockwise)
check(len(clockwise_tris) == 2 and all(
    (clockwise[j][0] - clockwise[i][0]) * (clockwise[k][1] - clockwise[i][1])
    - (clockwise[j][1] - clockwise[i][1]) * (clockwise[k][0] - clockwise[i][0]) > 0
    for i, j, k in clockwise_tris),
      "convex clockwise loop did not return consistently wound triangles")

# A face with inner loops is deliberately refused by the display bridge; never
# pass an outer loop to ear_clip and silently fill its hole.
_hole_face = types.SimpleNamespace(Loops=[object(), object()])
check(bridge._add_trimmed_planar_face(None, None, _hole_face) is False,
      "planar face with a hole was incorrectly accepted for single-loop fill")

# a concave L (a reflex vertex) -- the case a naive fan triangulation gets wrong
L = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]
tris = tessellate.ear_clip(L)
L_area = abs(tessellate.signed_area(L))
check(len(tris) == len(L) - 2, f"L: {len(tris)} tris, expected {len(L) - 2}")
check(abs(poly_area_from_tris(L, tris) - L_area) < 1e-12,
      f"L area {poly_area_from_tris(L, tris)}, expected {L_area} (=3)")
check(abs(L_area - 3.0) < 1e-12, f"L shoelace area {L_area}, expected 3")
# no triangle may poke outside the polygon: the triangulated area equals the
# polygon area exactly, which a spilled fan (area > 3) would violate.

# a circle sampled to 32 points -- the cap's shape. The triangulated area must
# equal the inscribed 32-gon's area, and that must be close to pi r^2 with an
# error set by the sample count: A_n = 0.5 n r^2 sin(2pi/n).
R = 5.0
N = 32
circle = [(R * math.cos(2 * math.pi * i / N), R * math.sin(2 * math.pi * i / N))
          for i in range(N)]
tris = tessellate.ear_clip(circle)
check(len(tris) == N - 2, f"circle: {len(tris)} tris, expected {N - 2}")
tri_area = poly_area_from_tris(circle, tris)
gon_area = 0.5 * N * R * R * math.sin(2 * math.pi / N)
check(abs(tri_area - gon_area) < 1e-9,
      f"circle tri area {tri_area:.6f} != inscribed 32-gon area {gon_area:.6f}")
# and the 32-gon is within the derived tolerance of the true disc
disc = math.pi * R * R
check(abs(gon_area - disc) < 0.02 * disc,
      f"32-gon area {gon_area:.4f} not within 2% of pi r^2 = {disc:.4f}")

# --- pure plane_basis / project_to_plane ------------------------------------
for normal in ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (1.0, 2.0, 3.0)):
    u, v = tessellate.plane_basis(normal)
    dot = lambda a, b: a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    length = lambda a: math.sqrt(dot(a, a))
    check(abs(length(u) - 1.0) < 1e-12 and abs(length(v) - 1.0) < 1e-12,
          f"plane_basis({normal}) axes not unit length")
    check(abs(dot(u, v)) < 1e-12, f"plane_basis({normal}) axes not orthogonal")
    check(abs(dot(u, normal)) < 1e-9 and abs(dot(v, normal)) < 1e-9,
          f"plane_basis({normal}) axes not in the plane")

# projecting a point at origin+2u+3v must read back (2, 3)
u, v = tessellate.plane_basis((0.0, 0.0, 1.0))
origin = (10.0, 20.0, 30.0)
p = tuple(origin[i] + 2.0 * u[i] + 3.0 * v[i] for i in range(3))
uv = tessellate.project_to_plane([p], origin, u, v)[0]
check(abs(uv[0] - 2.0) < 1e-9 and abs(uv[1] - 3.0) < 1e-9,
      f"project_to_plane read {uv}, expected (2, 3)")

# --- integration: a capped extrusion's caps are trimmed, not square ---------
def capped_circle_extrusion(radius, height):
    curve = r3.NurbsCurve.CreateFromCircle(r3.Circle(radius))
    return r3.Extrusion.Create(curve, float(height), True).ToBrep(False)


def mesh_from_geom(name, geom):
    bm = bridge.geometry_to_bmesh(geom)
    return bridge.bmesh_to_object(name, bm)


def bmesh_snapshot(bm):
    bm.normal_update()
    bm.verts.ensure_lookup_table()
    vertices = [(tuple(round(float(c), 9) for c in v.co),
                 tuple(round(float(c), 9) for c in v.normal)) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    return vertices, faces


# Equivalent native Extrusions reuse only viewport arrays, never the
# source geometry. The target is rotated and translated, so its canonical profile
# must hit while its world coordinates and normals must still match a fresh build.
bridge.clear_extrusion_display_cache()
_cache_points = [r3.Point3d(0, 0, 0), r3.Point3d(4, 0, 0),
                 r3.Point3d(1, 3, 0), r3.Point3d(0, 0, 0)]
_cache_source = r3.Extrusion.Create(
    r3.Polyline(_cache_points).ToNurbsCurve(), 6.0, True)
_cache_angle = math.radians(37.0)
_cache_profile = r3.Polyline(_cache_points).ToNurbsCurve()
for _i in range(len(_cache_profile.Points)):
    _cp = _cache_profile.Points[_i]
    _x = math.cos(_cache_angle) * _cp.X - math.sin(_cache_angle) * _cp.Y
    _y = math.sin(_cache_angle) * _cp.X + math.cos(_cache_angle) * _cp.Y
    _cache_profile.Points[_i] = r3.Point4d(_x + 17.0, _y - 4.0, _cp.Z + 3.0, _cp.W)
_cache_target = r3.Extrusion.Create(_cache_profile, 6.0, True)
_cache_source_bm = bridge.geometry_to_bmesh(_cache_source)
_cache_before = bridge.extrusion_display_cache_stats()
_cache_hit_bm = bridge.geometry_to_bmesh(_cache_target)
_cache_after = bridge.extrusion_display_cache_stats()
check(_cache_after["hits"] == _cache_before["hits"] + 1,
      "rigid-transform-equivalent extrusion did not hit the display cache")
check(len(_cache_hit_bm.verts) == len(_cache_source_bm.verts),
      "cached rigid extrusion vertex count changed")
check(len(_cache_hit_bm.faces) == len(_cache_source_bm.faces),
      "cached rigid extrusion face count changed")
bridge.clear_extrusion_display_cache()
_cache_fresh_bm = bridge.geometry_to_bmesh(_cache_target)
_cache_hit_snapshot = bmesh_snapshot(_cache_hit_bm)
_cache_fresh_snapshot = bmesh_snapshot(_cache_fresh_bm)
def _same_mesh(_left, _right):
    if len(_left[0]) != len(_right[0]) or _left[1] != _right[1]:
        return False
    for (_lp, _ln), (_rp, _rn) in zip(_left[0], _right[0]):
        if any(abs(a - b) > 1e-7 for a, b in zip(_lp, _rp)):
            return False
        if any(abs(a - b) > 1e-6 for a, b in zip(_ln, _rn)):
            return False
    return True

check(_same_mesh(_cache_hit_snapshot, _cache_fresh_snapshot),
      "cached transformed extrusion differs from an independent tessellation")
_cache_source_bm.free()
_cache_hit_bm.free()
_cache_fresh_bm.free()

# Exact profile/path/cap changes and non-uniform scale must miss. Count the
# underlying tessellations: disabling reuse makes this fixture fail deterministically.
_original_brep_to_bmesh = bridge.brep_to_bmesh
_tessellation_calls = 0
def _count_brep_to_bmesh(*args, **kwargs):
    global _tessellation_calls
    _tessellation_calls += 1
    return _original_brep_to_bmesh(*args, **kwargs)
bridge.brep_to_bmesh = _count_brep_to_bmesh
try:
    bridge.clear_extrusion_display_cache()
    _tessellation_calls = 0
    _a = r3.Extrusion.Create(
        r3.Polyline(_cache_points).ToNurbsCurve(), 6.0, True)
    _b = _a.Duplicate()
    _b.Transform(r3.Transform.Translation(100.0, 0.0, 0.0))
    bridge.geometry_to_bmesh(_a).free()
    bridge.geometry_to_bmesh(_b).free()
    check(_tessellation_calls == 1,
          f"rigid cache expected one tessellation, got {_tessellation_calls}")
    _changed_points = [r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0),
                       r3.Point3d(1, 3, 0), r3.Point3d(0, 0, 0)]
    _changed = r3.Extrusion.Create(
        r3.Polyline(_changed_points).ToNurbsCurve(), 6.0, True)
    bridge.geometry_to_bmesh(_changed).free()
    check(_tessellation_calls == 2, "changed profile incorrectly reused cached mesh")
    _scaled = _a.Duplicate()
    _scaled.Transform(r3.Transform.Scale(r3.Plane.WorldXY(), 2.0, 1.0, 1.0))
    bridge.geometry_to_bmesh(_scaled).free()
    check(_tessellation_calls == 3, "non-uniform transform incorrectly reused cached mesh")
finally:
    bridge.brep_to_bmesh = _original_brep_to_bmesh


EXT_R, EXT_H = 5.0, 6.0
ext_ob = mesh_from_geom("Ext1", capped_circle_extrusion(EXT_R, EXT_H))
verts = ext_ob.data.vertices

# GATE 1 (no vertex outside the trim curve): every vertex is within the circle
# of radius 5. The bounding-square bug puts cap corners out at r ~ 8.84.
max_r = max(math.hypot(v.co.x, v.co.y) for v in verts)
check(max_r <= EXT_R + 1e-4,
      f"a vertex lies outside the trim circle: max radius {max_r:.4f} > {EXT_R} "
      f"(the square-cap bug)")

# GATE 2 (cap area = pi r^2): the bottom cap is the faces whose verts all sit at
# z ~ 0. Sum their area; a bounding-square cap would give (2*6.25)^2 = 156.25.
import bmesh as _bmesh  # noqa: E402
_bm = _bmesh.new()
_bm.from_mesh(ext_ob.data)
cap_area = 0.0
for f in _bm.faces:
    if all(abs(v.co.z) < 1e-6 for v in f.verts):
        cap_area += f.calc_area()
_bm.free()
disc = math.pi * EXT_R * EXT_R
# tolerance from the 64-gon the cap edge is sampled to (bridge default):
#   A_64 = 0.5*64*r^2*sin(2pi/64); |A_64 - pi r^2| / (pi r^2) ~ 0.16%
check(abs(cap_area - disc) < 0.01 * disc,
      f"bottom cap area {cap_area:.4f} not within 1% of pi r^2 = {disc:.4f} "
      f"(square cap would be ~156.25)")
print(f"cap: max vertex radius {max_r:.4f} (<= {EXT_R}); "
      f"bottom-cap area {cap_area:.4f} (pi r^2 = {disc:.4f})")

# --- integration: a POLYGON-profile cap (all-linear boundary, but trimmed) ---
# Its domain is the bounding rectangle, so the grid would draw that rectangle;
# the real cap is a triangle of area 0.5*base*height.
tri_profile = r3.Polyline([r3.Point3d(0, 0, 0), r3.Point3d(4, 0, 0),
                           r3.Point3d(0, 3, 0), r3.Point3d(0, 0, 0)]).ToNurbsCurve()
tri_ext = r3.Extrusion.Create(tri_profile, 2.0, True).ToBrep(False)
tri_ob = mesh_from_geom("Tri1", tri_ext)
_bm = _bmesh.new()
_bm.from_mesh(tri_ob.data)
tri_cap_area = sum(f.calc_area() for f in _bm.faces
                   if all(abs(v.co.z) < 1e-6 for v in f.verts))
_bm.free()
check(abs(tri_cap_area - 6.0) < 1e-4,
      f"triangle-profile bottom cap area {tri_cap_area:.4f}, expected 6.0 "
      f"(a grid would give the 4x3=12 bounding rectangle)")
print(f"polygon cap area {tri_cap_area:.4f} (expected 6.0)")

# --- guard: a plain box still tessellates to its exact extent ---------------
box_ob = mesh_from_geom("BoxG2", box_brep(0, 0, 0, 2, 3, 4))
bb = vert_bounds(box_ob)
check(bb[:3] == (0.0, 0.0, 0.0) and abs(bb[3] - 2.0) < 1e-9
      and abs(bb[4] - 3.0) < 1e-9 and abs(bb[5] - 4.0) < 1e-9,
      f"box extent wrong after trim-planar tessellation: {bb}")

# ===========================================================================
# Adaptive curve tessellation: a large circle remains visually round.
# ===========================================================================

# --- pure tessellate.curve_segments_for -------------------------------------
def sagitta(radius, n):
    """Chord height of one of `n` equal segments on a circle of `radius`."""
    return radius * (1.0 - math.cos(math.pi / n))


TOL = 0.01
# a mid-range radius-50 circle: the returned count MEETS the tolerance and is
# minimal (one fewer segment would violate it) -- the honest chord-tolerance gate.
r = 50.0
length = 2.0 * math.pi * r
n = tessellate.curve_segments_for(length, TOL)
check(sagitta(r, n) <= TOL + 1e-12,
      f"curve_segments_for({length:.1f}, {TOL}) = {n}, but sagitta {sagitta(r, n):.5f} > {TOL}")
check(n <= 16 or sagitta(r, n - 1) > TOL,
      f"curve_segments_for gave {n}, but {n - 1} already meets the tolerance (wasteful)")

# Segment count increases monotonically with size.
n_small = tessellate.curve_segments_for(2 * math.pi * 5, TOL)
n_mid = tessellate.curve_segments_for(2 * math.pi * 50, TOL)
n_big = tessellate.curve_segments_for(2 * math.pi * 500, TOL)
check(n_small < n_mid < n_big,
      f"segment count not increasing with size: {n_small}, {n_mid}, {n_big}")

# clamps: a huge curve caps at `maximum`; a tiny curve now floors at the ANGLE
# count, not a fixed 16-gon chord minimum.
check(tessellate.curve_segments_for(2 * math.pi * 1e6, TOL) == 1024,
      "huge curve did not cap at maximum=1024")
check(tessellate.curve_segments_for(2 * math.pi * 1e6, TOL, maximum=64) == 64,
      "explicit maximum not honoured")
# the `minimum` floor still bites when it is set ABOVE the angle count
check(tessellate.curve_segments_for(2 * math.pi * 0.001, TOL, minimum=100) == 100,
      "explicit minimum=100 (above the angle count) not honoured")
# degenerate input never explodes AND is not treated as a tiny circle (no angle floor)
check(tessellate.curve_segments_for(0.0, TOL) == 16, "zero length not floored")
check(tessellate.curve_segments_for(length, 0.0) == 16, "zero tolerance not floored")

# --- scale-invariant maximum-angle criterion --------------------------------
# A circle of any radius gets the same count because angle is scale-free.
_counts = [tessellate.curve_segments_for(2 * math.pi * R, TOL)
           for R in (0.05, 0.5, 1.0, 5.0, 100.0)]
check(_counts[:4] == [72, 72, 72, 72],
      f"small circles of different radii got different counts {_counts[:4]} (want all 72)")
# the measured DEGREES PER SEGMENT is <= the angle limit at every radius (assert the
# angle, not the count: the count is chosen, while the error bound is promised)
for R in (0.05, 0.5, 1.0, 5.0, 100.0):
    _n = tessellate.curve_segments_for(2 * math.pi * R, TOL)
    check(360.0 / _n <= tessellate.CURVE_MAX_ANGLE_DEG + 1e-9,
          f"radius {R}: {360.0/_n:.2f} deg/segment exceeds the "
          f"{tessellate.CURVE_MAX_ANGLE_DEG} deg limit")
# the CHORD criterion still WINS where it is stricter -- max() works BOTH ways, not
# just one direction.
_big = tessellate.curve_segments_for(2 * math.pi * 500, 0.001)
check(_big > 72, f"chord criterion did not win on a large curve at a tight tolerance ({_big})")
check(360.0 / _big < tessellate.CURVE_MAX_ANGLE_DEG,
      "a chord-driven large curve should be FINER than the angle limit, not coarser")
# the angle input is a working keyword (back-compatible positional calls unaffected)
check(tessellate.curve_segments_for(2 * math.pi * 0.5, TOL, max_angle_deg=10.0) == 36,
      "max_angle_deg=10 should give 36 segments on a small circle")
# a STRAIGHT line is not turned into a 72-gon: the bridge draws it through its exact
# endpoints and never calls the circle-modelling count for one.
_line_pts = bridge._curve_display_points(
    r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(100, 0, 0)), bridge.CURVE_CHORD_TOL)
check(len(_line_pts) == 2,
      f"a straight line got {len(_line_pts)} display points, expected 2 (over-tessellated)")

# --- integration: bridge.curve_display_bmesh -------------------------------
def circle_curve(radius):
    return r3.NurbsCurve.CreateFromCircle(r3.Circle(float(radius)))


small_bm = bridge.curve_display_bmesh(circle_curve(5.0))
big_bm = bridge.curve_display_bmesh(circle_curve(500.0))
small_v = len(small_bm.verts)
big_v = len(big_bm.verts)
small_bm.free()
big_bm.free()
check(big_v > small_v,
      f"radius-500 circle ({big_v} verts) not finer than radius-5 ({small_v} verts) "
      f"-- the fixed-64 bug")
check(16 <= small_v - 1 <= 1024 and 16 <= big_v - 1 <= 1024,
      f"segment counts out of clamp range: small={small_v - 1}, big={big_v - 1}")
print(f"circle segments: r=5 -> {small_v - 1}, r=500 -> {big_v - 1}")

# curve_segments_for is an equal-arc estimate, but the display walks the
# PARAMETER uniformly, so on a rational-quadratic circle the worst chord
# deviation OVERSHOOTS the tolerance. Pin the real deviation to the honest bound
# the docstring now promises (~1.2x), so neither the overshoot nor a future
# under-tessellation can regress silently.
G6_TOL = bridge.CURVE_CHORD_TOL
worst_ratio = 0.0
for R in (1.0, 4.0, 4.5, 5.0, 30.0, 500.0):
    c6 = r3.NurbsCurve.CreateFromCircle(r3.Circle(R))    # centre at origin
    dpts = bridge._curve_display_points(c6, G6_TOL)
    worst = 0.0
    for a, b in zip(dpts, dpts[1:]):
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        worst = max(worst, R - math.hypot(mx, my))       # arc-to-chord sagitta
    worst_ratio = max(worst_ratio, worst / G6_TOL)
check(worst_ratio <= 1.2,
      f"real circle display deviates {worst_ratio:.2f}x the {G6_TOL} tolerance, "
      f"over the ~1.2x bound the docstring promises")
print(f"real display deviation: worst {worst_ratio:.2f}x tolerance (bound 1.2x)")

# a polyline is drawn through its EXACT corners, NOT sampled (corners must not
# round off). A 3-vertex open polyline -> exactly 3 display vertices.
poly = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(2, 2, 0),
                         r3.Point3d(4, 1, 0)])
pbm = bridge.curve_display_bmesh(poly)
poly_v = len(pbm.verts)
pbm.free()
check(poly_v == 3,
      f"polyline drawn with {poly_v} verts, expected its 3 exact corners "
      f"(adaptive sampling would round the corners off)")

# a straight line -> 2 vertices (one segment), not 16
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)).ToNurbsCurve()
lbm = bridge.curve_display_bmesh(line)
line_v = len(lbm.verts)
lbm.free()
check(line_v == 2, f"straight line drawn with {line_v} verts, expected 2")

# a JOINED shape (PolyCurve: line + arc + line) must keep its interior corners --
# uniform sampling across the whole curve drops them and "loses the shape".
pc = r3.PolyCurve()
pc.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0)))
pc.Append(r3.NurbsCurve.CreateFromArc(
    r3.Arc(r3.Point3d(5, 0, 0), r3.Point3d(7, 2, 0), r3.Point3d(5, 4, 0))))
pc.Append(r3.LineCurve(r3.Point3d(5, 4, 0), r3.Point3d(0, 4, 0)))
pcbm = bridge.curve_display_bmesh(pc)
pc_pts = {(round(v.co.x, 3), round(v.co.y, 3), round(v.co.z, 3)) for v in pcbm.verts}
pcbm.free()
for corner in ((5.0, 0.0, 0.0), (5.0, 4.0, 0.0)):
    check(corner in pc_pts,
          f"joined PolyCurve lost its corner {corner} (line/arc joint rounded off)")

# ===========================================================================
# A moved control point makes a real corner that must survive display.
# Exercise segment counts not divisible by four.
# ===========================================================================
def circle_with_corner(radius):
    """A circle whose on-curve control point at t=pi/2 is pushed out by 0.4R,
    which the kernel turns into a genuine ~49.6 deg C0 corner (MEASURED)."""
    c = r3.NurbsCurve.CreateFromCircle(r3.Circle(float(radius)))
    bridge.set_control_point(c, 2, (0.4 * radius, 1.4 * radius, 0.0))
    return c


def display_points(curve):
    # _curve_display_points is the function that OWNS the knot-sampling fix and
    # returns double-precision points; curve_display_bmesh then stores them as
    # float32 in bmesh, so testing the source avoids float32 noise (~2e-7).
    return bridge._curve_display_points(curve, bridge.CURVE_CHORD_TOL)


def max_turn_deg(pts):
    best = 0.0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        lu = math.sqrt(sum(x * x for x in u))
        lv = math.sqrt(sum(x * x for x in v))
        if lu < 1e-12 or lv < 1e-12:
            continue
        d = max(-1.0, min(1.0, sum(x * y for x, y in zip(u, v)) / (lu * lv)))
        best = max(best, math.degrees(math.acos(d)))
    return best


# UNLUCKY radii: n = 50, 62, 518 -- none divisible by 4, so the old uniform walk
# missed the pi/2 knot and tessellated the corner away. r=5 (n=52) would pass
# either way, which would not expose the defect.
UNLUCKY = (4.5, 7.0, 500.0)

# 1. the corner (the moved control point at t=pi/2) is represented exactly
for R in UNLUCKY:
    c = circle_with_corner(R)
    cp = c.PointAt(math.pi / 2)
    corner = (cp.X, cp.Y, cp.Z)
    near = min(math.dist(corner, p) for p in display_points(c))
    check(near < 1e-9,
          f"radius {R}: no display vertex within 1e-9 of the moved corner "
          f"(closest {near:.2e}) -- corner tessellated away")

# 2. knot-coverage INVARIANT (states the rule, not a symptom): every distinct
#    interior knot of a NurbsCurve is represented in the display -- catches
#    circles, arcs and ellipses in one stroke, moved or not. The interior knots
#    are computed INDEPENDENTLY here (from the raw knot vector), not via the
#    function under test, so the invariant cannot be satisfied circularly.
def independent_interior_knots(curve):
    vals = list(curve.Knots.ToList())
    d = curve.Domain
    eps = (d.T1 - d.T0) * 1e-9
    return sorted({v for v in vals if d.T0 + eps < v < d.T1 - eps})


knot_curves = [r3.NurbsCurve.CreateFromCircle(r3.Circle(7.0)),
               r3.NurbsCurve.CreateFromArc(
                   r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0)))]
for c in knot_curves:
    pts = display_points(c)
    interior = independent_interior_knots(c)
    check(len(interior) >= 1, f"expected interior knots on {type(c).__name__}")
    for tk in interior:
        kp = c.PointAt(tk)
        near = min(math.dist((kp.X, kp.Y, kp.Z), p) for p in pts)
        check(near < 1e-9,
              f"{type(c).__name__}: interior knot t={tk:.4f} not sampled "
              f"(closest {near:.2e})")

# 3. turn-angle gate (what the eye sees): the display must show most of the real
#    ~49.6 deg corner, not the ~35 deg a rounded-off corner leaves.
turn = max_turn_deg(display_points(circle_with_corner(4.5)))
check(turn > 45.0,
      f"largest display turn {turn:.1f} deg; the real corner is 49.6 deg "
      f"(a tessellated-away corner reads ~35 deg)")
print(f"corner: sampled radii remain exact; max display turn {turn:.1f} deg")

# A kink must survive tessellation. Force samples only at full-multiplicity knots
# (real C0 corners), not at every knot.
# (a) a SMOOTH free-form NURBS with SIMPLE interior knots (mult < degree) gets NO
#     forced samples -- it is C1+, so there is no corner to protect.
_smooth_nurbs = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(2, 3, 0), r3.Point3d(5, -1, 0),
     r3.Point3d(8, 2, 0), r3.Point3d(11, 0, 0), r3.Point3d(14, 4, 0)], 3)
_forced_smooth = bridge._interior_knot_params(_smooth_nurbs)
check(_forced_smooth == [],
      f"smooth simple-knot NURBS forced samples at {_forced_smooth}; only full-"
      f"multiplicity knots (C0 corners) should be forced")
# (b) the circle's quadrant knots ARE full-multiplicity (mult == degree), so they
#     ARE still forced -- the fix must not stop protecting real corners.
_forced_circle = bridge._interior_knot_params(
    r3.NurbsCurve.CreateFromCircle(r3.Circle(5.0)))
check(len(_forced_circle) == 3,
      f"circle should force its 3 full-mult quadrant knots, got {_forced_circle}")
# (c) the SMOOTH circle is not given a spurious corner by that forcing.
_smooth_turn = max_turn_deg(display_points(
    r3.NurbsCurve.CreateFromCircle(r3.Circle(5.0))))
check(_smooth_turn < 10.0,
      f"smooth circle shows a {_smooth_turn:.1f} deg corner -- knot-forcing made a "
      f"spurious kink")
# (d) a joined 2-segment polyline with a 90 deg corner keeps a vertex AT the corner,
#     at a true 90 deg, not smoothed.
_corner = (4.0, 4.0, 0.0)
_pl = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(*_corner),
                        r3.Point3d(8, 0, 0)])
_plp = display_points(_pl)
_near = min(math.dist(_corner, p) for p in _plp)
check(_near < 1e-9, f"polyline corner not a display vertex (closest {_near:.2e})")
check(abs(max_turn_deg(_plp) - 90.0) < 1e-6,
      f"polyline 90 deg corner reads {max_turn_deg(_plp):.3f} deg (smoothed?)")
# (e) a smooth curve with MANY simple knots does not explode the segment count.
_many = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(i, (i % 3) - 1, 0) for i in range(40)], 3)
_adaptive = bridge.curve_display_segments(_many, bridge.CURVE_CHORD_TOL) + 1
_got = len(display_points(_many))
check(bridge._interior_knot_params(_many) == [],
      "a smooth many-knot curve should force no samples")
check(_got <= _adaptive + 2,
      f"smooth many-knot curve exploded: {_got} display pts vs adaptive {_adaptive}")
print(f"full-multiplicity forcing: smooth simple-knot curves add 0 samples; "
      f"many-knot curve {_got} pts ~ adaptive {_adaptive}")

# ===========================================================================
# Display shading: curved surfaces must SMOOTH-shade so they read round.
# ===========================================================================

# The lever: every display face is smooth-flagged, so Blender averages normals
# across a connected surface (a cylinder wall) and it reads round. Hard creases
# come for free because brep_to_bmesh builds each brep face as its OWN vertex
# island, so smoothing cannot bleed across a box edge or a wall-to-cap seam.

# a box: all faces smooth, yet it stays crisp because its 6 faces are separate
# islands (unwelded) -- so the corner vertices are duplicated, not shared.
bbm = bridge.geometry_to_bmesh(box_brep(0, 0, 0, 2, 2, 2))
check(all(f.smooth for f in bbm.faces), "box faces were not marked smooth")
check(len(bbm.verts) > 8,
      f"box has {len(bbm.verts)} verts; welded (<=8) would smooth ACROSS its "
      f"90-degree edges and round the corners")
bbm.free()

# a capped extrusion: all faces smooth, and the cylinder wall is ONE connected
# island (it has interior manifold edges), so smoothing rounds it.
sbm = bridge.geometry_to_bmesh(capped_circle_extrusion(5.0, 6.0))
check(all(f.smooth for f in sbm.faces), "extrusion faces were not marked smooth")
manifold_edges = sum(1 for e in sbm.edges if len(e.link_faces) == 2)
check(manifold_edges > 0,
      "extrusion wall has no connected (manifold) edges to smooth-shade across")
print(f"shading: extrusion faces all smooth={all(f.smooth for f in sbm.faces)}, "
      f"{manifold_edges} connected wall edges")
sbm.free()

# ===========================================================================
# A hidden object must not render; a locked one remains drawn.
# ===========================================================================
import types as _types  # noqa: E402

_f = r3.File3dm()
_f.Objects.AddBrep(box_brep(0, 0, 0, 2, 2, 2), None)
_f.Objects[0].Attributes.Name = "Vis_BoxA"
_f.Objects.AddBrep(box_brep(5, 0, 0, 7, 2, 2), None)
_f.Objects[1].Attributes.Name = "Vis_BoxB"
_f.Objects.AddCurve(r3.NurbsCurve.CreateFromCircle(r3.Circle(3.0)), None)
_f.Objects[2].Attributes.Name = "Vis_Circle"
_model = _types.SimpleNamespace(file=_f)

# build the display objects the way rebuild_display does (via bmesh_to_object)
_disp = {}
for _fo in _f.Objects:
    _nm = _fo.Attributes.Name
    _disp[_nm] = bridge.bmesh_to_object(_nm, bridge.geometry_to_bmesh(_fo.Geometry))

# all Normal -> nothing hidden
check(bridge.apply_document_visibility(_model) == 0, "no objects hidden when all Normal")
check(not any(_disp[k].hide_viewport for k in _disp), "all objects drawn when Normal")

# hide BoxB -> it disappears, the others stay
_f.Objects[1].Attributes.Mode = r3.ObjectMode.Hidden
check(bridge.apply_document_visibility(_model) == 1, "exactly one object hidden")
check(_disp["Vis_BoxB"].hide_viewport is True and _disp["Vis_BoxB"].hide_render is True,
      "a Hidden object is not drawn")
check(not _disp["Vis_BoxA"].hide_viewport and not _disp["Vis_Circle"].hide_viewport,
      "hiding one object did not disturb the others")

# Show (Mode -> Normal) restores it, and it is the SAME object (no stale)
_same = _disp["Vis_BoxB"]
_f.Objects[1].Attributes.Mode = r3.ObjectMode.Normal
bridge.apply_document_visibility(_model)
check(_disp["Vis_BoxB"].hide_viewport is False, "Show restores the hidden object")
check(bpy.data.objects.get("Vis_BoxB") is _same,
      "Show reused the same Blender object (no stale/duplicate left behind)")

# Locked -> STILL drawn (locking is not hiding)
_f.Objects[1].Attributes.Mode = r3.ObjectMode.Locked
check(bridge.apply_document_visibility(_model) == 0
      and _disp["Vis_BoxB"].hide_viewport is False,
      "a Locked object is still drawn (locking is not hiding; unpickable is ui.py's)")
print("hidden objects disappear; locked objects remain drawn")

# ===========================================================================
# Surface control points pair across closed seams.
# ===========================================================================
def srf_loc(s, i, j):
    c = s.Points.GetControlPoint(i, j)
    w = c.W or 1.0
    return (c.X / w, c.Y / w, c.Z / w)


# a cylinder side is closed in U (seam column iu=0 <-> last)
cyl = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 10.0).ToNurbsSurface()
grid = bridge.surface_control_points(cyl)
check(len(grid) == cyl.Points.CountU and all(len(r) == cyl.Points.CountV for r in grid),
      f"grid dims {len(grid)}x{len(grid[0]) if grid else 0} != "
      f"CountU/V {cyl.Points.CountU}x{cyl.Points.CountV}")

nu = cyl.Points.CountU
check(cyl.IsClosed(0), "cylinder is closed in U (fixture)")
ok = bridge.set_surface_control_point(cyl, 0, 0, (9.0, 1.0, 0.0))
check(ok, "set_surface_control_point returned False")
check(math.dist(srf_loc(cyl, 0, 0), (9.0, 1.0, 0.0)) < 1e-9, "seam CV moved")
check(math.dist(srf_loc(cyl, nu - 1, 0), (9.0, 1.0, 0.0)) < 1e-9,
      "the seam-column twin did NOT move -- the surface would tear open")
check(cyl.IsClosed(0), "cylinder stayed closed in U after the seam edit")

# moving an INTERIOR column CV must not touch the seam
cyl2 = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 10.0).ToNurbsSurface()
before_last = srf_loc(cyl2, cyl2.Points.CountU - 1, 0)
bridge.set_surface_control_point(cyl2, 2, 0, (1.0, 2.0, 3.0))
check(math.dist(srf_loc(cyl2, cyl2.Points.CountU - 1, 0), before_last) < 1e-12,
      "moving an interior surface CV disturbed the seam column")

# a TORUS is closed in BOTH directions -- test each (a U-only impl passes all U tests)
_prof = r3.Circle(3.0).ToNurbsCurve()
_prof.Transform(r3.Transform.Rotation(math.pi / 2, r3.Vector3d(1, 0, 0), r3.Point3d(0, 0, 0)))
_prof.Translate(r3.Vector3d(10, 0, 0))
torus = r3.RevSurface.Create(_prof, r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 1)),
                             0.0, 2 * math.pi).ToNurbsSurface()
tu, tv = torus.Points.CountU, torus.Points.CountV
check(torus.IsClosed(0) and torus.IsClosed(1), "torus closed in both U and V (fixture)")
# move a V-seam CV on an interior column -> its V-twin moves, V stays closed
bridge.set_surface_control_point(torus, 3, 0, (12.0, 0.5, 4.0))
check(math.dist(srf_loc(torus, 3, tv - 1), (12.0, 0.5, 4.0)) < 1e-9,
      "torus V-seam twin (row 0 <-> last) did not move -- V-only tear")
check(torus.IsClosed(1), "torus stayed closed in V after the V-seam edit")

# an OPEN surface has no seam -- moving cv[0][0] leaves cv[last][0] alone
flat = r3.NurbsSurface.CreateRuledSurface(
    r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)),
    r3.LineCurve(r3.Point3d(0, 8, 0), r3.Point3d(10, 8, 0)))
fu = flat.Points.CountU
open_before = srf_loc(flat, fu - 1, 0)
bridge.set_surface_control_point(flat, 0, 0, (-5.0, -5.0, 0.0))
check(math.dist(srf_loc(flat, fu - 1, 0), open_before) < 1e-12,
      "an OPEN surface's far CV moved -- seam logic over-reached")

# a singular POLE is NOT a seam: on a sphere (closed U, pole rows at V-ends),
# a pole-row CV must get no seam twin, while a non-pole seam CV does.
sph = r3.Sphere(r3.Point3d(0, 0, 0), 4.0).ToNurbsSurface()
pole_twins = bridge._surface_seam_twins(sph, 0, 0)                 # (0,0) is on a pole row
mid_iv = sph.Points.CountV // 2
seam_twins = bridge._surface_seam_twins(sph, 0, mid_iv)           # a genuine U-seam CV
check(len(pole_twins) == 1,
      f"a singular pole CV was treated as a seam (got {len(pole_twins)} twins)")
check((sph.Points.CountU - 1, mid_iv) in seam_twins,
      "a genuine U-seam CV did not pair with its twin column")
print("surface seams move together; open surfaces and poles remain untouched")

# --- report -----------------------------------------------------------------
# --- seam welding: a closed surface must not leave a shading seam ------------
# The cylinder wall is closed in u; if its first and last grid columns are
# separate vertices the smooth normals do not wrap the seam and a line shows.
ext_brep = capped_circle_extrusion(5.0, 6.0)
wall = next(f for f in ext_brep.Faces if not f.IsPlanar())
cap = next(f for f in ext_brep.Faces if f.IsPlanar())

wbm = _bmesh.new()
bridge._build_grid(wbm, wall, 8, 4)      # closed in u -> last column reuses first
check(len(wbm.verts) == 8 * 5,
      f"closed wall grid has {len(wbm.verts)} verts, expected 40 -- the seam is "
      f"not welded, so a shading line shows down the cylinder")
wbm.free()

cbm = _bmesh.new()
bridge._build_grid(cbm, cap, 8, 4)       # a planar cap is NOT closed -> full grid
check(len(cbm.verts) == 9 * 5,
      f"open (planar) face grid has {len(cbm.verts)} verts, expected 45")
cbm.free()

# --- adaptive surface density: round where it curves, coarse where straight --
around = bridge._iso_segments(wall, 0, bridge.CURVE_CHORD_TOL)   # closed circumference
height = bridge._iso_segments(wall, 1, bridge.CURVE_CHORD_TOL)   # straight height
check(around >= 40,
      f"cylinder wall uses only {around} facets around (was a fixed 32) -- too "
      f"few to read round")
check(height == 1,
      f"cylinder wall uses {height} segments up its STRAIGHT height, expected 1")
print(f"adaptive surface: wall = {around} around x {height} up")

# ===========================================================================
# Adaptive, knot-aware surface tessellation.
# ===========================================================================
TOL = bridge.CURVE_CHORD_TOL


def srf_interior_knots(surface, direction):
    dom = surface.Domain(direction)
    kl = surface.KnotsU if direction == 0 else surface.KnotsV
    eps = (dom.T1 - dom.T0) * 1e-9
    return sorted({v for v in kl.ToList() if dom.T0 + eps < v < dom.T1 - eps})


# a PLANE collapses to the MINIMUM in both directions and stays exactly flat.
plane_srf = r3.NurbsSurface.CreateRuledSurface(
    r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)),
    r3.LineCurve(r3.Point3d(0, 8, 0), r3.Point3d(10, 8, 0)))
pus = bridge._surface_axis_params(plane_srf, 0, TOL)
pvs = bridge._surface_axis_params(plane_srf, 1, TOL)
check(len(pus) - 1 == 1 and len(pvs) - 1 == 1,
      f"a plane should tessellate at the minimum 1x1, got {len(pus)-1}x{len(pvs)-1}")
pbm = bridge.surface_to_bmesh(plane_srf)
check(len(pbm.verts) <= 4, f"a plane needs <=4 verts, got {len(pbm.verts)}")
check(max(abs(v.co.z) for v in pbm.verts) < 1e-9, "a tessellated plane is not flat")
pbm.free()

# a CYLINDER: the COUNT is ours to choose, but the SAGITTA is what we promised
# Assert the adaptive grid's worst chord height rather than a chosen segment count,
# allowing the documented equal-arc versus uniform-parameter tolerance.
cyl30 = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 10.0).ToNurbsSurface()
cus = bridge._surface_axis_params(cyl30, 0, TOL)
cvs = bridge._surface_axis_params(cyl30, 1, TOL)
mv = cvs[0]
pts30 = [cyl30.PointAt(u, mv) for u in cus]
worst30 = 0.0
for a, b in zip(pts30, pts30[1:]):
    mx, my = (a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0
    worst30 = max(worst30, abs(math.hypot(mx, my) - 5.0))
check(worst30 <= TOL * 1.3,
      f"cylinder grid worst sagitta {worst30:.5f} > tolerance {TOL} (x1.3 allowance)")
check(len(cvs) - 1 == 1,
      f"cylinder's straight height should be 1 segment, got {len(cvs)-1}")

# KNOT-AWARE: every interior knot in the curved direction is a sample line (the
# Interior knots are computed independently from the raw vector.
cyl_knots = srf_interior_knots(cyl30, 0)
check(len(cyl_knots) >= 1, "expected interior U knots on the cylinder (fixture)")
for kt in cyl_knots:
    check(min(abs(kt - u) for u in cus) < 1e-9,
          f"cylinder U-knot {kt:.4f} is not a sample line -- not knot-aware")

# non-uniform knots -> MORE samples where the knots are dense. Insert a cluster
# in the straight (V) direction on a fresh cylinder and check the dense half gets
# more sample lines than the sparse half of equal parameter width.
cylk = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 10.0).ToNurbsSurface()
dvk = cylk.Domain(1)
vmid = 0.5 * (dvk.T0 + dvk.T1)
clustered = False
try:
    for frac in (0.08, 0.11, 0.14, 0.17, 0.20):
        cylk.KnotsV.InsertKnot(dvk.T0 + (dvk.T1 - dvk.T0) * frac, 1)
    clustered = len(srf_interior_knots(cylk, 1)) >= 4
except Exception:  # noqa: BLE001
    clustered = False
if clustered:
    vsk = bridge._surface_axis_params(cylk, 1, TOL)
    dense = sum(1 for v in vsk if v < vmid)
    sparse = sum(1 for v in vsk if v >= vmid)
    check(dense > sparse,
          f"knot-dense half has {dense} sample lines, sparse half {sparse} -- "
          f"a knot-aware grid must put more where the knots are")
    for kt in srf_interior_knots(cylk, 1):
        check(min(abs(kt - v) for v in vsk) < 1e-9,
              f"inserted V-knot {kt:.4f} is not a sample line")
else:
    print("KnotsV.InsertKnot unavailable; non-uniform density check skipped")

# CLOSED surfaces stay WELDED on the ADAPTIVE grid -- no duplicate seam column
# Test both U and V closure so a one-axis implementation cannot pass.
cyl_bm = bridge.surface_to_bmesh(cyl30)                 # closed in U
cu, cv = len(bridge._surface_axis_params(cyl30, 0, TOL)) - 1, \
    len(bridge._surface_axis_params(cyl30, 1, TOL)) - 1
check(len(cyl_bm.verts) == cu * (cv + 1),
      f"adaptive closed-U cylinder has {len(cyl_bm.verts)} verts, expected "
      f"{cu*(cv+1)} (seam column welded, not duplicated)")
cyl_bm.free()

_torus_prof = r3.Circle(3.0).ToNurbsCurve()
_torus_prof.Transform(r3.Transform.Rotation(math.pi / 2, r3.Vector3d(1, 0, 0),
                                            r3.Point3d(0, 0, 0)))
_torus_prof.Translate(r3.Vector3d(10, 0, 0))
torus30 = r3.RevSurface.Create(
    _torus_prof, r3.Line(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 1)),
    0.0, 2 * math.pi).ToNurbsSurface()
tus = len(bridge._surface_axis_params(torus30, 0, TOL)) - 1
tvs = len(bridge._surface_axis_params(torus30, 1, TOL)) - 1
tor_bm = bridge.surface_to_bmesh(torus30)              # closed in BOTH
check(len(tor_bm.verts) == tus * tvs,
      f"adaptive closed-both torus has {len(tor_bm.verts)} verts, expected "
      f"{tus*tvs} (both seams welded)")
tor_bm.free()

# CLAMP: a near-zero tolerance cannot ask for a flood, a huge one cannot ask for 0.
tiny = bridge._surface_axis_params(cyl30, 0, 1e-12)
huge = bridge._surface_axis_params(plane_srf, 0, 1e9)
check(len(tiny) - 1 <= bridge.SURFACE_MAX_SEGMENTS + len(cyl_knots),
      f"a near-zero tolerance asked for {len(tiny)-1} segments (clamp broken)")
check(len(huge) - 1 >= 1, "a huge tolerance must still give at least 1 segment")
print(f"adaptive surface: plane min+flat; cylinder sagitta {worst30:.4f} <= {TOL}x1.3; "
      f"knot-aware; closed welded; clamped")

# ===========================================================================
# A smooth extruded curve must not expose display facets as vertical lines in
# Shaded mode. The periodic fixture has ten distinct control points; creation adds wrapped
# copies and affine-equivalent uniform knots. Keep the fixture self-contained.
# ===========================================================================
_g110_pts = [
    (-12.916098975959214, 2.6567925108776875, 0.0),
    (-8.011251263569637, 2.6567925108776875, 0.0),
    (3.433393398672703, 8.624357227618335, 0.0),
    (16.022502527139284, 5.027468905199315, 0.0),
    (11.935129433481304, -8.706104689491497, 0.0),
    (-3.67863578429218, -13.610952401881068, 0.0),
    (-18.393178921460905, -11.64901331692524, 0.0),
    (-24.687733485694196, 1.1035907352876546, 0.0),
    (-22.807541862611522, 10.668043774447325, 0.0),
    (-11.771634509734977, 13.283962554388435, 0.0),
]
_g110_curve = r3.NurbsCurve.Create(
    True, 3, [r3.Point3d(*p) for p in _g110_pts])
_g110_brep = r3.Extrusion.Create(_g110_curve, 17.59, True).ToBrep(False)
_g110_bm = bridge.brep_to_bmesh(_g110_brep, trim_planar=True, adaptive=True)
_g110_angles = [math.degrees(e.calc_face_angle()) for e in _g110_bm.edges
                if len(e.link_faces) == 2]
_g110_max = max(_g110_angles)
_g110_visible = sum(a > bridge.SURFACE_EDGE_SHOWN_DEG for a in _g110_angles)
check(_g110_visible == 0 and _g110_max < bridge.SURFACE_EDGE_SHOWN_DEG,
      f"the curved side has {_g110_visible} display facet joint(s) over "
      f"Blender's visible-edge threshold {bridge.SURFACE_EDGE_SHOWN_DEG} deg; max "
      f"{_g110_max:.2f} deg (today's broken grid: 47 joints, max 16.6 deg)")
check(len(_g110_bm.faces) < 589 * 3,
      f"turn-aware display stayed sane: {len(_g110_bm.faces)} facets "
      f"(< 3x the broken grid's 589)")
_g110_bm.free()
# A cylinder already stays under the threshold at its existing 72 facets: this
# targeted fix must not globally raise surface density.
check(len(cus) - 1 == 72,
      f"ordinary-cylinder density changed: {len(cus)-1}, expected 72")
_g110_tiny = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), 0.05), 1.0).ToNurbsSurface()
_g110_tiny_u = bridge._surface_axis_params(_g110_tiny, 0, TOL)
check(len(_g110_tiny_u) - 1 == 72,
      f"tiny-cylinder density changed: "
      f"{len(_g110_tiny_u)-1}, expected its existing 72")
print(f"extrusion max facet turn {_g110_max:.2f} deg, "
      f"{_g110_visible} visible joints; cylinders stay {len(cus)-1}/"
      f"{len(_g110_tiny_u)-1}")

# ===========================================================================
# Cap planar open boundary loops into a closed solid.
# ===========================================================================
def tube_brep(radius, height):
    return r3.Extrusion.Create(
        r3.NurbsCurve.CreateFromCircle(r3.Circle(radius)), height, False).ToBrep(False)


_tube = tube_brep(5.0, 6.0)
check(not _tube.IsSolid, "the tube fixture should start OPEN (not a solid)")
_cap = bridge.cap_planar_holes(_tube)
check(_cap.brep.IsSolid, f"an uncapped tube did not become solid ({_cap!r})")
check(_cap.capped == 2, f"a tube has TWO open ends; capped {_cap.capped} (one-loop bug?)")

# the cap's area equals the hole's enclosed area: the cap triangulates the SAME
# loop (ear-clip tiles the polygon exactly), so the test's real job is that the
# loop is the radius-5 rim, not a wrong/degenerate one. The welded mesh uses the
# fixed BOOLEAN_NU=32, so the rim is a 32-gon (area 78.03, ~0.5 under the ideal
# pi*25=78.54); a wrong loop would miss by far more than that.
_wbm = bridge._welded_brep_mesh(_tube)
_loops = bridge._boundary_loops(_wbm)
check(len(_loops) == 2, f"the open tube should have 2 boundary loops, got {len(_loops)}")
for _loop in _loops:
    _pts = [(v.co.x, v.co.y, v.co.z) for v in _loop]
    _pl = bridge._loop_plane(_pts, tessellate)
    _area = abs(tessellate.signed_area(
        tessellate.project_to_plane(_pts, _pl[0], _pl[1], _pl[2])))
    check(abs(_area - math.pi * 25.0) < 1.0,
          f"a cap loop's area {_area:.3f} != hole area ~{math.pi*25:.3f} (wrong loop?)")
_wbm.free()

# the capped solid still TESSELLATES (a brep that is solid but won't draw is no win)
_cbm = bridge.geometry_to_bmesh(_cap.brep)
check(len(_cbm.verts) > 0 and len(_cbm.faces) > 0, "the capped solid will not tessellate")
_cbm.free()

# an ALREADY-solid brep is returned unchanged and says so (no degenerate face)
_solidcap = bridge.cap_planar_holes(
    r3.Extrusion.Create(r3.NurbsCurve.CreateFromCircle(r3.Circle(3.0)), 4.0, True).ToBrep(False))
check(_solidcap.capped == 0 and "closed" in _solidcap.warning.lower(),
      f"an already-solid brep should be unchanged and say so ({_solidcap!r})")

# a NON-planar open loop is left open and REPORTED, not patched with a flat face.
# A ruled surface between two NON-coplanar lines has a non-planar 4-edge border.
_rs = r3.NurbsSurface.CreateRuledSurface(
    r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)),
    r3.LineCurve(r3.Point3d(0, 10, 5), r3.Point3d(10, 10, -5)))
_nonplanar = False
try:
    bridge.cap_planar_holes(r3.Brep.CreateFromSurface(_rs))
except ValueError as _e:
    _nonplanar = "planar" in str(_e).lower()
check(_nonplanar, "a non-planar hole must be refused, naming planarity (not patched)")
print(f"cap: tube -> solid (capped {_cap.capped}); area matches; already-solid "
      f"unchanged; non-planar refused")

# ===========================================================================
# Screen-space, view-dependent retessellation.
# ===========================================================================
R38 = 5.0
L38 = 2.0 * math.pi * R38
_n_lo = tessellate.screen_segments_for(L38, 10.0)        # zoomed OUT
_n_hi = tessellate.screen_segments_for(L38, 10000.0)     # zoomed IN
check(_n_hi > _n_lo, f"zooming in did not add segments ({_n_lo} -> {_n_hi})")
# both satisfy the PIXEL tolerance: the on-screen sagitta is <= SCREEN_PIXEL_TOL
# Allow the documented equal-arc versus parameter overshoot.
for _ppu, _nn in ((10.0, _n_lo), (10000.0, _n_hi)):
    _px = R38 * (1.0 - math.cos(math.pi / _nn)) * _ppu
    check(_px <= tessellate.SCREEN_PIXEL_TOL * 1.3 + 1e-9,
          f"at {_ppu} px/unit, {_nn} segs -> {_px:.3f}px sagitta > "
          f"{tessellate.SCREEN_PIXEL_TOL}px limit")
# clamp: an extreme zoom caps at `maximum`, never a million
check(tessellate.screen_segments_for(L38, 1e9) == 1024, "extreme zoom not capped")
check(tessellate.screen_segments_for(L38, 1e9, maximum=256) == 256,
      "explicit maximum not honoured by screen_segments_for")
# hysteresis: a 1% zoom is suppressed, a 10x zoom fires
_base = tessellate.screen_segments_for(L38, 1000.0)
_small = tessellate.screen_segments_for(L38, 1010.0)     # +1% zoom
_big = tessellate.screen_segments_for(L38, 10000.0)      # 10x zoom
check(not tessellate.should_retessellate(_base, _small),
      f"a 1% zoom should be suppressed ({_base} -> {_small})")
check(tessellate.should_retessellate(_base, _big),
      f"a 10x zoom should fire ({_base} -> {_big})")
# retessellate_curve_for_view touches exactly one object.
_cA, _cB = circle_curve(5.0), circle_curve(5.0)
_obA = bridge.bmesh_to_object("ZoomA", bridge.geometry_to_bmesh(_cA))
_obB = bridge.bmesh_to_object("ZoomB", bridge.geometry_to_bmesh(_cB))
_vA0, _vB0 = len(_obA.data.vertices), len(_obB.data.vertices)
_ok = bridge.retessellate_curve_for_view("ZoomA", _cA, 5000.0)
check(_ok, "retessellate_curve_for_view returned False for an existing object")
check(len(_obA.data.vertices) != _vA0,
      f"ZoomA was not re-tessellated denser ({_vA0} -> {len(_obA.data.vertices)})")
check(len(_obB.data.vertices) == _vB0,
      "retessellating ZoomA disturbed ZoomB")

# A deferred Gumball commit transforms the document curve and leaves the equivalent
# translation/rotation on its display object's matrix. Re-tessellation receives the
# current world-space document curve, so its fresh vertices must be converted back to
# object-local space; otherwise the retained matrix applies the transform twice.
from mathutils import Matrix  # noqa: E402

_gm = (Matrix.Translation((13.0, -7.0, 2.5)) @
       Matrix.Rotation(math.radians(37.0), 4, "Z"))
_gx = r3.Transform(1.0)
for _r in range(4):
    for _c in range(4):
        setattr(_gx, f"M{_r}{_c}", float(_gm[_r][_c]))
_g_local = r3.LineCurve(r3.Point3d(1.0, 2.0, -1.0),
                        r3.Point3d(5.0, -3.0, 4.0))
_g_document = _g_local.Duplicate()
check(_g_document.Transform(_gx), "Gumball-like document transform failed in fixture")
_gob = bridge.bmesh_to_object("ZoomGumball", bridge.geometry_to_bmesh(_g_local))
_gob.matrix_world = _gm.copy()
_gm_before = _gob.matrix_world.copy()
_gwant = sorted((p.X, p.Y, p.Z)
                for p in (_g_document.PointAtStart, _g_document.PointAtEnd))
check(bridge.retessellate_curve_for_view("ZoomGumball", _g_document, 5000.0),
      "Gumball-like curve was not re-tessellated")
_ggot = sorted((p.x, p.y, p.z)
               for p in (_gob.matrix_world @ v.co for v in _gob.data.vertices))
check(_gob.matrix_world == _gm_before,
      "retessellation changed the Gumball display object's matrix")
_gerr = max(abs(got - want) for gp, wp in zip(_ggot, _gwant)
            for got, want in zip(gp, wp))
check(_gerr < 2e-6,
      f"retessellation double-transformed translated+rotated curve: error {_gerr:g}, "
      f"{_ggot} != {_gwant}")
print(f"view retessellation: zoom {_n_lo}->{_n_hi} segments; clamped; hysteresis "
      f"suppresses 1% / fires 10x; one object touched; Gumball matrix stays coherent")

# --- report -----------------------------------------------------------------
print("\n" + "=" * 50)
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("TESSELLATE PASSED")
