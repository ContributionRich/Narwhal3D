#!/usr/bin/env python3
"""Focused check: a curve Trim intersects its target once, and keeps the old pieces.

Runs the real `TrimCmd.execute` against a minimal fake document. The synthetic fixture is
a line cut by two crossing lines, so the expected pieces are known exactly. The old
implementation split the curve twice (once to check it crosses, once to trim); the
intersection call count is the observable that tells the two apart.
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

from narwhal3d import analysis, bridge, commands, intersect  # noqa: E402

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def line(a, b):
    return r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b))


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


def ends(curve):
    a, b = curve.PointAtStart, curve.PointAtEnd
    return round(a.X, 6), round(b.X, 6)


def run_trim(objects, cutters, pick):
    ctx = FakeContext(objects)
    cmd = commands.TrimCmd()
    cmd.args = [cutters, pick]
    message, _done = cmd.execute(ctx)
    return message, ctx.added


target = line((0, 0, 0), (10, 0, 0))
cut_a = line((3, -1, 0), (3, 1, 0))
cut_b = line((7, -1, 0), (7, 1, 0))
pick = (1.0, 0.0, 0.0)
objects = [scene_object("Target", target), scene_object("CutA", cut_a),
           scene_object("CutB", cut_b)]

expected = bridge.trim_curve(target, [cut_a, cut_b], pick)
expected_ends = sorted(ends(c) for c in expected)

real_curve_curve = intersect.curve_curve
calls = []


def counting_curve_curve(a, b, *args, **kwargs):
    calls.append((a, b))
    return real_curve_curve(a, b, *args, **kwargs)


intersect.curve_curve = counting_curve_curve
try:
    calls.clear()
    message, added = run_trim(objects, ["CutA", "CutB"], pick)
finally:
    intersect.curve_curve = real_curve_curve

check(len(calls) == 2,
      "a curve Trim intersects the target once per cutter, not once to check and again to trim",
      f"{len(calls)} intersection call(s)")
check(sorted(ends(c) for c in added) == expected_ends,
      "the surviving pieces are exactly the pieces bridge.trim_curve keeps",
      f"got {sorted(ends(c) for c in added)}, want {expected_ends}")
# Report #128: Trim removes only the picked piece; the rest is ONE curve, not a piece
# per crossing (a leftover piece touching the cutters only at its ends could not be
# trimmed again).
check(expected_ends == [(3.0, 10.0)],
      "the fixture's closed-form remainder is the single curve [3,10]", repr(expected_ends))
check(message.startswith("Trimmed Target"), "the command reports the trimmed target", message)

intersect.curve_curve = counting_curve_curve
try:
    calls.clear()
    far = line((0, 50, 0), (10, 50, 0))
    refused, nothing = run_trim(
        [scene_object("Target", target), scene_object("Far", far)], ["Far"], pick)
finally:
    intersect.curve_curve = real_curve_curve
check(refused == "Trim: the cutting objects do not cross Target" and nothing == [],
      "cutters that miss the target are refused and change nothing", repr(refused))
check(len(calls) == 1, "the refusal path also intersects once", f"{len(calls)} call(s)")

# Operand preparation. The overlap test asks one operand ~120 closest-point questions. Each
# used to pack that operand again for the native search; one PreparedCurve packs it once.
# The pack count is the observable; a mutant that drops the prepared operand packs per question.
from narwhal3d import nearest  # noqa: E402

wave_a = r3.NurbsCurve.Create(False, 3, [r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0),
                                         r3.Point3d(6, -3, 0), r3.Point3d(10, 2, 0)])
wave_b = r3.NurbsCurve.Create(False, 3, [r3.Point3d(0, 6, 0), r3.Point3d(3, 9, 0),
                                         r3.Point3d(6, 2, 0), r3.Point3d(10, 7, 0)])

# A copy shifted 0.0005 is inside the other's box along its whole length but 500x beyond the
# tolerance, so every sample must be solved (the box prune cannot skip them).
near_copy = wave_a.Duplicate()
near_copy.Translate(r3.Vector3d(0, 0.0005, 0))

real_pack = nearest._native_pack
packs = []


def counting_pack(curve):
    packs.append(curve)
    return real_pack(curve)


nearest._native_pack = counting_pack
try:
    packs.clear()
    prepared_hits = [repr(x) for x in intersect.curve_curve(wave_a, near_copy)]
    prepared_packs = len(packs)
    real_prepared = intersect._prepared
    intersect._prepared = lambda curve: None
    try:
        packs.clear()
        unprepared_hits = [repr(x) for x in intersect.curve_curve(wave_a, near_copy)]
        unprepared_packs = len(packs)
    finally:
        intersect._prepared = real_prepared
finally:
    nearest._native_pack = real_pack

check(prepared_packs <= 2,
      "overlap detection packs the operand once, not once per closest query",
      f"{prepared_packs} pack(s)")
# The box prune (below) removes most closest queries on this pair, so the number of packs
# without the prepared operand is compared with the prepared count, not with a fixed 50.
check(unprepared_packs > prepared_packs and unprepared_hits == prepared_hits,
      "without the prepared operand every query repacks, with identical intersections",
      f"{unprepared_packs} pack(s), hits equal={unprepared_hits == prepared_hits}")


# --- Exact pruning and early exits. Each is held against the behaviour it replaced. ------

def reference_newton(ca, cb, t_a, t_b, iters=20):
    """The Newton loop before the fixed-point exit: the same arithmetic, never breaking early."""
    da, db = ca.Domain, cb.Domain
    for _ in range(iters):
        Pa, Pb = ca.PointAt(t_a), cb.PointAt(t_b)
        fx, fy, fz = Pa.X - Pb.X, Pa.Y - Pb.Y, Pa.Z - Pb.Z
        gap = math.sqrt(fx * fx + fy * fy + fz * fz)
        if gap <= 1e-14:
            return t_a, t_b, gap, (Pa.X, Pa.Y, Pa.Z)
        va = intersect._velocity(ca, t_a)
        vb = intersect._velocity(cb, t_b)
        step = intersect._solve2(va, vb, (fx, fy, fz))
        if step is None:
            break
        dta, dtb = step
        t_a = intersect._clamp(t_a - dta, da.T0, da.T1)
        t_b = intersect._clamp(t_b - dtb, db.T0, db.T1)
    Pa, Pb = ca.PointAt(t_a), cb.PointAt(t_b)
    gap = math.dist((Pa.X, Pa.Y, Pa.Z), (Pb.X, Pb.Y, Pb.Z))
    mid = ((Pa.X + Pb.X) / 2, (Pa.Y + Pb.Y) / 2, (Pa.Z + Pb.Z) / 2)
    return t_a, t_b, gap, mid


counts = {"velocity": 0, "dist": 0}
real_velocity = intersect._velocity
real_dist = intersect._dist_point_to_curve
real_beyond = intersect._beyond_box


def counting_velocity(c, t):
    counts["velocity"] += 1
    return real_velocity(c, t)


def counting_dist(curve, p, *args, **kwargs):
    counts["dist"] += 1
    return real_dist(curve, p, *args, **kwargs)


# Newton: a skew pair of lines never meets. Its iterate settles at a clamp after one step,
# so every later iteration repeats it. The fixed-point exit must stop there.
skew_a = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0))
skew_b = r3.LineCurve(r3.Point3d(5, 1, 0), r3.Point3d(5, 11, 0))
ta = 0.5 * (skew_a.Domain.T0 + skew_a.Domain.T1)
tb = skew_b.Domain.T0
intersect._velocity = counting_velocity
try:
    counts["velocity"] = 0
    settled = intersect._newton(skew_a, skew_b, ta, tb)
    settled_calls = counts["velocity"]
finally:
    intersect._velocity = real_velocity
check(settled_calls <= 4,
      "Newton stops at a fixed point instead of running all 20 iterations",
      f"{settled_calls} velocity evaluations")

# The exit changes no answer: compare with the original loop on the wavy pair, many starts.
exact_starts = [(wave_a.Domain.T0 + (wave_a.Domain.T1 - wave_a.Domain.T0) * i / 5,
                  wave_b.Domain.T0 + (wave_b.Domain.T1 - wave_b.Domain.T0) * j / 5)
                 for i in range(6) for j in range(6)]
exact_starts += [(ta, tb), (skew_a.Domain.T0, skew_b.Domain.T1)]
mismatch = []
for ca, cb, starts in ((wave_a, wave_b, exact_starts), (skew_a, skew_b, exact_starts[-2:])):
    for t_a0, t_b0 in starts:
        got = intersect._newton(ca, cb, t_a0, t_b0)
        want = reference_newton(ca, cb, t_a0, t_b0)
        if got != want:
            mismatch.append((t_a0, t_b0, got, want))
check(not mismatch, "the fixed-point exit returns exactly the original Newton answer",
      repr(mismatch[:2]))

# Overlap prune: a far pair is never solved, and a near pair keeps every hit it had before.
far = wave_a.Duplicate()
far.Translate(r3.Vector3d(0, 40, 0))
intersect._dist_point_to_curve = counting_dist
try:
    counts["dist"] = 0
    far_hits = intersect.curve_curve(wave_a, far)
    far_solves = counts["dist"]
    counts["dist"] = 0
    pruned_hits = [repr(x) for x in intersect.curve_curve(wave_a, wave_b)]
    pruned_solves = counts["dist"]
    intersect._beyond_box = lambda *args: False
    try:
        unpruned_hits = [repr(x) for x in intersect.curve_curve(wave_a, wave_b)]
    finally:
        intersect._beyond_box = real_beyond
finally:
    intersect._dist_point_to_curve = real_dist
check(far_hits == [] and far_solves == 0,
      "a pair whose boxes are 40 units apart is never solved sample by sample",
      f"{far_solves} solve(s)")
check(pruned_hits == unpruned_hits and pruned_solves > 0,
      "the overlap prune keeps every hit the unpruned search reports",
      f"pruned solves {pruned_solves}, hits equal={pruned_hits == unpruned_hits}")

# The pick threshold: decided from bounds, it must equal the tight-box test on every side.
pick_curve = wave_a
size_lo, size_hi = analysis.bounding_box(pick_curve)
tight = math.dist((size_lo.X, size_lo.Y, size_lo.Z), (size_hi.X, size_hi.Y, size_hi.Z))
threshold = max(0.05 * tight, 1e-9)
pick_mismatch = []
for d in (0.0, 0.5 * threshold, 0.999 * threshold, 1.001 * threshold, 2.0 * threshold, 10.0):
    if commands._within_pick_range(pick_curve, d, 0.05) != (d <= threshold):
        pick_mismatch.append(d)
check(not pick_mismatch, "the pick range agrees with the tight-box threshold on both sides",
      repr(pick_mismatch))

real_bounding = analysis.bounding_box
box_calls = []


def counting_bounding(geom):
    box_calls.append(1)
    return real_bounding(geom)


analysis.bounding_box = counting_bounding
try:
    commands._within_pick_range(pick_curve, 0.0, 0.05)
    commands._within_pick_range(pick_curve, 10.0, 0.05)
finally:
    analysis.bounding_box = real_bounding
check(not box_calls, "a clear hit or a clear miss never measures the tight box",
      f"{len(box_calls)} tight-box measurement(s)")

if failures:
    raise SystemExit(f"FAIL: {len(failures)}/{checks}: {failures}")
print(f"\n{checks}/{checks} Trim split-once checks passed")
