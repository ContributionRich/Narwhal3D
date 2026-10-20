"""Surface trimming tests for kernel results and Blender display conversion."""
import importlib.util
import math
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "narwhal3d"))

import rhino3dm as r3  # noqa: E402

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
trim = _load("narwhal3d.trim", "narwhal3d/trim.py")
intersect = _load("narwhal3d.intersect", "narwhal3d/intersect.py")

failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


# --- the test geometry: a 20x20 flat plane at z=5, cut by a radius-5 cylinder on
#     the Z axis (height 0..10, so the plane sits at mid-height). The intersection
#     is a circle of radius 5 at z=5. Disc area = pi*25 ~ 78.5398; the plane is 400;
#     annulus = 400 - 78.5398 ~ 321.4602. These are the closed-form oracles. --------
R = 5.0
DISC = math.pi * R * R
PLANE_AREA = 20.0 * 20.0
ANNULUS = PLANE_AREA - DISC


def plane_srf(z, h=10.0):
    return r3.NurbsSurface.CreateRuledSurface(
        r3.LineCurve(r3.Point3d(-h, -h, z), r3.Point3d(h, -h, z)),
        r3.LineCurve(r3.Point3d(-h, h, z), r3.Point3d(h, h, z)))


PLANE = plane_srf(5.0)
CYL = r3.Cylinder(r3.Circle(r3.Point3d(0, 0, 0), R), 10.0).ToNurbsSurface()
N = 64  # tessellation density for the tests (exact analytic field -> fast)

# The cutter as an EXACT signed field: distance to the Z axis minus R (negative
# inside the cylinder). This is the fast, robust path -- see trim.py on why a
# general closest-point field is not used.
CYL_FIELD = trim.cylinder_field((0, 0, 0), (0, 0, 1), R)

# --- 1. keep the DISC (keep_point at the axis, inside the cylinder) --------------
t0 = time.time()
disc = trim.trim_surface_mesh(PLANE, (0, 0, 5), CYL_FIELD, nu=N, nv=N)
dt = time.time() - t0
check(not disc.missed, "disc trim wrongly reported the cutter missed")
check(len(disc.vertices) > 0 and len(disc.faces) > 0,
      f"disc trim produced an empty mesh: {disc}")
check(abs(disc.kept_area - DISC) < 0.1,
      f"disc area {disc.kept_area:.4f} not ~{DISC:.4f} (pi*25)")
check(abs(disc.total_area - PLANE_AREA) < 1e-6,
      f"plane total area {disc.total_area:.4f} not {PLANE_AREA}")
print(f"disc: kept {disc.kept_area:.4f} (want {DISC:.4f}), "
      f"{len(disc.faces)} tris, {dt:.2f}s")

# --- 2. keep the ANNULUS (keep_point at a far corner, outside the cylinder); the
#        two kept areas must sum to the whole plane. ------------------------------
ann = trim.trim_surface_mesh(PLANE, (9, 9, 5), CYL_FIELD, nu=N, nv=N)
check(not ann.missed, "annulus trim wrongly reported the cutter missed")
check(abs(ann.kept_area - ANNULUS) < 0.1,
      f"annulus area {ann.kept_area:.4f} not ~{ANNULUS:.4f}")
check(abs((disc.kept_area + ann.kept_area) - PLANE_AREA) < 1e-6,
      f"disc + annulus = {disc.kept_area + ann.kept_area:.6f}, not {PLANE_AREA} "
      f"(the two sides must partition the plane)")
print(f"annulus: kept {ann.kept_area:.4f} (want {ANNULUS:.4f}); "
      f"disc+annulus = {disc.kept_area + ann.kept_area:.6f}")

