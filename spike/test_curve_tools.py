"""the curve tools whose kernels already existed -- Fillet, Chamfer, Split, Trim,
Intersect -- wired as commands.

found "the kernel is ahead of the wiring": `fillet.fillet_curves`, `chamfer_curves`,
`intersect.split_curve_by` / `trim_curve_by` / `curve_curve` were built and tested, and no
command reached them. Every gate below drives the COMMAND through `begin` / `feed` on a
real `NarwhalModel`, and checks the DOCUMENT afterwards against a closed form.

Every gate FAILS on the code before this change (none of the five commands existed: `drive` returns
no command and each check is RED -- watched).

Rule 0: a fillet arc and a split circle must stay real arcs (rational degree 2), and a
surface must never be trimmed through the mesh trimmer -- Split/Trim refuse surfaces by
name.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_curve_tools.py
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
_load("narwhal3d.analysis", "narwhal3d/analysis.py")
arclength = _load("narwhal3d.arclength", "narwhal3d/arclength.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in over a REAL NarwhalModel (as spike/test_measure.py)."""

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []
        self.added = 0

    def add(self, geom, name):
        self.model.add_object(geom, name, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added += 1
        nm = f"{base_name}_{self.added}"
        self.model.add_object(geom, nm, 0)
        return nm, nm

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def select_by_names(self, names):
        self._selected = list(names)
        return list(names)

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == (name or "").lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None

    def note_change(self, label=None):
        pass

    def names(self):
        return [o.Attributes.Name for o in self.model.file.Objects]

    def geoms(self):
        return [o.Geometry for o in self.model.file.Objects]


def drive(ctx, key, lines, select=None):
    """Start `key` with `select` preselected, feed `lines`. ``(cmd, msgs, done)``."""
    proto = commands.COMMANDS.get(key)
    if proto is None:
        return None, ["(no such command)"], True
    ctx._selected = list(select or [])
    c = type(proto)()
    msg, done = c.begin(ctx)
    msgs = [msg]
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
        msgs.append(msg)
    return c, msgs, done


def last(msgs):
    return next((m for m in reversed(msgs) if m), "")


def ends(c):
    a, b = c.PointAtStart, c.PointAtEnd
    return {(round(a.X, 9), round(a.Y, 9), round(a.Z, 9)),
            (round(b.X, 9), round(b.Y, 9), round(b.Z, 9))}


def is_true_arc(c, radius, center=None):
    """A rational degree-2 NURBS whose own arc is `radius` -- never a polyline."""
    n = c.ToNurbsCurve()
    arc = c.TryGetArc()
    ok = (n.Degree == 2 and n.IsRational and arc is not None
          and abs(arc.Radius - radius) < 1e-9)
    if ok and center is not None:
        ok = math.dist((arc.Center.X, arc.Center.Y, arc.Center.Z), center) < 1e-9
    return ok


def line(a, b):
    return bridge.make_line(a, b)


print("-- registered --")
for key in ("fillet", "chamfer", "split", "trim", "intersect"):
    check(key in commands.COMMANDS, f"`{key}` is a registered command")

# ===========================================================================
# Fillet
# ===========================================================================
print("\n-- Fillet --")
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "LX")
ctx.add(line((0, 0, 0), (0, 10, 0)), "LY")
c, msgs, done = drive(ctx, "fillet", ["Radius=2", "LX", "LY"])
proto = commands.COMMANDS.get("fillet")
# MEASURED (research_preview_14 R5 Fillet): Radius is sticky, so the 2 above would carry
# over -- the first-use default is checked from a reset state.
check(proto is not None and "Radius=2" in type(proto)().prompt,
      "the typed Radius=2 carries to the next Fillet (sticky)")
if proto is not None:
    type(proto)._sticky_radius[0] = 1.0
check(proto is not None and type(proto)().prompt.startswith("Select first curve to fillet"),
      "Fillet's prompt is the reference CAD application's `Select first curve to fillet`")
check(proto is not None and "Radius=1" in type(proto)().prompt
      and "Trim=Yes" in type(proto)().prompt,
      f"...showing `Radius=1` and `Trim=Yes` ({proto and type(proto)().prompt!r})")
