"""Synthetic command regressions for Fillet on segments of one joined curve.

Inputs provide world-space pick points through the command's normal pick_points
interface. OURS: two picks nearest the same end of one segment resolve to that
corner, including the seam of a closed curve. Opposite ends and unconnected open
ends remain refused. Tests assert the exact joined curve and rational arc.

Run with scripts/run_suite.sh spike/test_fillet_segments.py.
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


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
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


class Ctx:
    def __init__(self, named):
        self.model = model.NarwhalModel()
        self.n = 0
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        self.n += 1
        nm = f"{base_name}_{self.n}"
        self.model.add_object(geom, nm, 0)
        return nm, nm

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if (o.Attributes.Name or "").lower() == (name or "").lower():
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass

    def curves(self):
        return [(o.Attributes.Name, o.Geometry) for o in self.model.file.Objects]


def drive(named, options, picks):
    """Run Fillet: `options` lines, then each ``(name, point|None)`` answered as the
    viewport does it (point -> `pick_points[step]`, then the name fed)."""
    ctx = Ctx(named)
    c = type(commands.COMMANDS["fillet"])()
    msg, done = c.begin(ctx)
    for line in options:
        msg, done = c.feed(line, ctx)
    for name, point in picks:
        if done:
            break
        if point is not None:
            pp = dict(getattr(c, "pick_points", None) or {})
            pp[c.step_index] = point
            c.pick_points = pp
        msg, done = c.feed(name, ctx)
    return ctx, msg, done


def segments(curve):
    """Every single segment, nested polylines inside a polycurve included. (Written here,
    not borrowed from FilletCmd, so a mutation there cannot also fool the measurement.)"""
    parts = bridge.explode_curve(curve)
    if not parts:
        return [curve]
    return [s for p in parts for s in segments(p)]


def xyz(p):
    return (p.X, p.Y, p.Z)


def arcs_of(curve):
    """``[(radius, centre)]`` for every TRUE arc segment (rational degree 2)."""
    out = []
    for s in segments(curve):
        n = s.ToNurbsCurve()
        a = s.TryGetArc(1e-9) if not s.IsLinear() else None
        if a is not None and n.Degree == 2 and n.IsRational:
            out.append((a.Radius, xyz(a.Center)))
    return out


def sharp_corner(curve, corner):
    """True when two LINE segments of `curve` meet at `corner` at a real angle."""
    ends = []
    for s in segments(curve):
        if not s.IsLinear():
            continue
        a, b = xyz(s.PointAtStart), xyz(s.PointAtEnd)
        if math.dist(a, corner) < 1e-9:
            ends.append((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
        elif math.dist(b, corner) < 1e-9:
            ends.append((a[0] - b[0], a[1] - b[1], a[2] - b[2]))
    if len(ends) != 2:
        return False
    u, v = ends
    cosang = sum(x * y for x, y in zip(u, v)) / (math.hypot(*u) * math.hypot(*v))
    return abs(cosang) < 1 - 1e-6


def joined(points, closed=False):
    segs = [bridge.make_line(a, b) for a, b in zip(points, points[1:])]
    out = bridge.join_curves(list(enumerate(segs)), 0.01)
    assert len(out) == 1 and out[0][2] == closed, out
    return out[0][1]


SQ = joined([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0)], closed=True)

print("-- THE SPEC'S GATE: one closed 10x10 polyline, picks near (10,1) and (9,0) --")
ctx, msg, done = drive([("Sq", SQ)], ["Radius=2"], [("Sq", (10, 1, 0)), ("Sq", (9, 0, 0))])
objs = ctx.curves()
g = objs[0][1] if len(objs) == 1 else None
check(done and len(objs) == 1 and isinstance(g, r3.Curve) and g.IsClosed,
      f"ONE closed curve in the document, not two pieces ({[n for n, _ in objs]}; {msg!r})")
arcs = arcs_of(g) if g is not None else []
check(len(arcs) == 1 and abs(arcs[0][0] - 2) < 1e-9
      and math.dist(arcs[0][1], (8, 2, 0)) < 1e-9,
      f"exactly ONE rational degree-2 arc, radius 2, centred (8, 2): at the (10, 0) corner "
      f"({arcs})")
check(g is not None and all(sharp_corner(g, c) for c in ((0, 0, 0), (10, 10, 0), (0, 10, 0))),
      "the other three corners stay SHARP")
check(g is not None and len(segments(g)) == 5, f"4 lines + 1 arc ({g is not None and len(segments(g))})")

print("\n-- across the SEAM of the closed curve (last and first segment) --")
ctx, msg, done = drive([("Sq", SQ)], ["Radius=2"], [("Sq", (0.5, 0, 0)), ("Sq", (0, 0.5, 0))])
g = ctx.curves()[0][1] if len(ctx.curves()) == 1 else None
arcs = arcs_of(g) if g is not None else []
check(g is not None and g.IsClosed and len(arcs) == 1 and math.dist(arcs[0][1], (2, 2, 0)) < 1e-9
      and sharp_corner(g, (10, 0, 0)),
      f"the (0, 0) corner is rounded, centred (2, 2), the curve still closed ({arcs}; {msg!r})")

print("\n-- refusals on one curve: nothing changes --")
for picks, words, label in (
        ([("Sq", (3, 0, 0)), ("Sq", (7, 0, 0))], "same segment", "both picks on ONE segment"),
        ([("Sq", (5, 0, 0)), ("Sq", (5, 10, 0))], "do not meet", "opposite segments"),
        ([("Sq", None), ("Sq", None)], "DIFFERENT", "typed names, no pick points")):
    ctx, msg, done = drive([("Sq", SQ)], ["Radius=2"], picks)
    check(done and words in (msg or "") and len(ctx.curves()) == 1
          and ctx.curves()[0][0] == "Sq",
          f"{label}: refused ({msg!r})")

print("\n-- Trim=No on one curve: the arc is added, the curve untouched --")
ctx, msg, done = drive([("Sq", SQ)], ["Radius=2", "Trim=No"],
                       [("Sq", (10, 1, 0)), ("Sq", (9, 0, 0))])
names = [n for n, _ in ctx.curves()]
arc = [g for n, g in ctx.curves() if n != "Sq"]
check("Sq" in names and len(names) == 2 and arc and arcs_of(arc[0])
      and math.dist(arcs_of(arc[0])[0][1], (8, 2, 0)) < 1e-9,
      f"Sq kept, plus the R2 arc at (10, 0) ({names})")

print("\n-- picks on TWO different joined curves use the picked SEGMENT of each --")
A = joined([(-5, 3, 0), (-5, 0, 0), (0, 0, 0)])           # ends at the origin
B = joined([(0, 0, 0), (0, 5, 0), (3, 8, 0)])             # starts there
ctx, msg, done = drive([("A", A), ("B", B)], ["Radius=1"],
                       [("A", (-1, 0, 0)), ("B", (0, 1, 0))])
cur = dict(ctx.curves())
arc = [g for n, g in cur.items() if n.startswith("Fillet")]
check(len(arc) == 1 and arcs_of(arc[0]) and abs(arcs_of(arc[0])[0][0] - 1) < 1e-9
      and math.dist(arcs_of(arc[0])[0][1], (-1, 1, 0)) < 1e-9,
      f"the R1 arc rounds the origin, centred (-1, 1) ({msg!r})")
check("A" not in cur and "B" not in cur, "A and B were replaced by their trimmed versions")
rest = [g for n, g in cur.items() if not arcs_of(g)]
ends_ok = sorted(tuple(round(c, 9) for c in xyz(p)) for g in rest
                 for p in (g.PointAtStart, g.PointAtEnd))
check(len(rest) == 2 and all(len(segments(g)) == 2 for g in rest)
      and ends_ok == sorted([(-5.0, 3.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (3.0, 8.0, 0.0)]),
      f"each stays ONE 2-segment curve, now ending at the tangent points ({ends_ok})")
ctx, msg, done = drive([("A", A), ("B", B)], ["Radius=1", "Join=Yes"],
                       [("A", (-1, 0, 0)), ("B", (0, 1, 0))])
jg = ctx.curves()[0][1] if len(ctx.curves()) == 1 else None
check(jg is not None and len(arcs_of(jg)) == 1 and len(segments(jg)) == 5,
      f"Join=Yes: one curve of 2 + arc + 2 segments ({msg!r}; "
      f"{jg is not None and [type(x).__name__ for x in segments(jg)]})")

# Fillet AGAIN, on the curve that Join=Yes just made: a polycurve that NESTS polylines
# (MEASURED: it explodes to [PolylineCurve, NurbsCurve, PolylineCurve]). Its (-5, 0)
# corner lies INSIDE the first nested polyline, so only a fully flattened segment list
# can see the two segments either side of it.
ctx2, msg, done = drive([("Path", jg)], ["Radius=0.5"],
                        [("Path", (-5, 1.5, 0)), ("Path", (-3, 0, 0))])
kg = ctx2.curves()[0][1] if len(ctx2.curves()) == 1 else None
radii = sorted(round(r, 9) for r, _c in (arcs_of(kg) if kg is not None else []))
check(kg is not None and radii == [0.5, 1.0]
      and any(math.dist(c, (-4.5, 0.5, 0)) < 1e-9 for _r, c in arcs_of(kg)),
      f"...and filleting a corner INSIDE a nested polyline of it works: arcs R1 and R0.5, "
      f"the new one centred (-4.5, 0.5) ({radii}; {msg!r})")

print("\n-- an INTERIOR segment would cut its curve in two: refused --")
# Asymmetric on purpose: the kernel keeps the arm running to the FARTHER end (OURS, see
# FilletCmd), which here is (-5, 0). That leaves the (3, 0)-(3, 5) segment detached.
# Equal arms would keep the connected side and prove nothing.
C = joined([(-5, 0, 0), (3, 0, 0), (3, 5, 0)])
CUT = bridge.make_line((0, -3, 0), (0, 3, 0))     # (not "D": that prefixes `Dynamic`)
ctx, msg, done = drive([("C", C), ("Cut", CUT)], ["Radius=1"],
                       [("C", (-1, 0, 0)), ("Cut", (0, 1, 0))])
check(done and "cut C in two" in (msg or "") and sorted(n for n, _ in ctx.curves()) == ["C", "Cut"],
      f"refused by name, nothing changed ({msg!r})")

print("\n-- a CLOSED triangle: every corner (the seam one too), picked on either side, at the"
      " vertex, or twice on one segment --")
TRI = joined([(0, 0, 0), (10, 0, 0), (5, 8, 0), (0, 0, 0)], closed=True)
TRI_CORNERS = {"A": ((0, 0, 0), (5, 8, 0), (10, 0, 0)),     # (corner, previous, next)
               "B": ((10, 0, 0), (0, 0, 0), (5, 8, 0)),
               "C": ((5, 8, 0), (10, 0, 0), (0, 0, 0))}


def towards(v, p, d):
    """The point `d` away from `v` along the straight line to `p`."""
    m = math.dist(v, p)
    return tuple(a + (b - a) * d / m for a, b in zip(v, p))


def corner_centre(v, p, q, radius):
    """Centre of the radius arc tangent to lines v-p and v-q, inside the corner: on the
    bisector, at radius / sin(half the angle). Written here, not borrowed from the kernel."""
    up = tuple((a - b) / math.dist(p, v) for a, b in zip(p, v))
    uq = tuple((a - b) / math.dist(q, v) for a, b in zip(q, v))
    half = math.acos(sum(x * y for x, y in zip(up, uq))) / 2
    bis = tuple(x + y for x, y in zip(up, uq))
    m = math.hypot(*bis)
    return tuple(a + b * radius / (math.sin(half) * m) for a, b in zip(v, bis))


for name, (v, prev_p, next_p) in TRI_CORNERS.items():
    cases = (
        ("either side", towards(v, prev_p, 0.5), towards(v, next_p, 0.5)),
        ("either side, swapped", towards(v, next_p, 0.5), towards(v, prev_p, 0.5)),
        ("both AT the corner", v, v),
        ("both on the NEXT segment", towards(v, next_p, 0.5), towards(v, next_p, 0.8)),
        ("both on the PREVIOUS segment", towards(v, prev_p, 0.5), towards(v, prev_p, 0.8)),
    )
    for label, p0, p1 in cases:
        # (not "Tri": that prefixes the option `Trim`, so the name would be read as Trim=Yes)
        ctx, msg, done = drive([("Delta", TRI)], ["Radius=1"], [("Delta", p0), ("Delta", p1)])
        objs = ctx.curves()
        g = objs[0][1] if len(objs) == 1 else None
        arcs = arcs_of(g) if g is not None else []
        others = [c for k, (c, _p, _n) in TRI_CORNERS.items() if k != name]
        check(done and g is not None and g.IsClosed and len(arcs) == 1
              and abs(arcs[0][0] - 1) < 1e-9
              and math.dist(arcs[0][1], corner_centre(v, prev_p, next_p, 1.0)) < 1e-9
              and len(segments(g)) == 4 and all(sharp_corner(g, c) for c in others),
              f"corner {name}, {label}: ONE closed curve, R1 arc at the corner, the other "
              f"two corners sharp ({msg!r})")

print("\n-- an OPEN polyline's end has no neighbour: two picks on its end are refused --")
PL = joined([(0, 0, 0), (10, 0, 0), (10, 10, 0)])
ctx, msg, done = drive([("Pl", PL)], ["Radius=2"], [("Pl", (0.5, 0, 0)), ("Pl", (0.8, 0, 0))])
check(done and "same segment" in (msg or "") and [n for n, _ in ctx.curves()] == ["Pl"],
      f"refused, nothing changed ({msg!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("FILLET SEGMENT PICKING PASSED")
