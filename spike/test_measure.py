"""G-72: Area / Volume / BoundingBox measure the GEOMETRY, not the display proxy.

Rule 0: a solid may be a MASTER (the geometry) + a triangulated PROXY (display only).
`Volume` used to integrate the proxy's triangles and label the answer "exact" --
MEASURED on box-union-cylinder: 1140.84494 for a true 1000+45*pi = 1141.37167, -4.6e-4.
That is the number the owner sends to manufacture.

Every gate below FAILS on the pre-G-72 code (named per gate). The OCCT gates are SKIPPED,
not failed, when `vendor/occt` is absent, like spike/test_kernel.py.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_measure.py
"""
import importlib.util
import math
import os
import re
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


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
analysis = _load("narwhal3d.analysis", "narwhal3d/analysis.py")
arclength = _load("narwhal3d.arclength", "narwhal3d/arclength.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

checks = 0
failures = []
skipped = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


def skip(label, why):
    skipped.append(label)
    print(f"  skip {label}  ({why})")


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in backed by a REAL NarwhalModel (as test_project_cmd does)."""

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []

    def add(self, geom, name):
        self.model.add_object(geom, name, 0)
        return name

    def select(self, name):
        self._selected = [name]

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None

    def note_change(self, label=None):
        pass

    # G-73: commands that ADD objects (centroid points, MarkRadius) and select sets
    added = 0

    def add_geometry(self, geom, base_name, layer_index=None):
        FakeCtx.added += 1
        nm = f"{base_name}_{FakeCtx.added}"
        self.model.add_object(geom, nm, 0)
        return nm, nm

    def select_many(self, names):
        self._selected = list(names)

    def select_by_names(self, names):
        self._selected = list(names)
        return list(names)

    cplane = None

    def active_cplane(self):
        return self.cplane


def run(ctx, cmd_key, name):
    """Run a measuring command on `name` (preselected) and return its message."""
    ctx.select(name)
    c = type(commands.COMMANDS[cmd_key])()
    msg, _done = c.begin(ctx)
    return msg


MEAS = re.compile(r"^(\w+) = ([-0-9.eE+]+) \(\+/- ([0-9.eE+-]+)\)(?: square \w+)?$")


def parse(msg):
    """(label, value, err) from the reference CAD application's `Label = v (+/- e)`, or None."""
    m = MEAS.match(msg or "")
    return (m.group(1), float(m.group(2)), float(m.group(3))) if m else None


def first_number(msg):
    m = re.search(r"[:=]\s*([-0-9.eE+]+)", msg or "")
    return float(m.group(1)) if m else None


# Reached through getattr so this suite FAILS CLEANLY (rather than crashing) when run
# against the pre-G-72 analysis.py -- which is how every gate below was watched go RED.
exact_mass = getattr(analysis, "exact_mass", lambda *a, **k: None)
occt_shape_of = getattr(analysis, "occt_shape_of", lambda *a, **k: None)

have_occt = ob.available()
if not have_occt:
    print(f"  (OCCT absent: {ob.unavailable_reason()})")

ctx = FakeCtx()

# ===========================================================================
# 1. Volume / Area of box-union-cylinder are the CLOSED FORMS, not the proxy's.
#    box 10x10x10 at the origin; cylinder r=3, h=10, axis (10,5): half of it sticks out.
#    V = 1000 + (pi*9*10)/2              = 1000 + 45*pi
#    A = box 600 - the 2 half-discs swallowed by the box top/bottom are NOT removed
#        (they stay box face) ... worked out: box faces 600, minus the 6x10 slot the
#        cylinder opens in the x=10 face (60), plus the outer half wall (pi*3*10 = 30pi),
#        plus the two outer half-discs on top and bottom (2 * 9pi/2 = 9pi)
#      = 540 + 39*pi
#    PRE-G-72: Volume printed 1140.84 "exact" (-4.6e-4) -> RED.
# ===========================================================================
print("\n-- box-union-cylinder: master, not proxy --")
V_BU = 1000 + 45 * math.pi
A_BU = 540 + 39 * math.pi
if have_occt:
    bu = bridge.boolean_brep(bridge.make_box((0, 0, 0), (10, 10, 0), 10),
                             bridge.make_cylinder((10, 5, 0), 3, 10), "UNION")
    check(kernel.master_of(bu) is not None, "premise: the union has an analytic master")
    proxy_v = analysis.solid_volume(bu, 1, 1)
    check(abs(proxy_v - V_BU) / V_BU > 1e-5,
          f"premise: the PROXY really is wrong ({proxy_v!r} vs {V_BU!r}) -- otherwise "
          f"this test could not tell master from proxy")
    mp = exact_mass(bu, solid=True)
    check(mp is not None and abs(mp["volume"] - V_BU) / V_BU < 1e-9,
          f"exact volume == 1000+45pi to 1e-9 relative "
          f"(got {mp and mp['volume']!r}, want {V_BU!r})")
    check(mp is not None and abs(mp["area"] - A_BU) / A_BU < 1e-9,
          f"exact area == 540+39pi to 1e-9 relative "
          f"(got {mp and mp['area']!r}, want {A_BU!r})")
    ctx.add(bu, "Union1")
    pv = parse(run(ctx, "volume", "Union1"))
    check(pv is not None and pv[0] == "Volume",
          f"Volume prints the reference CAD application's `Volume = v (+/- e)` (got {run(ctx, 'volume', 'Union1')!r})")
    if pv:
        check(f"{pv[1]:.9g}" == f"{V_BU:.9g}",
              f"the COMMAND's volume is the closed form to its printed precision "
              f"({pv[1]:.9g} vs {V_BU:.9g})")
        check(abs(pv[1] - V_BU) <= pv[2] and pv[2] < 1e-6 * V_BU,
              f"its +/- is an honest, tight bound (err {pv[2]:g}, actual "
              f"{abs(pv[1] - V_BU):.1e})")
    pa = parse(run(ctx, "area", "Union1"))
    check(pa is not None and pa[0] == "Area" and abs(pa[1] - A_BU) <= pa[2]
          and pa[2] < 1e-6 * A_BU,
          f"the COMMAND's area is 540+39pi within its own tight +/- ({pa})")
    # a box: the reference CAD application prints `Area = 60`, no trailing zeros
    ctx.add(bridge.make_box((0, 0, 0), (6, 10, 0), 1.0), "Box6x10")
    vb = run(ctx, "volume", "Box6x10")
    check(vb.startswith("Volume = 60 (+/- "),
          f"a 6x10x1 box prints `Volume = 60 (+/- ...)` like the reference CAD application ({vb!r})")
else:
    skip("box-union-cylinder volume/area", "no OCCT")

# ===========================================================================
# 2. A face OCCT integrates badly (a NURBS SurfaceOfExtrusion) is still measured right:
#    GProp at explicit eps is wrong by up to 8.5e-3 on it and does not converge
#    (MEASURED, probe_measure_bspline.py). Oracle: area(profile) * h, the area by Green's
#    theorem with Gauss-Legendre on the EXACT curve -- independent of every code path here.
#    PRE-G-72: the proxy. -> RED. Also the gate on _nurbs_where_needed.
# ===========================================================================
print("\n-- a NURBS extrusion: the face GProp mis-integrates --")
GL = [(-0.9739065285171717, 0.0666713443086881), (-0.8650633666889845, 0.1494513491505806),
      (-0.6794095682990244, 0.2190863625159820), (-0.4333953941292472, 0.2692667193099963),
      (-0.1488743389816312, 0.2955242247147529), (0.1488743389816312, 0.2955242247147529),
      (0.4333953941292472, 0.2692667193099963), (0.6794095682990244, 0.2190863625159820),
      (0.8650633666889845, 0.1494513491505806), (0.9739065285171717, 0.0666713443086881)]


def green_area(c):
    ks = sorted(set(c.Knots.ToList()))
    tot = 0.0
    for a, b in zip(ks, ks[1:]):
        h, m = (b - a) / 2, (a + b) / 2
        for x, w in GL:
            p = c.PointAt(m + h * x)
            d = c.DerivativeAt(m + h * x, 1)[1]
            tot += w * h * (p.X * d.Y - p.Y * d.X)
    return abs(tot) / 2


PTS = [(0, 0, 0), (6, -1, 0), (9, 4, 0), (5, 8, 0), (-1, 6, 0), (0, 0, 0)]
prof = r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in PTS], 3)
H = 7.0
V_EX = green_area(prof) * H
A_EX = 2 * green_area(prof) + arclength.length_of(prof) * H
if have_occt:
    ex = bridge.make_extrusion(prof, H)
    shape = occt_shape_of(ex) or bridge._occt_operand(ex)
    check(shape is not None and "EXTRUSION" in ob.face_types(shape),
          f"premise: the extrusion is an analytic EXTRUSION face "
          f"({shape is not None and ob.face_types(shape)})")
    mp = exact_mass(ex, solid=True)
    check(mp is not None and abs(mp["volume"] - V_EX) / V_EX < 1e-9,
          f"NURBS-extrusion volume == Green-oracle to 1e-9 relative "
          f"(got {mp and mp['volume']!r}, want {V_EX!r})")
    check(mp is not None and abs(mp["area"] - A_EX) / A_EX < 1e-7,
          f"NURBS-extrusion area == oracle to 1e-7 relative "
          f"(got {mp and mp['area']!r}, want {A_EX!r})")
    check(mp is not None and mp["converted_faces"] == 1,
          f"exactly the ONE non-elementary face was NURBS-converted "
          f"({mp and mp['converted_faces']}) -- the planes keep their exact integral")
    ctx.add(ex, "Extrude1")
    pv = parse(run(ctx, "volume", "Extrude1"))
    check(pv is not None and abs(pv[1] - V_EX) <= max(pv[2], 5e-9 * V_EX),
          f"the COMMAND's extrusion volume is within its own +/- of the oracle ({pv})")
