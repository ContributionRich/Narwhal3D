#!/usr/bin/env python3
"""Curve/curve intersection in Rust: the same hits as `intersect.curve_curve`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_intersect.py

Parity is exact: for two valid `NurbsCurve`s, `curve_curve` returns the same hits
(point, t_a, t_b) through Rust as through `_subdivide` + `_dedupe`, bit for bit. Rust
ports the rhino3dm calls the recursion makes (`Split`, `GetBoundingBox`, `PointAt`,
`Intersection.LineLine`) from the openNURBS rhino3dm 8.35.0 pins, and repeats the
Python helpers with CPython's float semantics. The overlap test stays in Python;
other curve types keep the Python recursion.
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
native = load("native_intersect")
ix = load("intersect")
interp = load("interpolate")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def line(p, q):
    return r3.LineCurve(r3.Point3d(*p), r3.Point3d(*q)).ToNurbsCurve()


def fields(hits):
    return [(h.point, h.t_a, h.t_b, h.overlap, h.t_a_end, h.t_b_end) for h in hits]


def python_curve_curve(a, b, **kw):
    real = ix._native_point_hits
    ix._native_point_hits = lambda *args: None
    try:
        return ix.curve_curve(a, b, **kw)
    finally:
        ix._native_point_hits = real


def same(a, b, **kw):
    """(native route taken, results equal) for `curve_curve(a, b, **kw)`."""
    before = native._native_calls
    got = fields(ix.curve_curve(a, b, **kw))
    routed = native._native_calls > before
    return routed, got == fields(python_curve_curve(a, b, **kw)), len(got)


def circle(c, r):
    return r3.Circle(r3.Point3d(*c), r).ToNurbsCurve()


def tilted_tangencies():
    """A circle in a tilted plane with a line tangent to it, and a second circle
    touching it: chords there are parallel, so `_segment_closest` seeds Newton. Built
    in XY (tangent at (R, 0, 0)), then rotated and moved off the axes together."""
    r = 2.37
    curves = [circle((0, 0, 0), r), line((r, -3.1, 0), (r, 2.3, 0)), circle((r + 1.19, 0, 0), 1.19),
              # 5e-7 clear of the circle: inside a 1e-6 tolerance, so Newton stops short of
              # a zero gap and the hit is the midpoint of the two closest points
              line((r + 5e-7, -3.1, 0), (r + 5e-7, 2.3, 0)),
              circle((r + 1.19 + 5e-7, 0, 0), 1.19)]
    turn = r3.Transform.Rotation(0.83, r3.Vector3d(0.4, -0.7, 0.2), r3.Point3d(0, 0, 0))
    move = r3.Transform.Translation(1.3, -2.7, 0.9)
    for c in curves:
        c.Transform(turn)
        c.Transform(move)
    return curves


tilted, tilted_line, tilted_twin, near_line, near_twin = tilted_tangencies()
wave = cpc([(i, math.sin(i * 0.9) * 3, 0) for i in range(20)])
FIXTURES = {
    "two 20-point cubics": (wave, cpc([(i, math.cos(i * 0.6) * 3, 0) for i in range(20)])),
    "cubic x circle": (wave, circle((8, 0, 0), 4)),
    "circle x circle (rational)": (circle((0, 0, 0), 5), circle((6, 1, 0), 4)),
    "arc x line": (r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(7, 1, 0)).ToNurbsCurve(),
                   line((-1, 2, 0), (9, 2.5, 0))),
    "line x line": (line((0, 0, 0), (4, 4, 0)), line((0, 4, 0), (4, 0, 0))),
    "tangent line on a circle": (circle((0, 0, 0), 2), line((-3, 2, 0), (3, 2, 0))),
    "shared end points": (cpc([(0, 0, 0), (1, 2, 0), (3, 1, 0), (4, 0, 0)]),
                          cpc([(4, 0, 0), (5, -2, 0), (7, 1, 0), (8, 0, 0)])),
    "crossing at the first split parameter": (line((0, 0, 0), (2, 2, 0)), line((0, 2, 0), (2, 0, 0))),
    "tilted circle and its tangent line": (tilted, tilted_line),
    "tilted circles touching": (tilted, tilted_twin),
    "a line 5e-7 clear of a tilted circle": (tilted, near_line),
    "a circle 5e-7 clear of a tilted circle": (tilted, near_twin),
    "skew 3-D, no hit": (cpc([(0, 0, 0), (1, 2, 1), (3, 1, 2), (4, 0, 3)]),
                         cpc([(0, 0, 5), (1, 2, 6), (3, 1, 7), (4, 0, 8)])),
    "quadratic x degree 5": (cpc([(0, -3, 0), (2, 4, 0), (5, -2, 0), (8, 3, 0), (11, -1, 0)], 2),
                            cpc([(i, math.sin(i) * 2, 0) for i in range(12)], 5)),
    "interpolated x closed interpolated": (
        interp.interpolate_curve([(i * 1.5, math.sin(i) * 3, 0.0) for i in range(8)]),
        interp.interpolate_closed_curve([(5 + 3 * math.cos(k), 2 * math.sin(k), 0.0)
                                         for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)])),
    "huge coordinates": (cpc([(1e5 + i * 10, 1e5 + math.sin(i) * 30, 0) for i in range(10)]),
                         circle((1e5 + 40, 1e5, 0), 25)),
}

check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: curve_curve through Rust == the Python recursion, bit for bit --")
for name, (a, b) in FIXTURES.items():
    results = [same(a, b, tol=tol, max_depth=depth) for tol, depth in ((1e-6, 40), (1e-3, 40), (1e-9, 40), (1e-6, 12))]
    check(all(r[0] and r[1] for r in results),
          f"{name}: {[r[2] for r in results]} hits at 4 tolerance/depth settings, all equal")

print("\n-- random curve pairs: degrees 1-5, rational, multiple knots, 2-D and 3-D --")


def random_curve(r):
    degree = r.randint(1, 5)
    count = r.randint(degree + 1, degree + 10)
    rational = r.random() < 0.4
    curve = r3.NurbsCurve(3, rational, degree + 1, count)
    flat = r.random() < 0.7
    for i in range(count):
        x, y = r.uniform(-10, 10), r.uniform(-10, 10)
        z = 0.0 if flat else r.uniform(-2, 2)
        w = r.uniform(0.3, 3.0) if rational else 1.0
        curve.Points[i] = r3.Point4d(x * w, y * w, z * w, w)
    t0 = r.choice((0.0, -2.5, 7.0))
    t1 = t0 + r.choice((1.0, 10.0, r.uniform(0.5, 40)))
    interior = sorted(r.uniform(t0, t1) for _ in range(count - degree - 1))
    for i in range(1, len(interior)):
        if r.random() < 0.25:
            interior[i] = interior[i - 1]
    knots = [t0] * degree + interior + [t1] * degree
    for i, k in enumerate(knots):
        curve.Knots[i] = k
    return curve


rng = random.Random(20261108)
pairs = differ = routed = hits = rationals = 0
while pairs < 200:
    a, b = random_curve(rng), random_curve(rng)
    if not (a.IsValid and b.IsValid):
        continue
    pairs += 1
    rationals += a.IsRational or b.IsRational
    took, equal, n = same(a, b)
    routed += took
    differ += not equal
    hits += n
check(pairs == 200 and routed == 200 and rationals > 50 and hits > 300 and differ == 0,
      f"{pairs} pairs ({rationals} with a rational curve), {hits} hits: {differ} differ, {routed} routed")

print("\n-- what stays in Python --")
poly = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0)])
took, equal, n = same(poly, circle((3, 1, 0), 2))
check(not took and equal, f"a PolylineCurve keeps the Python recursion ({n} hits)")
bent = cpc([(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)])
straight = line((1, -1, 0), (1, 0, 0))
over_a, over_b = line((0, 0, 0), (4, 0, 0)), line((1, 0, 0), (6, 0, 0))
before = native._native_calls
got = ix.curve_curve(over_a, over_b)
check(native._native_calls == before and len(got) == 1 and got[0].overlap,
      "an overlap is found by the Python overlap test before any recursion")
check(same(bent, straight)[1], "a collinear cubic touching a line end gives the same answer")
try:
    ix.curve_curve(wave, wave)
    refused = False
except ValueError:
    refused = True
check(refused, "self-intersection (a is b) is still refused")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    a, b = FIXTURES["cubic x circle"]
    before = native._native_calls
    check(native._native_calls == before and fields(ix.curve_curve(a, b)) == fields(python_curve_curve(a, b)),
          "without the library the Python recursion answers")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- timing: the recursion alone (best of 3) --")


def best(fn, reps=1):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


faster = True
for name in ("two 20-point cubics", "cubic x circle", "circle x circle (rational)"):
    a, b = FIXTURES[name]

    def python_recursion():
        h = []
        ix._subdivide(a, b, 1e-6, 0, 40, h)
        return ix._dedupe(h, 1e-6)

    python = best(python_recursion)
    rust = best(lambda: native.point_hits(a, b, 1e-6, 40), 10)
    faster &= rust < python
    print(f"  {name}: python {python * 1e3:6.1f} ms, rust {rust * 1e3:5.2f} ms ({python / rust:.0f}x)")
check(faster, "the Rust recursion is faster end to end")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
