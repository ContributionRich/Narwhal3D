"""Gumball handle picking through selection_core: same handle as the Python path, and faster.

Run under Blender (the parity reference is Blender's own `location_3d_to_region_2d`):
  NARWHAL_SELECTION_CORE=$PWD/native/selection_core/target/release/libnarwhal_selection_core.so \
      tools/blender-patched/blender -b --factory-startup --python-exit-code 1 \
      --python spike/test_gumball_native_pick.py

Drives the real `ui._gumball_native_scored` (Rust projects the cached world hot
polylines and measures them) against the real Python path `ui._gumball_projected`
+ `gumball.pick`, on built widgets in several views and on hand-made handles for
the edge cases: a point that does not project (a None gap), an empty polyline, a
single point, the menu-ball tolerance and an exact tie. Also covers
`gumball.build_cached`, which keeps the handle list while the widget is unchanged.
"""
import math
import os
import random
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NARWHAL_SELECTION_CORE", os.path.join(
    ROOT, "artifacts", "selection-core", "linux-x86_64", "libnarwhal_selection_core.so"))
sys.path.insert(0, ROOT)

from mathutils import Matrix, Vector  # noqa: E402

from narwhal3d import gumball, native_selection, ui  # noqa: E402

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def perspective(eye, width=1200, height=800, fov_deg=50.0):
    eye = Vector(eye)
    forward = (-eye).normalized()
    right = forward.cross(Vector((0, 0, 1)))
    right = right.normalized() if right.length > 1e-9 else Vector((1, 0, 0))
    up = right.cross(forward)
    view = Matrix(((*right, -right.dot(eye)), (*up, -up.dot(eye)),
                   (*(-forward), forward.dot(eye)), (0, 0, 0, 1)))
    f = 1.0 / math.tan(math.radians(fov_deg / 2))
    near, far = 0.5, 5000.0
    proj = Matrix(((f * height / width, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                   (0, 0, -1, 0)))
    return (types.SimpleNamespace(perspective_matrix=proj @ view),
            types.SimpleNamespace(width=width, height=height, x=0, y=0), tuple(forward))


class Plane:
    origin = (0.0, 0.0, 0.0)
    xaxis = (1.0, 0.0, 0.0)
    yaxis = (0.0, 1.0, 0.0)
    zaxis = (0.0, 0.0, 1.0)


def python_choice(handles, region, rv3d, mouse):
    return gumball.pick(ui._gumball_projected(handles, region, rv3d), mouse)


def native_choice(handles, region, rv3d, mouse):
    scored = ui._gumball_native_scored(handles, region, rv3d, mouse)
    return "no native result" if scored is None else gumball.pick_scored(scored)


check(native_selection.available(), f"selection_core loads ({native_selection.backend_status()})")

print("\n-- built widgets: native picks the same handle on every cursor --")
rng = random.Random(7)
views = (("oblique", (300.0, -400.0, 250.0), 60.0),
         ("top", (0.0, -0.001, 600.0), 60.0),
         # The eye sits inside the widget: some hot points fall behind the camera and
         # project to None, so the Python path drops them and joins the neighbours.
         ("eye inside the widget", (20.0, -30.0, 15.0), 60.0))
for name, eye, size in views:
    rv3d, region, view_dir = perspective(eye)
    handles = gumball.build_cached((0, 0, 0), Plane(), size, view_dir=view_dir, extrude=True)
    projected = ui._gumball_projected(handles, region, rv3d)
    gaps = sum(p is None for _h, pls in projected for pl in pls for p in pl)
    mice = [(rng.uniform(0, 1200), rng.uniform(0, 800)) for _ in range(1500)]
    # Aim a third of the cursors at the handles' own projected points so hits are common.
    pixels = [p for _h, pls in projected for pl in pls for p in pl if p is not None]
    mice += [(x + rng.uniform(-12, 12), y + rng.uniform(-12, 12))
             for x, y in rng.sample(pixels, min(len(pixels), 750))]
    mismatched = [m for m in mice
                  if python_choice(handles, region, rv3d, m) is not native_choice(handles, region, rv3d, m)]
    hits = sum(python_choice(handles, region, rv3d, m) is not None for m in mice)
    exact = all(
        dict((id(h), d) for h, d in ui._gumball_native_scored(handles, region, rv3d, m))[id(h)]
        == min((gumball.polyline_distance_2d(m, pl) for pl in pls), default=math.inf)
        for m in mice[:200] for h, pls in projected)
    check(not mismatched and hits > 50,
          f"{name}: {len(mice)} cursors, {hits} hits, {len(mismatched)} mismatches")
    check(exact, f"{name}: every handle's native distance equals the Python distance exactly")
    if name == "eye inside the widget":
        check(gaps > 0, f"{name}: the view really has points that do not project ({gaps})")

print("\n-- hand-made handles for the edge cases --")
# Orthographic: world x, y in [-100, 100] map to pixels [0, 200]; w = 1 - z/100, so
# a point at z >= 100 does not project.
rv3d = types.SimpleNamespace(perspective_matrix=Matrix(
    ((0.01, 0, 0, 0), (0, 0.01, 0, 0), (0, 0, 0.001, 0), (0, 0, -0.01, 1))))
region = types.SimpleNamespace(width=200, height=200, x=0, y=0)


def handle(kind, *hot):
    return gumball.Handle(kind, 0, list(hot), [list(pl) for pl in hot], (1, 1, 1, 1), kind)


def same(handles, mouse, want, label):
    py = python_choice(handles, region, rv3d, mouse)
    na = native_choice(handles, region, rv3d, mouse)
    check(py is want and na is want, f"{label} (python {getattr(py, 'label', py)}, "
                                     f"native {getattr(na, 'label', na)})")


# A None gap: the middle point is behind the camera; joining its neighbours makes a
# segment along y = 100 px that the cursor at (150, 103) is 3 px from.
gap = handle(gumball.MOVE, [(0, 0, 0), (25, 25, 200), (100, 0, 0)])
same([gap], (150.0, 103.0), gap, "a None gap is dropped and its neighbours joined")
empty = handle(gumball.ROTATE, [])
none = gumball.Handle(gumball.SCALE, 0, [], [], (1, 1, 1, 1), "no hot polylines")
single = handle(gumball.ORIGIN, [(0, 0, 0)])
line = handle(gumball.MOVE, [(-50, 0, 0), (50, 0, 0)])
same([empty, none, single, line], (100.0, 104.0), line,
     "an empty polyline, no polylines and a single point never win; the line does")
same([empty, single], (100.0, 100.0), None, "with only those, nothing is picked")
menu_tol = gumball.menu_pick_tolerance_px()
menu = handle(gumball.MENU, [(-5, 0, 0), (5, 0, 0)])
arrow = handle(gumball.MOVE, [(-5, -9, 0), (5, -9, 0)])
same([menu, arrow], (100.0, 100.0 + menu_tol + 0.5), None,
     f"the menu ball is not picked beyond its own {menu_tol} px")
same([menu, arrow], (100.0, 100.0 - 1.0), menu, "the menu ball is picked within it")
same([menu, arrow], (100.0, 100.0 - menu_tol - 0.5), arrow,
     "a cursor past the menu tolerance falls through to the arrow inside 11 px")
first = handle(gumball.MOVE, [(-50, 0, 0), (50, 0, 0)])
second = handle(gumball.SCALE, [(-50, 0, 0), (50, 0, 0)])
same([first, second], (100.0, 105.0), first, "an exact tie goes to the earlier handle")
same([second, first], (100.0, 105.0), second, "and follows the order, not the kind")
edge = handle(gumball.MOVE, [(-50, 0, 0), (50, 0, 0)])
same([edge], (100.0, 100.0 + gumball.PICK_TOLERANCE_PX), edge,
     "a distance exactly equal to the tolerance is a hit")

print("\n-- build_cached --")
rv3d, region, view_dir = perspective((300.0, -400.0, 250.0))
a = gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True)
check(gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True) is a,
      "identical inputs return the same handle list")
