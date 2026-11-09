#!/usr/bin/env python3
"""Report #128: a Trim must remove exactly the picked piece when a cutter only partly overlaps.

The target is a closed circle. Cutter A is the circle's own arc from parameter 0.2 to 1.0, so it
overlaps the target there and the target leaves it at both ends. Cutter B is a line that crosses
the circle twice, outside that overlap. Every genuine crossing and both overlap ends must cut the
target: four cuts, four pieces. The old split discarded the overlap, so it made only two cuts.
The test then picks the piece between the overlap end at 1.0 and the upper crossing, and checks
that exactly that piece is removed.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
import types

import bpy  # noqa: F401 -- narwhal3d imports the UI, which needs bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rhino3dm as r3  # noqa: E402

from narwhal3d import bridge, commands, intersect, nearest  # noqa: E402

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def circle():
    return r3.Circle(r3.Point3d(0, 0, 0), 10.0).ToNurbsCurve()


def line(a, b):
    return r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b))


def xyz(p):
    return (p.X, p.Y, p.Z)


def close(p, q, tol=1e-6):
    return math.dist(p, q) <= tol


def endpoints_match(pieces, expected):
    """True when the pieces' end points are exactly the expected cut points (each cut is the end
    of two pieces, so every end must match some expected point and every expected point be met)."""
    ends = [xyz(pc.PointAtStart) for pc in pieces] + [xyz(pc.PointAtEnd) for pc in pieces]
    every_end_expected = all(any(close(e, w) for w in expected) for e in ends)
    every_point_met = all(any(close(e, w) for e in ends) for w in expected)
    return every_end_expected and every_point_met


target = circle()
t_lo, t_hi = 0.2, 1.0
cut_a = circle().Split(t_lo)[1].Split(t_hi)[0]
cut_b = line((-7, -20, 0), (-7, 20, 0))

ov_start = xyz(target.PointAt(t_lo))
ov_end = xyz(target.PointAt(t_hi))
cross_up = (-7.0, math.sqrt(51.0), 0.0)
cross_down = (-7.0, -math.sqrt(51.0), 0.0)
expected_ends = [ov_start, ov_end, cross_up, cross_down]

pieces = intersect.split_curve_by(target, [cut_a, cut_b])
check(len(pieces) == 4, "a partly overlapping cutter and a crossing cutter cut the target into 4 pieces",
      f"got {len(pieces)} pieces")
check(endpoints_match(pieces, expected_ends),
      "the cuts are the two overlap ends and the two crossings, to solver accuracy",
      f"ends={[tuple(round(c, 4) for c in xyz(p.PointAtStart)) for p in pieces]}")

# The piece between the overlap end at 1.0 and the upper crossing (through the top of the circle).
middle = next((pc for pc in pieces
               if close(xyz(pc.PointAtStart), ov_end) and close(xyz(pc.PointAtEnd), cross_up)), None)
check(middle is not None, "the piece between the overlap end and the upper crossing exists")
if middle is None:
    middle = pieces[0]
pick = middle.PointAt(0.5 * (middle.Domain.T0 + middle.Domain.T1))
kept = intersect.trim_curve_by(target, [cut_a, cut_b], (pick.X, pick.Y, pick.Z), pieces=pieces)
check(len(kept) == 3, "trimming removes exactly one piece", f"kept {len(kept)} of 4")
check(all(nearest.closest_point_on_curve(k, pick).distance > 1e-3 for k in kept),
      "the piece under the pick is the one removed")
check(not any(close(xyz(k.PointAtStart), ov_end) and close(xyz(k.PointAtEnd), cross_up)
              for k in kept),
      "the removed piece is the one between the overlap end and the upper crossing")


class FakeContext:
    """The document surface TrimCmd and _replace_curve use, nothing more."""

    def __init__(self, objects):
        self.model = types.SimpleNamespace(
            file=types.SimpleNamespace(Objects=objects),
            object_id_by_name=lambda name: None)
        self.added = []

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if obj.Attributes.Name.lower() == name.lower():
                return obj.Geometry
        return None

    def note_change(self, name=None):
        return False

    def object_by_name(self, name):
        return None

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added.append(geom)
        return len(self.added), f"{base_name} {len(self.added)}"


def scene_object(name, geom):
    return types.SimpleNamespace(
        Attributes=types.SimpleNamespace(Name=name, Mode=None, LayerIndex=0), Geometry=geom)


ctx = FakeContext([scene_object("Target", target), scene_object("CutA", cut_a),
                   scene_object("CutB", cut_b)])
cmd = commands.TrimCmd()
cmd.args = [["CutA", "CutB"], (pick.X, pick.Y, pick.Z)]
message, _done = cmd.execute(ctx)
check(message.startswith("Trimmed Target"), "the command trims the target", message)
# Only the two crossings bracketing the pick cut: the other three pieces stay ONE curve.
check(len(ctx.added) == 1, "the command keeps the rest of the closed curve as one curve",
      f"kept {len(ctx.added)}")
check(all(nearest.closest_point_on_curve(k, pick).distance > 1e-3 for k in ctx.added),
      "the command removes the picked piece")
others = [pc for pc in pieces if pc is not middle]
check(ctx.added and all(
          nearest.closest_point_on_curve(ctx.added[0], pc.PointAt(
              0.5 * (pc.Domain.T0 + pc.Domain.T1))).distance < 1e-9 for pc in others),
      "the kept curve still holds every piece that was not picked")
s, e = middle.PointAtStart, middle.PointAtEnd
check("\n" not in message and f"| pick ({pick.X:.3f}, {pick.Y:.3f}, {pick.Z:.3f}) on Target" in message,
      "the transcript line gives the pick to 3 decimals on one line", message)
check("cutters CutA, CutB" in message, "the transcript line names the cutters used", message)
check(f"removed ({s.X:.3f}, {s.Y:.3f}, {s.Z:.3f}) to ({e.X:.3f}, {e.Y:.3f}, {e.Z:.3f})" in message,
      "the transcript line gives the removed piece's end points", message)

# ---- #128 as reported: each pick removes its part, and the NEXT part can still be picked.
# The reported sequence was "Trimmed Curve55 -> Curve62, Curve63" then "Trim: the cutting
# objects do not cross Curve62": the first pick split the curve at EVERY crossing, so the
# other end was left a separate curve touching the cutters only at its ends.


class NamedContext(FakeContext):
    """FakeContext whose replaced curves really leave the document and come back named."""

    def __init__(self, objects):
        super().__init__(objects)
        self.serial = 0

    def add_geometry(self, geom, base_name, layer_index=None):
        self.serial += 1
        name = f"{base_name}{self.serial}"
        self.model.file.Objects.append(scene_object(name, geom))
        self.added.append(geom)
        return self.serial, name


def replace_in(ctx, name, pieces, **_kw):
    ctx.model.file.Objects[:] = [o for o in ctx.model.file.Objects
                                 if o.Attributes.Name.lower() != name.lower()]
    return [ctx.add_geometry(p, "Curve")[1] for p in pieces]


def curves_of(ctx, skip):
    return [o.Geometry for o in ctx.model.file.Objects if o.Attributes.Name not in skip]


def on(curve, point, tol=1e-9):
    return nearest.closest_point_on_curve(curve, point).distance < tol


def trim_sequence(objects, cutters, picks):
    """Run TrimCmd once per pick, as one command session; returns (messages, ctx)."""
    ctx = NamedContext(objects)
    cmd = commands.TrimCmd()
    messages = []
    real_replace, real_visible = commands._replace_curve, commands._visible_names
    commands._replace_curve = replace_in
    commands._visible_names = lambda c: [o.Attributes.Name for o in c.model.file.Objects]
    try:
        for p in picks:
            cmd.args = [cmd.args[0] if cmd.args else cutters, p]
            messages.append(cmd.execute(ctx)[0])
    finally:
        commands._replace_curve, commands._visible_names = real_replace, real_visible
    return messages, ctx


# (a) an open line crossed at x=3 and x=7: pick x=1 leaves ONE curve 3..10, then x=9 trims it.
msgs, ctx = trim_sequence(
    [scene_object("Target", line((0, 0, 0), (10, 0, 0))),
     scene_object("CutA", line((3, -1, 0), (3, 1, 0))),
     scene_object("CutB", line((7, -1, 0), (7, 1, 0)))],
    ["CutA", "CutB"], [(1.0, 0.0, 0.0), (9.0, 0.0, 0.0)])
first = ctx.added[0] if ctx.added else None
check(first is not None and close(xyz(first.PointAtStart), (3, 0, 0))
      and close(xyz(first.PointAtEnd), (10, 0, 0)) and len(msgs[0].split("->")[1].split("|")[0].split(",")) == 1,
      "open curve: the first pick leaves one curve from the first crossing to the end", msgs[0])
check(msgs[1].startswith("Trimmed "), "open curve: the next part can still be trimmed", msgs[1])
left = curves_of(ctx, {"CutA", "CutB"})
check(len(left) == 1 and close(xyz(left[0].PointAtStart), (3, 0, 0))
      and close(xyz(left[0].PointAtEnd), (7, 0, 0)),
      "open curve: after both picks only 3..7 is left",
      repr([(xyz(c.PointAtStart), xyz(c.PointAtEnd)) for c in left]))

# (b) the report's U: an open polycurve whose two legs cross one cutter. Pick both leg ends.
u = r3.PolyCurve()
for a, b in (((0, 5, 0), (0, 0, 0)), ((0, 0, 0), (10, 0, 0)), ((10, 0, 0), (10, 5, 0))):
    u.Append(line(a, b))
msgs, ctx = trim_sequence(
    [scene_object("U", u), scene_object("Cut", line((-1, 2, 0), (11, 2, 0)))],
    ["Cut"], [(0.0, 4.0, 0.0), (10.0, 4.0, 0.0)])
check(all(m.startswith("Trimmed ") for m in msgs), "U: both leg ends are trimmed", repr(msgs))
left = curves_of(ctx, {"Cut"})
check(len(left) == 1 and close(xyz(left[0].PointAtStart), (0, 2, 0))
      and close(xyz(left[0].PointAtEnd), (10, 2, 0))
      and on(left[0], (5, 0, 0)) and on(left[0], (0, 0, 0)) and on(left[0], (10, 0, 0)),
      "U: one curve is left, from (0,2) down, across and up to (10,2)",
      repr([(xyz(c.PointAtStart), xyz(c.PointAtEnd)) for c in left]))

# (c) the report's closed outline: a circle crossed by x=-5 and x=5. The first pick takes the
# right cap, which holds the seam; the second takes the left cap. Two arcs must remain, exact.
rc = math.sqrt(75.0)
msgs, ctx = trim_sequence(
    [scene_object("Ring", circle()),
     scene_object("CutL", line((-5, -20, 0), (-5, 20, 0))),
     scene_object("CutR", line((5, -20, 0), (5, 20, 0)))],
    ["CutL", "CutR"], [(10.0, 0.0, 0.0), (-10.0, 0.0, 0.0)])
check(all(m.startswith("Trimmed ") for m in msgs), "closed: both caps are trimmed", repr(msgs))
left = curves_of(ctx, {"CutL", "CutR"})
ends = sorted(sorted([tuple(round(c, 9) for c in xyz(k.PointAtStart)),
                      tuple(round(c, 9) for c in xyz(k.PointAtEnd))]) for k in left)
want = sorted(sorted([(round(-5.0, 9), round(s * rc, 9), 0.0), (5.0, round(s * rc, 9), 0.0)])
              for s in (1, -1))
check(ends == want, "closed: exactly the top and bottom arcs are left", repr(ends))
check(all(on(k, (0, s * 10, 0)) for k, s in zip(sorted(left, key=lambda k: -k.PointAtStart.Y), (1, -1))),
      "closed: the arcs are the circle's own top and bottom")
check(all(k.ToNurbsCurve().IsRational and k.ToNurbsCurve().Degree == 2
          and abs(math.hypot(k.PointAt(0.5 * (k.Domain.T0 + k.Domain.T1)).X,
                             k.PointAt(0.5 * (k.Domain.T0 + k.Domain.T1)).Y) - 10.0) < 1e-9
          for k in left),
      "closed: the arcs stay exact rational degree-2 NURBS on the circle")

# (d) closed, picked away from the seam first: the rest joins across the seam as ONE curve.
target_d = circle()
split_d = bridge.split_curve(target_d, [line((-5, -20, 0), (-5, 20, 0)), line((5, -20, 0), (5, 20, 0))])
kept_d, removed_d = bridge.trim_split_pieces(target_d, split_d, (0.0, 10.0, 0.0))
check(len(split_d) == 4 and len(kept_d) == 1 and len(removed_d) == 1,
      "closed, off the seam: one piece removed, the rest is one curve",
      f"split {len(split_d)}, kept {len(kept_d)}, removed {len(removed_d)}")
check(kept_d and on(removed_d[0], (0, 10, 0)) and not on(kept_d[0], (0, 10, 0), 1e-3)
      and all(on(kept_d[0], p) for p in ((10, 0, 0), (0, -10, 0), (-10, 0, 0),
                                         (10 * math.cos(math.pi / 6), 5.0, 0.0),
                                         (10 * math.cos(math.pi / 6), -5.0, 0.0))),
      "closed, off the seam: the kept curve runs right cap, bottom, left cap across the seam")

print(f"{checks} checks, {len(failures)} failed")
if failures:
    raise SystemExit(1)
