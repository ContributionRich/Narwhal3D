#!/usr/bin/env python3
"""Uniform curve samples in Rust: the same points as `bridge.sample_curve`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_curve_samples.py

Parity is exact: on a `NurbsCurve`, `bridge.sample_curve_xyz` returns the points
`sample_curve` gives, bit for bit. Rust evaluates the curve with the Near snap's port
of openNURBS's `ON_NurbsCurve::Evaluate` (`native/pure_core/src/nearest.rs`) at the
parameters Python computes, `T0 + (T1 - T0) * i / n`, on the curve's own `Domain`.
Other curves (a line, polyline, arc or poly curve) keep `sample_curve`.
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


native = load("native_nearest")
interp = load("interpolate")
bridge = load("bridge")
preview = load("preview")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def python_samples(curve, n):
    return [(p.X, p.Y, p.Z) for p in bridge.sample_curve(curve, n)]


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def rational():
    c = cpc([(0, 0, 0), (2, 6, 0), (7, 5, 2), (10, 0, 0)])
    c.Points[1] = r3.Point4d(2 * 3.0, 6 * 3.0, 0, 3.0)    # homogeneous, weight 3
    c.Points[2] = r3.Point4d(7 * 0.4, 5 * 0.4, 2 * 0.4, 0.4)
    return c


def polycurve():
    pc = r3.PolyCurve()
    pc.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(4, 0, 0)))
    pc.Append(cpc([(4, 0, 0), (5, -3, 1), (8, -1, 0), (10, 2, 0)]))
    return pc


def through(n, seed):
    r = random.Random(seed)
    return [(i * 2.0 + r.uniform(-0.7, 0.7), r.uniform(-4, 4), r.uniform(-1, 1)) for i in range(n)]


NURBS = {
    "circle": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "tilted arc": r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 2), r3.Point3d(7, 1, -1)).ToNurbsCurve(),
    "open cubic, 4 points": cpc([(0, 0, 0), (3, 5, 0), (6, -2, 1), (10, 3, 0)]),
    "open cubic, 40 points": cpc([(i * 0.5, math.sin(i * 0.4) * 3, math.cos(i * 0.3)) for i in range(40)]),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "rational cubic": rational(),
    "InterpCrv, 3 points": interp.interpolate_curve(through(3, 1)),
    "InterpCrv, 10 points": interp.interpolate_curve(through(10, 2)),
    "InterpCrv, 24 points": interp.interpolate_curve(through(24, 3)),
    "closed InterpCrv": interp.interpolate_closed_curve(
        [(5 * math.cos(k), 4 * math.sin(k), 0.0) for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)]),
}
OTHERS = {
    "LineCurve": r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 3, 1)),
    "polyline curve": r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0)]),
    "poly curve": polycurve(),
    "degree-1 control curve (a PolylineCurve)": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0)], 1),
}

check(native.sample(NURBS["circle"], 4) is not None, f"the pure-core library samples ({LIBRARY})")

print("\n-- parity: native samples == sample_curve's PointAt, bit for bit --")
for name, curve in NURBS.items():
    results = [(n, native.sample(curve, n), python_samples(curve, n)) for n in (1, 2, 7, 48, 217)]
    differ = [n for n, got, want in results if got != want]
    check(not differ, f"{name}: {len(results)} sample counts, differing at {differ or 'none'}")
for name, curve in OTHERS.items():
    got = bridge.sample_curve_xyz(curve, 12)
    check(native.sample(curve, 12) is None and got == python_samples(curve, 12),
          f"{name} (not a NurbsCurve) keeps sample_curve")

print("\n-- random curves: degrees 1-5, rational, multiple knots, shifted domains --")


def random_curve(r):
    degree = r.randint(1, 5)
    count = r.randint(degree + 1, degree + 9)
    is_rational = r.random() < 0.5
    curve = r3.NurbsCurve(3, is_rational, degree + 1, count)
    for i in range(count):
        x, y, z = (r.uniform(-10, 10) for _ in range(3))
        w = r.uniform(0.2, 4.0) if is_rational else 1.0
        curve.Points[i] = r3.Point4d(x * w, y * w, z * w, w)
    t0 = r.choice((0.0, -3.7, 12.25, r.uniform(-50, 50)))
    t1 = t0 + r.choice((1.0, 10.0, r.uniform(0.01, 80)))
    interior = sorted(r.uniform(t0, t1) for _ in range(count - degree - 1))
    for i in range(1, len(interior)):          # some knots doubled or tripled
        if r.random() < 0.25:
            interior[i] = interior[i - 1]
    knots = [t0] * degree + interior + [t1] * degree
    for i, k in enumerate(knots):
        curve.Knots[i] = k
    return curve


rng = random.Random(20261108)
curves = total = differ = rationals = 0
for _ in range(150):
    curve = random_curve(rng)
    if not curve.IsValid:
        continue
    curves += 1
    rationals += curve.IsRational
    for n in (rng.randint(1, 9), rng.randint(10, 300)):
        got, want = native.sample(curve, n), python_samples(curve, n)
        total += len(want)
        differ += got != want
check(curves > 100 and rationals > 30 and total > 10_000 and differ == 0,
      f"{total} points on {curves} random curves ({rationals} rational): {differ} sample lists differ")

print("\n-- callers take the Rust route; without the library they sample in Python --")
curve = NURBS["InterpCrv, 10 points"]
before = native._native_sample_calls
got = bridge.sample_curve_xyz(curve, 40)
check(native._native_sample_calls == before + 1 and got == python_samples(curve, 40),
      "sample_curve_xyz routes a NurbsCurve through Rust")
args = [(0.0, 0.0, 0.0), (3.0, 2.0, 0.0), (6.0, -1.0, 0.5)]
before = native._native_sample_calls
rows = preview.preview_interpcrv(args, (9.0, 1.0, 0.0), None)
built = bridge.make_interpolated_curve(args + [(9.0, 1.0, 0.0)], degree=3)
check(native._native_sample_calls == before + 1 and rows == [python_samples(built, 72)],
      "the InterpCrv preview samples its curve in Rust, the committed curve's PointAt points")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    before = native._native_sample_calls
    fallback = bridge.sample_curve_xyz(curve, 40)
    check(native._native_sample_calls == before and fallback == got,
          "without the library sample_curve answers, with the same points")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- per-call timing (best of 5) --")


def best(fn, reps):
    out = math.inf
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


for name, n in (("InterpCrv, 3 points", 48), ("InterpCrv, 10 points", 216),
                ("InterpCrv, 24 points", 552), ("circle", 48)):
    curve = NURBS[name]
    rust = best(lambda: bridge.sample_curve_xyz(curve, n), 50)
    python = best(lambda: python_samples(curve, n), 10)
    print(f"  {name}, {n + 1} samples: python {python * 1e6:8.1f} us, rust {rust * 1e6:7.1f} us "
          f"({python / rust:.1f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