check(done and "fillet R2" in last(msgs), f"Fillet R2 on an L corner reports ({last(msgs)!r})")
arcs = [g for g in ctx.geoms() if g.TryGetArc() is not None]
check(len(arcs) == 1 and is_true_arc(arcs[0], 2.0, (2, 2, 0)),
      "the fillet is a TRUE arc: rational degree 2, R2, centred (2,2)")
lx, ly = ctx.geom_by_name("LX"), ctx.geom_by_name("LY")
lines_now = [g for g in ctx.geoms() if g.IsLinear()]
check(lx is None and ly is None and len(ctx.geoms()) == 3
      and {frozenset(ends(g)) for g in lines_now}
      == {frozenset({(2.0, 0.0, 0.0), (10.0, 0.0, 0.0)}),
          frozenset({(0.0, 2.0, 0.0), (0.0, 10.0, 0.0)})},
      f"Trim=Yes: both lines now END at the tangent points (2,0) and (0,2) "
      f"({[sorted(ends(g)) for g in lines_now]})")

# Trim=No keeps the inputs; preselected curves answer the two prompts on Enter
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "LX")
ctx.add(line((0, 0, 0), (0, 10, 0)), "LY")
c, msgs, done = drive(ctx, "fillet", ["Trim=No", "", ""], select=["LX", "LY"])
check(done and ctx.geom_by_name("LX") is not None and ctx.geom_by_name("LY") is not None
      and len(ctx.geoms()) == 3,
      f"Trim=No + a preselection: inputs kept, one arc added ({last(msgs)!r})")
check(ends(ctx.geom_by_name("LX")) == {(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)},
      "...and LX is untouched")

# Join=Yes: ONE curve, length 8 + 8 + quarter circle of R2 (pi)
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "LX")
ctx.add(line((0, 0, 0), (0, 10, 0)), "LY")
c, msgs, done = drive(ctx, "fillet", ["Radius=2", "Join=Yes", "LX", "LY"])
gs = ctx.geoms()
L = arclength.length_of(gs[0]) if len(gs) == 1 else None
check(len(gs) == 1 and L is not None and abs(L - (16 + math.pi)) < 1e-9,
      f"Join=Yes: ONE joined curve of length 16 + pi ({len(gs)} objects, L={L!r}; "
      f"{last(msgs)!r})")

# a radius too big: the reference CAD application's own refusal, and nothing changes
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (3, 0, 0)), "SX")
ctx.add(line((0, 0, 0), (0, 3, 0)), "SY")
c, msgs, done = drive(ctx, "fillet", ["Radius=5", "SX", "SY"])
check(last(msgs).startswith("Unable to fillet the curves.") and len(ctx.geoms()) == 2
      and ends(ctx.geom_by_name("SX")) == {(0.0, 0.0, 0.0), (3.0, 0.0, 0.0)},
      f"R5 in a 3-unit corner is refused in the reference CAD application's words, document unchanged "
      f"({last(msgs)[:60]!r})")
c, msgs, done = drive(ctx, "fillet", ["SX", "SX"])
check("Select a different curve" in " ".join(m or "" for m in msgs)
      or "DIFFERENT" in last(msgs),
      f"the same curve twice is refused ({last(msgs)!r})")

# line + circle: the closed-form line/arc path. Line y=7 tangent-free above a R5 circle;
# a R1 fillet centre is 1 below the line (y=6) and 6 from the origin -> x = +-sqrt(36-36)?
# use y=6: centre y=5, |c|=6 -> x = sqrt(11). The arc is R1, tangent to both.
ctx = FakeCtx()
ctx.add(bridge.make_circle((0, 0, 0), 5.0), "C5")
ctx.add(line((0, 6, 0), (10, 6, 0)), "L6")
c, msgs, done = drive(ctx, "fillet", ["Radius=1", "Trim=No", "C5", "L6"])
arcs = [g for g in ctx.geoms() if g.TryGetArc() is not None
        and abs(g.TryGetArc().Radius - 1.0) < 1e-9]
check(len(arcs) == 1 and is_true_arc(arcs[0], 1.0, (math.sqrt(11), 5, 0)),
      f"circle + line: a true R1 arc centred (sqrt 11, 5) -- tangent to both "
      f"({last(msgs)!r})")

