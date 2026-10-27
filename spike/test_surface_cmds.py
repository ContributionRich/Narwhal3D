"""PlanarSrf, ExtrudeSrf, EdgeSrf -- closed-form gates, driven through the COMMANDS.

MEASURED (commands/planarsrf.htm, extrudesrf.htm, edgesrf.htm; prompts from the reference application:
"Select planar curves to build surface", "Extrusion distance", "Select 2, 3, or 4 open
curves"). Before these gates, none of the three commands existed (`COMMANDS` had no key for them),
so every gate below fails on that code.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_surface_cmds.py
"""
import importlib.util
import math
import os
import sys
import tempfile
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


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
analysis = _load("narwhal3d.analysis", "narwhal3d/analysis.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
nearest = _load("narwhal3d.nearest", "narwhal3d/nearest.py")
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


if not ob.available():
    print(f"  skip  needs OCCT ({ob.unavailable_reason()})")
    sys.exit(0)


class _Ob:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self, named=()):
        self.model = model.NarwhalModel()
        self.added = []
        self.selection = []
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        nm = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, nm, 0)
        self.added.append(geom)
        return nm, nm

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return [_Ob(n) for n in self.selection]

    def active_cplane(self):
        return None

    def note_change(self, label=None):
        pass


def run(key, named, lines, select=(), opts_=()):
    ctx = Ctx(named)
    ctx.selection = list(select)
    c = type(commands.COMMANDS[key])()
    for k, v in opts_:
        c.opt[k] = v
    msg, done = c.begin(ctx)
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
    return ctx, msg


def rel(a, b):
    return abs(a - b) / abs(b)


def area(g):
    mp = analysis.exact_mass(g, solid=False)
    if mp is not None:
        return mp["area"]
    shape = bridge.planar_face_shape(g)
    return ob.mass_properties(shape, solid=False)["area"] if shape is not None else None


def poly(pts):
    return r3.PolylineCurve([r3.Point3d(*p) for p in pts])


SQ = poly([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0)])
HOLE = bridge.make_circle((5, 5, 0), 2.0)
ISLAND = bridge.make_circle((5, 5, 0), 1.0)

check(all(k in commands.COMMANDS for k in ("planarsrf", "extrudesrf", "edgesrf")),
      "PlanarSrf, ExtrudeSrf and EdgeSrf are commands")

print("\n-- PlanarSrf --")
ctx, msg = run("planarsrf", [("S", SQ), ("H", HOLE)], [""], select=["S", "H"])
g = ctx.added[0] if ctx.added else None
check(len(ctx.added) == 1 and len(g.Faces) == 1 and len(g.Faces[0].Loops) == 2
      and rel(area(g), 100 - 4 * math.pi) < 1e-12,
      f"a square + a circle inside it: ONE face with a HOLE, area 100 - 4pi "
      f"({g is not None and area(g)!r}; {msg!r})")
ctx, msg = run("planarsrf", [("S", SQ), ("H", HOLE), ("I", ISLAND)], [""],
               select=["S", "H", "I"])
areas = sorted(area(x) for x in ctx.added)
check(len(ctx.added) == 2 and rel(areas[0], math.pi) < 1e-12
      and rel(areas[1], 100 - 4 * math.pi) < 1e-12,
      f"an island inside the hole is its OWN surface (pi) ({areas})")
far = poly([(20, 0, 0), (24, 0, 0), (24, 3, 0), (20, 3, 0), (20, 0, 0)])
over = poly([(8, 8, 0), (14, 8, 0), (14, 14, 0), (8, 14, 0), (8, 8, 0)])
ctx, msg = run("planarsrf", [("S", SQ), ("F", far), ("O", over)], [""], select=["S", "F", "O"])
areas = sorted(area(x) for x in ctx.added)
check(len(ctx.added) == 3 and [round(a, 9) for a in areas] == [12.0, 36.0, 100.0],
      f"separate and PARTIALLY OVERLAPPING curves each make their own surface "
      f"(MEASURED help) ({areas})")
up = poly([(0, 0, 5), (3, 0, 5), (3, 3, 5), (0, 3, 5), (0, 0, 5)])
ctx, msg = run("planarsrf", [("S", SQ), ("U", up)], [""], select=["S", "U"])
check(not ctx.added and "same plane" in msg, f"curves in two planes are refused ({msg!r})")
ctx, msg = run("planarsrf", [("L", bridge.make_line((0, 0, 0), (5, 0, 0)))], [""], select=["L"])
check(not ctx.added and "CLOSED" in msg, f"an open curve is refused ({msg!r})")