else:
    skip("NURBS extrusion", "no OCCT")

# ===========================================================================
# 3. BoundingBox: a master-backed cylinder's box is [c-r, c+r] EXACTLY.
#    cylinder r=3 at (10,5), h=10, unioned with a box wholly inside it -> one master
#    whose proxy box sits INSIDE the truth by the chord (MEASURED y 2.0084..7.9916).
#    PRE-G-72: the proxy box. -> RED.
# ===========================================================================
print("\n-- BoundingBox of a master is tight --")
if have_occt:
    cyl = bridge.boolean_brep(bridge.make_cylinder((10, 5, 0), 3, 10),
                              bridge.make_box((9, 4, 0), (11, 6, 0), 2), "UNION")
    check(kernel.master_of(cyl) is not None, "premise: the cylinder union has a master")
    pb = cyl.GetBoundingBox()
    check(abs(pb.Min.Y - 2.0) > 1e-4,
          f"premise: the proxy box is NOT exact (proxy ymin {pb.Min.Y!r})")
    lo, hi = analysis.bounding_box(cyl)
    got = (lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z)
    want = (7.0, 2.0, 0.0, 13.0, 8.0, 10.0)
    dev = max(abs(a - b) for a, b in zip(got, want))
    check(dev < 1e-12, f"bbox == [c-r, c+r] x [0, h] exactly (max dev {dev:.1e}; {got})")
    ctx.add(cyl, "Cyl1")
    msg = run(ctx, "boundingbox", "Cyl1")
    check("min (7, 2, 0)" in msg and "max (13, 8, 10)" in msg and "size (" in msg,
          f"the COMMAND reports the exact extents ({msg!r})")
