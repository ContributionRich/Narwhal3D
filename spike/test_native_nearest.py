#!/usr/bin/env python3
"""Real-rhino3dm Near integration, fallback, packing and performance guards.

The original random numeric corpus now replays directly against Rust ABIs in
native/pure_core/tests/nurbs_golden.rs. Every original fixture host query and all
wrapper fields are frozen in fixtures/nurbs_host.txt; no random engine lives here.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_nearest.py

Parity is exact: on a `NurbsCurve` the native search returns the same parameter,
point, distance and end flag as `nearest._search`, bit for bit. Rust evaluates the
curve with openNURBS's own algorithms (span search, Bezier de Casteljau and Lee,
basis functions and derivatives, quotient rule) in openNURBS's operation order, and
mirrors every arithmetic step of the Python search. Other curves (a line, polyline
or poly curve) are not packed and keep the Python search.

Tie rule: both scan the same candidates in the same order (samples by parameter,
then Newton results by seed) and keep the first strictly smaller distance, so an
exact tie goes to the earlier candidate. The equal-minima fixtures check that both
pick the same one of two mirror-image minima.
"""
import importlib.util
import hashlib
import json
import math
import os
import sys
import time
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "native", "pure_core", "target", "x86_64-unknown-linux-gnu", "release",
    "libnarwhal_pure_core.so"))
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
native = load("native_nearest")
nearest = load("nearest")
osnap = load("osnap")
TOL = 0.01
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def periodic(points, degree=3):
    return r3.NurbsCurve.CreateFromPoints([r3.Point3d(*p) for p in points], degree, True) \
        if hasattr(r3.NurbsCurve, "CreateFromPoints") else None


def rational():
    c = cpc([(0, 0, 0), (2, 6, 0), (7, 5, 2), (10, 0, 0)])
    c.Points[1] = r3.Point4d(2 * 3.0, 6 * 3.0, 0, 3.0)    # homogeneous, weight 3
    c.Points[2] = r3.Point4d(7 * 0.4, 5 * 0.4, 2 * 0.4, 0.4)
    return c


def polycurve():
    pc = r3.PolyCurve()
    pc.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(4, 0, 0)))
    pc.Append(r3.Arc(r3.Point3d(4, 0, 0), r3.Point3d(6, 2, 0), r3.Point3d(8, 0, 0)).ToNurbsCurve())
    pc.Append(cpc([(8, 0, 0), (9, -3, 1), (12, -1, 0), (14, 2, 0)]))
    return pc


def closed_cubic():
    pts = [(math.cos(2 * math.pi * i / 8) * (5 + (i % 2)), math.sin(2 * math.pi * i / 8) * (5 + (i % 2)), 0.3 * (i % 3))
           for i in range(8)]
    knots = [float(i) for i in range(len(pts) + 3 + 4 - 2)]
    c = r3.NurbsCurve(3, False, 4, len(pts) + 3)
    for i in range(len(pts) + 3):
        x, y, z = pts[i % len(pts)]
        c.Points[i] = r3.Point4d(x, y, z, 1.0)
    for i, k in enumerate(knots):
        c.Knots[i] = k
    return c


FIXTURES = {
    "LineCurve": r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 3, 1)),
    "uneven linear cubic": cpc([(0, 0, 0), (1, 0, 0), (2, 0, 0), (10, 0, 0)]),
    "circle": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "arc": r3.Arc(r3.Circle(r3.Point3d(0, 0, 0), 5.0), 1.5).ToNurbsCurve(),
    "tilted arc": r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 2), r3.Point3d(7, 1, -1)).ToNurbsCurve(),
    "open cubic, 4 points": cpc([(0, 0, 0), (3, 5, 0), (6, -2, 1), (10, 3, 0)]),
    "open cubic, 12 points": cpc([(i, math.sin(i) * 3, 0.2 * i) for i in range(12)]),
    "open cubic, 40 points": cpc([(i * 0.5, math.sin(i * 0.4) * 3, math.cos(i * 0.3)) for i in range(40)]),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "closed cubic": closed_cubic(),
    "rational cubic": rational(),
    "polyline curve": r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0), r3.Point3d(9, 4, 1)]),
    "poly curve": polycurve(),
}


check(native.available(), f"the pure-core library loads ({LIBRARY})")

# Numeric search is replayed in Rust. Preserve EVERY original fixture host query,
# including non-NURBS fallback and analytic method/ambiguity verification cases.
with open(os.path.join(ROOT, "native/pure_core/tests/fixtures/nurbs_host.txt"), "rb") as source:
    host_bytes = source.read()
check(hashlib.sha256(host_bytes).hexdigest()
      == "4cb19de12f9879bb0d63a5ee8ad1e07084961e107eb8a342733c799b691e3433",
      "all 31 original host groups / 3096 queries have frozen byte integrity")
