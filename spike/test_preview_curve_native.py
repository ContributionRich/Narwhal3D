#!/usr/bin/env python3
"""Rust Curve-preview sampling: parity with the Python de Boor sampler, routing and timing.

Run (the library defaults to the gate's release build):
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      python3 spike/test_preview_curve_native.py

`narwhal3d/preview.py` imports only the standard library at load time, so this runs
under the system python3 as well as the bundled runtime.

Tolerance: 1e-9 times the control-point bounding-box diagonal. The Rust sampler
repeats the Python arithmetic operation for operation, so the measured error is 0;
the tolerance only allows for a platform that fuses multiply-adds.
"""
import importlib.util
import math
import os
import sys
import time
import types

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


cplane = load("cplane")
native = load("native_bspline")
preview = load("preview")

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def control(n, seed):
    """A non-planar, irregular control polygon."""
    return [(math.cos(i * 1.3 + seed) * (5 + i), math.sin(i * 0.7 + seed) * 7, 0.4 * i * i - seed)
            for i in range(n)]


def tolerance(pts):
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return 1e-9 * math.dist(lo, hi)


check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: degree 2 and 3, 3 to 40 control points --")
worst, mismatched = 0.0, []
for degree in (2, 3):
    for n in range(3, 41):
        pts = control(n, degree)
        want = preview._sample_control_point_curve(pts, min(degree, n - 1), preview.CURVE_PREVIEW_PER_SPAN)
        got = native.control_point_curve_polyline(pts, degree, preview.CURVE_PREVIEW_PER_SPAN)
        if got is None or len(got) != len(want):
            mismatched.append((degree, n, None if got is None else len(got), len(want)))
            continue
        error = max(abs(a - b) for p, q in zip(got, want) for a, b in zip(p, q))
        worst = max(worst, error)
        if error > tolerance(pts) or got[0] != pts[0] or got[-1] != pts[-1]:
            mismatched.append((degree, n, error))
check(not mismatched, f"native samples match Python within 1e-9 x bbox diagonal (worst {worst:g}) {mismatched[:3]}")
check(worst == 0.0, f"and are bit-identical on this platform (worst {worst:g})")

print("\n-- the degree rule and the control-polygon case --")
pts = control(4, 0.5)
check(len(native.control_point_curve_polyline(pts, 5, 12)) == 13,
      "Degree=5 with four points clamps to a cubic: one span, 13 samples")
check(native.control_point_curve_polyline(pts, 1, 12) is None
      and preview.control_point_curve_polyline(pts, max_degree=1) == [tuple(p) for p in pts],
      "degree 1 stays in Python and is the control polygon")
check(preview.control_point_curve_polyline(pts[:1]) == [], "one point draws nothing")

print("\n-- routing --")
before = native._native_calls
cmd = types.SimpleNamespace(opt={"Degree": "3"})
rows = preview.preview_curve([(0, 0, 0), (4, 6, 0), (9, -2, 1)], (12, 3, 0), cplane.WORLD_TOP, cmd=cmd)
want = preview._sample_control_point_curve(
    [(0, 0, 0), (4, 6, 0), (9, -2, 1), (12, 3, 0)], 3, preview.CURVE_PREVIEW_PER_SPAN)
check(native._native_calls == before + 1 and rows == [want],
      "preview_curve takes its samples from Rust and they equal the Python ones")
saved = os.environ["NARWHAL_PURE_CORE"]
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    before = native._native_calls
    rows = preview.preview_curve([(0, 0, 0), (4, 6, 0), (9, -2, 1)], (12, 3, 0), cplane.WORLD_TOP, cmd=cmd)
    check(native._native_calls == before and rows == [want],
          "without the library the Python sampler gives the same preview")
finally:
    os.environ["NARWHAL_PURE_CORE"] = saved

print("\n-- per-redraw timing (best of 5) --")


def best(fn, reps):
    out = math.inf
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


for n, reps in ((4, 400), (10, 200), (30, 60)):
    pts = control(n, 0.0)
    rust = best(lambda: preview.control_point_curve_polyline(pts), reps)
    python = best(lambda: preview._sample_control_point_curve(pts, 3, preview.CURVE_PREVIEW_PER_SPAN), reps)
    print(f"  [perf] {n} control points: native {rust * 1e6:.1f} us, python {python * 1e6:.1f} us, "
          f"ratio {rust / python:.3f}")
    check(rust / python <= 0.9, f"native preview wins by at least 10% at {n} control points")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
