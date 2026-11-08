"""Measured command behaviour (research_all_13 notes), one section per command.

Each section cites its note in docs/measured/research_all_13/notes/ and checks the
measured prompts, defaults and results against the real command classes, headless.

Run: tools/blender-patched/5.2/python/bin/python3.13 spike/test_research13_commands.py
"""
import importlib.util
import math
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cplane = _load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(("  ok   " if ok else "  FAIL ") + label)


class Sel:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.added = []
        self.selected = []
        self.plane = plane

    def add(self, geom, name):
        self.model.add_object(geom, name, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added.append(geom)
        name = f"{base_name}{len(self.added)}"
        self.model.add_object(geom, name, 0)
        return name, name

    def geom_by_name(self, name):
        for f in self.model.file.Objects:
            if (f.Attributes.Name or "").lower() == (name or "").lower():
                return f.Geometry
        return None

    def selected_objects(self):
        return [Sel(n) for n in self.selected]

    def object_by_name(self, name):
        return None

    def select_by_names(self, names):
        self.selected = list(names)
        return self.selected

    def active_cplane(self):
        return self.plane

    def note_change(self, label=None):
        pass


def drive(key, lines, ctx, select=()):
    ctx.selected = list(select)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
    return c, msg, done


def base(prompt):
    return prompt.split(" (")[0]


# --- 126 Revolve ---------------------------------------------------------------------
# MEASURED: line (1,0,0)-(2,0,0) about (0,0,0)-(0,0,1), Enter at both angles -> ONE
# full surface of revolution, profile kept. Here Enter is also used at the axis end, which
# the note says takes the CPlane z-axis direction.
print("-- 126 Revolve --")
ctx = Ctx()
ctx.add(bridge.make_line((1, 0, 0), (2, 0, 0)), "Prof")
c, msg, done = drive("revolve", [], ctx, select=["Prof"])
prompts = [c.prompt]
for line in ("0,0,0", "", "", ""):
    msg, done = c.feed(line, ctx)
    prompts.append(c.prompt)
check(prompts[:4] ==
      ["Start of revolve axis:",
       "End of revolve axis (Press Enter to use CPlane z-axis direction):",
       "Start angle <0>:", "Revolution angle <360>:"], f"Revolve prompts ({prompts[:4]})")
m = kernel.master_of(ctx.added[0]) if ctx.added else None
check(done and m is not None and ctx.geom_by_name("Prof") is not None,
      f"Enter, Enter, Enter: one exact revolved surface, profile kept ({msg!r})")
if m is not None:
    area = ob.mass_properties(m, solid=False)["area"]
    check(abs(area - math.pi * (4 - 1)) < 1e-9,
          f"...a full 360 degree annulus pi (R^2 - r^2) = {math.pi * 3:.9f} ({area!r})")
ctx = Ctx()
ctx.add(bridge.make_line((1, 0, 0), (2, 0, 0)), "Prof")
c, msg, done = drive("revolve", ["0,0,0", "0,0,1", "90", "90"], ctx, select=["Prof"])
m = kernel.master_of(ctx.added[0]) if ctx.added else None
lo, hi = ob.tight_bbox(m) if m is not None else ((0, 0, 0), (0, 0, 0))
check(done and m is not None and lo[0] < -1.999 and hi[0] < 1e-6 and lo[1] > -1e-6,
      f"start 90 + revolution 90: the quarter from 90 to 180 degrees ({lo}, {hi})")

# --- 092 Offset ----------------------------------------------------------------------
print("-- 092 Offset --")


def line_ends(g):
    return sorted(tuple(round(v, 9) + 0.0 for v in (p.X, p.Y, p.Z))
                  for p in (g.PointAtStart, g.PointAtEnd))


ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "L")
c, msg, done = drive("offset", [], ctx, select=["L"])
check(not done and c.prompt.startswith("Side to offset ( Distance=1"),
      f"preselected: straight to the side prompt with Distance=1 ({c.prompt!r})")
msg, done = c.feed("2,1,0", ctx)
check(done and ctx.added and line_ends(ctx.added[0]) == [(0, 1, 0), (4, 1, 0)]
      and ctx.geom_by_name("L") is not None,
      f"side w2,1,0 at Distance 1 -> (0,1,0)-(4,1,0), original kept ({msg!r})")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "L")
c, msg, done = drive("offset", ["Distance=2", "2,-1,0"], ctx, select=["L"])
check(done and ctx.added and line_ends(ctx.added[0]) == [(0, -2, 0), (4, -2, 0)],
      f"Distance=2, side on -Y -> the line at y = -2 ({msg!r})")
c = type(commands.COMMANDS["offset"])()
check(c.opt["Distance"] == 2.0, "Distance is sticky across runs (MEASURED)")
c.feed("Distance=1", ctx)
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "L")
c, msg, done = drive("offset", ["3"], ctx, select=["L"])
check(not done and not ctx.added and c.opt["Distance"] == 3.0 and c.prompt.startswith(
    "Side to offset ( Distance=3"), f"MEASURED (P47): a typed number sets Distance and stays ({c.prompt!r})")
segs = c.preview_curves(ctx.geom_by_name, (2, -5, 0))
check(segs and all(abs(p[1] + 3.0) < 1e-9 for p in segs[0]),
      f"the live preview is the offset on the cursor's side ({segs and segs[0]})")
msg, done = c.feed("2,1,0", ctx)
check(done and ctx.added and line_ends(ctx.added[0]) == [(0, 3, 0), (4, 3, 0)],
      f"...and the side point then offsets by it ({msg!r})")
c.feed("Distance=1", ctx)

# --- 167 Split ------------------------------------------------------------------------
print("-- 167 Split --")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (6, 0, 0)), "L")
c, msg, done = drive("split", [], ctx, select=["L"])
check(not done and c.prompt.startswith("Select cutting objects ( Point"),
      f"preselected: straight to the cutting prompt with Point ({c.prompt!r})")
c.feed("Point", ctx)
check(c.prompt == "Point to split curve. Press Enter when done:",
      f"Point asks for split points ({c.prompt!r})")
msg, done = c.feed("3,0,0", ctx)
check(not done, "a point waits for Enter")
msg, done = c.feed("", ctx)
pieces = sorted((line_ends(f.Geometry) for f in ctx.model.file.Objects), key=lambda e: e[0])
check(done and pieces == [[(0, 0, 0), (3, 0, 0)], [(3, 0, 0), (6, 0, 0)]],
      f"split at (3,0,0): two pieces (0..3) and (3..6) ({msg!r}; {pieces})")
ctx = Ctx()
ctx.add(bridge.make_circle((0, 0, 0), 2.0), "C")
c, msg, done = drive("split", ["Point", "2,0.001,0", "-2,0,0", ""], ctx, select=["C"])
check(done and len(list(ctx.model.file.Objects)) == 2,
      f"a circle split at two points becomes two curves ({msg!r})")

# --- 093 OffsetSrf ----------------------------------------------------------------------
# MEASURED: a 4 x 4 planar surface, preselected; Enter at the flip prompt -> a closed solid
# polysurface with 6 surfaces at the sticky Distance 1, and the input surface deleted.
print("-- 093 OffsetSrf --")


def square_srf():
    srf = bridge.make_planar_srf([bridge.make_rectangle((0, 0, 0), (4, 4, 0))])
    return srf[0] if isinstance(srf, (list, tuple)) else srf


ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", [], ctx, select=["S"])
check(not done and c.prompt.startswith("Select object to flip direction. Press Enter when "
                                       "done ( Distance=1  Solid=Yes  DeleteInput=Yes"),
      f"preselected: the flip prompt with the measured defaults ({c.prompt!r})")
msg, done = c.feed("", ctx)
m = kernel.master_of(ctx.added[0]) if ctx.added else None
check(done and m is not None and ob.is_airtight(m) and ob.face_types(m) == {"PLANE": 6}
      and abs(ob.volume(m) - 16.0) < 1e-9 and ctx.geom_by_name("S") is None,
      f"Enter: a closed 6-face solid of volume 16, input deleted ({msg!r})")
ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", ["Distance=2", "S", ""], ctx, select=["S"])
m = kernel.master_of(ctx.added[0]) if ctx.added else None
lo, hi = ob.tight_bbox(m) if m is not None else ((0,) * 3, (0,) * 3)
c2 = type(commands.COMMANDS["offsetsrf"])()
check(done and m is not None and abs(ob.volume(m) - 32.0) < 1e-9 and c2.opt["Distance"] == 2.0,
      f"Distance=2 is used and sticky ({msg!r})")
ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", ["Distance=2", ""], ctx, select=["S"])
m2 = kernel.master_of(ctx.added[0]) if ctx.added else None
lo2, hi2 = ob.tight_bbox(m2) if m2 is not None else ((0,) * 3, (0,) * 3)
check(m is not None and m2 is not None and round(lo[2] + hi[2], 9) == -round(lo2[2] + hi2[2], 9),
      f"naming the surface at the flip prompt flips the side ({lo[2]}..{hi[2]} vs {lo2[2]}..{hi2[2]})")
type(commands.COMMANDS["offsetsrf"])().feed("Distance=1", ctx)
# MEASURED (research_preview_14 P48): a typed 2 at the flip prompt sets Distance and stays.
ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", ["2"], ctx, select=["S"])
check(not done and not ctx.added and c.opt["Distance"] == 2.0 and "Distance=2" in c.prompt
      and c.prompt.startswith("Select object to flip direction"),
      f"P48: typed 2 at the flip prompt sets Distance=2 and keeps the prompt ({c.prompt!r})")
msg, done = c.feed("", ctx)
m = kernel.master_of(ctx.added[0]) if ctx.added else None
check(done and m is not None and abs(ob.volume(m) - 32.0) < 1e-9,
      f"...then Enter offsets by 2: a 4 x 4 x 2 solid ({msg!r})")
# ThroughPoint (#54): the signed normal distance to a picked point is the offset
sticky_before = type(commands.COMMANDS["offsetsrf"])().opt["Distance"]
for pick, want_z, want_vol in (("1,1,3", (0.0, 3.0), 48.0), ("9,9,-1.5", (-1.5, 0.0), 24.0)):
    ctx = Ctx()
    ctx.add(square_srf(), "S")
    c, msg, done = drive("offsetsrf", ["ThroughPoint", pick], ctx, select=["S"])
    m = kernel.master_of(ctx.added[0]) if ctx.added else None
    lo, hi = ob.tight_bbox(m) if m is not None else ((0,) * 3, (0,) * 3)
    check(done and m is not None and abs(ob.volume(m) - want_vol) < 1e-9
          and (round(lo[2], 9), round(hi[2], 9)) == want_z and ctx.geom_by_name("S") is None,
          f"ThroughPoint {pick}: a solid from z={want_z[0]} to {want_z[1]}, volume {want_vol} "
          f"({msg!r})")