else:
    skip("master bbox", "no OCCT")

# a NURBS CURVE's box is the curve's, not its control hull (rhino3dm's GetBoundingBox and
# GetTightBoundingBox both return the hull -- MEASURED y -4..6 for a true -0.975..1.970).
wave = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(2, 6, 0), r3.Point3d(5, -4, 0), r3.Point3d(8, 0, 0)], 3)
d = wave.Domain
ys = [wave.PointAt(d.T0 + (d.T1 - d.T0) * i / 200000).Y for i in range(200001)]
lo, hi = analysis.bounding_box(wave)
check(abs(lo.Y - min(ys)) < 1e-9 and abs(hi.Y - max(ys)) < 1e-9,
      f"a NURBS curve's bbox is the CURVE's (y {lo.Y:.9f}..{hi.Y:.9f}, dense truth "
      f"{min(ys):.9f}..{max(ys):.9f}), not the control hull -4..6")
check(abs(lo.X) < 1e-12 and abs(hi.X - 8) < 1e-12,
      f"...and its x extent is the end points 0..8 ({lo.X}, {hi.X})")

# ===========================================================================
# 4. Closed curves are measured EXACTLY. MEASURED (research_all_13 #004): a circle r=2
#    reports "Area = 12.566371 (+/- 4.8e-06) square millimeters" -- the exact area, where
#    the old 512-gon shoelace was 3e-4 short. Value within its printed +/- of 16 pi, and
#    the +/- itself tight (an honest but loose bound would hide a polygon estimate).
# ===========================================================================
print("\n-- curve area is exact, with the unit --")
circ = r3.Circle(r3.Point3d(0, 0, 0), 4.0).ToNurbsCurve()
ctx.add(circ, "Circle1")
msg = run(ctx, "area", "Circle1")
pc = parse(msg)
check(pc is not None and abs(pc[1] - 16 * math.pi) <= pc[2] and pc[2] < 1e-6,
      f"curve Area is 16 pi within its +/-, and the +/- is tight ({msg!r})")