print("\n-- ExtrudeSrf --")
holed = bridge.make_planar_srf([SQ, HOLE])[0]
V = (100 - 4 * math.pi) * 3
ctx, msg = run("extrudesrf", [("holed_srf", holed)], ["holed_srf", "3"])
e = ctx.added[0] if ctx.added else None
em = kernel.master_of(e) if e is not None else None
check(em is not None and ob.face_types(em) == {"PLANE": 6, "CYLINDER": 1} and ob.is_airtight(em)
      and rel(ob.volume(em), V) < 1e-12,
      f"the holed square by 3: {{PLANE: 6, CYLINDER: 1}}, airtight, volume (100 - 4pi) 3 "
      f"({em is not None and ob.volume(em)!r}; {msg!r})")
ctx, msg = run("extrudesrf", [("holed_srf", holed)], ["holed_srf", "3"], opts_=[("BothSides", True)])
bm = kernel.master_of(ctx.added[0]) if ctx.added else None
lo, hi = ob.tight_bbox(bm) if bm is not None else ((0, 0, 0), (0, 0, 0))
check(bm is not None and rel(ob.volume(bm), 2 * V) < 1e-12 and abs(lo[2] + 3) < 1e-9
      and abs(hi[2] - 3) < 1e-9,
      f"BothSides: twice as long, -3 .. 3 ({lo[2]:g} .. {hi[2]:g})")
ctx, msg = run("extrudesrf", [("holed_srf", holed)], ["holed_srf", "3"], opts_=[("Solid", False)])
om = kernel.master_of(ctx.added[0]) if ctx.added else None
check(om is not None and ob.face_types(om) == {"PLANE": 4, "CYLINDER": 1}
      and not ob.is_airtight(om),
      f"Solid=No: only the side surfaces, open ({om is not None and ob.face_types(om)})")
ctx, msg = run("extrudesrf", [("holed_srf", holed)], ["holed_srf", "w0,0,5"])
pm = kernel.master_of(ctx.added[0]) if ctx.added else None
check(pm is not None and rel(ob.volume(pm), (100 - 4 * math.pi) * 5) < 1e-12,
      f"a PICKED point 5 above the surface extrudes 5 ({msg!r})")
drilled = bridge.boolean_brep(e, bridge.make_cylinder((1.5, 1.5, -1), 1.0, 5.0), "DIFFERENCE")
dm = kernel.master_of(drilled)
check(dm is not None and bridge.last_occt_decline() is None
      and rel(ob.volume(dm), V - math.pi * 3) < 1e-12,
      "it booleans exactly (minus an r1 cylinder: volume - 3pi)")
L = bridge.make_line
twist = bridge.make_edge_srf([L((0, 0, 0), (1, 0, 0)), L((1, 0, 0), (1, 1, 1)),
                              L((1, 1, 1), (0, 1, 0)), L((0, 1, 0), (0, 0, 0))])
ctx, msg = run("extrudesrf", [("twist_srf", twist)], ["twist_srf", "2"])
tm = kernel.master_of(ctx.added[0]) if ctx.added else None
check(tm is not None and ob.is_airtight(tm) and rel(ob.volume(tm), 2.0) < 1e-9,
      f"a NON-planar surface (z = xy) goes along the CPlane Z: volume = 1 x 1 x 2 "
      f"({tm is not None and ob.volume(tm)!r}; {msg!r})")
ctx, msg = run("extrudesrf", [("holed_srf", holed)], ["holed_srf", "SplitAtTangents=Yes"])
check("not implemented" in (msg or ""), f"SplitAtTangents says so ({msg!r})")

print("\n-- Loft --")
# MEASURED before: the command lofted exactly two named curves with a ruled surface and
# had no options, so three circles could not be lofted at all.
rings = [("r1", bridge.make_circle((0, 0, 0), 5)), ("r2", bridge.make_circle((0, 0, 10), 8)),
         ("r3", bridge.make_circle((0, 0, 20), 4))]
ctx, msg = run("loft", rings, [""], select=["r1", "r2", "r3"])
lm = kernel.master_of(ctx.added[0]) if ctx.added else None
check(lm is not None and kernel.honesty_problem(ctx.added[0]) is None
      and "Normal" in (msg or "") and "3 curves" in (msg or ""),
      f"three circles loft into one exact Normal surface ({msg!r})")
if lm is not None:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    gap = BRepExtrema_DistShapeShape(
        BRepBuilderAPI_MakeVertex(gp_Pnt(8, 0, 10)).Vertex(), lm).Value()
    check(gap < 1e-6, f"the surface passes through the middle circle (gap {gap:.2e})")
ctx, msg = run("loft", rings, [""], select=["r1", "r2", "r3"], opts_=[("Style", "Loose")])
loose = kernel.master_of(ctx.added[0]) if ctx.added else None
if loose is not None:
    gap = BRepExtrema_DistShapeShape(
        BRepBuilderAPI_MakeVertex(gp_Pnt(8, 0, 10)).Vertex(), loose).Value()
    check(gap > 0.5, f"Style=Loose is a different surface that leaves the middle curve ({gap:.2f})")
