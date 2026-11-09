"""A MIXED curve selection extrudes: closed coplanar profiles form exact regions, and every
open curve is extruded on its own, with one command or one Gumball commit.

Report #131: a selection of two closed coplanar curves and one open curve was refused
whole by PlanarSrf ("needs CLOSED curves"), in both ExtrudeCrv and the Gumball extrude.
The open curve's end gap is inside the document tolerance but is NOT closed, and nothing in
the extrude path closes it: the surface it makes is the honest result. The fixtures here are
synthetic; the reported document is private.

Refusals still refuse by name, and a refused set builds nothing.

Runs under Blender because the Gumball half drives the production UI transaction.
"""
from __future__ import annotations

import math
import os
import sys
from contextlib import redirect_stdout
from io import StringIO

import bpy
import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, commands, extrude_preview, kernel, ui  # noqa: E402
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


def doc_names(ctx):
    return [o.Attributes.Name for o in ctx.model.file.Objects]


def near_closed_open_curve(x0):
    """Open, its two ends 1e-7 apart: inside the model tolerance, still not closed."""
    pts = [(x0, 0.0, 0.0), (x0 + 5.0, 0.0, 0.0), (x0 + 5.0, 5.0, 0.0),
           (x0, 5.0, 0.0), (x0 + 1e-7, 0.0, 0.0)]
    curve = r3.PolylineCurve([r3.Point3d(*p) for p in pts])
    return curve


def ring_profiles(ctx, rotate=False):
    """Two closed coplanar concentric circles, optionally turned into the YZ plane."""
    outer = bridge.make_circle((0.0, 0.0, 0.0), 10.0)
    hole = bridge.make_circle((0.0, 0.0, 0.0), 4.0)
    if rotate:
        turn = r3.Transform.Rotation(math.pi / 2, r3.Vector3d(0, 1, 0), r3.Point3d(0, 0, 0))
        outer = bridge.transform_geometry(outer, turn)
        hole = bridge.transform_geometry(hole, turn)
    return ctx.add_geometry(outer, "Circle")[1], ctx.add_geometry(hole, "Circle")[1]


def new_objects(ctx, before):
    return [o for o in ctx.model.file.Objects if o.Attributes.Name not in before]


def ring_volume():
    return math.pi * (10.0 ** 2 - 4.0 ** 2) * 5.0


def is_exact_ring(geom):
    master = kernel.master_of(geom)
    return (master is not None and occt.face_types(master) == {"CYLINDER": 2, "PLANE": 2}
            and occt.is_airtight(master)
            and abs(occt.mass_properties(master, True)["volume"] - ring_volume()) < 1e-9)


def is_open_surface(geom):
    master = kernel.master_of(geom)
    return master is not None and not occt.is_airtight(master)


def extrude_with_command(ctx, selected, distance):
    ctx.select_by_names(selected)
    cmd = type(commands.COMMANDS["extrudecrv"])()
    msg, done = cmd.begin(ctx)
    msg, done = cmd.feed(str(distance), ctx)
    return msg


def extrusion_state(names, distance=5.0):
    return {
        "total": float(distance),
        "axis_dir": (0.0, 0.0, 1.0),
        "extrude": ("objects", list(names)),
        "ext_t": 0.0,
    }


print("-- ExtrudeCrv: two closed profiles + a near-closed open curve, one command --")
ctx = reset()
outer, hole = ring_profiles(ctx)
opn = ctx.add_geometry(near_closed_open_curve(30.0), "Open")[1]
check(not ctx.geom_by_name(opn).IsClosed,
      "precondition: the open curve is NOT closed (end gap 1e-7, inside tolerance)")
before = set(doc_names(ctx))
msg = extrude_with_command(ctx, [outer, hole, opn], 5.0)
made = new_objects(ctx, before)
check(len(made) == 2, f"both parts are built: the ring and the open surface ({msg!r})",
      len(made))
ring = [o.Geometry for o in made if is_exact_ring(o.Geometry)]
surf = [o.Geometry for o in made if is_open_surface(o.Geometry)]
check(len(ring) == 1, "the closed pair is ONE exact airtight ring solid "
      "{CYLINDER:2, PLANE:2}, volume pi(100-16)5")
