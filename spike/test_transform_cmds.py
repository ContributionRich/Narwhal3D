"""the transform commands, the reference CAD application's steps -- and `live_xform` for the ghost.

Rotate, Rotate3D, Scale, Scale1D, Scale2D, Mirror, Orient, Orient3Pt and Move Vertical,
each driven through the command on a real `NarwhalModel`, with the DOCUMENT geometry
checked against a hand-worked result; and `live_xform(cursor)` (the contract the
shell's ghost draws through) tested directly for every one, plus Move and Copy.

Every gate FAILS on the code before this change: Rotate asked for a typed x/y/z axis and an angle
only, Scale only took a typed factor about the Blender bbox centre (and needed bpy),
Mirror hard-wired world Z, and Rotate3D / Scale1D / Scale2D / Orient / Orient3Pt /
Move Vertical / `live_xform` did not exist.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_transform_cmds.py
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
analysis = _load("narwhal3d.analysis", "narwhal3d/analysis.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

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


class Ctx:
    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.plane = plane
        self.sel = []
        self.n = 0

    def add(self, geom, name):
        self.model.add_object(geom, name, 0)
        self.sel.append(name)

    def add_geometry(self, geom, base_name, layer_index=None):
        self.n += 1
        nm = f"{base_name}_{self.n}"
        self.model.add_object(geom, nm, 0)
        return nm, nm

    def selected_objects(self):
        return [FakeObj(n) for n in self.sel]

    def select_by_names(self, names):
        self.sel = list(names)
        return names

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if (o.Attributes.Name or "").lower() == name.lower():
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def note_change(self, label=None):
        pass

    def active_cplane(self):
        return self.plane


def start(ctx, key):
    proto = commands.COMMANDS.get(key)
    if proto is None:
        return None
    c = type(proto)()
    c.begin(ctx)
    return c


def run(ctx, key, lines):
    c = start(ctx, key)
    if c is None:
        return None, "(no such command)", True
    msg, done = None, False
    for line in lines:
        msg, done = c.feed(line, ctx)
        if done:
            break
    return c, msg, done


def ends(g):
    a, b = g.PointAtStart, g.PointAtEnd
    return [(round(a.X, 9) + 0.0, round(a.Y, 9) + 0.0, round(a.Z, 9) + 0.0),
            (round(b.X, 9) + 0.0, round(b.Y, 9) + 0.0, round(b.Z, 9) + 0.0)]


def line_ctx(p0, p1, plane=None, name="L"):
    ctx = Ctx(plane)
    ctx.add(bridge.make_line(p0, p1), name)
    return ctx


def apply(x, p):
    q = r3.Point3d(*p)
    q.Transform(x)
    return (round(q.X, 9) + 0.0, round(q.Y, 9) + 0.0, round(q.Z, 9) + 0.0)


def near(a, b, tol=1e-9):
    return a is not None and b is not None and math.dist(a, b) <= tol


for key in ("rotate", "rotate3d", "scale", "scale1d", "scale2d", "mirror", "orient",
            "orient3pt"):
    check(key in commands.COMMANDS, f"`{key}` is a registered command")

# ===========================================================================
print("\n-- Rotate --")
ctx = line_ctx((1, 0, 0), (2, 0, 0))
c, msg, done = run(ctx, "rotate", ["0,0,0", "90"])
check(done and ends(ctx.geom_by_name("L")) == [(0, 1, 0), (0, 2, 0)],
      f"centre 0, typed 90: (1,0)-(2,0) -> (0,1)-(0,2), counterclockwise ({msg!r})")
c = start(line_ctx((1, 0, 0), (2, 0, 0)), "rotate")
check(c is not None and c.prompt.startswith("Center of rotation"),
      f"The reference CAD application's prompt `Center of rotation` ({c and c.prompt[:30]!r})")
ctx = line_ctx((1, 0, 0), (2, 0, 0))
c = start(ctx, "rotate")
c.feed("0,0,0", ctx)
check(c.prompt.startswith("Angle or first reference point"),
      f"then `Angle or first reference point` ({c.prompt[:40]!r})")
check(c.live_xform((5, 0, 0)) is None,
      "live_xform at the angle step is None -- nothing turns before a reference exists")
c.feed("5,0,0", ctx)
check(c.prompt.startswith("Second reference point"),
      f"a CLICK there is the first reference point; the reference CAD application then asks `Second reference "
      f"point` ({c.prompt[:30]!r})")
lx = c.live_xform((0, 7, 0))
check(lx is not None and apply(lx, (1, 0, 0)) == (0, 1, 0),
      "live_xform((0,7,0)) is the 90 deg rotation the second reference would make")
msg, done = c.feed("0,3,0", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(0, 1, 0), (0, 2, 0)],
      f"two reference points (5,0) -> (0,3) rotate 90 deg ({msg!r})")
ctx = line_ctx((1, 0, 0), (2, 0, 0), plane=cplane.WORLD_FRONT)
c, msg, done = run(ctx, "rotate", ["0,0", "90"])
check(done and ends(ctx.geom_by_name("L")) == [(0, 0, 1), (0, 0, 2)],
      f"in FRONT the axis is Front's normal: (1,0,0) -> (0,0,1) ({ends(ctx.geom_by_name('L'))})")
ctx = line_ctx((1, 0, 0), (2, 0, 0))
c, msg, done = run(ctx, "rotate", ["Copy=Yes", "0,0,0", "180"])
copies = [o for o in ctx.model.file.Objects if o.Attributes.Name != "L"]
check(done and ends(ctx.geom_by_name("L")) == [(1, 0, 0), (2, 0, 0)] and len(copies) == 1
      and ends(copies[0].Geometry) == [(-1, 0, 0), (-2, 0, 0)],
      f"Copy=Yes: the original stays, a rotated copy is added ({msg!r})")
c, msg, done = run(line_ctx((1, 0, 0), (2, 0, 0)), "rotate", ["0,0,0", "0,0,0", "1,1,0"])
check(done and "on the rotation axis" in (msg or ""),
      f"a reference point AT the centre is refused ({msg!r})")

# ===========================================================================
print("\n-- Rotate3D --")
ctx = line_ctx((0, 1, 0), (0, 2, 0))
c, msg, done = run(ctx, "rotate3d", ["0,0,0", "1,0,0", "90"])
check(done and ends(ctx.geom_by_name("L")) == [(0, 0, 1), (0, 0, 2)],
      f"axis 0 -> +x, 90 deg: (0,1,0) -> (0,0,1), right-handed ({msg!r})")
ctx = line_ctx((0, 1, 0), (0, 2, 0))
c = start(ctx, "rotate3d")
for t in ("0,0,0", "1,0,0", "3,2,0"):
    c.feed(t, ctx)
lx = c.live_xform((7, 0, 5))
check(lx is not None and apply(lx, (0, 1, 0)) == (0, 0, 1),
      "reference points measured AROUND the axis: (3,2,0) -> (7,0,5) is 90 deg (live)")

# ===========================================================================
print("\n-- Scale --")
ctx = line_ctx((1, 0, 0), (2, 1, 0))
c, msg, done = run(ctx, "scale", ["0,0,0", "2"])
check(done and ends(ctx.geom_by_name("L")) == [(2, 0, 0), (4, 2, 0)],
      f"base 0, factor 2 ({msg!r})")
ctx = line_ctx((1, 0, 0), (2, 1, 0))
c = start(ctx, "scale")
check(c.prompt.startswith("Base point"), f"The reference CAD application's `Base point. Press Enter for automatic` "
      f"({c.prompt[:40]!r})")
c.feed("0,0,0", ctx)
c.feed("2,0,0", ctx)
check(c.prompt.startswith("Second reference point"), "a click at the factor prompt is the "
      "first reference point")
lx = c.live_xform((5, 0, 0))
check(lx is not None and apply(lx, (2, 0, 0)) == (5, 0, 0),
      "live_xform: the second reference at distance 5 scales by 5/2")
msg, done = c.feed("7", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(3.5, 0, 0), (7, 3.5, 0)],
      f"a TYPED new size 7 for a reference length 2 -> factor 3.5 (\"Type the new size for "
      f"the second reference point\") ({msg!r})")
# Enter at the base point: the selection's TIGHT bounding-box centre. An S-curve's hull
# centre is not its box centre (), so scaling about the right centre keeps it fixed.
wave = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(2, 6, 0), r3.Point3d(5, -4, 0), r3.Point3d(8, 0, 0)], 3)
ctx = Ctx()
ctx.add(wave, "W")
lo, hi = analysis.bounding_box(wave)
before = ((lo.X + hi.X) / 2, (lo.Y + hi.Y) / 2, 0.0)
c, msg, done = run(ctx, "scale", ["", "3"])
lo, hi = analysis.bounding_box(ctx.geom_by_name("W"))
after = ((lo.X + hi.X) / 2, (lo.Y + hi.Y) / 2, 0.0)
check(done and near(before, after, 1e-9),
      f"Enter at the base point scales about the TIGHT box centre: it stays put "
      f"({before} -> {after}; {msg!r})")

# ===========================================================================
print("\n-- Scale2D / Scale1D --")
ctx = line_ctx((1, 1, 1), (2, 2, 3))
c, msg, done = run(ctx, "scale2d", ["0,0,0", "2"])
check(done and ends(ctx.geom_by_name("L")) == [(2, 2, 1), (4, 4, 3)],
      f"Scale2D x2 in Top: x and y double, z is untouched ({msg!r})")
ctx = line_ctx((1, 1, 1), (2, 2, 3), plane=cplane.WORLD_FRONT)
c, msg, done = run(ctx, "scale2d", ["0,0", "2"])
check(done and ends(ctx.geom_by_name("L")) == [(2, 1, 2), (4, 2, 6)],
      f"Scale2D in Front: x and z double, y (Front's normal) is untouched "
      f"({ends(ctx.geom_by_name('L'))})")
ctx = line_ctx((1, 1, 0), (2, 2, 0))
c, msg, done = run(ctx, "scale1d", ["0,0,0", "1,0,0", "3,5,0"])
check(done and ends(ctx.geom_by_name("L")) == [(3, 1, 0), (6, 2, 0)],
      f"Scale1D by references (1,0)->(3,5): x3 ALONG x only ({msg!r})")
ctx = line_ctx((1, 4, 0), (2, 6, 0))
c = start(ctx, "scale1d")
c.feed("0,0,0", ctx)
c.feed("0.5", ctx)
check(c.prompt.startswith("Scale direction"), f"a typed factor asks for the direction "
      f"({c.prompt[:20]!r})")
lx = c.live_xform((0, 9, 0))
check(lx is not None and apply(lx, (1, 4, 0)) == (1, 2, 0), "live: halved along y")
msg, done = c.feed("0,1,0", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(1, 2, 0), (2, 3, 0)],
      f"factor 0.5 along y ({msg!r})")
ctx = line_ctx((1, 4, 0), (2, 6, 0))
c, msg, done = run(ctx, "scale1d", ["0,0,0", "0", "0,1,0"])
check(done and ends(ctx.geom_by_name("L")) == [(1, 0, 0), (2, 0, 0)],
      f"\"Typing 0 for the scale factor flattens the object in the picked direction\" "
      f"({msg!r})")
# MEASURED (research_all_13 note 132): the direction prompt offers XAxis/YAxis/ZAxis
# (and only there); XAxis finished the command. A DIAGONAL line separates 1D from uniform.
c = start(line_ctx((1, 4, 0), (2, 6, 0)), "scale1d")
check("XAxis" not in c.prompt, f"no axis options before the direction prompt ({c.prompt!r})")
ctx = line_ctx((1, 4, 0), (2, 6, 0))
c = start(ctx, "scale1d")
c.feed("0,0,0", ctx)
c.feed("2", ctx)
check("XAxis" in c.prompt and "ZAxis" in c.prompt,
      f"the direction prompt lists the axes ({c.prompt!r})")
msg, done = c.feed("YAxis", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(1, 8, 0), (2, 12, 0)],
      f"YAxis: x2 along y only ({msg!r}, {ends(ctx.geom_by_name('L'))})")
ctx = line_ctx((1, 4, 1), (2, 6, 3), plane=cplane.WORLD_FRONT)
c = start(ctx, "scale1d")
c.feed("0,0", ctx)
c.feed("2", ctx)
msg, done = c.feed("YAxis", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(1, 4, 2), (2, 6, 6)],
      f"YAxis in the Front CPlane is world Z (DERIVED) ({ends(ctx.geom_by_name('L'))})")

# ===========================================================================
print("\n-- Mirror --")
ctx = line_ctx((1, 0, 0), (4, 0, 0))
c = start(ctx, "mirror")
check(c.prompt.startswith("Start of mirror plane"), f"The reference CAD application's `Start of mirror plane` "
      f"({c.prompt[:30]!r})")
c.feed("0,0,0", ctx)
lx = c.live_xform((0, 5, 0))
check(lx is not None and apply(lx, (1, 0, 0)) == (-1, 0, 0), "live: mirrored across x=0")
msg, done = c.feed("0,5,0", ctx)
imgs = [o.Geometry for o in ctx.model.file.Objects if o.Attributes.Name != "L"]
check(done and len(imgs) == 1 and ends(imgs[0]) == [(-1, 0, 0), (-4, 0, 0)]
      and ends(ctx.geom_by_name("L")) == [(1, 0, 0), (4, 0, 0)],
      f"Copy=Yes (default): a mirror IMAGE at -1..-4, the original kept ({msg!r})")
ctx = line_ctx((1, 0, 0), (4, 0, 0), plane=cplane.WORLD_FRONT)
c, msg, done = run(ctx, "mirror", ["Copy=No", "0,0", "0,5"])
check(done and ends(ctx.geom_by_name("L")) == [(-1, 0, 0), (-4, 0, 0)],
      f"in FRONT the plane is perpendicular to FRONT's CPlane (world z hard-wired would "
      f"flip y) ({ends(ctx.geom_by_name('L'))})")
ctx = line_ctx((0, 0, 1), (1, 0, 2))
c, msg, done = run(ctx, "mirror", ["Copy=No", "ZAxis"])
check(done and ends(ctx.geom_by_name("L")) == [(0, 0, -1), (1, 0, -2)],
      f"ZAxis: across the CPlane, at once ({msg!r})")
ctx = line_ctx((1, 2, 0), (3, 4, 0))
c, msg, done = run(ctx, "mirror", ["Copy=No", "XAxis"])
check(done and ends(ctx.geom_by_name("L")) == [(1, -2, 0), (3, -4, 0)],
      f"XAxis: across the CPlane x axis (xz plane) ({msg!r})")
ctx = line_ctx((1, 2, 3), (2, 3, 4))
c, msg, done = run(ctx, "mirror", ["Copy=No", "3Point", "0,0,0", "1,0,0", "0,0,1"])
check(done and ends(ctx.geom_by_name("L")) == [(1, -2, 3), (2, -3, 4)],
      f"3Point: the plane through three points (the xz plane) ({msg!r})")

# ===========================================================================
print("\n-- Orient / Orient3Pt --")
ctx = line_ctx((0.5, 0, 0), (1, 0, 0))
c = start(ctx, "orient")
for t in ("0,0,0", "1,0,0"):
    c.feed(t, ctx)
lx = c.live_xform((5, 5, 0))
# MEASURED (research_preview_14 P20): nothing moves at "Target point 1"; the rotated copy
# follows the cursor only at "Target point 2".
check(lx is None, "no live copy at Target point 1 (P20)")
c.feed("5,5,0", ctx)
lx = c.live_xform((5, 7, 0))
check(lx is not None and apply(lx, (0, 0, 0)) == (5, 5, 0)
      and apply(lx, (1, 0, 0)) == (5, 6, 0),
      "live at Target point 2: the rotated copy follows the cursor (P20)")
msg, done = c.feed("5,7,0", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(5, 5.5, 0), (5, 6, 0)],
      f"Scale=No: moved and turned onto the target axis, SAME size ({msg!r})")
# MEASURED (research_all_13 #097): "Target point 1 <Reference point 1>" -- Enter takes
# reference point 1. Reference (0,0,0)-(4,0,0) to target (0,0,0)-(0,4,0) turns the line.
ctx = line_ctx((0, 0, 0), (4, 0, 0))
c = start(ctx, "orient")
for t in ("0,0,0", "4,0,0", ""):
    c.feed(t, ctx)
msg, done = c.feed("0,4,0", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(0, 0, 0), (0, 4, 0)],
      f"Orient: Enter at Target point 1 uses reference point 1 ({msg!r})")
pt = r3.Point(r3.Point3d(0, 0, 1))
for mode, want in (("3D", (5, 5, 2)), ("1D", (5, 5, 1))):
    ctx = Ctx()
    ctx.add(r3.Point(r3.Point3d(0, 0, 1)), "P")
    ctx.add(bridge.make_line((0, 0, 0), (1, 0, 0)), "L")
    c, msg, done = run(ctx, "orient", [f"Scale={mode}", "0,0,0", "1,0,0", "5,5,0", "5,7,0"])
    loc = ctx.geom_by_name("P").Location
    check(done and ends(ctx.geom_by_name("L")) == [(5, 5, 0), (5, 7, 0)]
          and near((loc.X, loc.Y, loc.Z), want),
          f"Scale={mode}: the reference pair lands on the targets, and a point off the "
          f"axis goes to {want} ({(loc.X, loc.Y, loc.Z)})")
ctx = Ctx()
for nm, p in (("A", (1, 0, 0)), ("B", (0, 1, 0)), ("C", (0, 0, 1))):
    ctx.add(r3.Point(r3.Point3d(*p)), nm)
c, msg, done = run(ctx, "orient3pt", ["0,0,0", "1,0,0", "0,1,0", "10,0,0", "10,1,0", "9,0,0"])
got = [ctx.geom_by_name(n).Location for n in "ABC"]
check(done and near((got[0].X, got[0].Y, got[0].Z), (10, 1, 0))
      and near((got[1].X, got[1].Y, got[1].Z), (9, 0, 0))
      and near((got[2].X, got[2].Y, got[2].Z), (10, 0, 1)),
      f"Orient3Pt: the reference frame maps onto the target frame, rigidly ({msg!r})")
ctx = Ctx()
ctx.add(r3.Point(r3.Point3d(1, 0, 0)), "A")
c = start(ctx, "orient3pt")
for t in ("Scale=Yes", "0,0,0", "1,0,0", "0,1,0", "10,0,0"):
    c.feed(t, ctx)
lx = c.live_xform((10, 2, 0))
check(lx is not None and apply(lx, (1, 0, 0)) == (10, 1, 0),
      "live at Target point 2: the base direction turns onto the cursor (size kept in the "
      "preview)")
for t in ("10,2,0", "9,0,0"):
    msg, done = c.feed(t, ctx)
a = ctx.geom_by_name("A").Location
check(done and near((a.X, a.Y, a.Z), (10, 2, 0)),
      f"Scale=Yes: resized by |T1T2|/|R1R2| = 2 ({(a.X, a.Y, a.Z)})")

# ===========================================================================
print("\n-- Move Vertical, and Move / Copy live_xform --")
ctx = line_ctx((1, 1, 0), (2, 1, 0))
c, msg, done = run(ctx, "move", ["Vertical", "1,1,0", "3"])
check(done and ends(ctx.geom_by_name("L")) == [(1, 1, 3), (2, 1, 3)],
      f"Vertical, typed 3: up the CPlane normal ({msg!r})")
ctx = line_ctx((1, 1, 0), (2, 1, 0))
c = start(ctx, "move")
c.feed("Vertical", ctx)
c.feed("1,1,0", ctx)
check(c.axis_constraint(cplane.WORLD_TOP) == ((1.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
      "Vertical constrains the to-click to the normal through the from point")
lx = c.live_xform((9, 9, -2))
check(lx is not None and apply(lx, (1, 1, 0)) == (1, 1, -2),
      "live: a cursor anywhere moves only along the normal (its projection, -2)")
msg, done = c.feed("w9,9,-2", ctx)
check(done and ends(ctx.geom_by_name("L")) == [(1, 1, -2), (2, 1, -2)],
      f"a picked to-point below the CPlane moves DOWN by its projection ({msg!r})")
for key in ("move", "copy"):
    ctx = line_ctx((1, 1, 0), (2, 1, 0))
    c = start(ctx, key)
    check(c.live_xform((5, 5, 5)) is None, f"{key}: live_xform is None at the from step")
    c.feed("1,1,0", ctx)
    lx = c.live_xform((4, 5, 6))
    check(lx is not None and apply(lx, (0, 0, 0)) == (3, 4, 6),
          f"{key}: live_xform is the translation from -> cursor")

# ===========================================================================
print("\n-- the master moves with the proxy (audit gate 5) --")
if ob.available():
    ctx = Ctx()
    bu = bridge.boolean_brep(bridge.make_box((0, 0, 0), (10, 10, 0), 10),
                             bridge.make_cylinder((10, 5, 0), 3, 10), "UNION")
    ctx.add(bu, "U")
    c, msg, done = run(ctx, "rotate", ["0,0,0", "90"])
    g = ctx.geom_by_name("U")
    lo, hi = analysis.bounding_box(g)
    got = (lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z)
    want = (-10.0, 0.0, 0.0, 0.0, 13.0, 10.0)
    check(kernel.master_of(g) is not None and max(abs(a - b) for a, b in zip(got, want)) < 1e-9,
          f"Rotate 90 turns box-union-cylinder's MASTER: exact box x -10..0, y 0..13 ({got})")
else:
    print("  skip  master rotation (no OCCT)")

# ===========================================================================
print("\n-- UseLastCenter / UseLastAxis (#61) --")
commands._AngleCmd._last_axis_points.clear()
c, msg, done = run(line_ctx((1, 0, 0), (2, 0, 0)), "rotate", ["UseLastCenter"])
check(not done and "no previous center" in (msg or ""),
      f"Rotate UseLastCenter before any Rotate is refused by name ({msg!r})")
run(line_ctx((1, 0, 0), (2, 0, 0)), "rotate", ["5,5,0", "30"])     # remembers (5,5,0)
ctx = line_ctx((6, 5, 0), (7, 5, 0))
c, msg, done = run(ctx, "rotate", ["UseLastCenter", "90"])
check(done and ends(ctx.geom_by_name("L")) == [(5, 6, 0), (5, 7, 0)],
      f"Rotate UseLastCenter: 90 about the last centre (5,5): (6,5)-(7,5) -> (5,6)-(5,7) "
      f"({ends(ctx.geom_by_name('L'))}, {msg!r})")
ctx = line_ctx((6, 5, 0), (7, 5, 0))
c, msg, done = run(ctx, "rotate", ["UseLastCenter", "180"])
check(done and ends(ctx.geom_by_name("L")) == [(4, 5, 0), (3, 5, 0)],
      "repeated UseLastCenter keeps the same centre")
c, msg, done = run(line_ctx((1, 0, 0), (2, 0, 0)), "rotate", ["0,0,0", "UseLastCenter"])
check(not done and "answers the center of rotation prompt" in (msg or ""),
      f"UseLastCenter after the centre is refused by name ({msg!r})")
c, msg, done = run(line_ctx((0, 1, 0), (0, 2, 0)), "rotate3d", ["UseLastAxis"])
check(not done and "no previous rotation axis" in (msg or ""),
      f"Rotate3D UseLastAxis before any Rotate3D is refused by name ({msg!r})")
run(line_ctx((0, 1, 0), (0, 2, 0)), "rotate3d", ["0,0,3", "1,0,3", "10"])   # x axis at z=3
ctx = line_ctx((0, 1, 3), (0, 2, 3))
c, msg, done = run(ctx, "rotate3d", ["UseLastAxis", "90"])
check(done and ends(ctx.geom_by_name("L")) == [(0, 0, 4), (0, 0, 5)],
      f"Rotate3D UseLastAxis: 90 about the last axis (x through z=3): "
      f"(0,1,3)-(0,2,3) -> (0,0,4)-(0,0,5) ({ends(ctx.geom_by_name('L'))}, {msg!r})")

# ===========================================================================
print("\n-- Scale Rigid (#61) --")


def box_of(g):
    lo, hi = analysis.bounding_box(g)
    return tuple(round(v, 9) + 0.0 for v in (lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z))


if ob.available():
    for key, base, want_box, want_sphere in (
            ("scale", "0,0,0", (1, 1, 1, 3, 3, 3), (19, -1, -1, 21, 1, 1)),
            ("scale2d", "0,0,0", (1, 1, 0, 3, 3, 2), (19, -1, -1, 21, 1, 1))):
        ctx = Ctx()
        ctx.add(bridge.make_box((0, 0, 0), (2, 2, 0), 2.0), "Box")
        ctx.add(bridge.make_sphere((10, 0, 0), 1.0), "Ball")
        c, msg, done = run(ctx, key, ["Rigid=Yes", base, "2"])
        gb, gs = ctx.geom_by_name("Box"), ctx.geom_by_name("Ball")
        check(done and box_of(gb) == want_box and box_of(gs) == want_sphere
              and "rigidly" in msg,
              f"{key} Rigid x2 about 0: the box keeps size 2, its centre goes to "
              f"{tuple((a + b) / 2 for a, b in zip(want_box[:3], want_box[3:]))}; the "
              f"radius-1 ball moves to x=20 ({box_of(gb)}, {box_of(gs)}, {msg!r})")
        recipe = bridge._kernel().recipe_of(gs) or {}
        check(recipe.get("kind") == "sphere" and recipe.get("radius") == 1.0
              and [round(v, 9) + 0.0 for v in recipe.get("xform", [])]
              == [1, 0, 0, 20, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
              f"{key} Rigid: the ball's exact recipe is a radius-1 sphere translated to "
              f"x=20, never scaled ({recipe})")
    ctx = Ctx()
    ctx.add(bridge.make_box((0, 0, 0), (2, 2, 0), 2.0), "Box")
    c, msg, done = run(ctx, "scale", ["Rigid=Yes", "Copy=Yes", "0,0,0", "3"])
    copies = [o.Geometry for o in ctx.model.file.Objects if o.Attributes.Name != "Box"]
    check(done and box_of(ctx.geom_by_name("Box")) == (0, 0, 0, 2, 2, 2) and len(copies) == 1
          and box_of(copies[0]) == (2, 2, 2, 4, 4, 4),
          f"Rigid with Copy leaves the original and adds a moved, unscaled copy ({msg!r})")
    ctx = Ctx()
    ctx.add(bridge.make_box((0, 0, 0), (2, 2, 0), 2.0), "Box")
    run(ctx, "scale", ["0,0,0", "2"])
    check(box_of(ctx.geom_by_name("Box")) == (0, 0, 0, 4, 4, 4), "without Rigid Scale still scales")
else:
    print("  skip  Scale Rigid (no OCCT)")

# ===========================================================================
print("\n-- options the help lists and we do not build still say so --")
for key, opt in (("rotate3d", "SurfaceNormal"),
                 ("mirror", "Object"), ("move", "Normal")):
    ctx = line_ctx((0, 0, 0), (1, 0, 0))
    c = start(ctx, key)
    m, _d = c.feed(opt, ctx) if c else ("(no command)", True)
    check("not implemented yet" in (m or ""), f"{key} {opt}: {m!r}")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("TRANSFORM COMMANDS PASSED")
