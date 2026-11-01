#!/usr/bin/env python3
"""`Join`: the pure chaining rules AND the kernel adapter that consumes them.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_join.py

Two halves, both tested here because a perfect chainer feeding a broken builder
joins nothing (the T-71 lesson):

* `joinchain.chains` / `.joinable` -- pure. Order, reversal, closure, and the
  fact that a curve joining nothing comes back alone.
* `bridge.join_curves` -- the rhino3dm half. Proves the result really is one
  curve with the right endpoints, that lines become a *PolylineCurve* and mixed
  geometry a *PolyCurve* (``commands/join.htm``: "Join turns lines into
  polylines, curves into polycurves"), and that the INPUTS ARE NOT MUTATED --
  `Reverse()` works in place, so a failed join must leave the document alone.
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


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


joinchain = _load("narwhal3d.joinchain", "narwhal3d/joinchain.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")

checks = 0
failures = []


def check(cond, label, detail=""):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))
        failures.append(label)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and math.dist(a, b) < tol


TOL = 0.01          # the documented absolute-tolerance default, in mm

P = r3.Point3d


def seg(key, a, b):
    return (key, a, b)


def line(a, b):
    return r3.LineCurve(P(*a), P(*b))


# ---------------------------------------------------------------------------
print()
print("=" * 64)
print("1. CHAINING: order, reversal, closure")
print("=" * 64)

# three lines already in order and already pointing the right way
cs = joinchain.chains([seg("a", (0, 0, 0), (5, 0, 0)),
                       seg("b", (5, 0, 0), (5, 4, 0)),
                       seg("c", (5, 4, 0), (0, 4, 0))], TOL)
check(len(cs) == 1, "three touching lines are ONE chain", f"{len(cs)}")
check(cs[0].keys == ("a", "b", "c"), "in order", f"{cs[0].keys}")
check(not any(r for _, r in cs[0].members),
      "and none needs reversing", f"{cs[0].members}")
check(not cs[0].closed, "the run is open")
check(close(cs[0].start, (0, 0, 0)) and close(cs[0].end, (0, 4, 0)),
      "with the right ends", f"{cs[0].start} {cs[0].end}")

# the middle one drawn BACKWARDS -- rhino3dm's Append would not fix this
cs = joinchain.chains([seg("a", (0, 0, 0), (5, 0, 0)),
                       seg("b", (5, 4, 0), (5, 0, 0)),      # backwards
                       seg("c", (5, 4, 0), (0, 4, 0))], TOL)
check(len(cs) == 1 and cs[0].keys == ("a", "b", "c"),
      "a backwards middle segment still chains", f"{cs}")
check(cs[0].members[1] == ("b", True),
      "and is flagged for reversal", f"{cs[0].members}")

# seeded from the MIDDLE: the first input is the middle of the run, so the
# chainer has to grow BOTH ways or it would return two chains.
cs = joinchain.chains([seg("mid", (5, 0, 0), (5, 4, 0)),
                       seg("first", (0, 0, 0), (5, 0, 0)),
                       seg("last", (5, 4, 0), (0, 4, 0))], TOL)
check(len(cs) == 1, "a run seeded from its MIDDLE is still one chain", f"{len(cs)}")
check(cs[0].keys == ("first", "mid", "last"),
      "and comes back in geometric order", f"{cs[0].keys}")

# closure
cs = joinchain.chains([seg("a", (0, 0, 0), (5, 0, 0)),
                       seg("b", (5, 0, 0), (5, 4, 0)),
                       seg("c", (5, 4, 0), (0, 4, 0)),
                       seg("d", (0, 4, 0), (0, 0, 0))], TOL)
check(len(cs) == 1 and cs[0].closed, "a loop is reported CLOSED", f"{cs}")
check(len(cs[0]) == 4, "with all four members exactly once", f"{len(cs[0])}")

# two separate runs
cs = joinchain.chains([seg("a", (0, 0, 0), (5, 0, 0)),
                       seg("b", (5, 0, 0), (5, 4, 0)),
                       seg("x", (50, 0, 0), (55, 0, 0)),
                       seg("y", (55, 0, 0), (55, 4, 0))], TOL)
check(len(cs) == 2, "two disjoint runs are two chains", f"{len(cs)}")
check({cs[0].keys, cs[1].keys} == {("a", "b"), ("x", "y")},
      "each with its own members", f"{[c.keys for c in cs]}")

# a lone curve
cs = joinchain.chains([seg("lonely", (0, 0, 0), (1, 1, 0))], TOL)
check(len(cs) == 1 and len(cs[0]) == 1, "a lone curve is a chain of one")
check(joinchain.joinable([seg("lonely", (0, 0, 0), (1, 1, 0))], TOL) == [],
      "but it is NOT joinable -- joining one curve to nothing is not a join")

# an already-closed curve must not be grown
cs = joinchain.joinable([seg("ring", (0, 0, 0), (0, 0, 0)),
                         seg("tail", (0, 0, 0), (9, 0, 0))], TOL)
check(cs == [] or all("ring" not in c.keys for c in cs),
      "a CLOSED curve is not spliced into", f"{[c.keys for c in cs]}")

# tolerance is respected in both directions
inside = joinchain.joinable([seg("a", (0, 0, 0), (5, 0, 0)),
                             seg("b", (5, 0.005, 0), (5, 4, 0))], TOL)
outside = joinchain.joinable([seg("a", (0, 0, 0), (5, 0, 0)),
                              seg("b", (5, 0.5, 0), (5, 4, 0))], TOL)
check(len(inside) == 1, "a gap INSIDE tolerance joins", f"{inside}")
check(outside == [], "a gap OUTSIDE tolerance does not", f"{outside}")

# ---------------------------------------------------------------------------
print()
print("=" * 64)
print("2. THE KERNEL HALF: what actually comes out")
print("=" * 64)

items = [("a", line((0, 0, 0), (5, 0, 0))),
         ("b", line((5, 0, 0), (5, 4, 0))),
         ("c", line((5, 4, 0), (0, 4, 0)))]
res = bridge.join_curves(items, TOL)
check(len(res) == 1, "three lines join into one result", f"{len(res)}")
keys, geom, closed = res[0]
check(keys == ("a", "b", "c"), "consuming all three", f"{keys}")
check(isinstance(geom, r3.PolylineCurve),
      "lines become a POLYLINE, as the help says",
      f"got {type(geom).__name__}")
check(int(geom.PointCount) == 4,
      "with 4 vertices, not 6 -- shared joints are ONE point",
      f"{geom.PointCount}")
check(close((geom.PointAtStart.X, geom.PointAtStart.Y, geom.PointAtStart.Z),
            (0, 0, 0))
      and close((geom.PointAtEnd.X, geom.PointAtEnd.Y, geom.PointAtEnd.Z),
                (0, 4, 0)),
      "and the right endpoints", f"{geom.PointAtStart} {geom.PointAtEnd}")
check(not closed, "reported open")

# reversal really happens
back = line((5, 4, 0), (5, 0, 0))
items = [("a", line((0, 0, 0), (5, 0, 0))),
         ("b", back),
         ("c", line((5, 4, 0), (0, 4, 0)))]
res = bridge.join_curves(items, TOL)
keys, geom, closed = res[0]
check(int(geom.PointCount) == 4,
      "a backwards member is reversed, not gapped", f"{geom.PointCount}")
check(close((back.PointAtStart.X, back.PointAtStart.Y, back.PointAtStart.Z),
            (5, 4, 0)),
      "and the INPUT curve is left untouched (Reverse works in place)",
      f"{back.PointAtStart}")

# closed loop
items = [("a", line((0, 0, 0), (5, 0, 0))),
         ("b", line((5, 0, 0), (5, 4, 0))),
         ("c", line((5, 4, 0), (0, 4, 0))),
         ("d", line((0, 4, 0), (0, 0, 0)))]
keys, geom, closed = bridge.join_curves(items, TOL)[0]
check(closed and geom.IsClosed,
      "a loop joins CLOSED, and the kernel agrees",
      f"chain={closed} geom={geom.IsClosed}")

# a line plus an arc must NOT be flattened into line segments
arc = bridge.make_arc((5, 0, 0), (6, 1, 0), (5, 2, 0))
items = [("a", line((0, 0, 0), (5, 0, 0))), ("arc", arc)]
res = bridge.join_curves(items, TOL)
check(len(res) == 1, "a line and an arc join", f"{res}")
keys, geom, closed = res[0]
check(isinstance(geom, r3.PolyCurve),
      "as a POLYCURVE ('curves into polycurves'), not a polyline",
      f"got {type(geom).__name__}")
check(int(geom.SegmentCount) == 2, "with both segments", f"{geom.SegmentCount}")
# The INVARIANT is "curvature survived", not "the segment is an ArcCurve".
# `bridge.make_arc` deliberately stores arcs as a rational NurbsCurve (exact, and
# what `CreateFromArc` returns), so asserting the class would be asserting a
# snapshot of that decision rather than the property Join must not break.
seg_types = [type(geom.SegmentCurve(i)).__name__ for i in range(geom.SegmentCount)]
curved = [i for i in range(geom.SegmentCount)
          if geom.SegmentCurve(i).IsArc() or not geom.SegmentCurve(i).IsLinear()]
check(not geom.IsPolyline() and curved,
      "and the arc segment is still CURVED -- curvature was not destroyed",
      f"{seg_types} curved={curved}")

# MEASURED (Research13 #78): two end-to-end lines and a curve join into the reference CAD application's
# "Open polycurve with 2 curve segments": segment 1 a 3-point POLYLINE (the lines
# merged), segment 2 the curve. Before this the app produced 3 separate segments.
items = [("a", line((0, 0, 0), (5, 0, 0))), ("b", line((5, 0, 0), (5, 2, 0))),
         ("arc", bridge.make_arc((5, 2, 0), (6, 3, 0), (5, 4, 0)))]
keys, geom, closed = bridge.join_curves(items, TOL)[0]
first = geom.SegmentCurve(0) if isinstance(geom, r3.PolyCurve) else None
check(isinstance(geom, r3.PolyCurve) and int(geom.SegmentCount) == 2
      and isinstance(first, r3.PolylineCurve) and int(first.PointCount) == 3
      and not geom.SegmentCurve(1).IsLinear(),
      "adjacent lines in a mixed chain merge into ONE polyline segment (2 segments)",
      f"{type(geom).__name__} {getattr(geom, 'SegmentCount', None)} "
      f"{[type(geom.SegmentCurve(i)).__name__ for i in range(int(getattr(geom, 'SegmentCount', 0)))]}")
check(geom.PointAtStart.DistanceTo(r3.Point3d(0, 0, 0)) < 1e-9
      and geom.PointAtEnd.DistanceTo(r3.Point3d(5, 4, 0)) < 1e-9,
      "and the merged chain keeps its exact ends")

# An all-straight PolyCurve is still a polyline as far as Join is concerned --
# "Join turns lines into polylines" is about the GEOMETRY, not the class it
# arrived in.
inner = r3.PolyCurve()
inner.Append(line((5, 0, 0), (5, 4, 0)))
inner.Append(line((5, 4, 0), (0, 4, 0)))
items = [("a", line((0, 0, 0), (5, 0, 0))), ("inner", inner)]
keys, geom, closed = bridge.join_curves(items, TOL)[0]
check(isinstance(geom, r3.PolylineCurve) and int(geom.PointCount) == 4,
      "a straight-only PolyCurve joins into a POLYLINE, with 4 vertices",
      f"{type(geom).__name__} {getattr(geom, 'PointCount', None)}")

# nesting: appending a PolyCurve into a PolyCurve NESTS it unless flattened
# (MEASURED: 1 + 2 segments gave SegmentCount 2, IsNested True, then 3).
curvy = r3.PolyCurve()
curvy.Append(line((5, 0, 0), (5, 2, 0)))
curvy.Append(bridge.make_arc((5, 2, 0), (6, 3, 0), (5, 4, 0)))
items = [("a", line((0, 0, 0), (5, 0, 0))), ("curvy", curvy)]
keys, geom, closed = bridge.join_curves(items, TOL)[0]
check(isinstance(geom, r3.PolyCurve) and not geom.IsNested,
      "a PolyCurve with a curve in it joins FLAT, not nested",
      f"{type(geom).__name__} IsNested={getattr(geom, 'IsNested', None)}")
check(int(geom.SegmentCount) == 3, "so all three segments are visible",
      f"{geom.SegmentCount}")

# nothing to join
res = bridge.join_curves([("a", line((0, 0, 0), (1, 0, 0))),
                          ("b", line((50, 0, 0), (51, 0, 0)))], TOL)
check(res == [], "curves that do not touch produce NO result at all", f"{res}")

# a non-curve is skipped, not crashed on
res = bridge.join_curves([("a", line((0, 0, 0), (5, 0, 0))),
                          ("pt", bridge.make_point((9, 9, 9))),
                          ("b", line((5, 0, 0), (5, 4, 0)))], TOL)
check(len(res) == 1 and res[0][0] == ("a", "b"),
      "a non-curve in the selection is skipped, not joined", f"{res}")

# OUR OWN Line command's geometry: `make_line` returns a NurbsCurve, not a
# LineCurve, so this is the case the real app hits. It must still make a polyline.
items = [("a", bridge.make_line((0, 0, 0), (5, 0, 0))),
         ("b", bridge.make_line((5, 0, 0), (5, 4, 0)))]
keys, geom, closed = bridge.join_curves(items, TOL)[0]
check(isinstance(geom, r3.PolylineCurve) and int(geom.PointCount) == 3,
      "two lines from OUR Line command join into a polyline (make_line returns "
      "a NurbsCurve, so an isinstance test would have missed this)",
      f"{type(geom).__name__} {getattr(geom, 'PointCount', None)}")

# curve_ends
check(bridge.curve_ends(bridge.make_point((1, 2, 3))) is None,
      "curve_ends refuses a point")
check(close(bridge.curve_ends(line((1, 2, 3), (4, 5, 6)))[1], (4, 5, 6)),
      "and reads a real curve's end")

# both joined shapes round-trip through a File3dm (so they can be SAVED)
pline_res = bridge.join_curves(
    [("a", bridge.make_line((0, 0, 0), (5, 0, 0))),
     ("b", bridge.make_line((5, 0, 0), (5, 4, 0)))], TOL)[0][1]
pcurve_res = bridge.join_curves(
    [("a", line((0, 0, 0), (5, 0, 0))),
     ("arc", bridge.make_arc((5, 0, 0), (6, 1, 0), (5, 2, 0)))], TOL)[0][1]
f = r3.File3dm()
f.Objects.AddCurve(pline_res)
f.Objects.AddCurve(pcurve_res)
back = [f.Objects[i].Geometry for i in range(len(f.Objects))]
check(len(back) == 2
      and getattr(back[0], "PointCount", None) == 3
      and getattr(back[1], "SegmentCount", None) == 2,
      "and both joined shapes survive a File3dm round-trip",
      f"{[type(g).__name__ for g in back]}")

print()
print("=" * 64)
print("3. EXPLODE: the inverse (commands/explode.htm)")
print("=" * 64)
#     Polycurve -> "Single segment curves."   Polyline -> "Single segment lines/curves."

# what Join just made, taken apart again
joined = bridge.join_curves([("a", bridge.make_line((0, 0, 0), (5, 0, 0))),
                             ("b", bridge.make_line((5, 0, 0), (5, 4, 0))),
                             ("c", bridge.make_line((5, 4, 0), (0, 4, 0)))], TOL)[0][1]
parts = bridge.explode_curve(joined)
check(parts is not None and len(parts) == 3,
      "a joined polyline explodes back into its 3 segments",
      f"{None if parts is None else len(parts)}")
check(parts is not None
      and close((parts[0].PointAtStart.X, parts[0].PointAtStart.Y,
                 parts[0].PointAtStart.Z), (0, 0, 0))
      and close((parts[-1].PointAtEnd.X, parts[-1].PointAtEnd.Y,
                 parts[-1].PointAtEnd.Z), (0, 4, 0)),
      "in order, spanning the same run")
check(isinstance(joined, r3.PolylineCurve) and int(joined.PointCount) == 4,
      "and the INPUT is untouched", f"{getattr(joined, 'PointCount', None)}")

pc = bridge.join_curves([("a", line((0, 0, 0), (5, 0, 0))),
                         ("arc", bridge.make_arc((5, 0, 0), (6, 1, 0),
                                                 (5, 2, 0)))], TOL)[0][1]
parts = bridge.explode_curve(pc)
check(parts is not None and len(parts) == 2,
      "a polycurve explodes into its segment curves",
      f"{None if parts is None else len(parts)}")
check(parts is not None and (parts[1].IsArc() or not parts[1].IsLinear()),
      "with the curved segment still curved",
      f"{None if parts is None else [type(x).__name__ for x in parts]}")

check(bridge.explode_curve(line((0, 0, 0), (1, 0, 0))) is None,
      "a single line has NOTHING to explode -- None, not an empty list")
check(bridge.explode_curve(bridge.make_circle((0, 0, 0), 3)) is None,
      "and neither does a circle")

print()
print("=" * 64)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"JOIN TESTS PASSED ({checks} checks)")
