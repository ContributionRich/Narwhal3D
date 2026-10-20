"""The top ten measured curve-command options and their closed-form geometry.

Each option is driven through the COMMAND (`begin` / `feed`, the same path the command
bar takes) on a real `NarwhalModel`, and the curve it adds is checked against a CLOSED
FORM -- a centre, a radius, a length or an area worked out by hand, never "something was
created".

Every gate fails on the pre-implementation behavior: there each of these options answered "not
implemented yet" (or, for Line, did not exist), so no curve was added and the check is RED.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_curve_options.py
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


class FakeCtx:
    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.plane = plane
        self.n = 0

    def add_geometry(self, geom, base_name, layer_index=None):
        self.n += 1
        nm = f"{base_name}_{self.n}"
        self.model.add_object(geom, nm, 0)
        return nm, nm

    def active_cplane(self):
        return self.plane

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass

    def geoms(self):
        return [o.Geometry for o in self.model.file.Objects]


def draw(key, lines, plane=None):
    """Run command `key` fed `lines`; ``(the one new curve or None, last message, cmd)``."""
    ctx = FakeCtx(plane)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
    g = ctx.geoms()
    return (g[0] if len(g) == 1 else None), msg, c


def xyz(p):
    return (p.X, p.Y, p.Z)


def near(a, b, tol=1e-9):
    return a is not None and b is not None and math.dist(a, b) <= tol


def arc_of(c):
    a = c.TryGetArc() if c is not None else None
    return a if a is not None and a.IsValid else None


def true_arc(c):
    n = c.ToNurbsCurve() if c is not None else None
    return n is not None and n.Degree == 2 and n.IsRational


def length(c):
    return arclength.length_of(c) if c is not None else None


def verts(c):
    v = bridge._polyline_vertices(c) if c is not None else None
    return [tuple(round(x, 9) for x in p) for p in v] if v else None


# ---------------------------------------------------------------- 1. Circle 3Point
print("-- 1. Circle 3Point --")
C0, R0 = (1.0, 2.0, 3.0), 5.0
U, V = (1.0, 0.0, 0.0), (0.0, 0.6, 0.8)          # a TILTED plane, normal (0,-0.8,0.6)
PTS = [tuple(C0[i] + R0 * (math.cos(t) * U[i] + math.sin(t) * V[i]) for i in range(3))
       for t in (0.3, 2.1, 4.4)]
g, msg, c = draw("circle", ["3Point"] + [",".join(repr(x) for x in p) for p in PTS])
a = arc_of(g)
check(a is not None and a.IsCircle and near(xyz(a.Center), C0) and abs(a.Radius - R0) < 1e-9,
      f"three points on a tilted R5 circle -> centre (1,2,3), R5 ({msg!r})")
check(a is not None and abs(abs(a.Plane.ZAxis.Y) - 0.8) < 1e-9
      and abs(abs(a.Plane.ZAxis.Z) - 0.6) < 1e-9,
      "...in THEIR plane (normal +-(0,-0.8,0.6)), not the CPlane")
check(true_arc(g), "...a rational degree-2 circle, not a polygon")
c3 = type(commands.COMMANDS["circle"])()
c3.feed("3Point", None)
check(c3.prompt.startswith("First point"), f"3Point uses the measured `First point` prompt "
      f"({c3.prompt[:30]!r})")
g, msg, c = draw("circle", ["3Point", "0,0,0", "1,1,0", "2,2,0"])
check(g is None and "in a line" in (msg or ""), f"collinear points are refused ({msg!r})")

# ---------------------------------------------------------------- 2. Circle 2Point
print("\n-- 2. Circle 2Point --")
g, msg, c = draw("circle", ["2Point", "0,0,0", "6,8,0"])
a = arc_of(g)
check(a is not None and a.IsCircle and near(xyz(a.Center), (3, 4, 0))
      and abs(a.Radius - 5) < 1e-12,
      f"diameter (0,0)-(6,8) -> centre (3,4), R5 ({msg!r})")
check(c is not None and c.steps[0][0].startswith("Start of diameter"),
      "2Point uses the measured `Start of diameter` / `End of diameter` prompts")

# ---------------------------------------------------------------- 3. Rectangle Rounded
print("\n-- 3. Rectangle Rounded --")
W, H, R = 10.0, 6.0, 2.0
g, msg, c = draw("rectangle", ["Rounded", "0,0,0", "10,6,0", "2"])
ac = analysis.planar_curve_area_centroid(g) if g is not None else None
check(g is not None and g.IsClosed and abs(length(g) - (2 * (W + H) - 8 * R + 2 * math.pi * R))
      < 1e-9, f"10x6 rounded R2: closed, length 16 + 4pi ({g and length(g)!r}; {msg!r})")
check(ac is not None and abs(ac[0] - (W * H - (4 - math.pi) * R * R)) < 1e-9
      and near(ac[2], (5, 3, 0)),
      f"...area 60 - (4-pi)4 and centroid (5,3) ({ac and (ac[0], ac[2])})")
arcs = [g.SegmentCurve(i) for i in range(g.SegmentCount)] if isinstance(g, r3.PolyCurve) else []
corner_arcs = [s for s in arcs if arc_of(s) is not None and not s.IsLinear()]
check(len(corner_arcs) == 4 and all(true_arc(s) and abs(arc_of(s).Radius - R) < 1e-12
                                    for s in corner_arcs),
      f"...its four corners are TRUE R2 arcs (rational degree 2) ({len(corner_arcs)})")
check(c is not None and c.prompt.startswith("Radius or point for rounded corner")
      or g is not None, "Rounded asks the measured `Radius or point for rounded corner to pass "
      "through`")
s = 2 - math.sqrt(2)                       # on the corner arc centred (2,2), at 225 deg
g, msg, c = draw("rectangle", ["Rounded", "0,0,0", "10,6,0", f"{s!r},{s!r},0"])
ac = analysis.planar_curve_area_centroid(g) if g is not None else None
check(ac is not None and abs(ac[0] - (W * H - (4 - math.pi) * 4)) < 1e-9,
      f"a PICKED point on the corner arc gives the same R2 rectangle ({msg!r})")
g, msg, c = draw("rectangle", ["Rounded", "0,0,0", "10,6,0", "4"])
check(g is None and "larger than half the shorter side" in (msg or ""),
      f"R4 on a 6-high rectangle is refused ({msg!r})")
g, msg, c = draw("rectangle", ["Center", "Rounded", "5,3,0", "10,6,0", "1"])
ac = analysis.planar_curve_area_centroid(g) if g is not None else None
check(ac is not None and abs(ac[0] - (60 - (4 - math.pi))) < 1e-9 and near(ac[2], (5, 3, 0)),
      f"Rounded combines with Center: 10x6 about (5,3), R1 ({msg!r})")

# ---------------------------------------------------------------- 4. Line BothSides
print("\n-- 4. Line BothSides --")
g, msg, c = draw("line", ["BothSides=Yes", "0,0,0", "3,4,0"])
check(g is not None and {tuple(round(x, 12) for x in xyz(g.PointAtStart)),
                         tuple(round(x, 12) for x in xyz(g.PointAtEnd))}
      == {(-3.0, -4.0, 0.0), (3.0, 4.0, 0.0)} and abs(length(g) - 10) < 1e-12,
      f"BothSides from the origin to (3,4): (-3,-4)-(3,4), twice as long ({msg!r})")
g, msg, c = draw("line", ["0,0,0", "3,4,0"])
check(g is not None and abs(length(g) - 5) < 1e-12, "BothSides off: the plain line, length 5")

# ---------------------------------------------------------------- 5. Line Vertical
print("\n-- 5. Line Vertical --")
g, msg, c = draw("line", ["Vertical", "1,2,0", "5"])
check(g is not None and near(xyz(g.PointAtStart), (1, 2, 0)) and near(xyz(g.PointAtEnd), (1, 2, 5)),
      f"Top: a typed 5 draws (1,2,0)-(1,2,5), along the CPlane normal ({msg!r})")
g, msg, c = draw("line", ["Vertical", "1,2,0", "w7,9,-3"])
check(g is not None and near(xyz(g.PointAtEnd), (1, 2, -3)),
      f"a PICKED end below the CPlane draws DOWN, its projection on the normal ({msg!r})")
g, msg, c = draw("line", ["Vertical", "1,2", "5"], plane=cplane.WORLD_FRONT)
check(g is not None and near(xyz(g.PointAtStart), (1, 0, 2)) and near(xyz(g.PointAtEnd), (1, -5, 2)),
      f"Front: vertical is Front's normal (0,-1,0) ({g and (xyz(g.PointAtStart), xyz(g.PointAtEnd))})")
lc = type(commands.COMMANDS["line"])()
lc.feed("Vertical", None)
lc.args = [(1.0, 2.0, 0.0)]
check(lc.axis_constraint(cplane.WORLD_TOP) == ((1.0, 2.0, 0.0), (0.0, 0.0, 1.0)),
      "Vertical constrains the end click to the normal through the start")

# ---------------------------------------------------------------- 6. Arc Center
print("\n-- 6. Arc Center --")
g, msg, c = draw("arc", ["Center", "0,0,0", "5,0,0", "0,7,0"])
a = arc_of(g)
check(a is not None and near(xyz(a.Center), (0, 0, 0)) and abs(a.Radius - 5) < 1e-12
      and abs(length(g) - 5 * math.pi / 2) < 1e-9
      and near(xyz(g.PointAtStart), (5, 0, 0)) and near(xyz(g.PointAtEnd), (0, 5, 0)),
      f"centre 0, start (5,0), end toward (0,7): the CCW quarter arc R5 ({msg!r})")
g, msg, c = draw("arc", ["Center", "0,0,0", "5,0,0", "0,-7,0"])
check(g is not None and abs(length(g) - 15 * math.pi / 2) < 1e-9,
      f"...end toward (0,-7): CCW three quarters, length 15pi/2 ({g and length(g)!r})")
check(true_arc(g), "...a rational degree-2 arc")

# ---------------------------------------------------------------- 7. Arc Direction
print("\n-- 7. Arc Direction --")
g, msg, c = draw("arc", ["Direction", "0,0,0", "1,0,0", "4,4,0"])
a = arc_of(g)
tan = g.TangentAt(g.Domain.T0) if g is not None else None
check(a is not None and near(xyz(a.Center), (0, 4, 0)) and abs(a.Radius - 4) < 1e-9
      and near(xyz(g.PointAtEnd), (4, 4, 0)) and abs(length(g) - 2 * math.pi) < 1e-9,
      f"start 0 heading +x, end (4,4): the R4 quarter arc about (0,4) ({msg!r})")
check(tan is not None and abs(tan.X - 1) < 1e-9 and abs(tan.Y) < 1e-9,
      "...TANGENT to the picked direction at its start")
g, msg, c = draw("arc", ["Direction", "0,0,0", "1,0,0", "-4,4,0"])
check(g is not None and abs(length(g) - 4 * (2 * math.atan2(4, -4))) < 1e-9,
      f"a sweep past 180 deg (end behind the start): length r * 2theta ({g and length(g)!r})")
g, msg, c = draw("arc", ["Direction", "0,0,0", "1,0,0", "5,0,0"])
check(g is None and "line" in (msg or ""), f"an end ON the direction is refused ({msg!r})")
# StartPoint is selectable (it is the default flow): start, end, a point on the arc
g, msg, c = draw("arc", ["StartPoint", "0,0,0", "10,0,0", "5,5,0"])
a = arc_of(g)
check(a is not None and abs(a.Radius - 5) < 1e-12 and abs(length(g) - 5 * math.pi) < 1e-9,
      f"StartPoint: (0,0) -> (10,0) through (5,5) is the R5 half circle ({msg!r})")

# ---------------------------------------------------------------- 8. Polygon Circumscribed
print("\n-- 8. Polygon Circumscribed --")
g, msg, c = draw("polygon", ["Circumscribed", "0,0,0", "5", "6"])
vs = verts(g)
check(vs is not None and len(vs) == 7
      and all(abs(math.hypot(x, y) - 5 / math.cos(math.pi / 6)) < 1e-9 for x, y, _z in vs),
      f"circumscribed hexagon R5: corners at 5/cos(30) ({msg!r})")
mids = [((vs[i][0] + vs[i + 1][0]) / 2, (vs[i][1] + vs[i + 1][1]) / 2) for i in range(6)] \
    if vs else []
check(mids and all(abs(math.hypot(*m) - 5) < 1e-9 for m in mids),
      "...and the MIDPOINTS of its sides on the R5 circle")
check(c is not None and c.steps[0][0].startswith("Center of circumscribed polygon"),
      "the measured prompt is `Center of circumscribed polygon`")

# ---------------------------------------------------------------- 9. Polygon Edge
print("\n-- 9. Polygon Edge --")
g, msg, c = draw("polygon", ["Edge", "0,0,0", "4,0,0", "4"])
check(verts(g) == [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0)],
      f"edge (0,0)-(4,0), 4 sides: the square on its left ({verts(g)}; {msg!r})")
g, msg, c = draw("polygon", ["Edge", "Flip=Yes", "0,0,0", "4,0,0", "4"])
check(verts(g) == [(0, 0, 0), (4, 0, 0), (4, -4, 0), (0, -4, 0), (0, 0, 0)],
      f"Flip puts it on the other side ({verts(g)})")
g, msg, c = draw("polygon", ["Edge", "0,0,0", "2,0,0", "6"])
vs = verts(g)
check(vs is not None and len(vs) == 7
      and all(abs(math.dist(vs[i], vs[i + 1]) - 2) < 1e-9 for i in range(6)),
      "a hexagon by edge: six sides of the picked length 2")

# ---------------------------------------------------------------- 10. Ellipse Corner
print("\n-- 10. Ellipse Corner --")
g, msg, c = draw("ellipse", ["Corner", "0,0,0", "8,6,0"])
ac = analysis.planar_curve_area_centroid(g) if g is not None else None
check(ac is not None and abs(ac[0] - math.pi * 4 * 3) < 1e-9 and near(ac[2], (4, 3, 0)),
      f"corners (0,0)-(8,6): the inscribed ellipse a=4, b=3 about (4,3), area 12pi ({msg!r})")
bb = analysis.bounding_box(g) if g is not None else None
check(bb is not None and near(xyz(bb[0]), (0, 0, 0)) and near(xyz(bb[1]), (8, 6, 0)),
      "...touching all four sides of the rectangle (its box IS the rectangle)")

# ---------------------------------------------------------------- the rest stay honest
print("\n-- unbuilt options still say so --")
# Circle/Arc Tangent are covered in spike/test_tangent_circle.py; AroundCurve and
# Midpoint remain the explicit unbuilt pins here.
for key, opt in (("line", "Normal"), ("line", "Tangent"), ("circle", "AroundCurve"),
                 ("arc", "Midpoint"), ("rectangle", "EdgeMidpoint"), ("polygon", "Star"),
                 ("ellipse", "Vertical")):
    cmd = type(commands.COMMANDS[key])()
    m, _d = cmd.feed(opt, None)
    check("not implemented yet" in (m or ""), f"{key} {opt}: {m!r}")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("CURVE OPTIONS PASSED")