check(type(commands.COMMANDS["offsetsrf"])().opt["Distance"] == sticky_before,
      f"ThroughPoint leaves the sticky Distance unchanged ({sticky_before})")
ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", ["Solid=No", "ThroughPoint", "2,2,2.5"], ctx, select=["S"])
g = ctx.added[0] if ctx.added else None
check(done and g is not None and abs(g.GetBoundingBox().Min.Z - 2.5) < 1e-9
      and abs(g.GetBoundingBox().Max.Z - 2.5) < 1e-9,
      f"ThroughPoint with Solid=No: the offset surface passes through z=2.5 ({msg!r})")
ctx = Ctx()
ctx.add(square_srf(), "S")
c, msg, done = drive("offsetsrf", ["ThroughPoint", "3,1,0"], ctx, select=["S"])
check(done and not ctx.added and "lies on the surface" in msg and ctx.geom_by_name("S") is not None,
      f"ThroughPoint on the surface is refused, nothing changes ({msg!r})")
c, msg, done = drive("offsetsrf", ["-1"], ctx, select=["S"])
check(not done and c.opt["Distance"] == 2.0, f"a non-positive typed distance is refused ({msg!r})")
type(commands.COMMANDS["offsetsrf"])().feed("Distance=1", ctx)

# --- 007 ArrayPolar: the axis is the CPlane normal -------------------------------------
print("-- 007 ArrayPolar --")
ctx = Ctx(cplane.WORLD_FRONT)
ctx.add(r3.Point(r3.Point3d(2, 0, 0)), "P")
c, msg, done = drive("arraypolar", ["", "4", "", ""], ctx, select=["P"])
locs = sorted(tuple(round(v, 9) + 0.0 for v in (g.Location.X, g.Location.Y, g.Location.Z))
              for g in ctx.added)
check(done and locs == [(-2.0, 0.0, 0.0), (0.0, 0.0, -2.0), (0.0, 0.0, 2.0)],
      f"in Front the copies turn about the CPlane normal (world Y), staying in XZ ({locs})")

# --- previews measured in research_preview_14 (P29 ArrayPolar, P53 Plane) ------------------
print("-- previews: ArrayPolar, Plane --")
preview = _load("narwhal3d.preview", "narwhal3d/preview.py")
TOP = cplane.WORLD_TOP
segs = preview.segments("arraypolar", [["P"], (0.0, 0.0, 0.0), 4.0], (3, 4, 0), TOP)
check(len(segs) == 2 and all(abs(math.hypot(p[0], p[1]) - 5.0) < 1e-9 for p in segs[0])
      and segs[1] == [(0.0, 0.0, 0.0), (3, 4, 0)],
      "ArrayPolar fill prompt: a circle through the cursor and a guide line")
check(preview.segments("arraypolar", [["P"], (0.0, 0.0, 0.0)], (3, 4, 0), TOP) == [],
      "...nothing before the item count")
segs = preview.segments("plane", [(1.0, 1.0, 0.0)], (4, 3, 0), TOP)
xs = sorted({round(p[0], 9) for p in segs[0]}) if segs else []
check(xs == [1.0, 4.0], f"Plane: a live rectangle from the corner to the cursor ({segs})")

# --- Box typed length -> Width (MEASURED research_preview_14 P01) -----------------------
print("-- Box: typed length --")
ctx = Ctx()
c, msg, done = drive("box", ["0,0,0", "4"], ctx)
check(not done and c.prompt == "Width. Press Enter to use length:",
      f"a typed length asks for the width ({c.prompt!r})")
segs = preview.segments("box", c.args, (4, 3, 0), TOP)
check(segs and sorted({round(p[1], 9) for p in segs[0]}) == [0.0, 3.0],
      "the live base follows the width cursor")
c.feed("3", ctx)
check(c.prompt.startswith("Height. Press Enter to use width"), f"then the height ({c.prompt!r})")
segs = preview.segments("box", c.args, (4, 3, 2), TOP)
check(len(segs) == 6 and max(p[2] for s_ in segs for p in s_) == 2.0
      and max(p[0] for p in segs[0]) == 4.0, "the live box is length x width x cursor height")
msg, done = c.feed("", ctx)
bb = ctx.added[0].GetBoundingBox() if ctx.added else None
check(done and bb is not None and (round(bb.Max.X, 9), round(bb.Max.Y, 9), round(bb.Max.Z, 9))
      == (4.0, 3.0, 3.0), f"4 x 3 base, Enter -> height = width 3 ({msg!r})")
ctx = Ctx()
c, msg, done = drive("box", ["0,0,0", "4", "", ""], ctx)
bb = ctx.added[0].GetBoundingBox() if ctx.added else None
check(done and bb is not None and round(bb.Max.Y, 9) == 4.0 and round(bb.Max.Z, 9) == 4.0,
      "Enter at Width = length (a square), Enter at Height = width -- a 4 cube")

# --- Plane typed length -> Width (MEASURED research_preview_14 P53) ----------------------
print("-- Plane: typed length --")
ctx = Ctx()
c, msg, done = drive("plane", ["0,0,0", "5"], ctx)
check(not done and c.prompt.startswith("Width. Press Enter to use length"),
      f"Plane: a typed length 5 asks for the width ({c.prompt!r})")
segs = preview.segments("plane", c.args, (5, 2, 0), TOP)
check(len(segs) == 1 and sorted({round(p[0], 9) for p in segs[0]}) == [0.0, 5.0],
      "the live rectangle has the locked length")
msg, done = c.feed("2", ctx)
bb = ctx.added[0].GetBoundingBox() if ctx.added else None
check(done and bb is not None and (round(bb.Max.X, 9), round(bb.Max.Y, 9)) == (5.0, 2.0),
      f"a 5 x 2 planar surface ({msg!r})")
ctx = Ctx()
c, msg, done = drive("plane", ["0,0,0", "3,2,0"], ctx)
check(done and ctx.added, f"two picked corners still build the plane ({msg!r})")

# --- 006 Array ------------------------------------------------------------------------
# MEASURED: in the FRONT CPlane, X 3, Y 2, spacings 2 and 3 -> circles at x = 0, 2, 4 and
# world z = 0, 3 (Y spacing along the CPlane y = world Z); prompts as listed.
print("-- 006 Array --")
ctx = Ctx(cplane.WORLD_FRONT)
ctx.add(bridge.make_circle((0, 0, 0), 0.5), "C")
c, msg, done = drive("array", [], ctx, select=["C"])
prompts = [c.prompt]
for line in ("3", "2", "1", "2", "3"):
    c.feed(line, ctx)
    prompts.append(c.prompt)
check([p.split(" <")[0].rstrip(":") for p in prompts] ==
      ["Number in X direction", "Number in Y direction", "Number in Z direction",
       "Unit cell or X spacing", "Y spacing or first reference point", "Press Enter to accept"],
      f"Array prompts ({prompts})")
msg, done = c.feed("", ctx)
centres = sorted((round(g.PointAtStart.X - 0.5, 9) + 0.0, round(g.PointAtStart.Z, 9) + 0.0)
                 for g in ctx.added)
check(done and centres == [(0.0, 3.0), (2.0, 0.0), (2.0, 3.0), (4.0, 0.0), (4.0, 3.0)],
      f"5 copies at x 0/2/4, world z 0/3 -- the CPlane axes ({centres})")
c = type(commands.COMMANDS["array"])()
c.begin(ctx)
check(c.prompt == "Number in X direction <3>:", f"the counts default to the last values ({c.prompt!r})")
ArrayDefault = commands.ArrayLinearCmd._last_counts
ArrayDefault[:] = [2, 1, 1]

# --- 136 / 150 Sel* filters: the measured report -----------------------------------
print("-- Sel* filter reports --")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "L")
ctx.add(bridge.make_circle((0, 0, 0), 1.0), "C")
ctx.add(square_srf(), "S")
_c, msg, _d = drive("selcrv", [], ctx)
check(msg == "2 curves added to selection.", f"SelCrv: {msg!r}")
_c, msg, _d = drive("selpt", [], ctx)
check(msg == "No objects added to selection.", f"SelPt with no points: {msg!r}")
_c, msg, _d = drive("selsrf", [], ctx)
check(msg == "1 surface added to selection.", f"SelSrf: {msg!r}")
_c, msg, _d = drive("selcrv", [], ctx, select=["L"])
check(msg == "1 curve added to selection.", f"an already selected curve is not counted: {msg!r}")

# --- prompts and messages (notes 083, 101, 106, 109, 124, 177, 178) -------------------
print("-- prompts and messages --")
c = type(commands.COMMANDS["lines"])()
check([c.variable_prompt(n) for n in (1, 2, 3)] ==
      ["Start of line:", "End of line:", "End of line. Press Enter when done:"], "Lines prompts")
c = type(commands.COMMANDS["points"])()
check([c.variable_prompt(n) for n in (1, 2)] ==
      ["Location of point object:", "Location of point object. Press Enter when done:"],
      "Points prompts")
check([p for p, _k in type(commands.COMMANDS["parabola"])().steps] ==
      ["Parabola focus:", "Parabola direction:", "Parabola end:"], "Parabola prompts")
check(type(commands.COMMANDS["removefromgroup"])().steps[-1][0]
      == "Select objects to remove from group:", "RemoveFromGroup prompt")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Open")
_c, msg, done = drive("planarsrf", [], ctx, select=["Open"])
check(done and msg == "No faces were made. Curve must be closed and planar." and not ctx.added,
      f"PlanarSrf on an open curve: {msg!r}")
ctx.add(bridge.make_rectangle((0, 0, 0), (4, 4, 0)), "Rect")
_c, msg, done = drive("planarsrf", [], ctx, select=["Rect"])
check(done and msg is None and len(ctx.added) == 1, f"PlanarSrf success is silent ({msg!r})")

# --- 142 SelLast: the objects the LAST command created ---------------------------------
print("-- 142 SelLast --")
selection_state = commands.selection_state
ctx = Ctx()
ctx.add(bridge.make_rectangle((0, 0, 0), (4, 4, 0)), "Rect")
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Line")
selection_state.begin_command(ctx.model)
selection_state.record_created(ctx.model, "Rect")
selection_state.begin_command(ctx.model)
selection_state.record_created(ctx.model, "Line")
selection_state.begin_command(ctx.model)          # e.g. SelNone: creates nothing
_c, msg, done = drive("sellast", [], ctx)
check(ctx.selected == ["Line"] and msg == "1 curve added to selection.",
      f"SelLast selects the last command's object only ({ctx.selected}, {msg!r})")

# --- 112 Polygon: a closed polyline -----------------------------------------------------
print("-- 112 Polygon --")
ctx = Ctx()
c, msg, done = drive("polygon", ["0,0,0", "2,0,0"], ctx)
g = ctx.added[0] if ctx.added else None
check(done and isinstance(g, r3.PolylineCurve) and g.IsClosed and g.PointCount == 6
      and g.PointAtStart.DistanceTo(r3.Point3d(2, 0, 0)) < 1e-12,
      f"Polygon: a closed polyline, a corner at (2,0,0) ({type(g).__name__})")

