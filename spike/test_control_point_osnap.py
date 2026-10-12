"""Visible curve control points participate in Point Osnap during commands."""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, commands, cplane, osnap, ui  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

checks = []


def check(condition, message):
    ok = bool(condition)
    checks.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {message}")


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
app_state.set_model(NarwhalModel())
ctx = app_state.ctx

curve = bridge.make_polyline(
    [(0.0, 0.0, 0.0), (2.0, 3.0, 0.0), (5.0, -1.0, 0.0),
     (8.0, 2.0, 0.0), (11.0, 0.0, 0.0)],
    degree=3,
)
_oid, name = ctx.add_geometry(curve, "ControlCurve")
control_points = bridge.control_points(curve)
check(len(control_points) == 5, "fixture is a five-control-point degree-3 curve")

ctx.select_by_names([name])
app_state.feed_line("Move")
check(isinstance(app_state.active_command, commands.MoveCmd),
      "Move starts with the curve preselected")
check(name in ui.effective_points_on(),
      "the selected curve's control points are visibly enabled")

targets = ui._snap_targets()
point_targets = [target for target in targets
                 if target.is_point and target.name == name]
check(len(point_targets) == len(control_points),
      "every visible control point is a Point-Osnap target")

wanted = control_points[2]
candidate = osnap.best(
    targets,
    (wanted[0] + 0.02, wanted[1] - 0.01, wanted[2]),
    (osnap.POINT,),
    cplane.WORLD_TOP,
    radius=0.1,
)
check(candidate is not None and candidate.mode == osnap.POINT
      and candidate.target.name == name
      and all(abs(a - b) < 1e-12 for a, b in zip(candidate.point, wanted)),
      "Move's base point can snap exactly to a selected curve control point")

cached_key = ui._control_point_snap_cache["key"]
cached_groups = ui._control_point_snap_cache["by_name"]
real_cp_rows = ui._cp_rows
ui._cp_rows = lambda: (_ for _ in ()).throw(AssertionError("control points walked again"))
try:
    reused = ui._control_point_snap_targets({name})
finally:
    ui._cp_rows = real_cp_rows
check(ui._control_point_snap_cache["key"] == cached_key
      and ui._control_point_snap_cache["by_name"] is cached_groups
      and len(reused) == len(control_points),
      "unchanged hover reuses the grouped control-point snap cache")

excluded = ui._snap_targets(exclude_names=(name,))
check(not [target for target in excluded if target.is_point and target.name == name],
      "explicit Gumball exclusion still removes the dragged curve's control points")

app_state.cancel()
ctx.select_by_names([], already_expanded=True)
hidden = ui._snap_targets()
check(name not in ui.effective_points_on()
      and not [target for target in hidden if target.is_point and target.name == name],
      "deselecting the curve hides its control points and their snap targets")

ui.set_points_on([name], True)
persistent = ui._snap_targets()
check(name in ui.effective_points_on()
      and len([target for target in persistent
               if target.is_point and target.name == name]) == len(control_points),
      "PointsOn keeps control-point Point snaps available without object selection")
ui.clear_points_on()

if not all(checks):
    raise SystemExit(1)
print(f"PASS: {sum(checks)}/{len(checks)} control-point Osnap checks")