else:
    check(False, f"Style=Loose builds ({msg!r})")
ctx, msg = run("loft", rings, [""], select=["r1", "r2", "r3"], opts_=[("Closed", True)])
check(not ctx.added and "not implemented" in (msg or ""),
      f"Closed=Yes says it is not implemented ({msg!r})")
ctx, msg = run("loft", rings[:1] + [("l1", L((0, 0, 5), (5, 0, 5)))], [""], select=["r1", "l1"])
check(not ctx.added and "all open or all closed" in (msg or ""),
      f"mixing open and closed curves is refused ({msg!r})")
ctx, msg = run("loft", rings[:1], [""], select=["r1"])
check(not ctx.added and "at least two" in (msg or ""), f"one curve is refused ({msg!r})")

print("\n-- SrfPt --")
_load("narwhal3d.surface_commands", "narwhal3d/surface_commands.py")
ctx, msg = run("srfpt", [], ["0,0,0", "10,0,0", "10,10,4", "0,10,0"])
sm = kernel.master_of(ctx.added[0]) if ctx.added else None
check(sm is not None and len(ob.unique_faces(sm)) == 1 and not ob.is_airtight(sm)
      and kernel.honesty_problem(ctx.added[0]) is None,
      f"four corners make one exact open surface ({msg!r})")
if sm is not None:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt

    def gap_to(point, shape):
        return BRepExtrema_DistShapeShape(
            BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex(), shape).Value()

    corners = max(gap_to(p, sm) for p in ((0, 0, 0), (10, 0, 0), (10, 10, 4), (0, 10, 0)))
    # Bilinear: the centre is the average of the four corners, (5, 5, 1); a planar
    # triangle fan through the corners would put it at z=0 or z=2 instead.
    check(corners < 1e-9 and gap_to((5, 5, 1), sm) < 1e-9,
          f"it passes through every corner and is bilinear (centre gap {gap_to((5, 5, 1), sm):.1e})")
    # Corners are joined in picking order, 1-2-3-4: the midpoint of side 2-3 is on the
    # surface. Joining them out of order gives a twisted bowtie with the same corners
    # and the same centre, which only this check can tell apart.
    check(gap_to((10, 5, 2), sm) < 1e-9,
          f"its sides join the corners in picking order (side 2-3 gap {gap_to((10, 5, 2), sm):.1e})")
ctx, msg = run("srfpt", [], ["0,0,0", "4,0,0", "0,3,0", ""])
tm = (kernel.master_of(ctx.added[0]) or bridge._occt_operand(ctx.added[0])) if ctx.added else None
tri_area = ob.mass_properties(tm, solid=False)["area"] if tm is not None else None
check(tri_area is not None and abs(tri_area - 6.0) < 1e-9,
      f"Enter at the fourth corner makes the exact triangle, area 6 ({tri_area}; {msg!r})")
ctx, msg = run("srfpt", [], ["0,0,0", "1,0,0", "2,0,0", ""])
check(not ctx.added and "in a line" in (msg or ""), f"collinear corners are refused ({msg!r})")

print("\n-- EdgeSrf --")
ctx, msg = run("edgesrf", [("a", L((0, 0, 0), (1, 0, 0))), ("b", L((1, 1, 1), (1, 0, 0))),
                           ("c", L((0, 1, 0), (1, 1, 1))), ("d", L((0, 0, 0), (0, 1, 0)))],
               [""], select=["a", "c", "b", "d"])
g = ctx.added[0] if ctx.added else None
f = g.Faces[0] if g is not None else None
dev = max(abs(p.Z - p.X * p.Y) for p in
          (f.PointAt(f.Domain(0).T0 + (f.Domain(0).T1 - f.Domain(0).T0) * i / 10,
                     f.Domain(1).T0 + (f.Domain(1).T1 - f.Domain(1).T0) * j / 10)
           for i in range(11) for j in range(11))) if f is not None else math.inf
check(dev < 1e-12, f"four lines of a twisted square, picked out of order and direction: "
                   f"z = xy exactly (max {dev:.1e}; {msg!r})")
R, H = 5.0, 9.0
a0 = bridge.make_arc((R, 0, 0), (R / math.sqrt(2), R / math.sqrt(2), 0), (0, R, 0))
a1 = bridge.make_arc((R, 0, H), (R / math.sqrt(2), R / math.sqrt(2), H), (0, R, H))
ctx, msg = run("edgesrf", [("A", a0), ("B", a1)], [""], select=["A", "B"])
g = ctx.added[0] if ctx.added else None
f = g.Faces[0] if g is not None else None
dev = max(abs(math.hypot(p.X, p.Y) - R) for p in
          (f.PointAt(f.Domain(0).T0 + (f.Domain(0).T1 - f.Domain(0).T0) * i / 10,
                     f.Domain(1).T0 + (f.Domain(1).T1 - f.Domain(1).T0) * j / 10)
           for i in range(11) for j in range(11))) if f is not None else math.inf