# --- 105 Pipe ------------------------------------------------------------------------
print("-- 105 Pipe --")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Rail")
c, msg, done = drive("pipe", [], ctx, select=["Rail"])
prompts = [c.prompt]
for line in ("", ""):
    msg, done = c.feed(line, ctx)
    prompts.append(c.prompt)
check([p.split(" (")[0].rstrip(":") for p in prompts] ==
      ["Start radius <1.000>", "End radius", "Point for next radius. Press Enter for none"],
      f"Pipe prompts, Enter twice ({prompts})")
msg, done = c.feed("", ctx)
m = kernel.master_of(ctx.added[0]) if ctx.added else None
check(done and m is not None and abs(ob.volume(m) - math.pi * 4) < 1e-9
      and ctx.geom_by_name("Rail") is not None,
      f"Enter at the next-radius prompt builds the radius-1 pipe; rail kept ({msg!r})")

# --- Pipe: the live radius circle (live observation of the reference program) ---
print("-- Pipe preview --")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Rail")
c, msg, done = drive("pipe", [], ctx, select=["Rail"])
segs = preview.segments("pipe", c.args, (1, 0, 2), TOP, resolve=ctx.geom_by_name, cmd=c)
ring = segs[0] if segs else []
check(len(segs) == 2 and ring and all(abs(p[0]) < 1e-9 and abs(math.hypot(p[1], p[2]) - 2.0) < 1e-9
                                      for p in ring),
      "start radius: a circle across the rail at its start, radius = the cursor's distance")
msg, done = c.feed("2,2.5,0", ctx)
check(not done and abs(c.args[1] - 2.5) < 1e-9,
      f"MEASURED (P52): a click at (2,2.5) gives its distance from the CURVE, 2.5 ({c.args})")
c.args[1] = 3.0
segs = preview.segments("pipe", c.args, (4, 1, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
check(len(segs) == 3 and all(abs(p[0] - 4.0) < 1e-9 and abs(math.hypot(p[1], p[2]) - 1.0) < 1e-9
                             for p in segs[1]),
      "end radius: the start circle stays, a live one follows the cursor at the end")

# --- Pipe: a tapered pipe (P52: the reference builds start != end) -------------------
print("-- Pipe taper --")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (10, 0, 0)), "Rail")
c, msg, done = drive("pipe", ["1", "2", ""], ctx, select=["Rail"])
m = kernel.master_of(ctx.added[0]) if ctx.added else None
# pi L * integral_0^1 (1 + 3t^2 - 2t^3)^2 dt = pi 10 * 83/35
want_local = math.pi * 10 * 83 / 35
check(done and m is not None and ob.is_airtight(m) and "not yet measured" in (msg or "")
      and abs(ob.volume(m) - want_local) < 1e-6 * want_local
      and not ob.face_types(m).get("CONE"),
      f"ShapeBlending=Local (the measured default): an S-law taper, airtight, volume "
      f"pi L mean(r^2), and the message says the law is unmeasured "
      f"({m is not None and ob.volume(m)} vs {want_local:.4f}; {msg!r})")
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (10, 0, 0)), "Rail")
c, msg, done = drive("pipe", ["ShapeBlending=Global", "1", "2", ""], ctx, select=["Rail"])
m = kernel.master_of(ctx.added[0]) if ctx.added else None
frustum = math.pi * 10 / 3 * (1 + 2 + 4)
check(done and m is not None and ob.face_types(m) == {"CONE": 1, "PLANE": 2}
      and ob.is_airtight(m) and abs(ob.volume(m) - frustum) < 1e-9,
      f"Global taper on a line: one exact CONE face, capped, frustum volume "
      f"({ob.face_types(m) if m is not None else None}, {msg!r})")
recipe = kernel.recipe_of(ctx.added[0]) if ctx.added else None
rebuilt = ob.from_recipe(recipe) if recipe else None
check(rebuilt is not None and abs(ob.volume(rebuilt) - frustum) < 1e-9,
      "...its recipe rebuilds the same taper (save/reload)")
ctx = Ctx()
ctx.add(bridge.make_arc((10, 0, 0), (10 * math.cos(math.pi / 4), 10 * math.sin(math.pi / 4), 0),
                        (0, 10, 0)), "Arc")
c, msg, done = drive("pipe", ["ShapeBlending=Global", "1", "2", ""], ctx, select=["Arc"])
m = kernel.master_of(ctx.added[0]) if ctx.added else None
length = 10 * math.pi / 2
want = math.pi * length * (1 + 2 + 4) / 3
faces = ob.face_types(m) if m is not None else {}
check(done and m is not None and ob.is_airtight(m) and faces.get("PLANE") == 2
      and len(faces) == 2 and abs(ob.volume(m) - want) < 1e-6 * want,
      f"Global taper on a quarter arc: one smooth swept face + 2 caps, airtight, "
      f"pi*L*(r0^2+r0 r1+r1^2)/3 ({faces}, {ob.volume(m) if m is not None else None} vs {want})")

# --- P56 SrfPt: prompts and the live outline -------------------------------------------
print("-- P56 SrfPt --")
_load("narwhal3d.surface_commands", "narwhal3d/surface_commands.py")
ctx = Ctx()
c, msg, done = drive("srfpt", ["0,0,0", "4,0,0", "4,4,0"], ctx)
check(c.prompt == "Fourth corner of surface:", f"P56: the fourth prompt wording ({c.prompt!r})")
check(preview.segments("srfpt", [(0.0, 0.0, 0.0)], (3, -1, 0), TOP) == [[(0.0, 0.0, 0.0), (3, -1, 0)]],
      "corner 2: a rubber line from corner 1 to the cursor")
check(preview.segments("srfpt", [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0)], (4, 4, 0), TOP)
      == [[(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4, 4, 0), (0.0, 0.0, 0.0)]],
      "corner 3: the triangle 1-2-cursor")
check(preview.segments("srfpt", list(c.args), (0, 4, 0), TOP)
      == [[(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 4.0, 0.0), (0, 4, 0), (0.0, 0.0, 0.0)]],
      "corner 4: the outline closes through the cursor back to corner 1")
msg, done = c.feed("", ctx)
check(done and ctx.added, f"Enter at the fourth prompt: the triangular surface ({msg!r})")