HOST = {(name, count): (queries, answers)
        for name, count, queries, answers in map(json.loads, host_bytes.splitlines())}


def host_case(curve, count):
    name = next(name for name, candidate in FIXTURES.items() if candidate is curve)
    return HOST[name, count]


def queries_for(curve, count):
    return host_case(curve, count)[0]


def frozen_fields(curve, count):
    return [(t, tuple(point), distance, method, end, ambiguous)
            for t, point, distance, method, end, ambiguous in host_case(curve, count)[1]]

for name, curve in FIXTURES.items():
    cache = nearest.NearCache(curve)
    before = native._native_calls
    queries = queries_for(curve, 150)
    got = [nearest.near_point(curve, q, cache, TOL) for q in queries]
    is_nurbs = isinstance(curve, r3.NurbsCurve)
    want = [answer[1] for answer in frozen_fields(curve, 150)] if is_nurbs else [None] * len(queries)
    check((cache.packed is not None) == is_nurbs and got == want
          and ((native._native_calls > before) if is_nurbs else native._native_calls == before),
          f"{name}: all {len(queries)} original host queries, type packing/routing")
    if is_nurbs:
        packed = cache.packed
        check(list(packed.knots) == list(curve.Knots.ToList())
              and list(packed.cvs) == [v for cp in curve.Points for v in (cp.X, cp.Y, cp.Z, cp.W)]
              and packed.order == curve.Order and packed.rational == curve.IsRational
              and packed.closed == curve.IsClosed, f"{name}: real homogeneous adapter framing")

edited = FIXTURES["rational cubic"].Duplicate()
old = native.pack(edited)
snapshot = tuple(old.cvs)
cp = edited.Points[1]
edited.Points[1] = r3.Point4d(cp.X + 1, cp.Y, cp.Z, cp.W)
check(tuple(old.cvs) == snapshot and tuple(native.pack(edited).cvs) != snapshot,
      "packed arrays own a snapshot; repacking sees real control-point edits")

print("\n-- analytic cases keep their exact answers --")
circle = FIXTURES["circle"]
cache = nearest.NearCache(circle)
check(cache.analytic is not None and cache.analytic[0] == "circle", "a circle is recognised once")
got = nearest.near_point(circle, (9.0, 8.0, 3.0), cache, TOL)
check(math.dist(got, (5.0, 5.0, 0.0)) < 1e-12, f"circle: the radial projection {got}")
check(nearest.near_point(circle, (1.0, 2.0, 7.0), cache, TOL)
      == nearest.closest_point_on_curve(circle, (1.0, 2.0, 7.0), TOL).point,
      "on the circle's axis both return the domain start")
line = FIXTURES["uneven linear cubic"]
got = nearest.near_point(line, (4.0, 3.0, 0.0), nearest.NearCache(line), TOL)
check(math.dist(got, (4.0, 0.0, 0.0)) < 1e-9, f"uneven linear cubic: the perpendicular foot {got}")
got = nearest.near_point(line, (-5.0, 1.0, 0.0), nearest.NearCache(line), TOL)
check(got == (0.0, 0.0, 0.0), "off the end: the end point")

print("\n-- closest_point_on_curve: an analytic target's verification search runs in Rust --")


def all_fields(c):
    return (c.t, c.point, c.distance, c.method, c.at_end, c.ambiguous)