check(dev < 1e-12 and rel(area(g), math.pi / 2 * R * H) < 1e-9,
      f"two arcs: the quarter CYLINDER (every point 5 from the axis, max {dev:.1e}; area "
      f"pi R H / 2 = {math.pi / 2 * R * H:.6f}, got {g is not None and area(g)!r})")
a1r = a1.Duplicate()
a1r.Reverse()
ctx, msg = run("edgesrf", [("A", a0), ("B", a1r)], [""], select=["A", "B"])
g = ctx.added[0] if ctx.added else None
f = g.Faces[0] if g is not None else None
dev = max(abs(math.hypot(p.X, p.Y) - R) for p in
          (f.PointAt(f.Domain(0).T0 + (f.Domain(0).T1 - f.Domain(0).T0) * i / 10,
                     f.Domain(1).T0 + (f.Domain(1).T1 - f.Domain(1).T0) * j / 10)
           for i in range(11) for j in range(11))) if f is not None else math.inf
check(dev < 1e-12, f"...the same with the second arc picked REVERSED (not a twisted "
                   f"surface: max {dev:.1e})")
bend = [bridge.make_arc((0, 0, 0), (0.5, -0.3, 0.2), (1, 0, 0)),
        bridge.make_arc((1, 0, 0), (1.3, 0.5, -0.2), (1, 1, 0)),
        bridge.make_arc((1, 1, 0), (0.5, 1.3, 0.2), (0, 1, 0)),
        bridge.make_arc((0, 1, 0), (-0.3, 0.5, -0.2), (0, 0, 0))]
g = bridge.make_edge_srf(bend)
worst = 0.0
for ei in range(len(g.Edges)):
    e_ = g.Edges[ei]
    for k in range(21):
        p = e_.PointAt(e_.Domain.T0 + (e_.Domain.T1 - e_.Domain.T0) * k / 20)
        worst = max(worst, min(nearest.closest_point_on_curve(c, (p.X, p.Y, p.Z)).distance
                               for c in bend))
check(worst < 1e-9, f"\"the edges ... precisely match the input curves\": 4 arcs, every "
                    f"surface edge ON an input arc (max {worst:.1e})")
tri = bridge.make_edge_srf([L((0, 0, 0), (1, 0, 0)), L((1, 0, 0), (0, 1, 0)),
                            L((0, 1, 0), (0, 0, 0))])
check(rel(area(tri), 0.5) < 1e-9, f"three lines: the triangle, area 1/2 ({area(tri)!r})")
for label, lines, sel, word in (
        ("one curve", [("a", L((0, 0, 0), (1, 0, 0)))], ["a"], "2, 3 or 4"),
        ("a closed curve", [("a", L((0, 0, 0), (1, 0, 0))), ("c", HOLE)], ["a", "c"], "OPEN"),
        ("curves that do not meet", [("a", L((0, 0, 0), (1, 0, 0))),
                                     ("b", L((5, 5, 0), (6, 5, 0))),
                                     ("c", L((0, 9, 0), (1, 9, 0)))], ["a", "b", "c"],
         "do not meet")):
    ctx, msg = run("edgesrf", lines, [""], select=sel)
    check(not ctx.added and word in (msg or ""), f"{label} is refused ({msg!r})")

print("\n-- save + reload rebuilds them (recipes: prism, edge_surface, planar_face) --")
TMP = tempfile.mkdtemp(prefix="g91_")
f3 = r3.File3dm()
keep = [e, twist, bridge.make_edge_srf([a0, a1])]
want = [ob.face_types(kernel.master_of(x)) for x in keep]
wv = ob.volume(kernel.master_of(e))
for x in keep:
    f3.Objects.Add(x, None)
path = os.path.join(TMP, "g91.3dm")
f3.Write(path, 8)
kernel.forget_masters()
back = [o.Geometry for o in r3.File3dm.Read(path).Objects]
restored, lost = bridge.rehydrate_document(back)
got = [ob.face_types(kernel.master_of(x)) if kernel.master_of(x) is not None else None
       for x in back]
check(restored == 3 and lost == 0 and got == want
      and abs(ob.volume(kernel.master_of(back[0])) - wv) < 1e-9,
      f"ExtrudeSrf and EdgeSrf results rebuild from their recipes ({restored}/{lost}; {got})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("PLANARSRF / EXTRUDESRF / EDGESRF PASSED")