# --- P54 Revolve: angle previews and picked angles --------------------------------------
print("-- P54 Revolve --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((1, 0, 0), (2, 0, 0)), "Prof")
c, msg, done = drive("revolve", ["0,0,0"], ctx, select=["Prof"])
segs = preview.segments("revolve", c.args, (0, 3, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
check(segs == [[(0.0, 0.0, 0.0), (0, 3, 0)]], f"axis end: a rubber line from the start ({segs})")
msg, done = c.feed("0,3,0", ctx)
segs = preview.segments("revolve", c.args, (2, 2, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
ring = segs[1] if len(segs) == 3 else []
check(len(segs) == 3 and segs[0] == [(0.0, 0.0, 0.0), (0.0, 3.0, 0.0)] and ring
      and all(abs(p[1]) < 1e-9 and abs(math.hypot(p[0], p[2]) - 2.0) < 1e-9 for p in ring)
      and max(p[0] for p in ring) > 1.99 and min(p[0] for p in ring) < -1.99,
      "start angle: the axis stays; a circle about it in the plane of the axis start, through "
      "the cursor's projection (edge-on in Top: x -2..2 at y 0)")
msg, done = c.feed("30", ctx)
check(not done and c.prompt.startswith("Revolution angle <360>") and c.args[3] == 30.0,
      f"P54: a typed start angle 30 advances ({c.prompt!r})")
msg, done = c.feed("4,0,0", ctx)
m = kernel.master_of(ctx.added[0]) if ctx.added else None
annulus = math.pi * (4 - 1)
area = ob.mass_properties(m, solid=False)["area"] if m is not None else 0.0
check(done and m is not None and abs(c.args[4] + 30.0) < 1e-9
      and abs(area - annulus * 30 / 360) < 1e-9,
      f"P54: a click at (4,0) finishes; the angle is measured from the start (-30) "
      f"({c.args[3:]}, area {area} vs {annulus / 12}; {msg!r})")
c, msg, done = drive("revolve", ["0,0,0", "0,3,0", "0,1,0"], ctx, select=["Prof"])
check(not done and len(c.args) == 3, f"a pick on the axis is refused ({msg!r})")

# --- P28 Array: the unit cell route ---------------------------------------------------
print("-- P28 Array --")
ctx = Ctx(TOP)
ctx.add(r3.Point(r3.Point3d(0, 0, 0)), "P")
c, msg, done = drive("array", ["3", "2", "1"], ctx, select=["P"])
check(base(c.prompt).rstrip(":") == "Unit cell or X spacing"
      and preview.segments("array", c.args, (2, 0, 0), TOP, resolve=ctx.geom_by_name, cmd=c) == [],
      f"counts 3 x 2 x 1, then 'Unit cell or X spacing' with nothing drawn ({c.prompt!r})")
msg, done = c.feed("2,0,0", ctx)
check(not done and base(c.prompt).rstrip(":") == "Other corner or length",
      f"P28: a click there is the unit cell's first corner ({c.prompt!r})")
segs = preview.segments("array", c.args, (4, 2, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
check(len(segs) == 1 and {(round(p[0], 9), round(p[1], 9)) for p in segs[0]}
      == {(2.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0)},
      f"'Other corner': the cell rectangle from the corner to the cursor ({segs})")
msg, done = c.feed("5", ctx)
check(not done and "not implemented" in (msg or ""), f"a typed cell length says so ({msg!r})")
msg, done = c.feed("4,2.5,0", ctx)
check(not done and base(c.prompt).rstrip(":") == "Press Enter to accept"
      and c.args[4:6] == [2.0, 2.5], f"the second corner gives XSpacing 2, YSpacing 2.5 ({c.args})")
segs = preview.segments("array", c.args, (9, 9, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
spots = sorted((round(s[0][0], 9), round(s[0][1], 9)) for s in segs)
check(spots == [(0.0, 2.5), (2.0, 0.0), (2.0, 2.5), (4.0, 0.0), (4.0, 2.5)],
      f"accept: the five copies are previewed at their places ({spots})")
msg, done = c.feed("", ctx)
locs = sorted((round(g.Location.X, 9), round(g.Location.Y, 9)) for g in ctx.added)
check(done and locs == spots, f"Enter creates them ({locs}; {msg!r})")
c, msg, done = drive("array", ["2", "2", "1", "0,0,0", "-2,3,0", ""], Ctx(TOP), select=["P"])
check(done and c.args[4:6] == [-2.0, 3.0], f"a corner toward -X arrays toward -X ({c.args})")

# --- P04 Cone / P05 Sphere / Circle / Cylinder: prompts and sticky size defaults -------
print("-- P04 P05 sticky size defaults --")
ctx = Ctx(TOP)
c, msg, done = drive("cone", [], ctx)
p0 = base(c.prompt)
c.feed("0,0,0", ctx)
p1 = base(c.prompt)
c.feed("2", ctx)
p2 = base(c.prompt)
check((p0, p1, p2) == ("Base of cone", "Radius <1.000>", "End of cone <10.000>"),
      f"P04: Cone prompts with their defaults ({p0!r}, {p1!r}, {p2!r})")
msg, done = c.feed("5", ctx)
check(done and ctx.added, f"P04: a typed height completes ({msg!r})")
c, msg, done = drive("cone", ["0,0,0"], ctx)
p1 = base(c.prompt)
msg, done = c.feed("", ctx)
p2 = base(c.prompt)
check(p1 == "Radius <2.000>" and p2 == "End of cone <5.000>" and c.args[1] == 2.0,
      f"P04: both defaults are sticky; Enter accepts the radius ({p1!r}, {p2!r})")
c.feed("", ctx)
c, msg, done = drive("sphere", [], ctx)
p0 = base(c.prompt)
c.feed("0,0,0", ctx)
p1 = base(c.prompt)
msg, done = c.feed("4.028,0,0", ctx)
c2, _m, _d = drive("sphere", ["1,1,0"], ctx)
check(p0 == "Center of sphere" and p1 == "Radius <1.000>" and done
      and base(c2.prompt) == "Radius <4.028>",
      f"P05: a picked radius 4.028 becomes the next default ({p1!r} -> {c2.prompt!r})")
c3, _m, _d = drive("circle", [], ctx)
check(base(c3.prompt) == "Center of circle", f"Circle: 'Center of circle' ({c3.prompt!r})")
c3.feed("0,0,0", ctx)
check(base(c3.prompt) == "Radius <1.000>", f"Circle: 'Radius <1.000>' ({c3.prompt!r})")
c4, _m, _d = drive("cylinder", [], ctx)
q0 = base(c4.prompt)
c4.feed("0,0,0", ctx)
c4.feed("3", ctx)
q2 = base(c4.prompt)
check(q0 == "Base of cylinder" and q2 == "End of cylinder <5.000>",
      f"Cylinder: 'Base of cylinder', 'End of cylinder <5.000>' ({q0!r}, {q2!r})")
segs = preview.segments("sphere", [(0.0, 0.0, 0.0)], (4, 0, 0), TOP)
radii = sorted(round(max(math.hypot(p[0], p[1]) for p in s), 6) for s in segs)
zs = sorted(round(s[0][2], 6) for s in segs[:3])
check(len(segs) == 7 and radii[:2] == [round(4 * math.sqrt(0.5), 6)] * 2
      and radii[2:] == [4.0] * 5 and zs == [round(-4 * math.sqrt(0.5), 6), 0.0, round(4 * math.sqrt(0.5), 6)],
      f"P05: the live wire sphere: equator, +-45 degree latitudes, 4 meridians ({radii}, {zs})")

# --- P13 Arc: centre-first default, typed angle; StartPoint preview ----------------------
print("-- P13 Arc --")
ctx = Ctx(TOP)
c, msg, done = drive("arc", [], ctx)
ps = [base(c.prompt)]
c.feed("1,1,0", ctx)
ps.append(base(c.prompt))
segs = preview.segments("arc", c.args, (4, 1, 0), TOP, cmd=c)
c.feed("4,1,0", ctx)
ps.append(base(c.prompt))
check(ps == ["Center of arc", "Start of arc", "End point or angle"]
      and segs == [[(1.0, 1.0, 0.0), (4, 1, 0)]], f"P13: centre-first prompts ({ps})")
segs = preview.segments("arc", c.args, (1, 5, 0), TOP, cmd=c)
arc = segs[0] if segs else []
check(len(segs) == 3 and all(abs(math.dist(p, (1, 1, 0)) - 3) < 1e-9 for p in arc)
      and abs(arc[-1][0] - 1) < 1e-9 and arc[-1][1] > 3.99,
      "P13 item 8: a rubber arc from the start to the cursor's angle + lines from the centre")
msg, done = c.feed("90", ctx)
g = ctx.added[0] if ctx.added else None
check(done and g is not None and g.PointAtEnd.DistanceTo(r3.Point3d(1, 4, 0)) < 1e-9
      and g.PointAt(0.5 * (g.Domain.T0 + g.Domain.T1)).DistanceTo(r3.Point3d(1 + 3 * math.sqrt(0.5), 1 + 3 * math.sqrt(0.5), 0)) < 1e-9,
      f"P13 item 9: typed 90 completes a CCW quarter arc (4,1)->(1,4) ({msg!r})")
c, msg, done = drive("arc", ["StartPoint", "-3,-3,0"], ctx)
ps = [base(c.prompt)]
c.feed("4,0,0", ctx)
ps.append(base(c.prompt))
segs = preview.segments("arc", c.args, (0, 4, 0), TOP, cmd=c)
live = segs[0] if segs else []
centre = (-0.1, -0.1, 0.0)        # equidistant from (-3,-3), (4,0) and (0,4)
check(ps == ["End of arc", "Point on arc"] and len(segs) == 1 and len(live) > 3
      and all(abs(math.dist(p, centre) - math.dist((4, 0, 0), centre)) < 1e-9 for p in live)
      and min(math.dist(p, (0, 4, 0)) for p in live) < 0.3,
      f"P13 item 5: at 'Point on arc' the arc through start, end and cursor ({ps})")

# --- P11 Polyline prompts ---------------------------------------------------------------
print("-- P11 Polyline --")
c, msg, done = drive("polyline", [], Ctx(TOP))
ps = [base(c.prompt)]
for pt in ("1,1,0", "4,0,0", "4,4,0"):
    c.feed(pt, ctx)
    ps.append(c.prompt)
check(ps[0] == "Start of polyline" and base(ps[1]) == "Next point of polyline"
      and base(ps[2]) == "Next point of polyline. Press Enter when done" and "Close" not in ps[2]
      and "Close" in ps[3], f"P11: Polyline prompts; Close only from 3 points ({ps})")

# --- P14 Ellipse --------------------------------------------------------------------------
print("-- P14 Ellipse --")
ctx = Ctx(TOP)
c, msg, done = drive("ellipse", [], ctx)
p0 = base(c.prompt)
c.feed("1,1,0", ctx)
c.feed("4,1,0", ctx)
segs = preview.segments("ellipse", c.args, (2, 5, 0), TOP)
ring = segs[0] if segs else []
check(p0 == "Ellipse center" and len(segs) == 2 and ring
      and max(p[1] for p in ring) - 5.0 < 1e-9 and abs(max(p[1] for p in ring) - 5.0) < 1e-6
      and abs(max(p[0] for p in ring) - 4.0) < 1e-6,
      f"P14: 'Ellipse center'; the live ellipse has perpendicular axes, b = the offset from "
      f"the first axis ({p0!r})")
msg, done = c.feed("2,5,0", ctx)
g = ctx.added[0] if ctx.added else None
check(done and g is not None and "b=4" in msg
      and g.PointAt(g.Domain.T0 + (g.Domain.T1 - g.Domain.T0) / 4).DistanceTo(r3.Point3d(1, 5, 0)) < 1e-9,
      f"...and the built ellipse has the same perpendicular axes ({msg!r})")

# --- P17 Curve / P18 InterpCrv prompts, Close, the InterpCrv preview ------------------
print("-- P17 Curve, P18 InterpCrv --")
for key in ("curve", "interpcrv"):
    ctx = Ctx(TOP)
    c, msg, done = drive(key, [], ctx)
    ps = [c.prompt]
    for pt in ("0,0,0", "4,0,0", "4,4,0"):
        c.feed(pt, ctx)
        ps.append(c.prompt)
    check([base(p) for p in ps] == ["Start of curve", "Next point",
                                    "Next point. Press Enter when done",
                                    "Next point. Press Enter when done"]
          and "Close" not in ps[2] and "Close" in ps[3],
          f"{key}: the measured prompts; Close from three points ({[base(p) for p in ps]})")
    msg, done = c.feed("Close", ctx)
    g = ctx.added[0] if ctx.added else None
    check(done and g is not None and g.IsClosed, f"{key}: Close closes and ends ({msg!r})")
# #43: a closed InterpCrv passes THROUGH every pick (it used to be a control-point curve)
interp = _load("narwhal3d.interpolate", "narwhal3d/interpolate.py")
picks = [(0.0, 0.0, 0.0), (5.0, 1.0, 0.0), (6.0, 4.0, 0.0), (2.0, 6.0, 0.0), (-1.0, 3.0, 0.0)]
ctx = Ctx(TOP)
c, msg, done = drive("interpcrv", [], ctx)
for pt in picks:
    c.feed(",".join(str(v) for v in pt), ctx)
msg, done = c.feed("Close", ctx)
g = ctx.added[0] if ctx.added else None
us = interp.closed_parameters(picks)
worst = (max(math.dist(tuple(getattr(g.PointAt(g.Domain.T0 + (g.Domain.T1 - g.Domain.T0) * u), a)
                             for a in "XYZ"), q) for u, q in zip(us, picks))
         if g is not None else math.inf)
check(done and g is not None and g.IsClosed and g.IsPeriodic and worst < 1e-9
      and "not implemented" not in msg,
      f"InterpCrv Close: a periodic curve through every pick ({worst:.1e}, {msg!r})")
segs = preview.segments("interpcrv", [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 4.0, 0.0)],
                        (0, 4, 0), TOP)
live = segs[0] if segs else []
check(len(segs) == 1 and all(min(math.dist(p, q) for p in live) < 0.05
                             for q in ((4, 0, 0), (4, 4, 0)))
      and math.dist(live[0], (0, 0, 0)) < 1e-9 and math.dist(live[-1], (0, 4, 0)) < 1e-9,
      "P18: the InterpCrv preview passes THROUGH the placed points and on to the cursor")

# --- P22 Rotate / P23 Rotate3D angle guides --------------------------------------------
print("-- P22 Rotate, P23 Rotate3D --")
segs = preview.segments("rotate", [["L"], (0.0, 0.0, 0.0)], (0, 4, 0), TOP)
check(len(segs) == 2 and all(abs(math.hypot(p[0], p[1]) - 4) < 1e-9 and p[2] == 0 for p in segs[0])
      and segs[1] == [(0.0, 0.0, 0.0), (0.0, 4.0, 0.0)],
      "P22: at the angle prompt a reference circle through the cursor + the line to it")
segs = preview.segments("rotate3d", [["L"], (0.0, 0.0, 0.0)], (4, 0, 0), TOP)
check(segs == [[(0.0, 0.0, 0.0), (4, 0, 0)]], "P23: a rubber axis from its start to the cursor")
segs = preview.segments("rotate3d", [["L"], (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)], (1, 5, 0), TOP)
ring = segs[1] if len(segs) == 3 else []
check(len(segs) == 3 and ring and all(abs(p[0]) < 1e-9 and abs(math.hypot(p[1], p[2]) - 5) < 1e-9
                                      for p in ring),
      "P23: at the angle prompt the circle about the axis, in the plane of its start")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((1, 1, 0), (4, 1, 0)), "L")
c, msg, done = drive("orient", ["0,0,0", "4,0,0"], ctx, select=["L"])
check(base(c.prompt) == "Target point 1 <Reference point 1>" and c.live_xform((-3, -3, 0)) is None,
      f"P20: 'Target point 1 <Reference point 1>', nothing moves there ({c.prompt!r})")

# --- P24 Scale / P30 Catenary / P31 Conic ---------------------------------------------
print("-- P24 Scale, P30 Catenary, P31 Conic --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((1, 1, 0), (4, 1, 0)), "L")
c, msg, done = drive("scale", [], ctx, select=["L"])
p0 = c.prompt
c.feed("0,0,0", ctx)
p1 = base(c.prompt)
segs = preview.segments("scale", c.args, (4, 4, 0), TOP)
check(p0.startswith("Base point. Press Enter for automatic. (") and p1 == "Scale factor or first reference point <1>"
      and segs == [[(0.0, 0.0, 0.0), (4, 4, 0)]] and c.live_xform((4, 4, 0)) is None,
      f"P24: prompts with <1>; the factor prompt draws base->cursor, no live copy ({p0!r}, {p1!r})")
msg, done = c.feed("2", ctx)
c2, _m, _d = drive("scale", ["0,0,0"], ctx, select=["L"])
check(done and base(c2.prompt) == "Scale factor or first reference point <2>",
      f"P24: a typed 2 completes and becomes the next default ({c2.prompt!r})")
msg, done = c2.feed("", ctx)
g = ctx.geom_by_name("L")
check(done and g is not None and g.PointAtEnd.DistanceTo(r3.Point3d(16, 4, 0)) < 1e-9,
      f"...and Enter takes it (scaled by 2 again: (16,4) end) ({msg!r})")
type(commands.COMMANDS["scale"])._sticky_factor.clear()
ctx = Ctx(TOP)
c, msg, done = drive("catenary", ["-3,0,0", "4,0,0"], ctx)
p2 = base(c.prompt)
msg, done = c.feed("", ctx)
check(p2 == "Catenary axis direction. Press Enter for default" and not done
      and base(c.prompt) == "Point on catenary" and c.args[2][1] > 0 and abs(c.args[2][0] - 0.5) < 1e-9
      and "PointCount=20" in c.prompt,
      f"P30: Enter at the axis prompt takes the in-plane default (+y side); PointCount 20 ({c.args})")
c, msg, done = drive("conic", ["-3,0,0"], ctx)
p1 = base(c.prompt)
c.feed("4,0,0", ctx)
p2 = base(c.prompt)
segs = preview.segments("conic", c.args, (0, 4, 0), TOP, cmd=c)
c.feed("0,4,0", ctx)
p3 = base(c.prompt)
msg, done = c.feed("", ctx)
check((p1, p2, p3) == ("End of conic", "Apex", "Curvature point or rho <0.500>")
      and segs == [[(-3.0, 0.0, 0.0), (0, 4, 0), (4.0, 0.0, 0.0)]] and done and ctx.added,
      f"P31: prompts; at 'Apex' two lines to the cursor; Enter takes rho 0.5 ({(p1, p2, p3)}, {msg!r})")

# --- P32 Copy: prompts and the placed copies -------------------------------------------
print("-- P32 Copy --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (1, 0, 0)), "L")
c, msg, done = drive("copy", [], ctx, select=["L"])
p0 = base(c.prompt)
c.feed("0,0,0", ctx)
p1 = base(c.prompt)
c.feed("0,2,0", ctx)
p2 = base(c.prompt)
segs = preview.segments("copy", c.args, (0, 4, 0), TOP, resolve=ctx.geom_by_name, cmd=c)
check((p0, p1, p2) == ("Point to copy from. Press Enter to use bounding box center",
                       "Point to copy to", "Point to copy to. Press Enter when done")
      and [(0.0, 0.0, 0.0), (0, 4, 0)] in segs
      and any(abs(s[0][1] - 2) < 1e-9 and abs(s[-1][0] - 1) < 1e-9 for s in segs),
      f"P32: prompts; the placed copy at y 2 stays drawn with the rubber line ({(p0, p1, p2)})")

# --- P34 Distance / P36-P38 extrusions / P39 HandleCurve / P40 Helix ------------------
print("-- P34 P36-P40 --")
c, msg, done = drive("distance", ["0,0,0"], Ctx(TOP))
check(base(c.prompt).rstrip(":") == "Second point for distance", f"P34: the second prompt ({c.prompt!r})")
ctx = Ctx(TOP)
ctx.add(r3.Circle(r3.Point3d(0, 0, 0), 2).ToNurbsCurve(), "C")
type(commands.COMMANDS["extrudecrv"])._sticky_values.clear()
c, msg, done = drive("extrudecrv", [], ctx, select=["C"])
p0 = c.prompt
msg, done = c.feed("3", ctx)
m = kernel.master_of(ctx.added[0]) if ctx.added else None
# MEASURED: P36's reference prompt had Solid=No. OURS (report 121): the app
# deliberately defaults Solid=Yes; test_report121_124.py pins this owner request.
# Keep the measured distance/default protocol, but assert the current app geometry.
check(base(p0) == "Extrusion distance <0>" and "Solid=Yes" in p0 and done
      and len(ctx.added) == 1 and m is not None
      and ob.face_types(m) == {"CYLINDER": 1, "PLANE": 2} and ob.is_airtight(m)
      and abs(ob.mass_properties(m)["volume"] - 12 * math.pi) < 1e-9,
      f"P36 / OURS report121: Solid=Yes; typed 3 completes an exact capped R2 cylinder "
      f"({p0!r}; {m is not None and ob.face_types(m)})")
c, msg, done = drive("extrudecrv", [], ctx, select=["C"])
p1 = base(c.prompt)
msg, done = c.feed("", ctx)
check(p1 == "Extrusion distance <3>" and done and len(ctx.added) == 2,
      f"P36: the default is sticky (<3>) and Enter takes it ({p1!r}, {msg!r})")
# Explicit No retains the measured open-tube behavior, not merely a prompt toggle.
ctx_no = Ctx(TOP)
ctx_no.add(r3.Circle(r3.Point3d(0, 0, 0), 2).ToNurbsCurve(), "C")
c, msg, done = drive("extrudecrv", ["Solid=No", "3"], ctx_no, select=["C"])
m_no = kernel.master_of(ctx_no.added[0]) if ctx_no.added else None
check(done and c.opt["Solid"] is False and len(ctx_no.added) == 1 and m_no is not None
      and ob.face_types(m_no) == {"CYLINDER": 1} and not ob.is_airtight(m_no)
      and abs(ob.mass_properties(m_no, solid=False)["area"] - 12 * math.pi) < 1e-9
      and ctx_no.geom_by_name("C") is not None,
      f"P36: toggled Solid=No completes the exact open R2 tube, input kept ({msg!r})")
# Help: Solid caps CLOSED planar profiles. An open line must remain a surface
# under either setting; Yes must not manufacture caps for an open boundary.
for solid in ("Yes", "No"):
    ctx_open = Ctx(TOP)
    ctx_open.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Profile")
    c, msg, done = drive("extrudecrv", [f"Solid={solid}", "3"], ctx_open, select=["Profile"])
    m_open = kernel.master_of(ctx_open.added[0]) if ctx_open.added else None
    check(done and len(ctx_open.added) == 1 and m_open is not None
          and ob.face_types(m_open) == {"PLANE": 1} and not ob.is_airtight(m_open)
          and abs(ob.mass_properties(m_open, solid=False)["area"] - 12) < 1e-9
          and ob.tight_bbox(m_open) == ((0.0, 0.0, 0.0), (4.0, 0.0, 3.0))
          and ctx_open.geom_by_name("Profile") is not None,
          f"P36: open profile with Solid={solid} completes a 4x3 uncapped surface ({msg!r})")
c, msg, done = drive("extrudecrvtapered", [], ctx, select=["C"])
check(base(c.prompt) == "Extrusion distance <1>" and "DraftAngle=5" in c.prompt,
      f"P37: ExtrudeCrvTapered first-use <1>, DraftAngle=5 ({c.prompt!r})")
c, msg, done = drive("extrudesrf", [], Ctx(TOP))
check(base(c.prompt) == "Select surfaces to Extrude" and "Solid=No" in c.prompt,
      f"P38: ExtrudeSrf prompt and Solid=No ({c.prompt!r})")
c, msg, done = drive("handlecurve", [], Ctx(TOP))
check(base(c.prompt) == "Curve point", f"P39: 'Curve point' ({c.prompt!r})")
c, msg, done = drive("helix", ["0,0,0", "0,0,5"], Ctx(TOP))
check(base(c.prompt).startswith("Radius and start point <"), f"P40: the radius prompt ({c.prompt!r})")

# --- P44 Move / P55 Spiral / P40 Helix lock / P45-P46 MoveEdge, MoveFace ---------------
print("-- P44 P55 P40 P45 P46 --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "L")
c, msg, done = drive("move", [], ctx, select=["L"])
check(base(c.prompt) == "Point to move from. Press Enter to use bounding box center",
      f"P44: Move's from prompt ({c.prompt!r})")
c, msg, done = drive("spiral", [], Ctx(TOP))
ps = [base(c.prompt)]
c.feed("0,0,0", ctx)
ps.append(base(c.prompt).rstrip(":"))
c.feed("0,0,5", ctx)
ps.append(base(c.prompt))
msg, done = c.feed("2", ctx)
segs = preview.segments("spiral", c.args, (4, 0, 0), TOP, cmd=c)
locked_r = max((math.hypot(p[0], p[1]) for s in segs for p in s), default=0)
stay = (not done and len(c.args) == 2 and base(c.prompt).startswith("First radius and start point"))
msg, done = c.feed("", ctx)
p3 = base(c.prompt)
msg, done = c.feed("3", ctx)
check(ps == ["Start of axis", "End of axis", "First radius and start point <1.000>"] and stay
      and abs(locked_r - 2) < 1e-6 and p3 == "Second radius <0.000>" and done,
      f"P55: Spiral prompts; typed first radius 2 STAYS locked (preview r {locked_r:.3f}), "
      f"Enter advances, typed second radius completes ({ps}, {p3!r}, {msg!r})")
c, msg, done = drive("helix", ["0,0,0", "0,0,5", "2"], Ctx(TOP))
segs = preview.segments("helix", c.args, (4, 0, 0), TOP, cmd=c)
r = max((math.hypot(p[0], p[1]) for s in segs for p in s), default=0)
check(not done and abs(r - 2) < 1e-6, f"P40: Helix's typed radius 2 locks the live coil ({r:.3f})")
_load("narwhal3d.solid_commands", "narwhal3d/solid_commands.py")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
c, msg, done = drive("moveface", ["2,2,3", "2,2,3"], ctx, select=["B"])
segs = preview.segments("moveface", c.args, (2, 2, 5), TOP, resolve=ctx.geom_by_name, cmd=c)
tops = [s for s in segs[1:] if all(abs(p[2] - 5) < 1e-9 for p in s)]
verticals = [s for s in segs[1:] if abs(s[0][2] - 0) < 1e-9 and abs(s[-1][2] - 5) < 1e-9
             or abs(s[-1][2] - 0) < 1e-9 and abs(s[0][2] - 5) < 1e-9]
check(base(c.prompt) == "To point" and len(tops) == 4 and len(verticals) == 4,
      f"P46: MoveFace 'To point'; the top face follows the cursor to z 5, sides stretch "
      f"({c.prompt!r}, {len(tops)} top, {len(verticals)} vertical)")
msg, done = c.feed("2,2,5", ctx)
check(done and "Moved face" in (msg or ""), f"...and the commit is the exact move ({msg!r})")

# --- R5 Fillet / Chamfer: option display and sticky values ------------------------------
print("-- R5 Fillet, Chamfer --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "A")
ctx.add(bridge.make_line((0, 0, 0), (0, 4, 0)), "B")
c, msg, done = drive("fillet", ["Radius=2"], ctx)
p = c.prompt
c2, _m, _d = drive("fillet", [], ctx)
check("ExtendArcsBy=Arc  ExtendOtherCurvesBy=Line  Dynamic=No" in p and "Radius=2" in c2.prompt,
      f"R5: Fillet shows the measured option values; Radius is sticky ({c2.prompt!r})")
type(commands.COMMANDS["fillet"])._sticky_radius[0] = 1.0
c, msg, done = drive("chamfer", [], ctx)
p0 = c.prompt
c.feed("Distances", ctx)
p1 = base(c.prompt)
c.feed("2", ctx)
p2 = base(c.prompt)
c.feed("3", ctx)
p3 = c.prompt
c2, _m, _d = drive("chamfer", [], ctx)
check("( Distances=1,1  Join=No" in p0 and p1 == "First chamfer distance <1.000>"
      and p2 == "Second chamfer distance <2.000>" and "Distances=2,3" in p3
      and "Distances=2,3" in c2.prompt,
      f"R5: Chamfer 'Distances=1,1', the measured sub-prompts, sticky 2,3 ({p1!r}, {p2!r}, {c2.prompt!r})")
type(commands.COMMANDS["chamfer"])._sticky_distances[:] = [1.0, 1.0]

# --- R5 Extend: boundary objects, or a typed length; then the curves ---------------------
print("-- R5 Extend --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 4, 0), (4, 4, 0)), "A")
ctx.add(bridge.make_line((6, 0, 0), (6, 8, 0)), "B")
ctx.add(bridge.make_arc((-1, 0, 0), (0, 1, 0), (1, 0, 0)), "C")
ctx.add(bridge.make_line((-5, -0.5, 0), (5, -0.5, 0)), "D")
c, msg, done = drive("extend", [], ctx)
p0 = c.prompt
c.feed("B", ctx)
c.feed("D", ctx)
c.feed("", ctx)
p1 = c.prompt
c.pick_points = {1: (3.5, 4.0, 0.0)}
c.feed("A", ctx)
p2 = c.prompt
c.pick_points = {1: (0.9, 0.3, 0.0)}
c.feed("C", ctx)
msg, done = c.feed("", ctx)
ends = sorted((round(g.PointAtStart.X, 9), round(g.PointAtStart.Y, 9), round(g.PointAtEnd.X, 9),
               round(g.PointAtEnd.Y, 9)) for g in ctx.added)
arc = next((g for g in ctx.added if not g.IsLinear()), None)
check(p0.startswith("Select boundary objects or enter extension length. Press Enter for dynamic "
                    "extend ( Type=Natural  Join=Merge")
      and base(p1) == "Select curve to extend"
      and base(p2) == "Select curve to extend. Press Enter when done" and done
      and (0.0, 4.0, 6.0, 4.0) in ends,
      f"R5: boundaries first, then curves; the line runs exactly to the boundary x=6 ({ends}; {msg!r})")
check(arc is not None and abs(arc.PointAtEnd.Y + 0.5) < 1e-9
      and abs(arc.PointAtEnd.X - math.sqrt(0.75)) < 1e-9 and abs(arc.PointAtStart.X + 1) < 1e-12,
      f"...the arc's picked end runs on its own circle exactly to the boundary y=-0.5 "
      f"({arc and (arc.PointAtEnd.X, arc.PointAtEnd.Y)})")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 4, 0), (4, 4, 0)), "A")
c, msg, done = drive("extend", ["2"], ctx)
p1 = c.prompt
c.pick_points = {1: (0.2, 4.0, 0.0)}
c.feed("A", ctx)
msg, done = c.feed("", ctx)
g = ctx.added[0] if ctx.added else None
check("ExtensionLength=2" in p1 and done and g is not None
      and abs(g.PointAtStart.X + 2) < 1e-9 and abs(g.PointAtEnd.X - 4) < 1e-9,
      f"R5: typed 2 sets ExtensionLength=2; the end nearest the pick (start) grows by 2 ({p1!r}, {msg!r})")
c, msg, done = drive("extend", [""], Ctx(TOP))
check(not done and "dynamic extend is not implemented" in (msg or ""), f"dynamic extend says so ({msg!r})")

# --- R5 Lines / Trim prompts -------------------------------------------------------------
print("-- R5 Lines, Trim --")
c, msg, done = drive("lines", [], Ctx(TOP))
ps = [base(c.prompt).rstrip(":")]
for pt in ("0,0,0", "1,0,0", "1,1,0"):
    c.feed(pt, ctx)
    ps.append(c.prompt)
check(ps[0] == "Start of line" and base(ps[1]) == "End of line"
      and base(ps[2]) == "End of line. Press Enter when done" and "Close" in ps[3] and "Close" not in ps[2],
      f"R5: Lines prompts; Close from the third point ({ps})")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "A")
ctx.add(bridge.make_line((2, -2, 0), (2, 2, 0)), "B")
c, msg, done = drive("trim", ["B", ""], ctx)
p1 = c.prompt
check(p1.startswith("Select object to trim, select pressing Shift to extend (")
      and "ExtendCuttingLines=No  ApparentIntersections=Yes" in p1,
      f"R5: Trim's measured trim prompt and option values ({p1!r})")

# --- R6 AddToGroup: objects first, then the group; a new name makes the group -----------
print("-- R6 AddToGroup --")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (1, 0, 0)), "L")
c, msg, done = drive("addtogroup", [], ctx, select=["L"])
p0 = base(c.prompt).rstrip(":")
msg, done = c.feed("Group1", ctx)
obj = next(o for o in ctx.model.file.Objects if o.Attributes.Name == "L")
gl = list(obj.Attributes.GetGroupList2() or [])
check(p0 == "Select group" and done and msg == 'Making a new group called "Group1".\nAdded object to group.'
      and len(gl) == 1 and ctx.model.file.Groups.FindIndex(gl[0]).Name == "Group1",
      f"R6: preselected -> 'Select group'; a new name makes the group and adds ({p0!r}, {msg!r})")
ctx.add(bridge.make_line((0, 2, 0), (1, 2, 0)), "M")
c, msg, done = drive("addtogroup", ["L"], ctx, select=["M"])
objm = next(o for o in ctx.model.file.Objects if o.Attributes.Name == "M")
check(done and list(objm.Attributes.GetGroupList2() or []) == gl,
      f"...picking a group member chooses that group ({msg!r})")

# --- R6 SelKeyValue / SelValue ---------------------------------------------------------
c, msg, done = drive("selkeyvalue", [], Ctx(TOP))
p0 = c.prompt
c.feed("k", Ctx(TOP))
p1 = c.prompt
msg, done = c.feed("v", Ctx(TOP))
check(p0 == "Enter text key:" and p1 == "Enter text value:" and done
      and msg == "No objects added to selection.",
      f"R6: SelKeyValue prompts and the no-match report ({p0!r}, {p1!r}, {msg!r})")

# --- research21: ProjectToCPlane / SetDocumentUserText prompts ------------------------
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 2), (1, 0, 3)), "L")
c, msg, done = drive("projecttocplane", [], ctx, select=["L"])
p1 = c.prompt
msg, done = c.feed("", ctx)
check(p1 == "Delete input objects? <No> ( No  Yes ):" and done and ctx.geom_by_name("L") is not None
      and ctx.added and abs(ctx.added[0].PointAtEnd.Z) < 1e-12,
      f"ProjectToCPlane asks 'Delete input objects? <No>'; Enter keeps the input ({p1!r}, {msg!r})")
c, msg, done = drive("setdocumentusertext", [], Ctx(TOP))
check(base(c.prompt).rstrip(":") == "Document text key", f"SetDocumentUserText key prompt ({c.prompt!r})")

# --- R7 setting commands: "<state>. New value ( ... ):", then the option ----------------
print("-- R7 New value prompts --")


class _FakeUi:
    def __init__(self):
        self.snap = False
        self.ortho = False

    def grid_snap_on(self):
        return self.snap

    def set_grid_snap(self, v):
        self.snap = bool(v)

    def ortho_on(self):
        return self.ortho

    def set_ortho(self, v):
        self.ortho = bool(v)


class _FakeSnap:
    disabled = False
    project = False
    snap_to_locked = True


_ui, _snap = _FakeUi(), _FakeSnap()
_saved = (commands._ui_module, commands._snap_settings)
commands._ui_module = lambda: _ui
commands._snap_settings = lambda: _snap
try:
    rows = []
    for key, want in (("setsnap", "Grid snap is off. New value ( On  Off  Toggle ):"),
                      ("setortho", "Ortho is off. New value ( On  Off  Toggle ):"),
                      ("disableosnap", "Osnaps are enabled. New value ( Enable  Disable  Toggle ):"),
                      ("projectosnap",
                       "Osnap projection is disabled. New value ( Enable  Disable  Toggle ):"),
                      ("snaptolocked",
                       "Snap to locked objects enabled. New value ( Enable  Disable  Toggle ):")):
        c, msg, done = drive(key, [], Ctx(TOP))
        rows.append((key, not done and c.prompt == want, c.prompt))
    check(all(ok for _k, ok, _p in rows), f"R7: the measured state prompts ({rows})")
    c, msg, done = drive("setsnap", ["Toggle"], Ctx(TOP))
    m2, d2 = None, None
    c2, m2, d2 = drive("snaptolocked", ["Toggle"], Ctx(TOP))
    c3, m3, d3 = drive("disableosnap", [""], Ctx(TOP))
    check(done and msg == "Grid snap is on." and _ui.snap and d2
          and m2 == "Snapping to locked objects is now disabled." and d3 and not _snap.disabled,
          f"R7: Toggle reports as measured; Enter keeps the state ({msg!r}, {m2!r})")
finally:
    commands._ui_module, commands._snap_settings = _saved

# --- R7 SetUserText / CPlane prompts ----------------------------------------------------
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (1, 0, 0)), "L")
c, msg, done = drive("setusertext", [], ctx, select=["L"])
p1 = c.prompt
c.feed("k", ctx)
p2 = c.prompt
check(p1 == "Text key ( AttachTo=Attributes ):" and p2 == "Text ( AttachTo=Attributes ):",
      f"R7: SetUserText prompts ({p1!r}, {p2!r})")


class _PlaneCtx(Ctx):
    def __init__(self):
        super().__init__(TOP)
        self.set_to = None

    def set_active_cplane(self, plane):
        self.set_to = plane
        self.plane = plane
        return True


pctx = _PlaneCtx()
c, msg, done = drive("cplane", [], pctx)
p0 = c.prompt
c.feed("World", pctx)
p1 = c.prompt
msg, done = c.feed("Front", pctx)
c2, m2, d2 = drive("cplane", ["2,2,0"], _PlaneCtx())
check(p0 == ("CPlane origin <0.000,0.000,0.000> ( All=No  Curve  Elevation  Gumball  Object  "
             "Rotate  Surface  Through  View  World  3Point  Undo  Redo ):")
      and p1 == "Choose world CPlane ( Back  Bottom  Front  Left  Right  Top ):"
      and done and pctx.set_to is cplane.WORLD_CPLANES["Front"] and d2,
      f"R7: CPlane prompt, World sub-prompt; World and an origin click end it ({p0!r}, {p1!r})")

# --- R8 Osnap / GetUserText / GumballAlignment prompts --------------------------------
print("-- R8 --")
_osnap = _load("narwhal3d.osnap", "narwhal3d/osnap.py")
_cls = next(v for v in vars(_osnap).values()
            if isinstance(v, type) and hasattr(v, "unimplemented_enabled"))
_settings = _cls(enabled=())
_saved = commands._snap_settings
commands._snap_settings = lambda: _settings
try:
    c, msg, done = drive("osnap", [], Ctx(TOP))
    p0 = c.prompt
    c.feed("Endpoint", Ctx(TOP))
    p1 = c.prompt
    on_end = _osnap.END in _settings.enabled
    msg, done = c.feed("", Ctx(TOP))
finally:
    commands._snap_settings = _saved
check(p0 == ("Osnap ( Endpoint=Off  Near=Off  Point=Off  Midpoint=Off  Center=Off  "
             "Intersection=Off  Perpendicular=Off  Tangent=Off  Quadrant=Off  Knot=Off  "
             "Vertex=Off  None ):") and "Endpoint=On" in p1 and on_end and done,
      f"R8: Osnap's command-line prompt; Endpoint toggles; Enter ends ({p0!r}, {p1!r})")
ctx = Ctx(TOP)
ctx.add(bridge.make_line((0, 0, 0), (1, 0, 0)), "L")
c, msg, done = drive("getusertext", [], ctx, select=["L"])
c2, _m, _d = drive("getdocumentusertext", [], Ctx(TOP))
check(c.prompt == "Text key <All keys>:" and c2.prompt == "Document text key <All document keys>:",
      f"R8: GetUserText / GetDocumentUserText prompts ({c.prompt!r}, {c2.prompt!r})")

# --- R9 Paste with nothing on the clipboard ---------------------------------------------
# An authoritative empty test input must not read or alter the user's desktop clipboard.
paste_ctx = Ctx(TOP)
paste_ctx.get_clipboard_text = lambda: ""
c, msg, done = drive("paste", [], paste_ctx)
check(done and msg == "Unable to paste, no objects, images or text on the clipboard.",
      f"R9: Paste with an empty clipboard reports it ({msg!r})")

# --- R5 Parabola3Pt: the axis runs parallel to end -> direction (exact control points) ----
ctx = Ctx(TOP)
c, msg, done = drive("parabola3pt", [], ctx)
ps = [base(c.prompt).rstrip(":")]
for pt in ("-4,4,0", "0,2,0", "4,4,0"):
    c.feed(pt, ctx)
    ps.append(base(c.prompt).rstrip(":"))
msg, done = c.feed("0,7,0", ctx)
g = ctx.added[0] if ctx.added else None
mid = g.Points[1] if g is not None else None
check(ps == ["Parabola start", "Point on parabola", "Parabola end", "Parabola direction"] and done
      and g.Degree == 2 and not g.IsRational and abs(mid.X - 9.6) < 1e-9 and abs(mid.Y + 3.2) < 1e-9,
      f"R5: Parabola3Pt prompts; direction (0,7) gives the measured middle control point "
      f"(9.6,-3.2) ({ps}, {mid and (mid.X, mid.Y)})")

# --- P46 MoveFace: a selected face answers "Select faces" at once ----------------------
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
top_id = bridge.faces_at_points(ctx.geom_by_name("B"), [(2, 2, 3)])[0]
ctx.selected_subobjects = lambda: [("B", "Face", top_id)]
ctx.clear_subobject_selection = lambda: None
c, msg, done = drive("moveface", [], ctx)
p0 = base(c.prompt)
msg, done = c.feed("2,2,3", ctx)
msg, done = c.feed("2,2,5", ctx)
m = kernel.master_of(ctx.geom_by_name("B")) if ctx.geom_by_name("B") is not None else None
lo, hi = ob.tight_bbox(m) if m is not None else ((0,) * 3, (0,) * 3)
check(p0 == "Point to move from" and done and abs(hi[2] - 5) < 1e-9,
      f"P46: a selected face skips the part prompt; the move is exact ({p0!r}, {msg!r}, top z {hi[2]})")
del ctx.selected_subobjects

# --- Shell / MoveFace: a viewport click answers the face prompt with the FACE under the
# cursor (`pick_face`), not the CPlane point behind it. Looking down on a box resting on
# the CPlane, that point is on the BOTTOM face; from any other view it is off the solid.
print("-- Shell / MoveFace face clicks --")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
ctx.add(bridge.make_box((10, 0, 0), (4, 4, 0), 3), "Other")
box = ctx.geom_by_name("B")
# (`faces_at_points` returns its indices sorted, so one call per pick.)
top_id = bridge.faces_at_points(box, [(2, 2, 3)])[0]
bottom_id = bridge.faces_at_points(box, [(2, 2, 0)])[0]
c0, _m, _d = drive("shell", [], ctx)
before = c0.pick_face("B", top_id, (2, 2, 3), ctx) if c0.step_index == 0 else "skipped step 0"
c, msg, done = drive("shell", [], ctx, select=["B"])
c2, _m, _d = drive("shell", [], ctx, select=["B"])
c2.feed("Thickness", ctx)
pending = c2.pick_face("B", top_id, (2, 2, 3), ctx)
answers = [c.pick_face("B", top_id, (2, 2, 3), ctx),
           c.pick_face("B", top_id, (2, 2, 3), ctx),
           c.pick_face("Other", 1, (12, 2, 3), ctx),
           c.pick_face(None, None, None, ctx)]
check(before is None and pending is None and c.step_index == 1
      and answers[0] == ("1 face(s) picked -- press Enter when done", False)
      and "already picked" in answers[1][0] and "belongs to Other" in answers[2][0]
      and "no face" in answers[3][0] and c._faces == [top_id],
      f"Shell: a face click is taken only at the face prompt, once, from the solid being "
      f"shelled ({answers})")
msg, done = c.feed("", ctx)
out = ctx.added[-1] if ctx.added else None
rec = kernel.recipe_of(out) if out is not None else None
check(done and rec is not None and rec.get("remove") == [top_id] and top_id != bottom_id
      and ctx.geom_by_name("B") is None,
      f"...and Enter shells with the TOP face removed, not the bottom ({msg!r}, "
      f"{rec and rec.get('remove')}, top {top_id}, bottom {bottom_id})")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
c, msg, done = drive("shell", [], ctx, select=["B"])
for f in range(1, 7):
    c.pick_face("B", f, None, ctx)
msg, done = c.feed("", ctx)
check(done and "leave at least one face unselected" in msg and not ctx.added,
      f"Shell: removing every face is refused, as the prompt says ({msg!r})")
try:
    bridge.shell_occt(ctx.geom_by_name("B"), [], 1.0, remove_faces=[7])
    bad = "no error"
except ValueError as e:
    bad = str(e)
check("not a face of the solid" in bad, f"shell_occt refuses a face index the solid lacks ({bad!r})")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
c, msg, done = drive("moveface", [], ctx, select=["B"])
p0 = base(c.prompt)
answer = c.pick_face("B", top_id, (2, 2, 3), ctx)
p1 = base(c.prompt)
late = c.pick_face("B", bottom_id, (2, 2, 0), ctx)
msg, done = c.feed("2,2,3", ctx)
msg, done = c.feed("2,2,5", ctx)
m = kernel.master_of(ctx.geom_by_name("B")) if ctx.geom_by_name("B") is not None else None
lo, hi = ob.tight_bbox(m) if m is not None else ((9,) * 3, (0,) * 3)
check(p0 == "Select a face" and answer == (f"face {top_id} of B selected", False)
      and c.args[:2] == ["B", None]
      and p1 == "Point to move from" and late is None and done
      and abs(hi[2] - 5) < 1e-9 and abs(lo[2]) < 1e-9,
      f"MoveFace: a face click picks the top face and the move is exact ({p0!r}, {answer}, "
      f"{p1!r}, z {lo[2]}..{hi[2]})")
c = type(commands.COMMANDS["moveedge"])()
check(c.pick_face("B", top_id, None, ctx) is None, "MoveEdge ignores face clicks")
c, msg, done = drive("moveedge", [], Ctx(TOP))
check(base(c.prompt) == "Select edges", f"P45: MoveEdge's first prompt ({c.prompt!r})")

# --- MoveEdge: a viewport click answers the edge prompts with the EDGE under the cursor
# (`pick_edge`, given a point on that edge), not the CPlane point behind it.
print("-- MoveEdge edge clicks --")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
ctx.add(bridge.make_box((10, 0, 0), (4, 4, 0), 3), "Other")
changes = []
ctx.note_change = lambda label=None: changes.append(label)
c, msg, done = drive("moveedge", [], ctx)
p0 = base(c.prompt)
answer = c.pick_edge("B", 99, (2, 0, 3), "plain", ctx)
p2 = base(c.prompt)
expected = c._resolve(ctx.geom_by_name("B"), (2, 0, 3), ctx)
args_after_pick = list(c.args)
later = c.pick_edge("B", 99, (2, 0, 3), "plain", ctx)
no_change_yet = not changes
msg, done = c.feed("2,0,3", ctx)
msg, done = c.feed("2,0,5", ctx)
m = kernel.master_of(ctx.geom_by_name("B")) if ctx.geom_by_name("B") is not None else None
lo, hi = ob.tight_bbox(m) if m is not None else ((9,) * 3, (0,) * 3)
check(args_after_pick == ["B", None] and c._sub_id == expected and c.args == ["B", None, (2, 0, 3), (2, 0, 5)],
      f"MoveEdge: the edge pick stores ['B', None] (no sampled point), the exact edge id, "
      f"and the typed moves fill args [from, to] ({args_after_pick}, {c.args}, {c._sub_id})")
moved = ctx.geom_by_name("B")
rec = kernel.recipe_of(moved)
rebuilt = ob.from_recipe(rec) if rec else None
rebuilt_top = ob.tight_bbox(rebuilt)[1][2] if rebuilt is not None else None
check(no_change_yet and len(changes) == 1 and changes[0] == "MoveEdge" and m is not None
      and ob.is_airtight(m) and abs(hi[2] - 5) < 1e-9
      and rebuilt_top is not None and abs(rebuilt_top - 5) < 1e-9,
      f"MoveEdge: the pick changes nothing; the move is ONE undo step, exact, airtight and "
      f"its recipe rebuilds the same top ({changes}, airtight {m is not None and ob.is_airtight(m)}, "
      f"top z {hi[2]}, recipe top z {rebuilt_top})")
check(p0 == "Select edges" and answer == (f"edge {expected} of B selected", False)
      and p2 == "Point to move from" and later is None and done and abs(hi[2] - 5) < 1e-9,
      f"MoveEdge: a click on an edge at 'Select edges' picks the solid AND that edge, and "
      f"the move is exact ({answer}, {p2!r}, {msg!r}, top z {hi[2]})")
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
ctx.add(bridge.make_box((10, 0, 0), (4, 4, 0), 3), "Other")
c, msg, done = drive("moveedge", [], ctx, select=["B"])
answers = [c.pick_edge("Other", 1, (12, 0, 3), "plain", ctx),
           c.pick_edge("B", 1, (2, 2, 3), "plain", ctx),
           c.pick_edge("B", 1, (2, 0, 3), "shift", ctx)]
c.feed("DirectionConstraint", ctx)
pending = c.pick_edge("B", 1, (2, 0, 3), "plain", ctx)
check(c.step_index == 1 and "belongs to Other" in answers[0][0] and not answers[0][1]
      and "not on an edge" in answers[1][0] and not answers[1][1]
      and answers[2] is None and pending is None,
      f"MoveEdge: another solid's edge and a face point are refused at 'Select an edge'; "
      f"Shift and a pending option decline ({answers}, {pending})")
check(not hasattr(commands.COMMANDS["moveface"], "pick_edge"),
      "MoveFace offers no edge picker (its clicks stay object picks)")
# The stored args never hold the viewport point: a different point on the SAME edge picks the
# same exact edge id, and "Select an edge" stores [owner, None] too.
ctx = Ctx(TOP)
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 3), "B")
c, msg, done = drive("moveedge", [], ctx, select=["B"])
c.pick_edge("B", 1, (2, 0, 3), "plain", ctx)
ids = [c._sub_id]
args_one = list(c.args)
c2, _m, _d = drive("moveedge", [], ctx, select=["B"])
c2.pick_edge("B", 1, (3.5, 0, 3), "plain", ctx)
check(c2.args == ["B", None] and c2._sub_id == ids[0] == c._resolve(ctx.geom_by_name("B"), (2, 0, 3), ctx)
      and args_one == ["B", None] and c2.step_index == 2,
      f"MoveEdge 'Select an edge': args stay ['B', None]; another point on the same edge picks "
      f"edge {ids[0]} ({c2.args}, {c2._sub_id})")