# --- 3. a cutter that MISSES: the surface is returned untouched and REPORTS it,
#        rather than an empty mesh. -----------------------------------------------
far = trim.cylinder_field((100, 0, 0), (0, 0, 1), R)
miss = trim.trim_surface_mesh(PLANE, (0, 0, 5), far, nu=N, nv=N)
check(miss.missed, "a cutter that does not cross the surface must report missed")
check(len(miss.vertices) > 0 and len(miss.faces) > 0,
      "a missed trim must return the whole surface, not an empty mesh")
check(abs(miss.kept_area - PLANE_AREA) < 1e-6,
      f"a missed trim must keep the whole plane: {miss.kept_area:.4f}")
check(len(miss.boundary) == 0, "a missed trim must have no cut boundary")
print(f"miss: missed={miss.missed}, kept {miss.kept_area:.4f}, "
      f"boundary {len(miss.boundary)}")

# --- 4. the trimmed boundary lies on the intersection curve. Every crossing
#        point must be at radius 5 and height z=5 (the closed-form circle), AND
#        within tolerance of the surface_surface polyline. --------------------------
worst_r = max(abs(math.hypot(p[0], p[1]) - R) for p in disc.boundary)
worst_z = max(abs(p[2] - 5.0) for p in disc.boundary)
check(worst_r < 1e-4, f"boundary not on radius {R}: worst {worst_r:.2e}")
check(worst_z < 1e-4, f"boundary not at z=5: worst {worst_z:.2e}")
check(disc.max_deviation < 1e-4,
      f"boundary max deviation off the cutter: {disc.max_deviation:.2e}")

ssx = intersect.surface_surface(PLANE, CYL)
check(len(ssx) == 1 and ssx[0].closed,
      f"expected one closed intersection loop here ({ssx})")
if ssx:
    poly = ssx[0].polyline

    def _pt_seg(p, a, b):
        abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        ap = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
        dd = abx * abx + aby * aby + abz * abz
        t = 0.0 if dd == 0 else (ap[0] * abx + ap[1] * aby + ap[2] * abz) / dd
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        q = (a[0] + abx * t, a[1] + aby * t, a[2] + abz * t)
        return math.dist(p, q)

    worst_to_curve = 0.0
    for p in disc.boundary:
        d = min(_pt_seg(p, poly[k], poly[k + 1]) for k in range(len(poly) - 1))
        worst_to_curve = max(worst_to_curve, d)
    # Distance to the intersection polyline (nearest segment, not vertex) is bounded by the
    # polyline's chord sagitta from the true circle, so it is tiny -- it proves the
    # cut lands where the surfaces meet.
    check(worst_to_curve < 0.01,
          f"trim boundary strays from the intersection curve: {worst_to_curve:.5f}")
    print(f"boundary: r err {worst_r:.1e}, z err {worst_z:.1e}, "
          f"to intersection curve {worst_to_curve:.5f}")

# --- 5. the result TESSELLATES and DRAWS. Under Blender, drive the real display
#        path trim_result_to_bmesh and assert a non-empty, finite mesh. -----------
try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except Exception:
    HAVE_BPY = False

if HAVE_BPY:
    bm = bridge.trim_result_to_bmesh(disc)
    nverts, nfaces = len(bm.verts), len(bm.faces)
    check(nverts > 0 and nfaces > 0,
          f"trim result drew an empty bmesh: {nverts} verts, {nfaces} faces")
    finite = all(math.isfinite(c) for v in bm.verts for c in (v.co.x, v.co.y, v.co.z))
    check(finite, "trim result bmesh has a non-finite vertex")
    rr = max(math.hypot(v.co.x, v.co.y) for v in bm.verts)
    check(rr <= R + 1e-3,
          f"disc bmesh reaches radius {rr:.4f} > {R} (kept the wrong side?)")
    print(f"draw (bpy): {nverts} verts, {nfaces} faces, max radius {rr:.4f}")
else:
    print("draw skipped (no bpy; Blender covers trim_result_to_bmesh)")

# --- report ------------------------------------------------------------------
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("TRIM PASSED")
