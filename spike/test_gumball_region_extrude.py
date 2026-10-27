"""Gumball extrusion treats selected planar profiles as exact regions.

Runs under Blender because this exercises the production UI transaction that gathers the
selected objects, builds the live extrusion candidate, and commits its display object.
"""
from __future__ import annotations

import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, kernel, ui  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f" ({detail})" if detail else ""))


def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    app_state.set_model(NarwhalModel())
    ui.invalidate_snap_cache()
    return app_state.ctx


def add_circle(ctx, center, radius):
    return ctx.add_geometry(bridge.make_circle(center, radius), "Circle")[1]


def extrusion_state(names, distance=5.0):
    return {
        "total": float(distance),
        "axis_dir": (0.0, 0.0, 1.0),
        "extrude": ("objects", list(names)),
        "ext_t": 0.0,
    }


print("-- nested selected curves become one hollow Gumball extrusion --")
ctx = reset()
outer = add_circle(ctx, (0.0, 0.0, 0.0), 10.0)
inner = add_circle(ctx, (0.0, 0.0, 0.0), 4.0)
ctx.select_by_names([outer, inner])
ctx.undo.clear()
st = extrusion_state([outer, inner])
ui._extrude_compute(st)
candidates = st.get("ext_cands") or []
check(len(candidates) == 1,
      "one candidate is built for the annular profile region", len(candidates))
result = candidates[0][2] if candidates else None
master = kernel.master_of(result) if result is not None else None
face_types = occt.face_types(master) if master is not None else {}
check(master is not None and face_types == {"CYLINDER": 2, "PLANE": 2},
      "the candidate has two cylindrical walls and two planar annular caps", face_types)
check(master is not None and occt.is_airtight(master),
      "the hollow candidate is airtight")
expected_volume = math.pi * (10.0 ** 2 - 4.0 ** 2) * 5.0
volume = occt.mass_properties(master, True)["volume"] if master is not None else None
check(volume is not None and abs(volume - expected_volume) < 1e-9,
      "the hollow candidate has exact annular volume", f"{volume} vs {expected_volume}")
recipe = kernel.recipe_of(result) if result is not None else None
check(recipe is not None and recipe.get("kind") == "prism"
      and recipe.get("a", {}).get("kind") == "planar_face"
      and len(recipe.get("a", {}).get("loops", ())) == 2,
      "the candidate keeps a prism recipe with outer and inner loops", recipe)
check(bool(st.get("ext_ghost")), "the live Gumball preview contains the hollow result edges")

ctx.begin_undo("Gumball extrude")
message = ui._extrude_commit(st)
ctx.end_undo()
solids = [fobj for fobj in ctx.model.file.Objects
          if getattr(fobj.Geometry, "IsSolid", False)]
check(len(solids) == 1 and outer in [f.Attributes.Name for f in ctx.model.file.Objects]
      and inner in [f.Attributes.Name for f in ctx.model.file.Objects],
      "commit adds one solid and retains both input profiles", message)
committed_master = kernel.master_of(solids[0].Geometry) if solids else None
check(committed_master is not None and occt.is_airtight(committed_master)
      and occt.face_types(committed_master) == {"CYLINDER": 2, "PLANE": 2},
      "the committed document object remains the exact hollow solid")

print("\n-- disjoint selected curves remain separate Gumball extrusions --")
ctx = reset()
left = add_circle(ctx, (-20.0, 0.0, 0.0), 3.0)
right = add_circle(ctx, (20.0, 0.0, 0.0), 5.0)
ctx.select_by_names([left, right])
st = extrusion_state([left, right], distance=7.0)
ui._extrude_compute(st, force=True)
disjoint = st.get("ext_cands") or []
check(len(disjoint) == 2,
      "two disjoint profile regions produce two exact candidates", len(disjoint))
check(all(kernel.master_of(row[2]) is not None
          and occt.is_airtight(kernel.master_of(row[2])) for row in disjoint),
      "every disjoint candidate is an airtight analytic master")

