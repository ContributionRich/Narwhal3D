#!/usr/bin/env python3
"""Owner bug #128: a Trim must remove exactly the picked piece when a cutter only partly overlaps.

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

from narwhal3d import commands, intersect, nearest  # noqa: E402

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
check(len(ctx.added) == 3, "the command keeps three pieces", f"kept {len(ctx.added)}")
check(all(nearest.closest_point_on_curve(k, pick).distance > 1e-3 for k in ctx.added),
      "the command removes the picked piece")
s, e = middle.PointAtStart, middle.PointAtEnd
check("\n" not in message and f"| pick ({pick.X:.3f}, {pick.Y:.3f}, {pick.Z:.3f}) on Target" in message,
      "the transcript line gives the pick to 3 decimals on one line", message)
check("cutters CutA, CutB" in message, "the transcript line names the cutters used", message)
check(f"removed ({s.X:.3f}, {s.Y:.3f}, {s.Z:.3f}) to ({e.X:.3f}, {e.Y:.3f}, {e.Z:.3f})" in message,
      "the transcript line gives the removed piece's end points", message)

print(f"{checks} checks, {len(failures)} failed")
if failures:
    raise SystemExit(1)