check(all(isinstance(commands.COMMANDS[k], commands._NewValueMixin)
          for k in ("setsnap", "setortho", "disableosnap", "projectosnap", "snaptolocked",
                    "gumball")),
      "the setting commands share the New value prompt (their menu rows answer Toggle)")

# --- P52 Pipe: Esc at the last prompt builds the pipe ---------------------------------------
ctx = Ctx()
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Rail")
c, msg, done = drive("pipe", ["", ""], ctx, select=["Rail"])
check(c.escape_commits() and not type(commands.COMMANDS["line"])().escape_commits(),
      "P52: Esc at 'Point for next radius' commits (other commands still cancel on Esc)")

# --- P16 Polygon prompt; workspace buttons' Mode=Circumscribed ---------------------------
c, msg, done = drive("polygon", [], Ctx(TOP))
p0 = c.prompt
c.feed("Mode=Circumscribed", Ctx(TOP))
check(p0.endswith("( NumSides=5  Mode=Inscribed  Edge  Star  Vertical  AroundCurve ):")
      and "Mode=Circumscribed" in c.prompt and c.prompt.startswith("Center of circumscribed polygon"),
      f"P16: Polygon's option list; Mode=Circumscribed switches the construction ({p0!r}, {c.prompt!r})")

# --- R10 Line Perpendicular / Tangent to a picked curve ----------------------------------
ctx = Ctx(TOP)
ctx.add(bridge.make_line((-5, -4, 0), (5, -4, 0)), "F")
c, msg, done = drive("line", ["Perpendicular", "2,2,0", "3,-4,0"], ctx)
g = ctx.added[0] if ctx.added else None
check(done and g is not None and g.PointAtEnd.DistanceTo(r3.Point3d(2, -4, 0)) < 1e-9,
      f"R10: Line Perpendicular from (2,2) to the line y=-4 ends at its foot (2,-4) ({msg!r})")