print("\n-- bug #105: live, non-forced preview errors stay inside the drag --")
ctx = reset()
single = add_circle(ctx, (0.0, 0.0, 0.0), 2.0)
st = extrusion_state([single], distance=3.0)
original_extrude = bridge.gumball_extrude
try:
    def broken_preview(*_args, **_kwargs):
        raise RuntimeError("synthetic live preview failure")

    bridge.gumball_extrude = broken_preview
    ui._extrude_compute(st)
finally:
    bridge.gumball_extrude = original_extrude
check(not (st.get("ext_cands") or [])
      and "synthetic live preview failure" in (st.get("ext_why") or ""),
      "unexpected live preview errors are reported without escaping or poisoning the modal drag")
check(abs(ui.GUMBALL_EXTRUDE_LIVE_INTERVAL - 1.0 / 30.0) < 1e-12,
      "Gumball extrusion owns a dedicated 30 Hz live-preview throttle")

# Whole-object scaling after display meshing must target the explicitly selected
# extrusion, preserve its profile, and finish one reversible UI transaction.
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from narwhal3d import commands, gumball


def curve_points(ctx, name):
    curve = ctx.geom_by_name(name)
    d = curve.Domain
    points = [curve.PointAt(d.T0 + (d.T1 - d.T0) * i / 32) for i in range(33)]
    return [(p.X, p.Y, p.Z) for p in points]


def display_points(name):
    bpy.context.view_layer.update()
    ob = bpy.data.objects[name]
    return [tuple(ob.matrix_world @ v.co) for v in ob.data.vertices]


def points_close(a, b, tolerance=2e-5):
    return len(a) == len(b) and all(abs(x - y) < tolerance
                                   for p, q in zip(a, b) for x, y in zip(p, q))


def display_matches_scaled_cylinder(name):
    # Redo rebuilds the ellipse's render mesh (536 vertices here), rather than
    # preserving the unscaled cylinder's sampling/order (420 or 296 vertices).
    points = display_points(name)
    if not points:
        return False
    bounds = [tuple(min(p[i] for p in points) for i in range(3)),
              tuple(max(p[i] for p in points) for i in range(3))]
    return (points_close(bounds, ((-6, -3, 0), (6, 3, 5)), 0.01)
            and all(-1e-5 <= z <= 5 + 1e-5
                    and (x / 6) ** 2 + (y / 3) ** 2 <= 1 + 1e-5
                    and (abs(z) < 1e-5 or abs(z - 5) < 1e-5
                         or abs((x / 6) ** 2 + (y / 3) ** 2 - 1) < 1e-5)
                    for x, y, z in points))


def exact_scaled(ctx, name, factor):
    geom = ctx.geom_by_name(name)
    master = bridge._occt_operand(geom)
    recipe = kernel.recipe_of(geom)
    expected = ((-3.0 * factor, -3.0, 0.0), (3.0 * factor, 3.0, 5.0))
    return (master is not None and recipe is not None
            and all(occt.is_airtight(shape)
                    and points_close(occt.tight_bbox(shape), expected, 1e-6)
                    and abs(occt.volume(shape) - math.pi * 45 * factor) < 1e-6
                    and occt.face_count(shape) == 3
                    for shape in (master, occt.from_recipe(recipe))))


def selected_extrusion(command=False):
    ctx = reset()
    ui.clear_subobject_selection()
    ui._selected_cps.clear()
    profile = add_circle(ctx, (0.0, 0.0, 0.0), 3.0)
    ctx.select_by_names([profile])
    ctx.begin_undo("Extrude fixture")
    if command:
        cmd = commands.ExtrudeCmd()
        cmd.begin(ctx)
        msg, done = cmd.feed("5", ctx)
        check(done, "ExtrudeCrv control completes", msg)
    else:
        st = extrusion_state([profile])
        ui._extrude_compute(st, force=True)
        ui._extrude_commit(st)
    ctx.end_undo()
    made = [f.Attributes.Name for f in ctx.model.file.Objects if f.Attributes.Name != profile]
    check(len(made) == 1 and ui.selection_names() == [profile],
          "extrusion retains profile selection; test explicitly reselects its result")
    ctx.select_by_names(made)
    ctx.undo.clear()
    return ctx, profile, made[0]


