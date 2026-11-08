#!/usr/bin/env python3
"""Curve display tessellation in Rust: the same samples as `bridge._display_samples`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_display_samples.py

Parity is exact: on a `NurbsCurve` the native route returns the parameters and
points `_python_display_samples` returns, bit for bit. Rust evaluates the curve with
the Near snap's port of openNURBS (`DerivativeAt(t, 1)` for probes, `PointAt` for
other vertices) and repeats the Python arithmetic in its order, including CPython's
`x ** 2` (libm `pow`), `math.atan2`, compensated `sum()` and `min`/`max` tie rules.
Other curves keep the Python route.
"""
import importlib.util
import math
import os
import platform
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


native = load("native_display")
interp = load("interpolate")
bridge = load("bridge")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def kinked_circle():
    """A circle with one control point moved: a real C0 corner at a double knot."""
    c = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()
    q = c.Points[2]
    c.Points[2] = r3.Point4d(q.X + 2.0, q.Y + 1.0, q.Z, q.W)
    return c


def zigzag(n):
    return cpc([(i * 0.7, (1.5 if i % 2 else -1.5) * (1 + 0.1 * i), 0.05 * i) for i in range(n)])


def tolerances(curve):
    base = bridge.display_tolerance(curve)
    return (base, base * 0.01, base * 37.0, 1e-6, 0.0)


NURBS = {
    "circle": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "kinked circle": kinked_circle(),
    "tilted arc": r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 2), r3.Point3d(7, 1, -1)).ToNurbsCurve(),
    "straight cubic": cpc([(0, 0, 0), (1, 1, 1), (2, 2, 2), (5, 5, 5)]),
    "open cubic, 4 points": cpc([(0, 0, 0), (3, 5, 0), (6, -2, 1), (10, 3, 0)]),
    "open cubic, 40 points": cpc([(i * 0.5, math.sin(i * 0.4) * 3, math.cos(i * 0.3)) for i in range(40)]),
    "40-point zigzag": zigzag(40),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "degree 5": cpc([(i, math.sin(i) * 4, 0.3 * i) for i in range(9)], 5),
    "InterpCrv, 10 points": interp.interpolate_curve(
        [(i * 2.0, math.sin(i) * 3, 0.0) for i in range(10)]),
    "closed InterpCrv": interp.interpolate_closed_curve(
        [(5 * math.cos(k), 4 * math.sin(k), 0.0) for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)]),
    "huge circle": r3.Circle(r3.Point3d(1e5, -2e5, 3e3), 4e4).ToNurbsCurve(),
}


def compare(curve, tol):
    return native.display_samples(curve, tol) == bridge._python_display_samples(curve, tol)


check(native.available(), f"the pure-core library loads ({LIBRARY})")
# Production routes only on a qualified runtime (Linux/glibc). Parity is MEASURED here on
# whatever runtime runs this suite, so it is also how another platform gets qualified.
QUALIFIED = native.QUALIFIED_RUNTIME
native.QUALIFIED_RUNTIME = True
print(f"  (this runtime: {sys.platform}, {platform.libc_ver()}; qualified for production: {QUALIFIED})")

print("\n-- parity: native (params, points) == _python_display_samples, bit for bit --")
for name, curve in NURBS.items():
    differ = [tol for tol in tolerances(curve) if not compare(curve, tol)]
    got = native.display_samples(curve, bridge.display_tolerance(curve))
    check(not differ and got is not None,
          f"{name}: {len(got[0]) if got else 0} vertices, differing tolerances: {differ or 'none'}")
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(4, 1, 0))
poly = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0)])
check(native.display_samples(line, 0.01) is None and native.display_samples(poly, 0.01) is None,
      "a LineCurve and a PolylineCurve (not NurbsCurves) keep the Python route")

print("\n-- random curves: degrees 1-5, rational, multiple and full knots, shifted domains --")