check(msg.endswith(" square " + ctx.model.unit_system().lower()),
      f"the area names its unit ({msg!r})")
ctx.add(r3.Circle(r3.Point3d(20, 0, 0), 1.0).ToNurbsCurve(), "Circle2")
ctx.select_many(["Circle1", "Circle2"])
c = type(commands.COMMANDS["area"])()
msg, _done = c.begin(ctx)
pc = parse(msg.split(" square ")[0].replace("Cumulative Area", "Cumulative"))
check(msg.startswith("Cumulative Area = ") and msg.endswith(" for 2 objects")
      and pc is not None and abs(pc[1] - 17 * math.pi) <= pc[2],
      f"two curves: the measured cumulative line, 17 pi ({msg!r})")
# MEASURED (#004): Units=Inch on 66 mm^2 -> "Area = 0.102300205 (+/- ...) square inches"
# (the 16 pi circle here: 16 pi / 645.16 in^2); the choice is sticky.
c = type(commands.COMMANDS["area"])()
ctx.select("Circle1")
c.feed("Units=Inch", ctx)
c2 = type(commands.COMMANDS["area"])()
check(c2.opt["Units"] == "Inch", "Area Units is sticky across runs (MEASURED)")
ctx.select("Circle1")
msg, _d = c2.begin(ctx)
pc = parse(msg.split(" square ")[0])
check(msg.endswith(" square inches") and pc is not None
      and abs(pc[1] - 16 * math.pi / 645.16) <= pc[2] + 1e-15,
      f"Units=Inch reports square inches, 16 pi / 645.16 ({msg!r})")
c2.feed("Units=ModelUnits", ctx)
ctx.add(bridge.make_line((0, 0, 0), (4, 0, 0)), "Open1")
ctx.select("Open1")
c = type(commands.COMMANDS["area"])()
msg, done = c.begin(ctx)
check(not done and msg is None and c.step_index == 0,
      f"an open curve is not measured: the prompt is asked again ({msg!r}, {done})")

# ===========================================================================
# G-73: Angle, Radius, Diameter, AreaCentroid, VolumeCentroid, EvaluatePt.
# PRE-G-73 none of the six exists, so every gate below is RED (COMMANDS.get -> None).
# ===========================================================================
print("\n-- G-73: the measuring set --")
G73 = ("angle", "radius", "diameter", "areacentroid", "volumecentroid", "evaluatept")
for key in G73:
    check(key in commands.COMMANDS, f"`{key}` is a registered command")


def drive(ctx, key, lines, select=None):
    """Start command `key` (with `select` preselected) and feed `lines`; returns
    ``(command, [messages], done)``. A missing command returns (None, [], True)."""
    proto = commands.COMMANDS.get(key)
    if proto is None:
        return None, [], True
    ctx.select_many(select or [])
    c = type(proto)()
    msgs = []
    msg, done = c.begin(ctx)
    msgs.append(msg)
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
        msgs.append(msg)
    return c, msgs, done


def last(msgs):
    return next((m for m in reversed(msgs) if m), "") if msgs else ""


def count_objects(ctx):
    return len(ctx.model.file.Objects)


# -- Angle: four points, the reference CAD application's prompts and `Angle = %s` -------------------
c, msgs, done = drive(ctx, "angle", ["0,0,0", "10,0,0", "0,0,0", "5,5,0"])
check(c is not None and c.prompt == "" or (c and done), "Angle finishes after four picks")
# MEASURED (research_all_13 #002): the report has three decimals ("Angle = 90.000").
check(last(msgs) == "Angle = 45.000", f"two imaginary lines at 45 deg -> `Angle = 45.000` ({last(msgs)!r})")
proto = commands.COMMANDS.get("angle")
check(proto is not None and type(proto)().prompt.startswith("Start of first line"),
      "Angle's first prompt is the reference CAD application's `Start of first line`")
# a tiny angle: acos(u.v/|u||v|) returns EXACTLY 0 here (the dot rounds to 1.0); atan2 of
# the cross and dot keeps it. Oracle: atan(1e-9) in degrees.
# The report now prints three decimals (measured), so the precision guard reads the value
# the command prints FROM -- analysis.angle_between -- with the same oracle and tolerance.
want = math.degrees(math.atan(1e-9))
got = analysis.angle_between((1.0, 0.0, 0.0), (1.0, 1e-9, 0.0))
check(abs(got - want) / want < 1e-9, f"a 1e-9 rad angle is {want:.10g} deg, not 0 ({got!r})")
# MEASURED (research_all_13 #002): an end ON the start re-asks "End of first line:"
# silently, and the next point is taken instead -- never an angle of 0.
c, msgs, done = drive(ctx, "angle", ["0,0,0", "0,0,0"])
check(not done and msgs[-1] is None and c.prompt.startswith("End of first line"),
      f"a zero-length first line re-asks its end silently ({c.prompt!r})")