def scale_update(ctx):
    size = 10.0
    ref = size * gumball.PROPORTIONS["scale_at"]
    st = {"kind": gumball.SCALE, "mode": gumball.SCALE, "axis": 0,
          "origin": (0.0, 0.0, 0.0), "axis_dir": (-1.0, 0.0, 0.0),
          "axis_2d": ((0.0, 0.0), (-100.0, 0.0)), "axis_len": size,
          "mouse0": (-ref * 10.0, 0.0), "scale_at": ref,
          "accum": 1.0, "total": 0.0, "brep_part": None, "extrude": None}
    ctx.begin_undo("Gumball scale")
    ui._gumball_drag = st
    check(ui._gumball_update(-ref * 20.0, 0.0, shift_held=False),
          "real scale update creates a deferred preview")
    check(st.get("defer", {}).get("selected_names") == tuple(ui.selection_names()),
          "deferred target is the explicitly selected result")
    return st


for via_command in (False, True):
    print("\n-- displayed extrusion scale:", "ExtrudeCrv" if via_command else "Gumball", "--")
    ctx, profile, result_name = selected_extrusion(via_command)
    profile_before = curve_points(ctx, profile)
    display_before = display_points(result_name)
    st = scale_update(ctx)
    check(exact_scaled(ctx, result_name, 1), "preview leaves document geometry unchanged")
    message = ui._gumball_end()
    check(exact_scaled(ctx, result_name, 2),
          "selected displayed extrusion and replay are airtight 12 x 6 x 5", message)
    check("scaled" in (message or "") and ui._gumball_drag is None
          and not ctx.undo.recording and ctx.undo.undo_names() == ["Gumball scale"],
          "successful release closes one undo record")
    check(points_close(display_points(result_name),
                       [(2 * x, y, z) for x, y, z in display_before]),
          "display coordinates scale exactly once with the document")
    check(curve_points(ctx, profile) == profile_before,
          "the retained source profile is unchanged")
    ctx.undo_last()
    check(exact_scaled(ctx, result_name, 1) and ui.selection_names() == [result_name]
          and points_close(display_points(result_name), display_before)
          and curve_points(ctx, profile) == profile_before,
          "one Undo restores exact geometry, display and selection")
    ctx.redo_last()
    check(exact_scaled(ctx, result_name, 2) and display_matches_scaled_cylinder(result_name),
          "Redo restores exact geometry and retessellated display on the scaled cylinder")


print("\n-- failed scale commits must not report success --")
for error in (bridge.NonUniformTransformError("synthetic exact-scale refusal"),
              RuntimeError("synthetic unexpected scale failure")):
    ctx, profile, result_name = selected_extrusion()
    before_display = display_points(result_name)
    before_profile = curve_points(ctx, profile)
    before_history = len(app_state.history)
    st = scale_update(ctx)
    captured = StringIO()
    with patch.object(bridge, "transform_geometry", side_effect=error), redirect_stdout(captured):
        message = ui._gumball_end()
    logged = "\n".join(app_state.history[before_history:])
    check("scaled" not in (message or "").lower() and bool(st.get("transform_error"))
          and message == st["transform_error"],
          "failed release returns its error, not a success label", message)
    check("NameError" not in captured.getvalue() and "app_state" not in captured.getvalue()
          and str(error) in (logged if isinstance(error, bridge.NonUniformTransformError)
                             else message or ""),
          "actual refusal is logged or unexpected error returned without a logging exception",
          logged + captured.getvalue())
    check(exact_scaled(ctx, result_name, 1)
          and points_close(display_points(result_name), before_display)
          and curve_points(ctx, profile) == before_profile,
          "failed commit restores preview display and leaves document/profile unchanged")
    check(ui._gumball_drag is None and not ctx.undo.recording,
          "failed release clears drag and closes undo transaction")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for failure in failures:
        print("FAIL:", failure)
    raise SystemExit(1)
print("GUMBALL PROFILE REGION EXTRUSION PASSED")