for name in ("circle", "arc", "tilted arc", "uneven linear cubic", "LineCurve"):
    curve = FIXTURES[name]
    queries = queries_for(curve, 60)
    before = native._native_calls
    got = [all_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    calls = native._native_calls - before
    real = nearest._native_pack
    nearest._native_pack = lambda curve: None
    try:
        want = [all_fields(nearest.closest_point_on_curve(curve, q)) for q in queries]
    finally:
        nearest._native_pack = real
    native_route = isinstance(curve, r3.NurbsCurve)
    check(got == want == frozen_fields(curve, 60)
              and (calls >= len(queries) if native_route else calls == 0),
          f"{name}: {len(queries)} answers equal the Python verification, "
          f"{calls} native searches")
on_axis = nearest.closest_point_on_curve(FIXTURES["circle"], (1.0, 2.0, 7.0))
check(on_axis.ambiguous and on_axis.t == FIXTURES["circle"].Domain.T0,
      "an on-axis query is still the ambiguous domain start")

print("\n-- the snap uses it, and falls back without the library --")
target = osnap.SnapTarget(points=[(0, 0, 0), (10, 3, 0)], curve=FIXTURES["open cubic, 12 points"])
before = native._native_calls
p1 = osnap._refine_near(target, (4.0, 1.0, 0.0))
check(native._native_calls > before and target._near is not None, "Near takes the Rust route and caches the curve")
check(nearest.near_point(FIXTURES["poly curve"], (5.0, 1.0, 0.0), nearest.NearCache(FIXTURES["poly curve"]), TOL) is None,
      "a poly curve (not a NurbsCurve) keeps the Python search")
check(p1 == nearest.closest_point_on_curve(target.curve, (4.0, 1.0, 0.0)).point,
      "and returns the Python point")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    fresh = osnap.SnapTarget(points=[(0, 0, 0), (10, 3, 0)], curve=FIXTURES["open cubic, 12 points"])
    before = native._native_calls
    p2 = osnap._refine_near(fresh, (4.0, 1.0, 0.0))
    check(native._native_calls == before and p2 == nearest.closest_point_on_curve(fresh.curve, (4.0, 1.0, 0.0)).point,
          "without the library the Python search answers")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- PreparedCurve.closest_many: one native call, the same answers as closest --")


def fields(c):
    return (c.t, c.point, c.distance, c.method, c.at_end, c.ambiguous)


for name, curve in FIXTURES.items():
    prepared = nearest.PreparedCurve(curve)
    queries = queries_for(curve, 40) + [(float("nan"), 0.0, 0.0)]
    before = native._native_many_calls
    got = [fields(c) for c in prepared.closest_many(queries[:-1])]
    batched = native._native_many_calls - before
    want = [fields(prepared.closest(q)) for q in queries[:-1]]
    general = isinstance(curve, r3.NurbsCurve) and nearest._is_general_nurbs(curve)
    expected = int(general) if native.batch_capability() == "qualified" else 0
    check(got == want == frozen_fields(curve, 40) and batched == expected,
          f"{name}: {len(want)} answers equal closest, {'one batch' if expected else 'per point'}")
cubic = nearest.PreparedCurve(FIXTURES["open cubic, 12 points"])
mixed = [(1.0, 2.0, 0.0), (float("nan"), 1.0, 0.0), (5.0, -1.0, 0.0)]


def outcome(fn):
    try:
        return ("value", fn())
    except Exception as exc:  # noqa: BLE001 -- the error itself is compared
        return ("error", type(exc).__name__)


got = outcome(lambda: [fields(c) for c in cubic.closest_many(mixed)])
check(got == outcome(lambda: [fields(cubic.closest(q)) for q in mixed]) and got[0] == "error",
      f"a non-finite point goes to closest itself, which raises as before ({got[1]})")
check([fields(c) for c in cubic.closest_many(mixed[::2])] == [fields(cubic.closest(q)) for q in mixed[::2]],
      "the finite points are answered by the batch")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    before = native._native_many_calls
    plain = nearest.PreparedCurve(FIXTURES["open cubic, 12 points"])
    got = [fields(c) for c in plain.closest_many(mixed[::2])]
    check(native._native_many_calls == before and got == [fields(plain.closest(q)) for q in mixed[::2]],
          "without the library closest_many answers point by point in Python")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

bad = native.pack(FIXTURES["open cubic, 12 points"])
bad.order = 0
before = (native._native_calls, native._native_many_calls)
check(native.search(bad, (1.0, 2.0, 0.0)) is None
      and native.search_many(bad, [(1.0, 2.0, 0.0)]) is None
      and before == (native._native_calls, native._native_many_calls),
      "invalid packed metadata is refused without recording native success")
platform = sys.platform
signature, binding = native._many_signature, native._many_binding
try:
    sys.platform = "win32"
    native._many_signature = None
    before = native._native_many_calls
    check(native.batch_capability().startswith("not qualified")
          and native.search_many(cubic._packed, mixed[::2]) is None
          and [fields(c) for c in cubic.closest_many(mixed[::2])]
              == [fields(cubic.closest(q)) for q in mixed[::2]]
          and native._native_many_calls == before,
          "unqualified batch platform uses real scalar routing, not a batch")
finally:
    sys.platform = platform
    native._many_signature, native._many_binding = signature, binding

print("\n-- per-query Near timing (best of 3, cache built once) --")


def best(fn, reps):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


queries = [(math.cos(k) * 7, math.sin(k * 1.3) * 6, 0.5) for k in range(30)]
for name in ("circle", "open cubic, 4 points", "open cubic, 40 points"):
    curve = FIXTURES[name]
    cache = nearest.NearCache(curve)
    rust = best(lambda: [nearest.near_point(curve, q, cache, TOL) for q in queries], 3) / len(queries)
    python = best(lambda: [nearest.closest_point_on_curve(curve, q, TOL) for q in queries], 3) / len(queries)
    print(f"  [perf] {name}: native {rust * 1e6:.1f} us, python {python * 1e6:.1f} us, ratio {rust / python:.3f}")
    check(rust / python <= 0.9, f"Near wins by at least 10% on {name}")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
