"""the `InsertKnot` command -- add one knot to a curve at the closest point.

MEASURED (the command reference InsertKnot`): Steps: "Select the object. Pick a location
for the knot." Options: `Automatic` (add halfway between existing knots), `Midpoints`
(guide markers), `Symmetrical` (both sides of the centre), `Undo`, `Direction`/`Toggle`
(surfaces only), `SubCrv`. This command is the BASE form -- one curve, one picked
location -- so the curve gets exactly ONE knot at the parameter closest to the pick,
as the task requires.

The mutation is `NurbsCurve.Knots.InsertKnot(t, 1)` (MEASURED present, mutates in
place): inserting at a parameter that is not already a knot adds exactly ONE control
point and keeps the shape (MEASURED: a circle 9 -> 10 and a line 2 -> 3, both with the
shape unchanged to ~1e-15). `t` is the parameter of the point on the curve CLOSEST to
the picked location (sampled -- rhino3dm has no `ClosestPoint`).

THE MASTER RULE (as with the other curve edits): a curve with an analytic master is
refused by name -- `Knots.InsertKnot` would change only the display proxy and leave
the OCCT master at the old knot structure (audit gate 5).

Test (per the task): point count +1, shape unchanged.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_insertknot.py
"""
import importlib.util
import math
import os
import sys
import types

import rhino3dm as r3  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
kernel = _load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py")
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


