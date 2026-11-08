#!/usr/bin/env python3
"""Rebuild performance: prepared arc-length tables and prepared NURBS closest-point queries.

Every optimised result is checked against the algorithm it replaced, kept here verbatim
(`reference_parameter_at_length`) or reached through the unchanged Python search
(`python_only`). Comparisons are exact (`==`): no tolerance is used anywhere.

Operation-count gates fail when an optimisation is bypassed: an arc-length table is built
once per operation, a NURBS curve is packed once per fit rather than once per sample, and
each non-analytic NURBS closest query reaches the native search.

The native gates need a core that exports narwhal_pure_core_nearest_on_nurbs. Point
NARWHAL_PURE_CORE at a build of native/pure_core, e.g.:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_rebuild_performance.py
"""
import importlib.util
import math
import os
import random
import sys
import time
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        f"narwhal3d.{name}", os.path.join(ROOT, "narwhal3d", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


load("units")
arclength = load("arclength")
native = load("native_nearest")
nearest = load("nearest")
rebuild = load("rebuild")

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


class Patch:
    """Replace a module attribute for a `with` block, restoring it afterwards."""

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.saved = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.saved)


class Count(Patch):
    """Count calls to a module attribute while still running it."""

    def __init__(self, module, name):
        self.calls = 0
        super().__init__(module, name, None)

    def __enter__(self):
        self.saved = getattr(self.module, self.name)

        def counted(*args, **kwargs):
            self.calls += 1
            return self.saved(*args, **kwargs)

        setattr(self.module, self.name, counted)
        return self


def python_only():
    """The Python closest-point search, as before the native route existed."""
    return Patch(nearest, "_native_pack", lambda curve: None)


def reference_parameter_at_length(curve, s, tolerance=None):
    """`arclength.parameter_at_length` as it was before any table: every Newton step
    re-integrates the curve from its start."""
    tol = arclength.default_tolerance() if tolerance is None else float(tolerance)
    d = curve.Domain
    total = arclength.length_of(curve)
    if s < -tol or s > total + tol:
        raise ValueError(f"arc length {s} is outside the curve [0, {total}]")
    s = min(max(s, 0.0), total)
    if s <= tol:
        return d.T0
    if s >= total - tol:
        return d.T1
    lo, hi = d.T0, d.T1
    t = d.T0 + (d.T1 - d.T0) * (s / total)
    for _ in range(60):
        f = arclength.length_of(curve, d.T0, t) - s
        if abs(f) <= tol * 1e-3:
            break
        if f > 0:
            hi = t
        else:
            lo = t
        speed = arclength._speed(curve, t)
        if speed > arclength._PARAM_EPS:
            step = t - f / speed
        else:
            step = 0.5 * (lo + hi)
        t = step if lo < step < hi else 0.5 * (lo + hi)
    return t


class ReferenceLength:
    """Stands in for arclength.PreparedLength in the reference rebuild pipeline."""

    def __init__(self, curve):
        self._curve = curve
        self.total = arclength.length_of(curve)

    def parameter_at(self, s, tolerance=None):
        return reference_parameter_at_length(self._curve, s, tolerance)


class ReferencePrepared:
    """Stands in for nearest.PreparedCurve in the reference rebuild pipeline."""

    def __init__(self, curve):
        self._curve = curve

    def closest(self, point, tolerance=None):
        with python_only():
            return nearest.closest_point_on_curve(self._curve, point, tolerance)

    def closest_many(self, points, tolerance=None):
        return [self.closest(q, tolerance) for q in points]


def circle_nurbs():
    return r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve()


def cubic_open():
    return r3.NurbsCurve.CreateControlPointCurve(
        [r3.Point3d(0, 0, 0), r3.Point3d(2, 5, 1), r3.Point3d(5, -3, 0),
         r3.Point3d(8, 4, 2), r3.Point3d(11, 0, 0), r3.Point3d(13, 3, 1)], 3)


