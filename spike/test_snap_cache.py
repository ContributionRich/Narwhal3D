"""The gumball-drag caches stay EXACT: no stale snap, curve or marker after any change.

Run:
  tools/blender-5.2.2-linux-x64/blender -b --factory-startup --python spike/test_snap_cache.py

Report: "gumball dragging items in these opened files is incredibly slow". MEASURED on
a large user project (5,702 objects): 14 s on the first move of every drag (the whole snap cache
was re-sampled after each release), 10.4 s for the first redraw after a release (every
curve re-sampled), ~100 ms per pane per frame walking the document for point markers.
The fix keeps those per OBJECT and re-reads only what changed. A stale cache is worse
than a slow one -- it snaps to, or draws, where an object USED to be -- so this suite
drives the REAL `ui.snap_at`, the real gumball release (`_gumball_end`), undo, a plain
transform, an in-record edit and a delete, and after each one compares against a
from-scratch answer (`bridge.snap_targets` + `osnap.best_on_screen`, fresh samples).
It also pins the speed-up itself: a gumball release must NOT rebuild the snap index,
and must re-sample only the curve that moved.

Headless Blender: the pane is a stand-in carrying a real `mathutils` perspective
matrix, which is all `snap_at` reads from it.
"""
import math
import os
import sys
import traceback

import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, commands, cplane, gumball, osnap  # noqa: E402
from narwhal3d import ui  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


W, H = 1200, 800


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def view_matrix(eye, target, fov=50.0, up=(0.0, 0.0, 1.0), ortho_half=None):
    f = _norm(_sub(target, eye))
    s = _norm(_cross(f, up))
    u = _cross(s, f)
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731
    view = Matrix(((s[0], s[1], s[2], -dot(s, eye)), (u[0], u[1], u[2], -dot(u, eye)),
                   (-f[0], -f[1], -f[2], dot(f, eye)), (0, 0, 0, 1)))
    if ortho_half is not None:
        hw, hh = ortho_half
        proj = Matrix(((1 / hw, 0, 0, 0), (0, 1 / hh, 0, 0), (0, 0, -0.001, 0), (0, 0, 0, 1)))
    else:
        t = 1.0 / math.tan(math.radians(fov) / 2.0)
        n_, f_ = 0.1, 1000.0
        proj = Matrix(((t * H / W, 0, 0, 0), (0, t, 0, 0),
                       (0, 0, (f_ + n_) / (n_ - f_), 2 * f_ * n_ / (n_ - f_)), (0, 0, -1, 0)))
    return proj @ view


class Region:
    width, height, x, y = W, H, 0, 0


class RV3D:
    def __init__(self, m):
        self.perspective_matrix = m


class Space:
    def __init__(self, m):
        self.region_3d = RV3D(m)


VIEWS = {"perspective": Space(view_matrix((30, -45, 30), (0, 0, 0))),
         "top": Space(view_matrix((0, 0, 80), (0, 0, 0), up=(0, 1, 0), ortho_half=(40, 27)))}
REGION = Region()


def project(space, p):
    import bpy_extras.view3d_utils as v3u
    q = v3u.location_3d_to_region_2d(REGION, space.region_3d, Vector(p))
    return None if q is None else (q.x, q.y)


def reference(space, cursor, exclude=()):
    """The from-scratch answer: every document object freshly sampled."""
    targets = [t for t in bridge.snap_targets(app_state.model) if t.name not in set(exclude)]
    s = osnap.best_on_screen(targets, cursor, ui._snap_settings.active(),
                             lambda p: project(space, p), ui._snap_settings.radius_px,
                             cplane.WORLD_TOP)
    return osnap.apply_projection(s, cplane.WORLD_TOP, ui._snap_settings.project)


def same(a, b):
    return (a is None and b is None) or (
        a is not None and b is not None and a.mode == b.mode
        and math.dist(a.point, b.point) < 1e-9)


def agree(cursors, label, exclude=()):
    bad, found = [], 0
    for vname, space in VIEWS.items():
        for c in cursors:
            got = ui.snap_at(None, space, REGION, c, exclude_names=exclude)
            want = reference(space, c, exclude)
            found += want is not None
            if not same(got, want):
                bad.append((vname, c, got, want))
    check(not bad, f"{label}: snap_at == a from-scratch search ({found} snaps)",
          repr(bad[:2]))
    return found


def snap_point(c, view="top"):
    s = ui.snap_at(None, VIEWS[view], REGION, c)
    return None if s is None else (s.mode, tuple(round(v, 6) for v in s.point))


def doc_geom(name):
    for f in app_state.model.file.Objects:
        if (f.Attributes.Name or "") == name:
            return f.Geometry
    return None


def fresh_layer_items():
    """`_layer_curve_items` recomputed with an EMPTY cache -- the ground truth."""
    saved = dict(ui._layer_curve_cache)
    ui._layer_curve_cache.update(key=None, items=[], doc=None, stale=set(), verify=False,
                                 pts={})
    try:
        return {n: p for _c, p, n in ui._layer_curve_items()}
    finally:
        ui._layer_curve_cache.clear()
        ui._layer_curve_cache.update(saved)