ctx = Ctx(TOP)
ctx.add(r3.Circle(r3.Point3d(0, 0, 0), 3).ToNurbsCurve(), "C")
c, msg, done = drive("line", ["Tangent", "6,0,0", "1.5,2.6,0"], ctx)
g = ctx.added[0] if ctx.added else None
check(done and g is not None and g.PointAtEnd.DistanceTo(r3.Point3d(1.5, 1.5 * math.sqrt(3), 0)) < 1e-7,
      f"R10: Line Tangent from (6,0) to the r3 circle ends at (1.5, 2.598) "
      f"({g and (g.PointAtEnd.X, g.PointAtEnd.Y)})")

# --- R10 Circle Vertical / Ellipse Diameter, FromFoci / Rectangle, Plane Vertical / Star ---
print("-- R10 variants --")
ctx = Ctx(TOP)
c, msg, done = drive("circle", ["Vertical", "0,0,0", "3"], ctx)
stay = not done and c.step_index == 1
msg, done = c.feed("0,4,0", ctx)
g = ctx.added[0] if ctx.added else None
ok_plane = g is not None and all(abs(g.PointAt(t).X) < 1e-9 for t in
                                 [g.Domain.T0 + (g.Domain.T1 - g.Domain.T0) * k / 7 for k in range(8)])