def nonuniform_open(bump=0.0):
    """Clamped cubic with non-uniform interior knots; `bump` moves one control point."""
    c = r3.NurbsCurve(3, False, 4, 8)
    pts = [(0, 0, 0), (1, 3, 0), (2, -2, 1), (4, 2, 0), (5, -1, 2), (7, 3, 1), (8, 0, 0), (9, 2, -1)]
    for i, (x, y, z) in enumerate(pts):
        c.Points[i] = r3.Point4d(x, y, z + (bump if i == 3 else 0.0), 1.0)
    for i, k in enumerate([0, 0, 0, 0, 0.4, 1.1, 3, 3, 3, 3]):
        c.Knots[i] = float(k)
    return c


def closed_43_spans():
    """A closed cubic with 43 spans and irregular spacing, the span count of the saved trace."""
    pts = []
    for i in range(43):
        th = 2 * math.pi * (i + 0.35 * math.sin(i)) / 43
        r = 10 + 2 * math.sin(3 * th)
        pts.append(r3.Point3d(r * math.cos(th), r * math.sin(th), 0.8 * math.sin(2 * th)))
    return r3.NurbsCurve.Create(True, 3, pts)


def degenerate_zero_length():
    c = r3.NurbsCurve(3, False, 4, 4)
    for i in range(4):
        c.Points[i] = r3.Point4d(1, 1, 1, 1.0)
    for i, k in enumerate([0, 0, 0, 0, 1, 1]):
        c.Knots[i] = float(k)
    return c


FIXTURES = {
    "circle (rational, analytic total)": circle_nurbs,
    "open cubic, uniform": cubic_open,
    "open cubic, non-uniform knots": nonuniform_open,
    "closed cubic, 43 spans": closed_43_spans,
}


