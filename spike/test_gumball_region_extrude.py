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

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for failure in failures:
        print("FAIL:", failure)
    raise SystemExit(1)
print("GUMBALL PROFILE REGION EXTRUSION PASSED")