def dist(a, b):
    """`Point3d - Point3d` is unsupported in rhino3dm, so measure with components."""
    return math.sqrt((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2)


def samples(c, n=101):
    """n evenly-spaced points across the curve's domain (the shape to compare)."""
    d = c.Domain
    return [c.PointAt(d.T0 + (d.T1 - d.T0) * i / (n - 1)) for i in range(n)]


class Stub:
    """A selected-object stand-in: `InsertKnot` only reads its `.name`."""

    def __init__(self, name):
        self.name = name


class Ctx:
    """The named document objects plus a (possibly non-empty) selection and a
    note_change recorder (mirrors the real NarwhalContext's surface the command uses)."""

    def __init__(self, named, selected=()):
        self.model = model.NarwhalModel()
        self._selected = [Stub(nm) for nm in selected]
        self.changes = []
        self.added = []
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if (o.Attributes.Name or "").lower() == (name or "").lower():
                return o.Geometry
        return None

    def selected_objects(self):
        return list(self._selected)

    def note_change(self, label=None):
        self.changes.append(label)

    def add_geometry(self, geom, base_name, layer_index=None):
        name = f"{base_name}{len(self.added) + 1:02d}"
        self.added.append(geom)
        self.model.add_object(geom, name, 0)
        return name, name


def fresh(name):
    return type(commands.COMMANDS[name])()


def run_typed(named, selected, name, loc):
    """Drive the command through the TYPED path (the object name, then the pick).

    The OBJECTS step ADVANCES on the first name (`_feed_objects_step`), and the POINT
    step is the final step, so feeding the location EXECUTES the command. Feed
    sequence: the name, then the location.
    """
    ctx = Ctx(named, selected)
    c = fresh("insertknot")
    c.begin(ctx)
    last, done = None, False
    for line in [str(name), str(loc)]:
        last, done = c.feed(line, ctx)
        if done:
            break
    return last, done, ctx


# ===========================================================================
print("the command is registered and asks for an object then a pick location")
# ===========================================================================
check("insertknot" in commands.COMMANDS,
      "InsertKnot is registered in the COMMANDS registry")
check(commands.COMMANDS["insertknot"].name == "InsertKnot",
      "insertknot -> name is InsertKnot")
cmd = commands.COMMANDS["insertknot"]
check([k for _p, k in cmd.steps] == [commands.OBJECTS, commands.POINT],
      f"{cmd.name} asks for the object then a pick location "
      f"(got {[k for _p, k in cmd.steps]!r})")
check(cmd.preselect_names == 1, f"{cmd.name} takes the selection (preselect_names == 1)")

# ===========================================================================
print()
print("a circle GAINS ONE POINT, shape unchanged (closest point to the pick)")
# ===========================================================================
# Pick (4, 6, 0): closest point on the circle is between the existing knots, so the
# insertion adds exactly one control point (9 -> 10), not a multiplicity bump.
CIRCLE = bridge.make_circle((0, 0, 0), 5.0)
gc = bridge.make_circle((0, 0, 0), 5.0)
n_before = len(gc.Points)
before = samples(gc)
msg, done, ctx = run_typed([("Circle", CIRCLE)], (), "Circle", "4,6,0")
check(not done and "knot inserted" in msg,
      f"a pick inserts and the LOOP stays open (MEASURED #073) ({msg!r} / done={done})")
g = ctx.geom_by_name("Circle")
check(g is not None, "the circle is still in the document")
n_after = len(g.Points)
check(n_after == n_before + 1,
      f"the point count grew by exactly 1 ({n_before} -> {n_after})")
after = samples(g)
md = max(dist(a, b) for a, b in zip(before, after))
check(md < 1e-9, f"the shape is unchanged (maxdev {md:g} < 1e-9)")
cc = g.TryGetCircle(1e-9)
check(cc is not None and abs(cc.Radius - 5.0) < 1e-9,
      f"it is still a circle of radius 5 ({None if cc is None else cc.Radius:g})")
check("knot inserted" in msg and "Circle:" in msg,
      f"the report says a knot was inserted on the circle ({msg!r})")

# ===========================================================================
print()
print("a line GAINS ONE POINT, shape unchanged")
# ===========================================================================
# A degree-1 line has no interior knots, so any interior insertion adds one control
# point (2 -> 3) and keeps the line straight.
LINE = bridge.make_line((0, 0, 0), (10, 0, 0))
gl = bridge.make_line((0, 0, 0), (10, 0, 0))
n_before = len(gl.Points)
before = samples(gl)
msg, done, ctx = run_typed([("Line", LINE)], (), "Line", "3,1,0")
check(not done, f"the loop stays open after the pick ({msg!r} / done={done})")
g = ctx.geom_by_name("Line")
check(g is not None, "the line is still in the document")
check(len(g.Points) == n_before + 1,
      f"the point count grew by exactly 1 ({n_before} -> {len(g.Points)})")
after = samples(g)
md = max(dist(a, b) for a, b in zip(before, after))
check(md < 1e-9, f"the shape is unchanged (maxdev {md:g} < 1e-9)")
check("knot inserted" in msg, f"the report says a knot was inserted ({msg!r})")

# ===========================================================================
print()
print("the SELECTION path: select the curve first, then pick the location")
# ===========================================================================
CIRCLE2 = bridge.make_circle((0, 0, 0), 4.0)
ctx = Ctx([("Circle", CIRCLE2)], ("Circle",))
c = fresh("insertknot")
# begin() consumes the pre-selection into the OBJECTS step, so only the pick remains.
msg, done = c.begin(ctx)
n_before = len(ctx.geom_by_name("Circle").Points)
msg, done = c.feed("2,7,0", ctx)     # the location -> inserts, loop stays open
check(not done, f"the selection path inserts on the location feed ({msg!r} / done={done})")
g = ctx.geom_by_name("Circle")
check(g is not None, "the circle is still in the document")
check(len(g.Points) == n_before + 1,
      f"the point count grew by exactly 1 ({n_before} -> {len(g.Points)})")
check("knot inserted" in msg, f"the report says a knot was inserted ({msg!r})")

# ===========================================================================
print()
print("a curve WITH A MASTER is REFUSED by name (the master must not be left)")
# ===========================================================================
c2 = bridge.make_circle((0, 0, 0), 6.0)
kernel.register_master(c2, "TEST_MASTER")
ctx0 = Ctx([])
ctx0.model.add_object(c2, "MasterCurve", 0)
check(kernel.master_of(ctx0.geom_by_name("MasterCurve")) is not None,
      "the master is reachable through the document object")
c = fresh("insertknot")
c.begin(ctx0)
msg, done = c.feed("MasterCurve", ctx0)   # the object
msg, done = c.feed("3,6,0", ctx0)         # the location (final step) -> executes
check(done, f"the run completes ({done})")
check("not implemented yet" in msg,
      f"the master-bearing curve is refused by name ({msg!r})")
check(kernel.master_of(ctx0.geom_by_name("MasterCurve")) is not None,
      "the master is STILL registered after the refusal")
kernel.drop_master(c2)

# ===========================================================================
print()
print("a non-curve object is refused (InsertKnot does not apply)")
# ===========================================================================
PT = r3.Point(r3.Point3d(1, 2, 3))
msg, done, ctx = run_typed([("Point", PT)], (), "Point", "1,2,3")
check(done, f"the run completes ({done})")
check("is not a curve" in msg, f"a point is refused as a non-applicable type ({msg!r})")

# ===========================================================================
print()
print("a missing name is reported, not a crash")
# ===========================================================================
msg, done, ctx = run_typed([("Circle", bridge.make_circle((0, 0, 0), 2.0))],
                           (), "Nope", "1,2,3")
check(done, f"a missing object runs and reports ({msg!r} / done={done})")
check("Nope" in msg and "No object named" in msg,
      f"a missing name says so by name ({msg!r})")

# ===========================================================================
print()
print("note_change fires once, BEFORE the mutation")
# ===========================================================================
CIRCLE3 = bridge.make_circle((0, 0, 0), 3.0)
ctx = Ctx([("Circle", CIRCLE3)], ())
c = fresh("insertknot")
c.begin(ctx)
msg, done = c.feed("Circle", ctx)    # the object (advances)
msg, done = c.feed("4,6,0", ctx)     # the location -> inserts
check(not done, f"the pick inserts and waits for more ({done})")
msg, done = c.feed("", ctx)          # "Press Enter when done"
check(done and msg is None, f"Enter ends the loop silently ({msg!r}, {done})")
check(ctx.changes == ["InsertKnot"],
      f"note_change fired once, named InsertKnot, before mutating  ({ctx.changes})")
check(len(ctx.geom_by_name("Circle").Points) > len(bridge.make_circle((0, 0, 0), 3.0).Points),
      "the mutation is reflected in the document (a point was added)")

print("MEASURED (research_all_13 note 073): the loop, Automatic and Symmetrical")
# ===========================================================================
check(commands.InsertKnotCmd().prompt.startswith("Select curve or surface for knot insertion"),
      "nothing selected: the reference CAD application's object prompt")
ctx = Ctx([("L", bridge.make_line((0, 0, 0), (4, 0, 0)))], ("L",))
c = fresh("insertknot")
c.begin(ctx)
check(c.prompt.startswith("Point on curve to add knot. Press Enter when done"),
      f"preselected: straight to the loop prompt ({c.prompt!r})")
c.feed("2,0,0", ctx)
check(len(ctx.geom_by_name("L").Points) == 3, "pick 2 on a 0-4 line: 2 -> 3 points")
c.feed("3,1,0", ctx)
check(len(ctx.geom_by_name("L").Points) == 4, "an off-curve pick is projected: 3 -> 4")
c.feed("Automatic", ctx)
check(len(ctx.geom_by_name("L").Points) == 7,
      f"Automatic: one knot halfway in each of 3 intervals: 4 -> 7 "
      f"({len(ctx.geom_by_name('L').Points)})")
msg, done = c.feed("", ctx)
check(done, "Enter ends it")
ctx = Ctx([("L", bridge.make_line((0, 0, 0), (4, 0, 0)))], ("L",))
c = fresh("insertknot")
c.begin(ctx)
c.feed("Symmetrical=Yes", ctx)
c.feed("1,0,0", ctx)
knots = sorted({round(float(k), 9) for k in ctx.geom_by_name("L").Knots})
check(len(ctx.geom_by_name("L").Points) == 4 and knots == [0.0, 1.0, 3.0, 4.0],
      f"Symmetrical: pick 1 -> knots at 1 and 3, 2 -> 4 points ({knots})")

# ===========================================================================
print()
print("Midpoints=Yes places one marker per span and leaves the curve alone (#50)")
# ===========================================================================


def knotted_curve():
    """Degree 2, knots 0 0 1 3 3: distinct knot values 0, 1 and 3."""
    c = r3.NurbsCurve(3, False, 3, 4)
    for i, (x, y) in enumerate([(0, 0), (1, 2), (3, 2), (4, 0)]):
        c.Points[i] = r3.Point4d(x, y, 0, 1)
    for i, k in enumerate([0, 0, 1, 3, 3]):
        c.Knots[i] = k
    return c


def curve_state(g):
    return ([float(k) for k in g.Knots],
            [(p.X, p.Y, p.Z, p.W) for p in g.Points])


def marker_check(ctx, msg, done, label):
    g = ctx.geom_by_name("K")
    want = [g.PointAt(0.5), g.PointAt(2.0)]
    got = [m.Location for m in ctx.added]
    check(done and msg == "InsertKnot: 2 midpoint markers placed",
          f"{label}: finishes with the marker count ({msg!r}, done={done})")
    check(len(got) == 2 and all(isinstance(m, r3.Point) for m in ctx.added),
          f"{label}: two point objects were added ({len(got)})")
    check(len(got) == 2 and all(dist(a, b) <= 1e-12 for a, b in zip(got, want)),
          f"{label}: the markers sit at PointAt(0.5) and PointAt(2.0)")
    check(curve_state(g) == base_state, f"{label}: knots and control points are unchanged")
    check(ctx.changes == ["InsertKnot"], f"{label}: one note_change, so one Undo step ({ctx.changes})")


base_state = curve_state(knotted_curve())
check(sorted({k for k in base_state[0]}) == [0.0, 1.0, 3.0], "fixture: distinct knots 0, 1, 3")

# preselected: Midpoints=Yes at the knot-point prompt runs at once
ctx = Ctx([("K", knotted_curve())], ("K",))
c = fresh("insertknot")
c.begin(ctx)
msg, done = c.feed("Midpoints=Yes", ctx)
marker_check(ctx, msg, done, "preselected")
check("not implemented" not in (msg or ""), "the old refusal is gone")

# typed: set the toggle at the object prompt, then name the curve
ctx = Ctx([("K", knotted_curve())])
c = fresh("insertknot")
c.begin(ctx)
msg, done = c.feed("Midpoints=Yes", ctx)
check(not done and c.opt.get("Midpoints") is True and not ctx.added,
      f"at the object prompt the toggle is stored and nothing is placed yet ({msg!r})")
msg, done = c.feed("K", ctx)
marker_check(ctx, msg, done, "typed")

# a master-bearing curve is still refused by the shared curve check
mc = bridge.make_circle((0, 0, 0), 6.0)
kernel.register_master(mc, "TEST_MASTER")
ctx = Ctx([("M", mc)], ("M",))
c = fresh("insertknot")
c.begin(ctx)
msg, done = c.feed("Midpoints=Yes", ctx)
check(done and "analytic master" in msg and not ctx.added and ctx.changes == [],
      f"Midpoints on a master-bearing curve is refused by name ({msg!r})")
kernel.drop_master(mc)

# Midpoints=No keeps the knot-point loop
ctx = Ctx([("K", knotted_curve())], ("K",))
c = fresh("insertknot")
c.begin(ctx)
msg, done = c.feed("Midpoints=No", ctx)
check(not done and not ctx.added and c.step_index == 1,
      f"Midpoints=No changes nothing and stays in the loop ({msg!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("(InsertKnot) PASSED")