def random_curve(r):
    degree = r.randint(1, 5)
    count = r.randint(degree + 1, degree + 14)
    rational = r.random() < 0.5
    curve = r3.NurbsCurve(3, rational, degree + 1, count)
    scale = r.choice((0.01, 1.0, 10.0, 1000.0))
    for i in range(count):
        x, y, z = (r.uniform(-10, 10) * scale for _ in range(3))
        w = r.uniform(0.2, 4.0) if rational else 1.0
        curve.Points[i] = r3.Point4d(x * w, y * w, z * w, w)
    t0 = r.choice((0.0, -3.7, 12.25, r.uniform(-50, 50)))
    t1 = t0 + r.choice((1.0, 10.0, r.uniform(0.01, 80)))
    interior = sorted(r.uniform(t0, t1) for _ in range(count - degree - 1))
    i = 1
    while i < len(interior):                 # some knots repeated, up to full multiplicity
        if r.random() < 0.3:
            for j in range(i, min(len(interior), i + r.randint(1, degree))):
                interior[j] = interior[i - 1]
            i += degree
        i += 1
    knots = [t0] * degree + interior + [t1] * degree
    for i, k in enumerate(knots):
        curve.Knots[i] = k
    return curve


rng = random.Random(20261108)
curves = cases = differ = rationals = cornered = 0
for _ in range(500):
    curve = random_curve(rng)
    if not curve.IsValid:
        continue
    curves += 1
    rationals += curve.IsRational
    groups, degree = bridge._knot_groups(curve, curve.Domain.T0, curve.Domain.T1)
    cornered += any(m >= degree for _v, m in groups)
    for tol in tolerances(curve):
        cases += 1
        differ += not compare(curve, tol)
check(curves > 300 and rationals > 100 and cornered > 50 and differ == 0,
      f"{cases} tessellations of {curves} random curves ({rationals} rational, "
      f"{cornered} with corners): {differ} differ")

print("\n-- the display path uses it, and falls back without the library --")
curve = NURBS["open cubic, 40 points"]
tol = bridge.display_tolerance(curve)
want = bridge._python_display_samples(curve, tol)
before = native._native_calls
check(bridge._curve_display_points(curve, tol) == want[1] and native._native_calls == before + 1,
      "_curve_display_points takes the Rust route with the Python points")
before = native._native_calls
check(bridge.curve_display_segments(curve) == len(want[0]) - 1 and native._native_calls == before + 1,
      "curve_display_segments counts the same samples through Rust")
before = native._native_calls
big = zigzag(400)
check(native.display_samples(big, 1e-5) == bridge._python_display_samples(big, 1e-5)
      and native._native_calls == before + 1,
      f"a curve past the first buffer ({len(bridge._python_display_samples(big, 1e-5)[0])} vertices) "
      f"is retried at its size")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    before = native._native_calls
    check(bridge._display_samples(curve, tol) == want and native._native_calls == before,
          "without the library the Python route answers, with the same samples")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- an unqualified runtime keeps the Python route --")
native.QUALIFIED_RUNTIME = False
try:
    before = native._native_calls
    curve = NURBS["open cubic, 40 points"]
    tol = bridge.display_tolerance(curve)
    check(native.display_samples(curve, tol) is None
          and bridge._display_samples(curve, tol) == bridge._python_display_samples(curve, tol)
          and native._native_calls == before,
          "with QUALIFIED_RUNTIME False (Windows, macOS) the display stays in Python")
finally:
    native.QUALIFIED_RUNTIME = True
check(QUALIFIED == (sys.platform.startswith("linux") and platform.libc_ver()[0] == "glibc"),
      f"production routes natively only on Linux/glibc (here: {QUALIFIED})")

print("\n-- timing (best of 3, display tolerance) --")


def best(fn, reps):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


rng = random.Random(3)
batch = [cpc([(i * 2 + rng.random(), rng.uniform(-5, 5), rng.uniform(-1, 1))
              for i in range(rng.randint(4, 20))]) for _ in range(300)]
batch_tols = [bridge.display_tolerance(c) for c in batch]
python = best(lambda: [bridge._python_display_samples(c, t) for c, t in zip(batch, batch_tols)], 1)
rust = best(lambda: [native.display_samples(c, t) for c, t in zip(batch, batch_tols)], 3)
print(f"  300 open cubics of 4-20 points: python {python * 1e3:7.1f} ms, rust {rust * 1e3:6.1f} ms "
      f"({python / rust:.1f}x)")
for name in ("circle", "open cubic, 4 points", "40-point zigzag"):
    c = NURBS[name]
    t = bridge.display_tolerance(c)
    py = best(lambda: bridge._python_display_samples(c, t), 20)
    rs = best(lambda: native.display_samples(c, t), 200)
    print(f"  {name}: python {py * 1e6:8.1f} us, rust {rs * 1e6:7.1f} us ({py / rs:.1f}x)")
check(rust < python, "the Rust route is faster end to end")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