zmax = max(g.PointAt(g.Domain.T0 + (g.Domain.T1 - g.Domain.T0) * k / 64).Z for k in range(65)) if g else 0
check(stay and done and ok_plane and abs(zmax - 3) < 1e-6,
      f"R10: Circle Vertical -- typed 3 stays; the pick at (0,4) sets the plane (YZ), radius 3 ({msg!r})")
ctx = Ctx(TOP)
c, msg, done = drive("ellipse", ["Diameter", "-4,0,0", "4,0,0", "0,2,0"], ctx)
check(done and "a=4" in msg and "b=2" in msg, f"R10: Ellipse Diameter -- radii 4 and 2 ({msg!r})")
c, msg, done = drive("ellipse", ["FromFoci", "-1.5,0,0", "1.5,0,0", "0,2,0"], ctx)
check(done and "b=2" in msg and "a=2.5" in msg, f"R10: Ellipse FromFoci -- a = 2.5, b = 2 ({msg!r})")
ctx = Ctx(TOP)
c, msg, done = drive("rectangle", ["Vertical", "-4,0,0", "4,0,0", "3"], ctx)
p3 = base(c.prompt)
msg, done = c.feed("0,0,1", ctx)
g = ctx.added[0] if ctx.added else None
zs = sorted({round(g.Point(i).Z, 9) for i in range(g.PointCount)}) if g is not None else []
check(p3 == "Choose rectangle" and done and zs == [0.0, 3.0] and abs(g.GetLength() - 22) < 1e-9
      if g is not None and hasattr(g, "GetLength") else (p3 == "Choose rectangle" and done and zs == [0.0, 3.0]),
      f"R10: Rectangle Vertical -- edge, typed height 3, the side pick: 8 x 3 in XZ ({p3!r}, {zs})")