for line in ("1,0,0", "0,0,0", "0,1,0"):
    msg, done = c.feed(line, ctx)
check(done and msg == "Angle = 90.000", f"...and the next end is used ({msg!r})")
# TwoObjects: two line OBJECTS (30 deg apart) and a refusal for a circle
ctx.add(bridge.make_line((0, 0, 0), (10, 0, 0)), "LineA")
ctx.add(bridge.make_line((1, 1, 0), (1 + math.cos(math.pi / 6), 1 + math.sin(math.pi / 6), 0)),
        "LineB")
_c, msgs, _d = drive(ctx, "angle", ["TwoObjects", ""], select=["LineA", "LineB"])
check(first_number(last(msgs)) is not None and abs(first_number(last(msgs)) - 30) < 1e-9,
      f"Angle TwoObjects on two lines 30 deg apart -> 30 ({last(msgs)!r})")
_c, msgs, _d = drive(ctx, "angle", ["TwoObjects", ""], select=["LineA", "Circle1"])
check("not a line" in last(msgs), f"TwoObjects refuses a circle by name ({last(msgs)!r})")
_c, msgs, _d = drive(ctx, "angle", ["SubCrv"])
check("not implemented yet" in last(msgs), f"Angle SubCrv says so ({last(msgs)!r})")

# MEASURED (research_all_13 #040): w0,0,0 -> w3,4,0 reports angles, deltas and the distance.
c, msgs, done = drive(ctx, "distance", ["0,0,0", "3,4,0"])
unit = ctx.model.unit_system().lower()
check(c.steps[0][0] == "First point for distance:" and done and last(msgs) ==
      "World angles and deltas: xy = 53.130  elevation = 0.000  dx = 3.000  dy = 4.000  "
      f"dz = 0.000\nDistance = 5.000 {unit}", f"Distance: the measured report ({last(msgs)!r})")
c, msgs, done = drive(ctx, "distance", ["0,0,0", "0,0,-2"])
check("elevation = -90.000" in last(msgs) and "dz = -2.000" in last(msgs),
      f"Distance: a downward pair gives elevation -90 ({last(msgs)!r})")

# -- Radius / Diameter --------------------------------------------------------
RAD = re.compile(r"^Radius = ([-0-9.eE+]+)    Diameter = ([-0-9.eE+]+)")
arc = r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 3, 0), r3.Point3d(6, 0, 0)).ToNurbsCurve()
check(not arc.IsCircle(), "premise: IsCircle() is False for an ARC (so it cannot be the test)")
ctx.add(arc, "Arc3")
c, msgs, done = drive(ctx, "radius", ["3,3,0"], select=["Arc3"])
m = RAD.match(last(msgs))
check(m is not None and m.group(1) == "3" and m.group(2) == "6",
      f"Radius on an R3 arc -> `Radius = 3    Diameter = 6` ({last(msgs)!r})")
check(c is not None and not done, "...and the command WAITS for the next pick "
      "(`Press Enter when done`)")
if c is not None:
    msg, done = c.feed("", ctx)
    check(done and not (msg or "").endswith("cancelled"),
          f"Enter after a measurement FINISHES, it does not cancel ({msg!r})")
# a free-form curve: an exact rational ellipse a=6, b=3 (an affine image of a circle).
# Radius of curvature b^2/a = 1.5 at the end of the major axis, a^2/b = 12 at the minor.
ell = r3.Circle(6.0).ToNurbsCurve()
_sq = r3.Transform(1.0)
_sq.M11 = 0.5
ell.Transform(_sq)
ctx.add(ell, "Ellipse1")
c, msgs, _d = drive(ctx, "radius", ["6,0,0", "0,3,0"], select=["Ellipse1"])
r1 = RAD.match(msgs[1] or "") if len(msgs) > 1 else None
r2 = RAD.match(msgs[2] or "") if len(msgs) > 2 else None
check(r1 is not None and abs(float(r1.group(1)) - 1.5) < 1e-9,
      f"ellipse at the major-axis end: radius b^2/a = 1.5 ({msgs[1:2]!r})")
check(r2 is not None and abs(float(r2.group(1)) - 12.0) < 1e-9,
      f"...and at the minor-axis end a^2/b = 12, on the SECOND pick ({msgs[2:3]!r})")