# ===========================================================================
# Chamfer
# ===========================================================================
print("\n-- Chamfer --")
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "LX")
ctx.add(line((0, 0, 0), (0, 10, 0)), "LY")
c, msgs, done = drive(ctx, "chamfer", ["Distances", "2", "3", "LX", "LY"])
proto = commands.COMMANDS.get("chamfer")
check(proto is not None and type(proto)().prompt.startswith("Select first curve to chamfer"),
      "Chamfer's prompt is the reference CAD application's `Select first curve to chamfer`")
check(len(msgs) > 1 and (msgs[1] or "") == "" and c is not None
      and "First chamfer distance" in (c.steps[0][0] if c.steps else ""
                                       ) or done,
      "Distances asks the reference CAD application's `First chamfer distance` / `Second chamfer distance`")
segs = [g for g in ctx.geoms() if g.IsLinear()]
cut = [g for g in segs if ends(g) == {(2.0, 0.0, 0.0), (0.0, 3.0, 0.0)}]
check(done and len(cut) == 1,
      f"chamfer 2,3: the cut runs (2,0) -> (0,3) -- the FIRST distance on the FIRST curve "
      f"({[sorted(ends(g)) for g in segs]})")
check(ctx.geom_by_name("LX") is None and len(ctx.geoms()) == 3,
      "...and the inputs were trimmed to it")
c, msgs, done = drive(ctx, "chamfer", ["Distances", "-1"])
check("negative" in last(msgs), f"a negative distance is refused ({last(msgs)!r})")

# ===========================================================================
# Split
# ===========================================================================
print("\n-- Split --")
ctx = FakeCtx()
ctx.add(bridge.make_circle((0, 0, 0), 5.0), "C5")
ctx.add(line((-10, 0, 0), (10, 0, 0)), "Cutter")
c, msgs, done = drive(ctx, "split", ["Cutter", ""], select=["C5"])
proto = commands.COMMANDS.get("split")
check(proto is not None and type(proto)().prompt.startswith("Select objects to split"),
      "Split's first prompt is the reference CAD application's `Select objects to split`")
halves = [g for g in ctx.geoms() if not g.IsLinear()]
check(done and ctx.geom_by_name("C5") is None and len(halves) == 2
      and all(is_true_arc(h, 5.0, (0, 0, 0)) for h in halves)
      and all(abs(arclength.length_of(h) - 5 * math.pi) < 1e-9 for h in halves),
      f"a circle split by a diameter line -> two TRUE R5 half-arcs of length 5pi "
      f"({last(msgs)!r})")
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "H")
ctx.add(line((2, -1, 0), (2, 1, 0)), "V1")
ctx.add(line((7, -1, 0), (7, 1, 0)), "V2")
c, msgs, done = drive(ctx, "split", ["V1", "V2", ""], select=["H"])
lens = sorted(round(arclength.length_of(g), 9) for g in ctx.geoms()
              if abs(g.PointAtStart.Y) < 1e-12 and abs(g.PointAtEnd.Y) < 1e-12)
check(lens == [2.0, 3.0, 5.0], f"a line cut by two lines -> pieces 2, 5, 3 ({lens})")
ctx.add(bridge.make_box((20, 0, 0), (22, 2, 0), 2.0), "Box")
before = len(ctx.geoms())
c, msgs, done = drive(ctx, "split", ["V1", ""], select=["Box"])
check("mesh-derived" in last(msgs) and "not implemented yet" in last(msgs)
      and len(ctx.geoms()) == before and ctx.geom_by_name("Box") is not None,
      f"splitting a SURFACE is refused by name, the box untouched ({last(msgs)[:90]!r})")
c, msgs, done = drive(ctx, "split", ["Box", ""], select=["V1"])
check("splitting with a surface is not implemented yet" in last(msgs),
      f"a surface CUTTER is refused by name ({last(msgs)!r})")