c, msg, done = drive("plane", ["Vertical", "-4,0,0", "4,0,0", "3", "0,0,-1"], Ctx(TOP))
check(done and "area 24" in (msg or ""), f"R10: Plane Vertical -- an 8 x 3 vertical surface ({msg!r})")
ctx = Ctx(TOP)
c, msg, done = drive("polygon", ["Star", "0,0,0", "4,0,0", "1.5"], ctx)
g = ctx.added[0] if ctx.added else None
radii = sorted({round(math.hypot(g.Point(i).X, g.Point(i).Y), 9) for i in range(g.PointCount)}) if g else []
check(done and g is not None and g.PointCount == 11 and g.IsClosed and radii == [1.5, 4.0],
      f"R10: Polygon Star -- 10 alternating points, radii 4 and 1.5 ({radii}; {msg!r})")

c, msg, done = drive("circle", ["Vertical", "0,0,0"], Ctx(TOP))
segs = preview.segments("circle", c.args, (0, 3, 0), TOP, cmd=c)
check(segs and all(abs(p[0]) < 1e-9 for p in segs[0]) and max(p[2] for p in segs[0]) > 2.99,
      "R10: Circle Vertical previews a circle in the vertical plane through the cursor")
c, msg, done = drive("polygon", ["Star", "0,0,0", "4,0,0"], Ctx(TOP))
segs = preview.segments("polygon", c.args, (1.5, 0, 0), TOP, cmd=c)
check(segs and len(segs[0]) == 11, "R10: Polygon Star previews the star with the cursor's inner radius")

c, msg, done = drive("plane", ["3Point", "0,0,0", "4,0,0", "0,3,0"], Ctx(TOP))
check(done and "area 12" in (msg or ""), f"Plane 3Point builds the 4 x 3 surface ({msg!r})")
c, msg, done = drive("circle", ["Vertical", "Diameter", "0,0,0", "4", "0,5,0"], Ctx(TOP))
check(done and "r=2" in (msg or ""), f"Circle Vertical + Diameter: a typed 4 is a diameter ({msg!r})")
c, msg, done = drive("line", ["Perpendicular", "Point"], Ctx(TOP))
check(not done and base(c.prompt) == "Start of line", f"Line Perpendicular's Point route ({c.prompt!r})")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