_c, msgs, _d = drive(ctx, "radius", ["5,0,0"], select=["LineA"])
check(last(msgs) == "Radius of curvature is infinite.",
      f"a line -> the reference CAD application's `Radius of curvature is infinite.` ({last(msgs)!r})")
# MEASURED (research_all_13 note 120): a PRESELECTED circle runs at once, no point
# prompt: "Diameter = ..." then "Radius = ..." (radius-4 circle here).
_c, msgs, done = drive(ctx, "radius", [], select=["Circle1"])
unit = ctx.model.unit_system().lower()
check(done and msgs[0] == f"Diameter = 8 {unit}\nRadius = 4 {unit}",
      f"a preselected circle prints Diameter and Radius at once ({msgs[0]!r})")
_c, msgs, done = drive(ctx, "radius", [], select=["Arc3"])
check(not done, "a preselected ARC (unmeasured) keeps the point prompt")
_c, msgs, _d = drive(ctx, "radius", ["40,40,0"], select=["Arc3"])
check("not on a curve" in last(msgs) and not RAD.match(last(msgs)),
      f"a pick nowhere near the curve is refused, not measured ({last(msgs)!r})")
# nothing selected: the NEAREST curve is the one picked
_c, msgs, _d = drive(ctx, "radius", ["3,3,0"])
m = RAD.match(last(msgs))
check(m is not None and m.group(1) == "3",
      f"with nothing selected, the curve under the pick is measured ({last(msgs)!r})")
# Diameter reports its own value first
_c, msgs, _d = drive(ctx, "diameter", ["3,3,0"], select=["Arc3"])
check(last(msgs) == "Diameter = 6    Radius = 3",
      f"Diameter on the R3 arc -> `Diameter = 6    Radius = 3` ({last(msgs)!r})")
# MarkRadius: a point at the evaluated point + the curvature circle through it
before = count_objects(ctx)
_c, msgs, _d = drive(ctx, "radius", ["MarkRadius=Yes", "6,0,0"], select=["Ellipse1"])
new = [o.Geometry for o in list(ctx.model.file.Objects)[before:]]
pts = [g for g in new if isinstance(g, r3.Point)]
circles = [g for g in new if isinstance(g, r3.Curve) and g.TryGetArc() is not None]
ok_mark = (len(pts) == 1 and len(circles) == 1
           and abs(circles[0].TryGetArc().Radius - 1.5) < 1e-9
           and math.dist((circles[0].TryGetArc().Center.X, circles[0].TryGetArc().Center.Y),
                         (4.5, 0.0)) < 1e-9
           and abs(circles[0].TryGetArc().Plane.ZAxis.Z) > 1 - 1e-9)
check(ok_mark, "MarkRadius adds the point and the OSCULATING circle (R1.5, centre (4.5,0), "
      f"in the ellipse's plane) ({[type(g).__name__ for g in new]})")
_c, msgs, _d = drive(ctx, "radius", ["Units"])
check("not implemented yet" in last(msgs), f"Radius Units says so ({last(msgs)!r})")
if have_occt:
    ctx.add(bridge.make_cylinder((20, 0, 0), 4.0, 10.0), "CylR4")
    _c, msgs, _d = drive(ctx, "radius", ["24,0,5"], select=["CylR4"])
    m = RAD.match(last(msgs))
    check(m is not None and m.group(1) == "4",
          f"Radius on a cylinder's wall reads the analytic R4 ({last(msgs)!r})")
    _c, msgs, _d = drive(ctx, "radius", ["24,0,0"], select=["CylR4"])
    m = RAD.match(last(msgs))
    check(m is not None and m.group(1) == "4",
          f"...and on its rim (a tie with the flat cap) the WALL wins ({last(msgs)!r})")
    ctx.add(bridge.make_sphere((0, 30, 0), 2.5), "SphR")
    _c, msgs, _d = drive(ctx, "radius", ["0,30,2.5"], select=["SphR"])
    m = RAD.match(last(msgs))
    check(m is not None and m.group(1) == "2.5",
          f"Radius on a sphere reads its analytic R2.5 ({last(msgs)!r})")
    # a master proxy: the boolean's wall is the CYLINDER face of the master, while the
    # proxy's triangle there has curvature 0 -- reading the proxy would say "infinite"
    _c, msgs, _d = drive(ctx, "radius", ["13,5,5"], select=["Union1"])
    m = RAD.match(last(msgs))
    check(m is not None and m.group(1) == "3",
          f"Radius on box-union-cylinder's wall reads the MASTER's R3 ({last(msgs)!r})")
    # its BOTTOM rim: 0 from the wall AND the bottom plane, and in the master's face order
    # that plane is face 2, the wall face 4 (MEASURED) -- so only a real curved-wins
    # tie-break reads 3 here. (At the TOP rim the wall comes first, so that pick cannot
    # tell.)
    _c, msgs, _d = drive(ctx, "radius", ["13,5,0"], select=["Union1"])
    m = RAD.match(last(msgs))
    check(m is not None and m.group(1) == "3",
          f"...and on its bottom RIM the wall beats the flat bottom ({last(msgs)!r})")