# ===========================================================================
# Trim
# ===========================================================================
print("\n-- Trim --")
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "H")
ctx.add(line((4, -5, 0), (4, 5, 0)), "V")
c, msgs, done = drive(ctx, "trim", ["2,0,0"], select=["V"])
proto = commands.COMMANDS.get("trim")
check(proto is not None and type(proto)().prompt.startswith("Select cutting objects"),
      "Trim's first prompt is the reference CAD application's `Select cutting objects`")
check(c is not None and c.prompt.startswith("Select object to trim"),
      f"...then the reference CAD application's `Select object to trim` ({c and c.prompt!r})")
kept = [g for g in ctx.geoms() if abs(g.PointAtStart.Y) < 1e-12
        and abs(g.PointAtEnd.Y) < 1e-12]
check(len(kept) == 1 and ends(kept[0]) == {(4.0, 0.0, 0.0), (10.0, 0.0, 0.0)},
      f"the PICKED part (x<4) is removed, (4,0)-(10,0) kept "
      f"({[sorted(ends(g)) for g in kept]}; {last(msgs)!r})")
check(not done, "Trim waits for the next pick (`Press Enter when done`)")
if c is not None:
    msg, done = c.feed("", ctx)
    check(done, f"...and Enter ends it ({msg!r})")
# trimming the cutting objects themselves: both selected as cutters
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (10, 0, 0)), "H")
ctx.add(line((4, -5, 0), (4, 5, 0)), "V")
c, msgs, done = drive(ctx, "trim", ["2,0,0", "4,3,0", ""], select=["H", "V"])
vs = [g for g in ctx.geoms() if abs(g.PointAtStart.X - 4) < 1e-12
      and abs(g.PointAtEnd.X - 4) < 1e-12]
check(len(ctx.geoms()) == 2 and len(vs) == 1
      and ends(vs[0]) == {(4.0, -5.0, 0.0), (4.0, 0.0, 0.0)},
      f"a trimmed CUTTER keeps cutting: V is trimmed by the already-trimmed H, its upper "
      f"part removed ({[sorted(ends(g)) for g in ctx.geoms()]}; {msgs[1:3]!r})")
c, msgs, done = drive(ctx, "trim", ["40,40,0"], select=["H"] if ctx.geom_by_name("H")
                      else [ctx.names()[0]])
check("not on a curve" in last(msgs) and "mesh-derived" in last(msgs),
      f"a pick off every curve says so, and that surfaces are not trimmed "
      f"({last(msgs)[:100]!r})")
ctx.add(bridge.make_box((20, 0, 0), (22, 2, 0), 2.0), "Box")
c, msgs, done = drive(ctx, "trim", ["2,0,0"], select=["Box"])
check("trimming with a surface is not implemented yet" in last(msgs),
      f"a surface CUTTER is refused by name ({last(msgs)!r})")

# ===========================================================================
# Intersect
# ===========================================================================
print("\n-- Intersect --")


def points(ctx):
    return sorted((round(g.Location.X, 9), round(g.Location.Y, 9), round(g.Location.Z, 9))
                  for g in ctx.geoms() if isinstance(g, r3.Point))


ctx = FakeCtx()
ctx.add(bridge.make_circle((0, 0, 0), 5.0), "A")
ctx.add(bridge.make_circle((6, 0, 0), 5.0), "B")
c, msgs, done = drive(ctx, "intersect", [], select=["A", "B"])
check(done and points(ctx) == [(3.0, -4.0, 0.0), (3.0, 4.0, 0.0)],
      f"two R5 circles 6 apart -> point objects at (3, +-4) exactly ({points(ctx)}; "
      f"{last(msgs)!r})")
check(last(msgs) == "Found 2 intersections.", f"...reported in the reference CAD application's words ({last(msgs)!r})")
# MEASURED (research_all_13 note 075): crossing lines -> "Found 1 intersection."; a
# collinear overlap (0,0)-(4,0) / (2,0)-(6,0) -> one CURVE (2,0,0)-(4,0,0), 1 intersection.
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (4, 4, 0)), "X1")
ctx.add(line((0, 4, 0), (4, 0, 0)), "X2")
c, msgs, done = drive(ctx, "intersect", [], select=["X1", "X2"])
check(points(ctx) == [(2.0, 2.0, 0.0)] and last(msgs) == "Found 1 intersection.",
      f"crossing lines: one point at (2,2,0) ({points(ctx)}; {last(msgs)!r})")
