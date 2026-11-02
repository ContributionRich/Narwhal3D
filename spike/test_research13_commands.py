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

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