else:
    skip("Radius on solids", "no OCCT")

# -- AreaCentroid ---------------------------------------------------------------
CEN = re.compile(r"^(Cumulative )?(Area|Volume) Centroid = ([^ ]+) \(\+/- ([^)]+)\)(.*)$")


def parse_centroid(msg):
    m = CEN.match(msg or "")
    if not m:
        return None
    xyz = [float(v) for v in m.group(3).split(",")]
    err = [float(v) for v in m.group(4).split(",")]
    return xyz, err, m.group(5)


def honest(pc, truth, label):
    """The printed centroid is within its printed +/- of the truth, per axis."""
    ok = pc is not None and all(abs(pc[0][i] - truth[i]) <= pc[1][i] for i in range(3))
    check(ok, f"{label}: printed {pc and pc[0]} +/- {pc and pc[1]} contains the truth "
              f"{tuple(round(t, 12) for t in truth)}")
    return ok


# a TILTED circle: its centroid is its centre, exactly -- the 512-gon would also get that
# by symmetry, so the asymmetric NURBS region below is the gate that needs exactness.
import types as _types  # noqa: E402

tilt = _types.SimpleNamespace(xaxis=(1.0, 0.0, 0.0),
                              yaxis=(0.0, math.cos(0.7), math.sin(0.7)))
ctx.add(bridge.make_circle((3, -2, 1), 4.0, tilt), "TiltCircle")
before = count_objects(ctx)
_c, msgs, _d = drive(ctx, "areacentroid", [], select=["TiltCircle"])
pc = parse_centroid(last(msgs))
check(pc is not None and pc[2] == "", f"The reference CAD application's `Area Centroid = x,y,z (+/- ...)` "
      f"({last(msgs)!r})")
honest(pc, (3, -2, 1), "tilted circle centroid = its centre")
new = [o.Geometry for o in list(ctx.model.file.Objects)[before:]]
check(len(new) == 1 and isinstance(new[0], r3.Point)
      and math.dist((new[0].Location.X, new[0].Location.Y, new[0].Location.Z),
                    (3, -2, 1)) < 1e-9,
      "a POINT OBJECT is placed at the centroid, as the reference CAD application does")
# the asymmetric cubic NURBS region from section 2 (in z=0). Oracle: the shoelace centroid
# of a 400 000-gon on the exact curve (independent of Green/Gauss-Legendre), ~1e-10.
ctx.add(prof, "Blob")
N = 400000
d = prof.Domain
P = [prof.PointAt(d.T0 + (d.T1 - d.T0) * i / N) for i in range(N)]
A2 = cx = cy = 0.0
for i in range(N):
    a_, b_ = P[i], P[(i + 1) % N]
    cr = a_.X * b_.Y - b_.X * a_.Y
    A2 += cr
    cx += (a_.X + b_.X) * cr
    cy += (a_.Y + b_.Y) * cr
BLOB_C = (cx / (3 * A2), cy / (3 * A2), 0.0)
_c, msgs, _d = drive(ctx, "areacentroid", [], select=["Blob"])
pc = parse_centroid(last(msgs))
check(pc is not None and max(abs(pc[0][i] - BLOB_C[i]) for i in range(3)) < 1e-8,
      f"asymmetric NURBS region: centroid == the 400k-gon oracle to 1e-8 "
      f"({pc and pc[0]} vs {BLOB_C})")
check(pc is not None and max(pc[1]) < 1e-8,
      f"...and its printed +/- is tight ({pc and pc[1]})")
# cumulative: R1 at the origin + R2 at (10,0): area-weighted (pi*0 + 4pi*10)/5pi = 8
ctx.add(bridge.make_circle((0, 0, 0), 1.0), "C1")
ctx.add(bridge.make_circle((10, 0, 0), 2.0), "C2")
_c, msgs, _d = drive(ctx, "areacentroid", [], select=["C1", "C2"])
pc = parse_centroid(last(msgs))
check(pc is not None and last(msgs).startswith("Cumulative Area Centroid = ")
      and pc[2] == " for 2 planar curves",
      f"two curves -> the reference CAD application's `Cumulative ... for 2 planar curves` ({last(msgs)!r})")