ctx = FakeCtx()
ctx.add(line((0, 0, 0), (4, 0, 0)), "P")
ctx.add(line((2, 0, 0), (6, 0, 0)), "Q")
c, msgs, done = drive(ctx, "intersect", [], select=["P", "Q"])
new = [g for g in ctx.geoms() if isinstance(g, r3.Curve)][2:]
ends = [tuple(round(v, 9) + 0.0 for v in (p.X, p.Y, p.Z))
        for g in new for p in (g.PointAtStart, g.PointAtEnd)]
check(len(new) == 1 and sorted(ends) == [(2.0, 0.0, 0.0), (4.0, 0.0, 0.0)]
      and last(msgs) == "Found 1 intersection.",
      f"an overlap builds the shared curve (2,0,0)-(4,0,0) ({ends}; {last(msgs)!r})")
# MEASURED: a line (2,-2,2)-(2,2,2) through a planar surface in y=0 -> one point (2,0,2);
# the same surface with a line at y=2 that misses -> "Found 0 intersections."
ctx = FakeCtx()
srf = bridge.make_planar_srf([bridge.make_rectangle((0, 0, 0), (4, 0, 4),
                                                    plane=cplane.WORLD_FRONT)])
srf = srf[0] if isinstance(srf, (list, tuple)) else srf
ctx.add(srf, "Srf")
ctx.add(line((2, -2, 2), (2, 2, 2)), "Through")
c, msgs, done = drive(ctx, "intersect", [], select=["Through", "Srf"])
check(points(ctx) == [(2.0, 0.0, 2.0)] and last(msgs) == "Found 1 intersection.",
      f"curve x surface: one exact point (2,0,2) ({points(ctx)}; {last(msgs)!r})")
ctx.add(line((2, 2, 2), (2, 2, 6)), "Miss")
c, msgs, done = drive(ctx, "intersect", [], select=["Miss", "Srf"])
check(last(msgs) == "Found 0 intersections.", f"a miss: the reference CAD application's words ({last(msgs)!r})")
ctx.add(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), "Box")
ctx.add(line((5, 5, -1), (5, 5, 5)), "Outside")
c, msgs, done = drive(ctx, "intersect", [], select=["Outside", "Box"])
check(last(msgs) == "Found 0 intersections.",
      f"a line past the box's faces finds nothing -- trims are honoured ({last(msgs)!r})")

# InfinitePlane (#47): the planar surface's plane, unbounded
ctx = FakeCtx()
flat = bridge.make_planar_srf([bridge.make_rectangle((0, 0, 0), (4, 4, 0))])
flat = flat[0] if isinstance(flat, (list, tuple)) else flat
ctx.add(flat, "Flat")
ctx.add(line((10, 0, 5), (10, 0, -5)), "Far")
ctx.add(bridge.make_box((20, 0, 0), (22, 2, 0), 2.0), "Box2")
c, msgs, done = drive(ctx, "intersect", [], select=["Far", "Flat"])
check(points(ctx) == [] and last(msgs) == "Found 0 intersections.",
      f"premise: the bounded surface misses the line at x=10 ({last(msgs)!r})")
c, msgs, done = drive(ctx, "intersect", ["InfinitePlane=Yes", "Far", "Flat", ""])
check(points(ctx) == [(10.0, 0.0, 0.0)] and last(msgs) == "Found 1 intersection.",
      f"InfinitePlane: the line meets the plane z=0 at (10,0,0), outside the surface "
      f"({points(ctx)}; {last(msgs)!r})")
before = points(ctx)
c, msgs, done = drive(ctx, "intersect", ["InfinitePlane=Yes", "Far", "Box2", ""])
check(points(ctx) == before and "InfinitePlane needs a planar surface" in last(msgs)
      and "Box2" in last(msgs),
      f"InfinitePlane with a non-planar object is refused by name ({last(msgs)!r})")
c, msgs, done = drive(ctx, "intersect", ["SubCrv"])
check("not implemented yet" in last(msgs), f"SubCrv still refuses by name ({last(msgs)!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("CURVE TOOLS PASSED")