check(len(surf) == 1, "the open curve is an exact SURFACE, not a solid and not refused")
check(all(n in doc_names(ctx) for n in (outer, hole, opn)),
      "the input profiles stay in the document")

print("\n-- ExtrudeCrv: the open curve first, closed profiles in a turned plane --")
ctx = reset()
opn = ctx.add_geometry(near_closed_open_curve(30.0), "Open")[1]
outer, hole = ring_profiles(ctx, rotate=True)
# The command takes its set in DOCUMENT order, so the open curve must be created first.
check(doc_names(ctx)[0] == opn, "precondition: the open curve leads the selection")
before = set(doc_names(ctx))
msg = extrude_with_command(ctx, [opn, outer, hole], 5.0)
made = new_objects(ctx, before)
ring = [o.Geometry for o in made if is_exact_ring(o.Geometry)]
check(len(made) == 2 and len(ring) == 1,
      f"open-first selection still builds the ring and the surface ({msg!r})", len(made))
if ring:
    bb = ring[0].GetBoundingBox()
    check(abs((bb.Max.X - bb.Min.X) - 5.0) < 1e-6 and (bb.Max.Z - bb.Min.Z) > 19.0,
          "the ring is extruded 5 along the CLOSED profiles' normal (X), not CPlane Z",
          f"x extent {bb.Max.X - bb.Min.X}, z extent {bb.Max.Z - bb.Min.Z}")

print("\n-- Gumball extrude: the same mixed set, preview then one commit --")
ctx = reset()
outer, hole = ring_profiles(ctx)
opn = ctx.add_geometry(near_closed_open_curve(30.0), "Open")[1]
ctx.select_by_names([outer, hole, opn])
st = extrusion_state([opn, outer, hole], distance=5.0)
captured = StringIO()
with redirect_stdout(captured):
    ui._extrude_compute(st, force=True)
cands = st.get("ext_cands") or []
check(len(cands) == 2 and st.get("ext_why") is None,
      f"the live preview builds both candidates, with no refusal ({st.get('ext_why')!r})",
      len(cands))
check("preview failed" not in captured.getvalue(),
      "no 'Gumball profile extrusion preview failed' line is logged")
check(any(is_exact_ring(c[2]) for c in cands) and any(is_open_surface(c[2]) for c in cands),
      "the candidates are the exact ring and the open surface")
check(bool(st.get("ext_ghost")), "the live preview contains result edges")
guide = extrude_preview.prepare([(ctx.geom_by_name(n), None) for n in (opn, outer, hole)])
check(bool(guide), "the live drag guide accepts the mixed set (open curve and closed loops)")

before = set(doc_names(ctx))
ctx.begin_undo("Gumball extrude")
message = ui._extrude_commit(st)
ctx.end_undo()
made = new_objects(ctx, before)
check(message is not None and message.startswith("Gumball: extruded 5 ->")
      and len(made) == 2,
      f"commit adds both parts in one transaction ({message!r})", len(made))
check(all(n in doc_names(ctx) for n in (outer, hole, opn)),
      "commit keeps the input profiles, as the closed-only route does")

print("\n-- refusals still refuse by name, and build nothing --")
ctx = reset()
outer, hole = ring_profiles(ctx)
twisted = r3.PolylineCurve([r3.Point3d(*p) for p in
                            [(0, 0, 0), (10, 0, 0), (10, 10, 5), (0, 10, 0), (0, 0, 0)]])
bent = ctx.add_geometry(twisted, "Bent")[1]
before = set(doc_names(ctx))
msg = extrude_with_command(ctx, [outer, bent], 5.0)
check(not new_objects(ctx, before) and "planar" in (msg or ""),
      f"a closed NON-planar profile is refused by name, nothing built ({msg!r})")

ctx = reset()
outer, hole = ring_profiles(ctx)
raised = ctx.add_geometry(bridge.make_circle((0.0, 0.0, 2.0), 4.0), "Raised")[1]
before = set(doc_names(ctx))
msg = extrude_with_command(ctx, [outer, raised], 5.0)
check(not new_objects(ctx, before) and "not all in the same plane" in (msg or ""),
      f"noncoplanar closed profiles are still refused, nothing built ({msg!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILED:")
    for label in failures:
        print(f"  - {label}")
    sys.exit(1)
print("EXTRUDE MIXED PROFILES PASSED")
