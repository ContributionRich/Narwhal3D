"""Solid-part picking (hover / click on faces, edges, vertices) is culled, not changed.

Run:
  tools/blender-5.2.2-linux-x64/blender -b --factory-startup --python-exit-code 1 \
      --python spike/test_brep_pick_culling.py

`ui.pick_brep_part` used to project EVERY face triangle, edge point and vertex of EVERY solid on
each mouse move (about 0.2 s per hover on a 44-object document). It now projects only the items
whose cached screen rectangle can reach the cursor, through the native rectangle query. This suite
checks, against a copy of the old logic on synthetic solids:

  * EXACT: the same (name, kind, id) for every cursor, both shadings, faces-only and not, including
    cursors placed exactly at the tolerance boundary of projected edges and vertices;
  * BUDGET: projections per pick stay a small fraction of all projectable points (deterministic);
    and the culled pick is measurably faster than the old scan;
  * FRESH: after a solid's parts change, the answer follows the new geometry.

Requires the native selection core (the same requirement as spike/test_pick_candidates.py).
"""
import math
import os
import random
import sys
import time
import traceback

import bpy  # noqa: F401  (Blender is the host of this suite)
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import native_selection, subobject, ui  # noqa: E402

results = []

W, H = 800, 600
TOLERANCE = 8.0
#: Culled projections per pick must stay under this share of all projectable points.
PROJECTION_SHARE_BUDGET = 0.25
#: The culled pick must take less than this share of the old scan's time.
TIME_SHARE_BUDGET = 0.5


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


class Region:
    width, height, x, y = W, H, 0, 0


class RV3D:
    def __init__(self, view, projection):
        self.view_matrix = view
        self.perspective_matrix = projection @ view


class Space:
    def __init__(self, rv3d, shading):
        self.region_3d = rv3d
        self.shading = type("Shading", (), {"type": shading})()


def view_matrix(eye, target, fov=50.0, up=(0.0, 0.0, 1.0)):
    def norm(v):
        n = math.sqrt(sum(c * c for c in v))
        return tuple(c / n for c in v)

    def sub(a, b):
        return tuple(x - y for x, y in zip(a, b))

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    f = norm(sub(target, eye))
    s = norm(cross(f, up))
    u = cross(s, f)

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    return Matrix(((s[0], s[1], s[2], -dot(s, eye)), (u[0], u[1], u[2], -dot(u, eye)),
                   (-f[0], -f[1], -f[2], dot(f, eye)), (0, 0, 0, 1)))


def projection():
    t = 1.0 / math.tan(math.radians(50.0) / 2.0)
    n, f = 0.1, 1000.0
    return Matrix(((t * H / W, 0, 0, 0), (0, t, 0, 0),
                   (0, 0, (f + n) / (n - f), 2 * f * n / (n - f)), (0, 0, -1, 0)))


def scene(seed, solids=8, faces=20, edges=25, vertices=10):
    """Deterministic synthetic solids: triangles, polylines and vertices in world space."""
    rng = random.Random(seed)
    parts_by_name = {}
    for index in range(solids):
        cx, cy, cz = rng.uniform(-7, 7), rng.uniform(-7, 7), rng.uniform(-3, 3)

        def point(spread):
            return (cx + rng.uniform(-spread, spread), cy + rng.uniform(-spread, spread),
                    cz + rng.uniform(-spread, spread))

        face_list = []
        for fid in range(1, faces + 1):
            triangles = []
            for _ in range(2):
                corner = point(2.5)
                triangles.append(tuple(
                    tuple(c + rng.uniform(-0.6, 0.6) for c in corner) for _ in range(3)))
            face_list.append((fid, triangles))
        edge_list = [(eid, [point(3.0) for _ in range(rng.randint(2, 5))])
                     for eid in range(1, edges + 1)]
        vertex_list = [(vid, point(3.0)) for vid in range(1, vertices + 1)]
        parts_by_name[f"Solid{index}"] = {"faces": face_list, "edges": edge_list,
                                          "vertices": vertex_list}
    return parts_by_name


def reference(region, rv3d, space, rx, ry, parts_by_name, tolerance, faces_only):
    """The OLD `pick_brep_part` body, verbatim in logic (every item projected)."""
    faces, edges, verts = [], [], []
    for name, parts in parts_by_name.items():
        for fid, tris in parts.get("faces", ()):
            pt = []
            for tri in tris:
                c = [ui._project_with_depth(region, rv3d, p) for p in tri]
                if None not in c:
                    pt.append(tuple(c))
            faces.append(((name, fid), pt))
        if faces_only:
            continue
        for eid, pl in parts.get("edges", ()):
            edges.append(((name, eid), [ui._project_with_depth(region, rv3d, p) for p in pl]))
        for vid, p in parts.get("vertices", ()):
            verts.append(((name, vid), ui._project_with_depth(region, rv3d, p)))
    occlude = getattr(getattr(space, "shading", None), "type", "SOLID") != "WIREFRAME"
    got = subobject.pick_part(faces, edges, verts, (rx, ry), tolerance, occlude=occlude)
    if got is None:
        return None
    kind, (name, sid) = got
    return (name, kind, sid)


