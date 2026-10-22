"""Rust/Python parity, safety, smoothness and performance for coil NURBS planning."""
import importlib.util
import math
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "native", "pure_core", "target",
                   "x86_64-unknown-linux-gnu", "release",
                   "libnarwhal_pure_core.so")
if not os.path.isfile(LIB):
    raise SystemExit("pure-core library is not built")
os.environ["NARWHAL_PURE_CORE"] = LIB
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        "narwhal3d." + name, os.path.join(ROOT, "narwhal3d", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["narwhal3d." + name] = mod
    setattr(pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod


native = load("native_coil")
coil = load("coil")
catenary = load("catenary")
checks = []


def check(condition, label):
    checks.append((bool(condition), label))


native._reset_for_tests()
args = ((0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (1.0, 0.0, 0.0),
        2.0, 2.0, 3.0, False, 0.001)
rust = native.plan(*args)
python = coil.python_plan(*args)
check(rust is not None and rust[2:] == python[2:]
      and max(abs(a - b) for a, b in zip(rust[0], python[0])) < 1e-12
      and rust[1] == python[1],
      "native helix plan is structurally identical to the Python fallback")
curve = coil.curve_from_plan(rust)
check(curve.IsValid and curve.Degree == 3 and not curve.IsPolyline()
      and curve.PointAtStart.DistanceTo(curve.PointAtEnd) > 9.9,
      "coil materializes as one real smooth cubic NURBS, not a polyline")
domain = curve.Domain
maximum = 0.0
for index in range(2001):
    fraction = index / 2000.0
    theta = math.tau * 3.0 * fraction
    expected = coil.r3.Point3d(2.0 * math.cos(theta), 2.0 * math.sin(theta),
                               10.0 * fraction)
    actual = curve.PointAt(domain.T0 + (domain.T1 - domain.T0) * fraction)
    maximum = max(maximum, actual.DistanceTo(expected))
check(maximum <= 0.001 and maximum <= rust[3] * 1.02,
      "independent dense sampling confirms the published deviation bound")
minimum_tangent_dot = 1.0
maximum_speed_jump = 0.0
for knot in sorted(set(rust[1]))[1:-1]:
    epsilon = min(1e-8, knot * 0.1, (1.0 - knot) * 0.1)
    left = curve.DerivativeAt(knot - epsilon, 1)[1]
    right = curve.DerivativeAt(knot + epsilon, 1)[1]
    ll = math.sqrt(left.X * left.X + left.Y * left.Y + left.Z * left.Z)
    rl = math.sqrt(right.X * right.X + right.Y * right.Y + right.Z * right.Z)
    minimum_tangent_dot = min(minimum_tangent_dot,
                              (left.X * right.X + left.Y * right.Y + left.Z * right.Z)
                              / (ll * rl))
    maximum_speed_jump = max(maximum_speed_jump, abs(ll - rl) / max(ll, rl))
check(minimum_tangent_dot > 1.0 - 1e-8 and maximum_speed_jump < 1e-6,
      "every internal coil knot is tangent-continuous (C1)")
spiral_args = ((1.0, 2.0, 3.0), (1.0, 2.0, 7.0), (0.0, 1.0, 0.0),
               1.0, 4.0, 2.0, True, 0.002)
rust_spiral = native.plan(*spiral_args)
python_spiral = coil.python_plan(*spiral_args)
check(rust_spiral is not None
      and max(abs(a - b) for a, b in zip(rust_spiral[0], python_spiral[0])) < 1e-11
      and rust_spiral[2:] == python_spiral[2:],
      "native variable-radius reverse spiral matches the complete fallback")
flat = native.plan((0, 0, 0), (0, 0, 5), (1, 0, 0),
                   1, 4, 2, False, 0.01, True)
check(flat is not None and all(abs(flat[0][index]) < 1e-12
                               for index in range(2, len(flat[0]), 3)),
      "native Flat spiral has exactly zero axial rise")
check(native.plan((0, 0, 0), (0, 0, 0), (1, 0, 0), 1, 1, 1, False, 0.01) is None,
      "degenerate axes are rejected without a false native success")


def best(fn, repeat=100):
    elapsed = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(repeat):
            fn()
        elapsed = min(elapsed, time.perf_counter() - start)
    return elapsed


for turns, repeat in ((1.0, 200), (3.0, 100), (20.0, 40)):
    perf_args = ((0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (1.0, 0.0, 0.0),
                 10.0, 20.0, turns, False, 0.001)
    native_time = best(lambda: coil.make_curve(*perf_args, force_native=True), repeat)
    python_time = best(lambda: coil.curve_from_plan(coil.python_plan(*perf_args)), repeat)
    ratio = native_time / python_time
    print("  [perf] coil turns=%g native=%.5fs python=%.5fs ratio=%.3f" %
          (turns, native_time, python_time, ratio))
    check(ratio <= 0.90,
          "native complete planning/materialization wins by 10%% at %g turn(s)" % turns)
loose = native.plan((0, 0, 0), (0, 0, 10), (1, 0, 0),
                    2, 2, 3, False, 0.01)
check(loose is not None and loose[2] == 18 and len(loose[0]) // 3 == 55,
      "ordinary tolerance uses six spans and about eighteen controls per turn")
check(coil._NATIVE_MIN_SPANS == 6,
      "production crossover activates native planning at one complete turn")

cat_args = ((-5.0, 0.0, 0.0), (5.0, 0.0, 0.0),
            (0.0, -1.0, 0.0), (0.0, -2.0, 0.0), 0.001)
rust_cat = native.catenary_plan(*cat_args)
python_cat = catenary.python_plan(*cat_args)
check(rust_cat is not None and rust_cat[2:] == python_cat[2:]
      and max(abs(a-b) for a, b in zip(rust_cat[0], python_cat[0])) < 1e-11,
      "native catenary plan matches the complete Python fallback")
cat_curve = coil.curve_from_plan(rust_cat)
mid = cat_curve.PointAt((cat_curve.Domain.T0 + cat_curve.Domain.T1) / 2)
check(mid.DistanceTo(coil.r3.Point3d(0, -2, 0)) < 1e-9
      and cat_curve.PointAtStart.DistanceTo(coil.r3.Point3d(-5, 0, 0)) < 1e-12
      and cat_curve.PointAtEnd.DistanceTo(coil.r3.Point3d(5, 0, 0)) < 1e-12,
      "catenary NURBS has exact endpoints and midpoint sag")
cat_native_time = best(lambda: catenary.make_curve(*cat_args, force_native=True), 200)
cat_python_time = best(lambda: coil.curve_from_plan(catenary.python_plan(*cat_args)), 200)
cat_ratio = cat_native_time / cat_python_time
print("  [perf] catenary native=%.5fs python=%.5fs ratio=%.3f" %
      (cat_native_time, cat_python_time, cat_ratio))
check(cat_ratio <= 0.90, "native catenary planning/materialization wins by 10%")
check(catenary._NATIVE_MIN_SPANS == 8,
      "production catenary uses the measured-winning native planner")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
