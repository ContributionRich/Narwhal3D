#!/usr/bin/env python3
"""Near snap in Rust: the same nearest point as `nearest.closest_point_on_curve`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_nearest.py

Tolerance: the native and Python points agree within 1e-9 x max(1, the curve's
bounding-box diagonal). The two evaluate the same curve with different code
(openNURBS through rhino3dm, de Boor in Rust), so they agree to rounding, not bit
for bit; the measured worst case is printed.

Tie rule: both scan the same candidates in the same order (samples by parameter,
then Newton results by seed) and keep the first strictly smaller distance, so an
exact tie goes to the earlier candidate. The equal-minima fixtures check that both
pick the same one of two mirror-image minima.
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


def diagonal(curve):
    box = curve.GetBoundingBox()
    return max(1.0, math.dist((box.Min.X, box.Min.Y, box.Min.Z), (box.Max.X, box.Max.Y, box.Max.Z)))


check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: native near_point == closest_point_on_curve(...).point --")
rng = random.Random(77)
worst_overall = 0.0
for name, curve in FIXTURES.items():
    cache = nearest.NearCache(curve)
    box = curve.GetBoundingBox()
    lo = (box.Min.X - 4, box.Min.Y - 4, box.Min.Z - 3)
    hi = (box.Max.X + 4, box.Max.Y + 4, box.Max.Z + 3)
    queries = [tuple(rng.uniform(a, b) for a, b in zip(lo, hi)) for _ in range(150)]
    bound = 1e-9 * diagonal(curve)
    worst, missing = 0.0, 0
    for q in queries:
        want = nearest.closest_point_on_curve(curve, q, TOL).point
        got = nearest.near_point(curve, q, cache, TOL)
        if got is None:
            missing += 1
            continue
        worst = max(worst, math.dist(want, got))
    worst_overall = max(worst_overall, worst / bound * 1e-9)
    routed = 150 - missing
    if cache.knot_sampled:
        ok = cache.packed is not None and missing == 0 and worst <= bound
    else:   # only analytic targets are searched natively; the rest stay in Python
        ok = worst <= bound and (routed == 150) == (cache.analytic is not None)
    check(ok, f"{name}: {routed}/150 routed natively, worst {worst:.1e} (bound {bound:.1e})")
print(f"  [info] worst relative disagreement {worst_overall:.1e} of the bounding-box diagonal")

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

print("\n-- two equal minima --")
twin = cpc([(0, 5, 0), (2, -5, 0), (5, 8, 0), (8, -5, 0), (10, 5, 0)])
for label, q in (("mirror dips, query on the axis", (5.0, -20.0, 0.0)),
                 ("both ends, query on the axis", (5.0, 40.0, 0.0))):
    want = nearest.closest_point_on_curve(twin, q, TOL).point
    got = nearest.near_point(twin, q, nearest.NearCache(twin), TOL)
    check(math.dist(want, got) <= 1e-9 * diagonal(twin),
          f"{label}: both pick the same minimum (python {tuple(round(v, 9) for v in want)}, "
          f"native {tuple(round(v, 9) for v in got)})")

print("\n-- the snap uses it, and falls back without the library --")
target = osnap.SnapTarget(points=[(0, 0, 0), (10, 3, 0)], curve=FIXTURES["open cubic, 12 points"])
before = native._native_calls
p1 = osnap._refine_near(target, (4.0, 1.0, 0.0))
check(native._native_calls > before and target._near is not None, "Near takes the Rust route and caches the curve")
check(not nearest.NearCache(FIXTURES["poly curve"]).knot_sampled
      and nearest.near_point(FIXTURES["poly curve"], (5.0, 1.0, 0.0), nearest.NearCache(FIXTURES["poly curve"]), TOL) is None,
      "a poly curve (sampled by SpanCount in Python) keeps the Python search")
check(math.dist(p1, nearest.closest_point_on_curve(target.curve, (4.0, 1.0, 0.0)).point) < 1e-9,
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