def cursors(rv3d, parts_by_name, rng):
    """A grid, random points, and points exactly at +/- tolerance of projected edges/vertices."""
    out = [(x, y) for x in range(12, W, 37) for y in range(12, H, 31)]
    out += [(rng.uniform(0, W), rng.uniform(0, H)) for _ in range(120)]
    region = Region()
    for parts in list(parts_by_name.values())[:3]:
        for _eid, pl in parts["edges"][:6]:
            a, b = pl[0], pl[-1]
            for t in (0.25, 0.75):
                p = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
                q = ui._project_with_depth(region, rv3d, p)
                if q is None:
                    continue
                for offset in (TOLERANCE - 0.5, TOLERANCE + 0.5, 0.0):
                    out.append((q[0] + offset, q[1]))
                    out.append((q[0], q[1] - offset))
        for _vid, p in parts["vertices"][:3]:
            q = ui._project_with_depth(region, rv3d, p)
            if q is not None:
                out.append((q[0] + TOLERANCE - 0.5, q[1]))
    return [(int(round(x)), int(round(y))) for x, y in out]


def total_projectable(parts_by_name):
    total = 0
    for parts in parts_by_name.values():
        total += sum(3 * len(tris) for _fid, tris in parts["faces"])
        total += sum(len(pl) for _eid, pl in parts["edges"])
        total += len(parts["vertices"])
    return total


def run_scene(label, rv3d, parts_by_name, rng):
    region = Region()
    real_brep_parts = ui._brep_parts_all
    ui._brep_parts_all = lambda: parts_by_name
    counter = {"n": 0}
    real_project = ui._project_with_depth

    def counted(*args, **kwargs):
        counter["n"] += 1
        return real_project(*args, **kwargs)
    ui._project_with_depth = counted
    try:
        mismatches, culled_projections, old_time, new_time, picks = [], 0, 0.0, 0.0, 0
        total = total_projectable(parts_by_name)
        for shading in ("SOLID", "WIREFRAME"):
            space = Space(rv3d, shading)
            for faces_only in (False, True):
                for rx, ry in cursors(rv3d, parts_by_name, rng):
                    started = time.perf_counter()
                    expected = reference(region, rv3d, space, rx, ry, parts_by_name,
                                         TOLERANCE, faces_only)
                    old_time += time.perf_counter() - started
                    counter["n"] = 0
                    started = time.perf_counter()
                    got = ui.pick_brep_part(None, space, region, rx, ry, TOLERANCE, faces_only)
                    new_time += time.perf_counter() - started
                    culled_projections += counter["n"]
                    picks += 1
                    if got != expected and len(mismatches) < 5:
                        mismatches.append((shading, faces_only, rx, ry, expected, got))
        share = culled_projections / max(1, picks * total)
        check(not mismatches, f"{label}: culled pick == old scan on every cursor",
              repr(mismatches))
        check(native_selection.available() and share <= PROJECTION_SHARE_BUDGET,
              f"{label}: projections per pick within budget",
              f"{share:.3f} of {total} projectable points per pick (budget {PROJECTION_SHARE_BUDGET})")
        check(new_time <= TIME_SHARE_BUDGET * old_time + 0.01,
              f"{label}: culled pick is faster than the old scan",
              f"new {new_time * 1e3:.1f} ms vs old {old_time * 1e3:.1f} ms over {picks} picks")
        print(f"[brep-pick] {label}: {picks} picks, {share:.3f} projections/pick share, "
              f"old {old_time * 1e3:.0f} ms, new {new_time * 1e3:.0f} ms", flush=True)
    finally:
        ui._brep_parts_all = real_brep_parts
        ui._project_with_depth = real_project


try:
    rng = random.Random(11)
    view = view_matrix((30, -45, 30), (0, 0, 0))
    rv3d = RV3D(view, projection())
    parts = scene(7)
    run_scene("perspective scene A", rv3d, parts, rng)

    # A changed solid must be picked from its NEW geometry (cache keyed by the parts object).
    shifted = dict(parts)
    name = "Solid3"
    old = parts[name]
    shift = lambda p: (p[0] + 2.5, p[1], p[2])  # noqa: E731
    shifted[name] = {
        "faces": [(fid, [tuple(shift(p) for p in t) for t in tris])
                  for fid, tris in old["faces"]],
        "edges": [(eid, [shift(p) for p in pl]) for eid, pl in old["edges"]],
        "vertices": [(vid, shift(p)) for vid, p in old["vertices"]],
    }
    ui._brep_parts_all = lambda: parts
    before = [ui.pick_brep_part(None, Space(rv3d, "WIREFRAME"), Region(), x, y, TOLERANCE)
              for x, y in cursors(rv3d, parts, rng)[:200]]
    ui._brep_parts_all = lambda: shifted
    fresh = [ui.pick_brep_part(None, Space(rv3d, "WIREFRAME"), Region(), x, y, TOLERANCE)
             for x, y in cursors(rv3d, shifted, rng)[:200]]
    expected_fresh = [reference(Region(), rv3d, Space(rv3d, "WIREFRAME"), x, y, shifted,
                                TOLERANCE, False)
                      for x, y in cursors(rv3d, shifted, rng)[:200]]
    check(fresh == expected_fresh and before != fresh,
          "a changed solid is picked from its new geometry",
          f"{sum(a != b for a, b in zip(before, fresh))} answers changed")
    ui._brep_parts_all = lambda: parts
    run_scene("perspective scene B (other view)", RV3D(view_matrix((-25, 20, 12), (1, 0, 0)),
                                                       projection()),
              scene(19, solids=6, faces=30, edges=40, vertices=14), random.Random(5))
except BaseException:
    results.append((False, "suite crashed", traceback.format_exc()))

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(("PASS " if ok else "FAIL ") + label + (("  -- " + detail) if (detail and not ok) else ""))
print(f"brep_pick_culling: {len(results) - len(failed)}/{len(results)} passed")
if failed:
    sys.exit(1)