def layer_items():
    return {n: p for _c, p, n in ui._layer_curve_items()}


def select_only(*names):
    for o in bpy.data.objects:
        o.select_set(o.name in names)


try:
    app_state.set_model(NarwhalModel())
    app_state.active_command = None
    ctx = app_state.ctx
    red = app_state.model.add_layer("Red", (255, 0, 0))

    def add(geom, base, layer=None):
        ctx.begin_undo(f"add {base}")
        _id, name = ctx.add_geometry(geom, base, layer)
        ctx.end_undo()
        return name

    L1 = add(bridge.make_line((0, 0, 0), (10, 0, 0)), "Line", red)
    L2 = add(bridge.make_line((5, -5, 0), (5, 5, 0)), "Line", red)
    C1 = add(bridge.make_circle((-15, 8, 0), 4.0), "Circle", red)
    P1 = add(bridge.make_point((-5, 3, 2)), "Point")
    PL = add(bridge.make_polyline([(0, 12, 0), (6, 12, 0), (6, 18, 0), (0, 18, 0)],
                                  degree=1, closed=True), "Polyline", red)
    B1 = add(bridge.make_box((12, 10, 0), (18, 14, 0), 3.0, cplane.WORLD_TOP), "Box")
    names = [L1, L2, C1, P1, PL, B1]
    check(len(set(names)) == 6 and all(names), "six objects made", repr(names))

    def all_cursors():
        out = []
        for t in bridge.snap_targets(app_state.model):
            out.extend(t.vertices)
            if t.center is not None:
                out.append(t.center)
        return [(p[0] + 0.15, p[1] - 0.1, p[2]) for p in out] + [(30, 30, 0), (2, 2, 2)]

    # ---------------------------------------------------------------- equivalence
    found = agree(all_cursors(), "fresh document")
    check(found > 20, "the comparison is not vacuous", f"{found} snaps")
    check(snap_point((10.1, 0.1, 0)) == ("End", (10.0, 0.0, 0.0)), "Line1 End at (10,0,0)",
          repr(snap_point((10.1, 0.1, 0))))
    lc0 = layer_items()
    check(lc0 == fresh_layer_items() and set(lc0) >= {L1, L2, C1, PL},
          "layer-colour curves are drawn for every red curve", repr(sorted(lc0)))

    # ------------------------------------------------- the gumball release (#31 path)
    builds = ui._snap_index.get("builds", 0)
    resampled = ui._layer_curve_cache["resampled"]
    select_only(L1)
    ob = bpy.data.objects[L1]
    ctx.begin_undo("Gumball Move")
    ui._gumball_drag = {"kind": gumball.MOVE, "mode": gumball.MOVE, "axis": 0,
                        "total": 3.0,
                        "defer": {"start": {L1: ob.matrix_world.copy()},
                                  "acc": Matrix.Translation((3.0, 0.0, 0.0))}}
    msg = ui._gumball_end()
    g = doc_geom(L1)
    moved_to = (g.PointAtEnd.X, g.PointAtEnd.Y, g.PointAtEnd.Z)
    check(math.dist(moved_to, (13, 0, 0)) < 1e-9, "the release moved the document line",
          f"{msg!r} end={moved_to}")
    check(snap_point((13.1, 0.1, 0)) == ("End", (13.0, 0.0, 0.0)),
          "after the release the MOVED line snaps at its new end",
          repr(snap_point((13.1, 0.1, 0))))
    check(snap_point((10.1, 0.1, 0)) is None,
          "...and NOT at its old end (no stale target)", repr(snap_point((10.1, 0.1, 0))))
    agree(all_cursors() + [(10.1, 0.1, 0)], "after a gumball release")
    check(ui._snap_index.get("builds", 0) == builds,
          "the release did NOT rebuild the snap index (only the moved object was re-read)",
          f"builds {builds} -> {ui._snap_index.get('builds', 0)}")
    lc1 = layer_items()
    check(lc1 == fresh_layer_items(), "layer curves after the release == fresh ones")
    check(ui._layer_curve_cache["resampled"] - resampled == 1,
          "the release re-sampled exactly ONE curve (the moved one)",
          f"{ui._layer_curve_cache['resampled'] - resampled}")

    # ----------------------------------------------- undo: a change nobody reported
    resampled = ui._layer_curve_cache["resampled"]
    ctx.undo_last()
    check(snap_point((10.1, 0.1, 0)) == ("End", (10.0, 0.0, 0.0))
          and snap_point((13.1, 0.1, 0)) is None,
          "after UNDO the line snaps where it is again, not where it was",
          f"{snap_point((10.1, 0.1, 0))} / {snap_point((13.1, 0.1, 0))}")
    agree(all_cursors(), "after undo")
    check(layer_items() == fresh_layer_items(), "layer curves after undo == fresh ones")
    check(ui._layer_curve_cache["resampled"] - resampled == 1,
          "undo re-sampled only the curve whose content changed (fingerprints)",
          f"{ui._layer_curve_cache['resampled'] - resampled}")
    ctx.redo_last()
    check(snap_point((13.1, 0.1, 0)) == ("End", (13.0, 0.0, 0.0)), "after REDO: new end",
          repr(snap_point((13.1, 0.1, 0))))
    check(layer_items() == fresh_layer_items(), "layer curves after redo == fresh ones")

    # ------------------------------------- a plain transform (Move's path), unreported
    select_only(C1)
    commands.apply_transform(ctx, bridge.translation_xform((0.0, -6.0, 0.0)))
    check(snap_point((-15.1, 2.1, 0)) == ("Cen", (-15.0, 2.0, 0.0)),
          "a transformed circle snaps at its new centre", repr(snap_point((-15.1, 2.1, 0))))
    agree(all_cursors(), "after a plain transform")
    check(layer_items() == fresh_layer_items(), "layer curves after a transform == fresh")

    # ---------------------------------- an IN-RECORD edit, reported by invalidation
    ctx.begin_undo("in-record edit")
    ctx.note_change("in-record edit")
    key0 = ui._doc_key()
    # warm the point-object cache at THIS key, so a missed invalidation would show
    warm = dict(ui._document_point_locations())
    check(math.dist(warm.get(P1, (0, 0, 0)), (-5, 3, 2)) < 1e-9, "(point cache warmed)",
          repr(warm.get(P1)))
    bridge.transform_geometry(doc_geom(L2), bridge.translation_xform((0.0, 0.0, 4.0)))
    gp = doc_geom(P1)
    bridge.transform_geometry(gp, bridge.translation_xform((1.0, 0.0, 0.0)))
    check(ui._doc_key() == key0, "(an in-record edit does not move the document key)")
    ui.invalidate_snap_cache([L2])
    check(snap_point((5.1, 5.1, 4.0), "perspective") == ("End", (5.0, 5.0, 4.0)),
          "a NAMED invalidation re-reads that object", repr(snap_point((5.1, 5.1, 4), "perspective")))
    ui.invalidate_snap_cache()
    locs = dict(ui._document_point_locations())
    check(math.dist(locs.get(P1, (0, 0, 0)), (-4, 3, 2)) < 1e-9,
          "a full invalidation refreshes the point-object locations", repr(locs.get(P1)))
    ctx.end_undo()
    agree(all_cursors(), "after an in-record edit")

    # ------------------------------------------------------- delete and add
    select_only(L2)
    app_state.feed_line("Delete")
    check(snap_point((5.1, 5.1, 4.0), "perspective") is None,
          "a DELETED line is no longer snappable", repr(snap_point((5.1, 5.1, 4), "perspective")))
    check(L2 not in layer_items(), "...nor drawn as a layer curve")
    L3 = add(bridge.make_line((20, -10, 0), (25, -10, 0)), "Line", red)
    check(snap_point((25.1, -10.1, 0)) == ("End", (25.0, -10.0, 0.0)),
          "a NEW line is snappable at once", repr(snap_point((25.1, -10.1, 0))))
    check(layer_items() == fresh_layer_items() and L3 in layer_items(),
          "...and drawn as a layer curve")
    agree(all_cursors(), "after delete + add")

    # ----------------------------------- the moved point object and control polygons
    select_only(P1)
    commands.apply_transform(ctx, bridge.translation_xform((0.0, 2.0, 0.0)))
    locs = dict(ui._document_point_locations())
    check(math.dist(locs.get(P1, (0, 0, 0)), (-4, 5, 2)) < 1e-9,
          "a moved point object's marker follows it", repr(locs.get(P1)))
    select_only(PL)
    poly = ui._control_polygons().get(PL, [])
    check(len(poly) >= 5 and poly[0] == poly[-1], "a CLOSED polyline's polygon closes",
          repr(poly))
    fid = next(f.Attributes.Id for f in app_state.model.file.Objects
               if f.Attributes.Name == PL)
    ctx.begin_undo("open it")
    ctx.note_change("open it")
    app_state.model.replace_geometry(
        fid, bridge.make_polyline([(0, 12, 0), (6, 12, 0), (6, 18, 0), (0, 18, 0)],
                                  degree=1, closed=False))
    ctx.end_undo()
    poly = ui._control_polygons().get(PL, [])
    check(len(poly) == 4 and poly[0] != poly[-1],
          "...and once it is replaced by an OPEN one, the polygon no longer closes",
          repr(poly))
    agree(all_cursors(), "at the end")
except Exception:  # noqa: BLE001
    results.append((False, "suite raised", traceback.format_exc()))

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok or not detail else f"\n       {detail}"))
print("=" * 60)
if failed:
    print(f"SNAP CACHE TESTS FAILED: {len(failed)} of {len(results)}")
    sys.exit(1)
print(f"SNAP CACHE TESTS PASSED ({len(results)} checks)")