def spread_parameters(curve, count):
    """Parameters spread across the curve, plus values straddling a few interior knots."""
    d = curve.Domain
    ts = [d.T0 + (d.T1 - d.T0) * (i + 0.37) / count for i in range(count)]
    knots = arclength._interior_knots(curve)
    for k in knots[:: max(1, len(knots) // 4)]:
        ts += [k - 1e-7, k + 1e-7]
    return sorted(t for t in ts if d.T0 < t < d.T1)


def queries_near(curve, count, seed):
    rng = random.Random(seed)
    d = curve.Domain
    out = []
    for _ in range(count):
        p = curve.PointAt(rng.uniform(d.T0, d.T1))
        out.append((p.X + rng.gauss(0, 0.4), p.Y + rng.gauss(0, 0.4), p.Z + rng.gauss(0, 0.4)))
    out.append((1e3, -1e3, 0.0))              # beyond the end: clamped to the endpoint
    return out


def same_number(a, b):
    """Bit-equal for finite values; NaN matches NaN (rhino3dm returns NaN off the domain)."""
    return a == b or (math.isnan(a) and math.isnan(b))


def closest_fields(c):
    return (c.t, c.point, c.distance, c.method, c.at_end, c.ambiguous)


def rebuilt_equal(a, b):
    same_cvs = [(p.X, p.Y, p.Z, p.W) for p in a.Points] == [(p.X, p.Y, p.Z, p.W) for p in b.Points]
    return same_cvs and list(a.Knots.ToList()) == list(b.Knots.ToList())


print("-- arc-length inversion: prepared table against the reference --")
for name, make in FIXTURES.items():
    curve = make()
    total = arclength.length_of(curve)
    s_values = [0.0, 1e-13, total * 0.013, total * 0.25, total * 0.5, total * 0.999, total]
    for t in spread_parameters(curve, 6):
        s_values.append(arclength.length_of(curve, curve.Domain.T0, t))
    s_values = [s for s in s_values if 0.0 <= s <= total]
    prepared = arclength.PreparedLength(curve)
    mismatches = [s for s in s_values
                  if prepared.parameter_at(s) != reference_parameter_at_length(curve, s)
                  or arclength.parameter_at_length(curve, s) != reference_parameter_at_length(curve, s)]
    check(not mismatches, f"{name}: {len(s_values)} inversions bit-identical to the reference")

print("\n-- prepared objects are snapshots: later edits cannot reach them --")
curve = nonuniform_open()
s_probe = [arclength.length_of(curve) * f for f in (0.2, 0.5, 0.8)]
prepared = arclength.PreparedLength(curve)
expected_before = [reference_parameter_at_length(curve, s) for s in s_probe]
curve.Points[3] = r3.Point4d(4, 2, 2.5, 1.0)           # move one control point
check([prepared.parameter_at(s) for s in s_probe] == expected_before,
      "control-point edit: the prepared length still answers for the curve it was made from")
fresh = [arclength.parameter_at_length(curve, s) for s in s_probe]
check(fresh == [reference_parameter_at_length(curve, s) for s in s_probe],
      "control-point edit: a fresh call answers for the edited curve")
check(fresh != expected_before, "control-point edit changes the inversion (the test can see it)")
curve.Knots[4] = 0.6                                    # move an interior knot
check([arclength.parameter_at_length(curve, s) for s in s_probe]
      == [reference_parameter_at_length(curve, s) for s in s_probe],
      "knot edit: a fresh call answers for the edited curve")

print("\n-- several curves interleaved stay exact --")
curves = [nonuniform_open(bump=0.1 * i) for i in range(6)] + [cubic_open(), circle_nurbs()]
probe = {id(c): [arclength.length_of(c) * f for f in (0.3, 0.6)] for c in curves}
mismatch = 0
for _pass in range(2):
    for c in curves:
        for s in probe[id(c)]:
            mismatch += arclength.parameter_at_length(c, s) != reference_parameter_at_length(c, s)
check(mismatch == 0, f"{len(curves)} curves round-robin, two passes: every answer is its own")

print("\n-- partial lengths: edges, knots and reversed bounds --")
for name, make in (("non-uniform open", nonuniform_open), ("closed 43 spans", closed_43_spans)):
    curve = make()
    d = curve.Domain
    table = arclength._prepare(curve)
    ts = [d.T0, d.T0 + 1e-13, d.T0 + 1e-6, d.T1 - 1e-6, d.T1 - 1e-13, d.T1, d.T0 - 0.5, d.T1 + 0.5]
    for k in arclength._interior_knots(curve):
        ts += [k - 1e-9, k, k + 1e-9]
    ts += spread_parameters(curve, 4)
    bad = [t for t in ts if not same_number(arclength._length_to(curve, table, t),
                                            arclength.length_of(curve, d.T0, t))]
    check(not bad, f"{name}: partial length equals length_of at {len(ts)} parameters")
prepared_total = arclength.PreparedLength(closed_43_spans()).total
check(prepared_total == arclength.length_of(closed_43_spans()),
      "the prepared total equals length_of, bit for bit (closed 43 spans)")

print("\n-- zero-length and refused inputs --")
zero = degenerate_zero_length()
check(arclength.parameter_at_length(zero, 0.0) == zero.Domain.T0
      and reference_parameter_at_length(zero, 0.0) == zero.Domain.T0,
      "zero-length curve: s = 0 returns the domain start")
refused = []
for c in (zero, nonuniform_open()):
    for s in (-1.0, arclength.length_of(c) + 1.0):
        try:
            arclength.parameter_at_length(c, s)
            refused.append(s)
        except ValueError:
            pass
check(not refused, "out-of-range arc lengths are refused with ValueError")

print("\n-- closest point: native search against the Python search --")
native_ok = native.available()
check(native_ok, "native core exports narwhal_pure_core_nearest_on_nurbs (set NARWHAL_PURE_CORE)")
if not native_ok:
    print("       the gates below need the native core; the Python fallback cannot prove them")
for name, make in FIXTURES.items():
    curve = make()
    queries = queries_near(curve, 40, seed=len(name))
    tol = nearest.default_tolerance()
    expected_native = sum(1 for q in queries
                          if nearest._analytic_target(curve, nearest._pt(q), tol) is None)
    with Count(native, "search") as calls:
        got = [closest_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    with python_only():
        want = [closest_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    check(got == want, f"{name}: {len(queries)} closest points bit-identical to the Python search")
    check(calls.calls == expected_native,
          f"{name}: {calls.calls} native searches for {expected_native} non-analytic queries")

print("\n-- prepared closest queries: packed once, snapshot, analytic routes kept --")
curve = closed_43_spans()
queries = queries_near(curve, 50, seed=5)
with Count(nearest, "_native_pack") as packs:
    prepared = nearest.PreparedCurve(curve)
    answers = [closest_fields(prepared.closest(q)) for q in queries]
check(packs.calls == 1, f"a prepared curve packs once ({packs.calls} pack for {len(queries)} queries)")
with python_only():
    want = [closest_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
check(answers == want, "prepared answers bit-identical to the Python search")
with Count(nearest, "_native_pack") as packs:
    [nearest.closest_point_on_curve(curve, q) for q in queries[:10]]
check(packs.calls == 10, "the default call packs per query, so each answer reads the curve it is given")

curve = closed_43_spans()
q = queries_near(curve, 1, seed=3)[0]
with python_only():
    before = closest_fields(nearest.closest_point_on_curve(curve, q))
prepared = nearest.PreparedCurve(curve)
curve.Points[10] = r3.Point4d(curve.Points[10].X, curve.Points[10].Y, curve.Points[10].Z + 2.0, 1.0)
check(closest_fields(prepared.closest(q)) == before, "a prepared curve ignores a later control-point edit")
with python_only():
    edited = closest_fields(nearest.closest_point_on_curve(curve, q))
check(closest_fields(nearest.closest_point_on_curve(curve, q)) == edited,
      "the default call answers for the edited curve")
check(edited != before, "the edit changes the closest point (the test can see it)")

circle = circle_nurbs()
off_axis = (1.0 + 9.0, 2.0, 3.0)
analytic = nearest.PreparedCurve(circle).closest(off_axis)
check(analytic.method == "circle", "a prepared circle keeps its analytic route")
with python_only():
    check(closest_fields(analytic) == closest_fields(nearest.closest_point_on_curve(circle, off_axis)),
          "the prepared analytic answer equals the default answer")
circle = circle_nurbs()
with python_only():
    circle_before = closest_fields(nearest.closest_point_on_curve(circle, off_axis))
prepared_circle = nearest.PreparedCurve(circle)
circle.Points[0] = r3.Point4d(circle.Points[0].X + 3.0, circle.Points[0].Y, circle.Points[0].Z, 1.0)
check(closest_fields(prepared_circle.closest(off_axis)) == circle_before,
      "a prepared circle ignores a later edit to its analytic form")

print("\n-- native core missing: the Python search answers, bit for bit --")
saved_env = os.environ.get("NARWHAL_PURE_CORE")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-directory", "libnarwhal_pure_core.so")
try:
    curve = nonuniform_open()
    queries = queries_near(curve, 10, seed=11)
    check(not native.available(), "a missing library reports the native core unavailable")
    got = [closest_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    prepared = nearest.PreparedCurve(curve)
    prepared_got = [closest_fields(prepared.closest(q)) for q in queries]
    with python_only():
        want = [closest_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    check(got == want and prepared_got == want, "fallback answers equal the Python search")
    snap = nonuniform_open()
    snap_queries = queries_near(snap, 4, seed=13)
    with python_only():
        snap_before = [closest_fields(nearest.closest_point_on_curve(snap, q)) for q in snap_queries]
    snap_prepared = nearest.PreparedCurve(snap)
    snap.Points[3] = r3.Point4d(4, 2, 2.5, 1.0)
    check([closest_fields(snap_prepared.closest(q)) for q in snap_queries] == snap_before,
          "without the native core, a prepared curve ignores later edits")
    cubic = cubic_open()
    fallback_curve, _info = rebuild.rebuild_curve(cubic, 6, 3)
    with Patch(arclength, "PreparedLength", ReferenceLength), Patch(nearest, "PreparedCurve", ReferencePrepared):
        reference_curve, _info = rebuild.rebuild_curve(cubic, 6, 3)
    check(rebuilt_equal(fallback_curve, reference_curve),
          "fallback rebuild (no native core) is bit-identical to the reference pipeline")
finally:
    if saved_env is None:
        os.environ.pop("NARWHAL_PURE_CORE", None)
    else:
        os.environ["NARWHAL_PURE_CORE"] = saved_env

print("\n-- operation-count gate: one arc-length table per operation --")
curve = closed_43_spans()
check(curve.SpanCount == 43, "fixture: the closed curve has the saved trace's 43 spans")
total = arclength.length_of(curve)
s_values = [total * (i + 0.5) / 60 for i in range(60)]
with Count(arclength, "_speed") as fast_speed:
    prepared = arclength.PreparedLength(curve)
    new_out = [prepared.parameter_at(s) for s in s_values]
with Count(arclength, "_speed") as ref_speed:
    ref_out = [reference_parameter_at_length(curve, s) for s in s_values]
print(f"       60 inversions on 43 spans: {fast_speed.calls} derivative evaluations "
      f"(reference {ref_speed.calls})")
check(new_out == ref_out, "60 inversions bit-identical to the reference")
MAX_DERIVATIVES_FOR_60_INVERSIONS = 20_000
check(fast_speed.calls <= MAX_DERIVATIVES_FOR_60_INVERSIONS,
      f"derivative evaluations <= {MAX_DERIVATIVES_FOR_60_INVERSIONS} for 60 inversions")

print("\n-- rebuild: 100 points on a 43-span closed curve, operation counts and time --")
curve = closed_43_spans()
samples = 2000                                         # max(20 * 100, 400)
class CountTargets(Patch):
    """Count the targets passed to `native.search_many` while still running it."""

    def __init__(self):
        self.calls = self.targets = 0
        super().__init__(native, "search_many", None)

    def __enter__(self):
        self.saved = native.search_many

        def counted(packed, targets):
            targets = list(targets)
            self.calls += 1
            self.targets += len(targets)
            return self.saved(packed, targets)

        native.search_many = counted
        return self


with Count(arclength, "_prepare") as tables, Count(nearest, "_native_pack") as packs, \
        Count(native, "search") as searches, CountTargets() as batches:
    started = time.perf_counter()
    rebuilt, info = rebuild.rebuild_curve(curve, 100, 3)
    elapsed = time.perf_counter() - started
fits = rebuild.PARAM_ITERATIONS + 1
print(f"       {tables.calls} arc-length tables, {packs.calls} packs, {batches.targets} native searches "
      f"in {batches.calls} batches ({searches.calls} single), {elapsed:.2f} s")
check(tables.calls == 3, "one arc-length table for the samples, one per max_deviation direction")
check(packs.calls == fits + 2,
      f"one pack per fit ({fits}) plus one per max_deviation direction, none per sample")
if native.batch_capability() == "qualified":
    check(searches.calls == 0 and batches.calls == fits + 2
      and batches.targets == fits * samples + 2 * 200,
      "every closest query of the fits and the deviation check reaches the native search, "
      "one batch per prepared curve")
else:
    check(searches.calls == fits * samples + 2 * 200,
          "off the qualified platform every closest query is a single native search")
check(rebuilt.IsValid and info["point_count"] == 100, "the 100-point rebuild is valid")
check(elapsed < 10.0, f"100-point rebuild under 10 s ({elapsed:.2f} s)")

print("\n-- rebuild: whole pipeline against the reference pipeline --")
for name, make, count in (("circle, 8 points", circle_nurbs, 8), ("open cubic, 6 points", cubic_open, 6)):
    curve = make()
    with Patch(arclength, "PreparedLength", ReferenceLength), Patch(nearest, "PreparedCurve", ReferencePrepared):
        ref_curve, ref_info = rebuild.rebuild_curve(curve, count, 3)
    started = time.perf_counter()
    new_curve, new_info = rebuild.rebuild_curve(curve, count, 3)
    elapsed = time.perf_counter() - started
    check(rebuilt_equal(ref_curve, new_curve) and ref_info["deviation"] == new_info["deviation"],
          f"{name}: rebuilt control points, knots and deviation bit-identical ({elapsed:.2f} s)")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