honest(pc, (8, 0, 0), "cumulative centroid of R1 + R2 circles")
# MEASURED (research_all_13 #005): an unmeasurable selection (a point there) is discarded
# and the object prompt is asked again; no centroid, no point.
before = count_objects(ctx)
c, msgs, done = drive(ctx, "areacentroid", [], select=["LineA"])
check(not done and msgs[-1] is None and c.step_index == 0 and count_objects(ctx) == before,
      f"an open curve is discarded and the prompt asked again ({msgs!r})")
_c, msgs, done = drive(ctx, "areacentroid", [""])
check(done, "nothing selected: the command PROMPTS, and an empty Enter ends it")

# -- VolumeCentroid: box-union-cylinder, closed form ----------------------------
# box 1000 at (5,5,5); outer half-cylinder 45pi with centroid x = 10 + 4r/(3pi) = 10 + 4/pi
V_HALF = 45 * math.pi
BU_C = ((1000 * 5 + V_HALF * (10 + 4 / math.pi)) / (1000 + V_HALF), 5.0, 5.0)
if have_occt:
    before = count_objects(ctx)
    _c, msgs, _d = drive(ctx, "volumecentroid", [], select=["Union1"])
    pc = parse_centroid(last(msgs))
    check(pc is not None and last(msgs).startswith("Volume Centroid = "),
          f"The reference CAD application's `Volume Centroid = x,y,z (+/- ...)` ({last(msgs)!r})")
    honest(pc, BU_C, "box-union-cylinder volume centroid (closed form)")
    check(pc is not None and max(pc[1]) < 1e-8,
          f"...to a tight +/- -- the PROXY's centroid is off by ~1e-4 ({pc and pc[1]})")
    new = [o.Geometry for o in list(ctx.model.file.Objects)[before:]]
    check(len(new) == 1 and isinstance(new[0], r3.Point)
          and math.dist((new[0].Location.X, new[0].Location.Y, new[0].Location.Z),
                        BU_C) < 1e-8, "VolumeCentroid places its point at the centroid")
    _c, msgs, _d = drive(ctx, "volumecentroid", [], select=["Union1", "Box6x10"])
    check(last(msgs).startswith("Cumulative Volume Centroid = ")
          and last(msgs).endswith(" for 2 solids"),
          f"two solids -> `Cumulative ... for 2 solids` ({last(msgs)!r})")
    # cumulative closed form: + the 6x10x1 box (60 at (3,5,0.5))
    VB = 1000 + V_HALF
    honest(parse_centroid(last(msgs)),
           tuple((VB * BU_C[i] + 60 * (3, 5, 0.5)[i]) / (VB + 60) for i in range(3)),
           "cumulative volume centroid")
    _c, msgs, _d = drive(ctx, "volumecentroid", [], select=["Circle1"])
    check("not a solid" in last(msgs) and parse_centroid(last(msgs)) is None,
          f"a curve is refused by name ({last(msgs)!r})")
else:
    skip("VolumeCentroid", "no OCCT")

# -- EvaluatePt: world AND CPlane coordinates -----------------------------------
cp_mod = sys.modules["narwhal3d.cplane"]
ctx.cplane = cp_mod.CPlane((1, 2, 3), (0, 1, 0), (0, 0, 1))   # a moved Right-like plane
c, msgs, done = drive(ctx, "evaluatept", ["w5,6,7"])
# typed `w5,6,7` is WORLD (5,6,7); in that CPlane: x = 6-2 = 4, y = 7-3 = 4, z = along
# x cross y = (1,0,0) -> 5-1 = 4
check(last(msgs) == "Point in world coordinates = 5,6,7    CPlane coordinates = 4,4,4",
      f"EvaluatePt prints the reference CAD application's line, in world and CPlane coordinates ({last(msgs)!r})")
check(c is not None and not done, "EvaluatePt waits for the next point (`Press Enter when "
      "done`)")
if c is not None:
    _m, done = c.feed("", ctx)
    check(done, "...and Enter finishes it")
ctx.cplane = None
_c, msgs, _d = drive(ctx, "evaluatept", ["Label"])
check("not implemented yet" in last(msgs), f"EvaluatePt Label says so ({last(msgs)!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed"
      + (f", {len(skipped)} skipped" if skipped else ""))
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("MEASURE (G-72, G-73) PASSED")