fresh = gumball.build((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True)
check([(h.kind, h.axis, h.hot, h.polylines) for h in a]
      == [(h.kind, h.axis, h.hot, h.polylines) for h in fresh], "and it equals a fresh build")
changed = {
    "origin": gumball.build_cached((1, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True),
    "size": gumball.build_cached((0, 0, 0), Plane(), 61.0, view_dir=view_dir, extrude=True),
    "view": gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=(0, 0, -1), extrude=True),
    "extrude": gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=False),
}
tilted = Plane()
tilted.xaxis = (0.0, 1.0, 0.0)
tilted.yaxis = (-1.0, 0.0, 0.0)
changed["plane"] = gumball.build_cached((0, 0, 0), tilted, 60.0, view_dir=view_dir, extrude=True)
saved = gumball.PROPORTIONS["menu_radius"]
gumball.PROPORTIONS["menu_radius"] = saved * 2
try:
    changed["proportions"] = gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir,
                                                  extrude=True)
finally:
    gumball.PROPORTIONS["menu_radius"] = saved
check(all(v is not a for v in changed.values()),
      f"every input is part of the key ({[k for k, v in changed.items() if v is a]} reused)")
check(len(gumball._build_cache) <= gumball._BUILD_CACHE_SIZE, "the cache stays bounded")

print("\n-- per mouse move, 15-handle widget (best of 5) --")
mice = [(rng.uniform(450, 750), rng.uniform(250, 550)) for _ in range(400)]


def best(fn):
    out = math.inf
    for _ in range(5):
        start = time.perf_counter()
        for m in mice:
            fn(m)
        out = min(out, (time.perf_counter() - start) / len(mice))
    return out * 1e6


def before(m):
    handles = gumball.build((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True)
    return python_choice(handles, region, rv3d, m)


def after(m):
    handles = gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True)
    return native_choice(handles, region, rv3d, m)


cached = gumball.build_cached((0, 0, 0), Plane(), 60.0, view_dir=view_dir, extrude=True)
t_before, t_after = best(before), best(after)
t_python_pick = best(lambda m: python_choice(cached, region, rv3d, m))
t_native_pick = best(lambda m: native_choice(cached, region, rv3d, m))
print(f"  [perf] build + project + pick: before {t_before:.1f} us, after {t_after:.1f} us")
print(f"  [perf] project + pick only (cached handles): python {t_python_pick:.1f} us, "
      f"native {t_native_pick:.1f} us")
check(t_native_pick <= 0.9 * t_python_pick, "the native projection and pick wins by at least 10%")
check(t_after <= 0.9 * t_before, "the whole hit test wins by at least 10%")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
sys.exit(1 if failed else 0)
