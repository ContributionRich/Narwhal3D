#!/usr/bin/env python3
"""LineCurve samples without `PointAt`: the same bits, on the platforms measured.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_line_samples.py

`native_nearest.sample` hands a LineCurve's ends and domain to Rust
(native/pure_core/src/line_samples.rs), which repeats openNURBS's `ON_LineCurve::Evaluate`
and `ON_Line::PointAt` (eb92af3) at the parameters `curve_snap_target` and
`bridge.sample_curve` use, `T0 + span * i / n`. Parity is checked on the packed bytes of
every coordinate, so a -0.0 counts. The route needs a platform in
`native_nearest.LINE_QUALIFIED_PLATFORMS` (Linux x86-64, where this suite measures it) AND
the loaded rhino3dm passing the parity check on the witness lines; otherwise lines keep
`PointAt`. Malformed calls go to the export in a child process, so an abort fails a check.
"""
import importlib.util
import json
import math
import os
import platform
import random
import struct
import subprocess
import sys
import time
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
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


for name in ("native_nearest", "osnap"):
    load(name)
native_nearest = sys.modules["narwhal3d.native_nearest"]
bridge = load("bridge")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def point_at_samples(curve, n):
    d = curve.Domain
    t0, span = d.T0, d.T1 - d.T0
    return [(p.X, p.Y, p.Z) for p in (curve.PointAt(t0 + span * i / n) for i in range(n + 1))]


def packed(points):
    return b"".join(struct.pack("<3d", *p) for p in points)


here = (sys.platform, platform.machine())
qualified = (here in native_nearest.LINE_QUALIFIED_PLATFORMS
             and native_nearest.line_capability() == "qualified")
print(f"platform {here}: {'qualified' if qualified else 'NOT qualified (parity skipped)'}")

if qualified:
    print("\n-- the Rust evaluation gives PointAt's bits --")
    rng = random.Random(20261114)
    lines = total = differ = 0
    for _ in range(4000):
        scale = rng.choice((1.0, 1e-9, 1e-3, 1e3, 1e9, 1e15))
        ends = [[rng.uniform(-1, 1) * scale for _ in range(3)] for _ in range(2)]
        for end in ends:                          # shared and signed-zero coordinates
            for k in range(3):
                if rng.random() < 0.1:
                    end[k] = rng.choice((0.0, -0.0))
        if rng.random() < 0.15:
            k = rng.randrange(3)
            ends[1][k] = ends[0][k]
        curve = r3.LineCurve(r3.Point3d(*ends[0]), r3.Point3d(*ends[1]))
        if rng.random() < 0.6:
            t0 = rng.choice((0.0, -0.0, rng.uniform(-1e4, 1e4)))
            curve.Domain = r3.Interval(t0, t0 + rng.choice((1.0, 3.0, rng.uniform(1e-6, 1e6))))
        n = rng.choice((1, 2, 3, 7, 64, 128, 255, 1000))
        got, want = native_nearest.sample(curve, n), point_at_samples(curve, n)
        lines += 1
        total += len(want)
        differ += got is None or packed(got) != packed(want)
    check(differ == 0 and total > 400_000,
          f"{total} points on {lines} lines (scales 1e-9..1e15, -0.0, shared coordinates, "
          f"shifted domains): {differ} lines differ")
    # T0 + span * 3 / 3 rounds one ulp past T1, so s > 1 and (1 - s) * 0.0 is -0.0: the
    # last point keeps the end's -0.0, which `PointAtEnd` (+0.0) would have lost
    neg = r3.LineCurve(r3.Point3d(21746754.257355727, 0.0, 568173071.4154744),
                       r3.Point3d(953038915.070224, -0.0, 468194674.0347103))
    neg.Domain = r3.Interval(0.0, 936643351.9404957)
    check(packed(native_nearest.sample(neg, 3)) == packed(point_at_samples(neg, 3))
          and math.copysign(1.0, native_nearest.sample(neg, 3)[3][1]) == -1.0,
          "a -0.0 end coordinate past T1 by one ulp: PointAt's -0.0, read from curve.Line")

print("\n-- only LineCurve takes the route --")
others = {
    "degree-1 NurbsCurve": r3.NurbsCurve.Create(False, 1, [r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 0)]),
    "PolylineCurve": r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0), r3.Point3d(1, 1, 0)]),
    "circle": r3.Circle(r3.Point3d(0, 0, 0), 2.0).ToNurbsCurve(),
}
before = native_nearest._native_line_calls
for c in others.values():
    native_nearest.sample(c, 16)
check(native_nearest._native_line_calls == before,
      f"{', '.join(others)}: never the line route (their own evaluation)")
line = r3.LineCurve(r3.Point3d(1, 2, 3), r3.Point3d(-4, 5, 0.5))
check(native_nearest.sample(line, 0) is None and native_nearest.sample(line, -3) is None,
      "a sample count below 1: None")

print("\n-- curve_snap_target samples a line with it, the same target --")


def fields(target):
    return (packed(target.points), packed(target.vertices), target.knots, target.center,
            target.closed, target.name)


real = native_nearest._line_samples
calls = {"n": 0}


def counting(curve, n):
    calls["n"] += 1
    got = real(curve, n)
    calls["routed"] = got is not None
    return got


def point_at_target(curve):
    native_nearest._line_samples = lambda curve, n: None
    try:
        return bridge.curve_snap_target(curve, name="l")
    finally:
        native_nearest._line_samples = counting


native_nearest._line_samples = counting
try:
    target = bridge.curve_snap_target(line, name="l")
    check(calls["n"] == 1 and calls.get("routed") == qualified
          and fields(target) == fields(point_at_target(line)),
          f"the line's target equals the PointAt target ({'routed' if qualified else 'not routed'})")
finally:
    native_nearest._line_samples = real

print("\n-- unqualified platforms keep PointAt --")
real_platform, real_machine = sys.platform, platform.machine
try:
    verdicts = {}
    for plat, machine in (("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"),
                          ("darwin", "x86_64"), ("win32", "AMD64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native_nearest._line_state = None
        verdicts[(plat, machine)] = native_nearest.line_capability() == "qualified"
        if plat == "linux" and machine == "aarch64":
            off = (native_nearest.sample(line, 128) is None
                   and fields(bridge.curve_snap_target(line, name="l")) == fields(point_at_target(line)))
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native_nearest._line_state = None
check(off, "on Linux aarch64 samples answers None, even with the export present, and the "
      "target is unchanged")
check(verdicts == {("linux", "x86_64"): True, ("linux", "aarch64"): False,
                   ("darwin", "arm64"): False, ("darwin", "x86_64"): False,
                   ("win32", "AMD64"): False},
      f"the gate qualifies only Linux x86-64: {verdicts}")

print("\n-- an SDK whose PointAt rounds differently keeps PointAt --")
real_point_at = native_nearest._point_at_samples


def one_ulp_off(curve, n):
    """A stand-in for a rhino3dm build that fuses multiply-adds: one sample one ulp off."""
    pts = real_point_at(curve, n)
    x, y, z = pts[n // 2]
    pts[n // 2] = (math.nextafter(x, math.inf), y, z)
    return pts


native_nearest._point_at_samples = one_ulp_off
native_nearest._line_state = None
try:
    text = native_nearest.line_capability()
    check(text.startswith("rhino3dm ") and "differs" in text
          and native_nearest.sample(line, 128) is None
          and fields(bridge.curve_snap_target(line, name="l")) == fields(point_at_target(line)),
          f"one ulp off on one witness sample: {text!r}; the target is unchanged")
finally:
    native_nearest._point_at_samples = real_point_at
    native_nearest._line_state = None
def fused_point_at(curve, n):
    """ON_LineCurve::Evaluate + ON_Line::PointAt as an FMA-contracting build computes them."""
    line, d = curve.Line, curve.Domain
    f = (line.From.X, line.From.Y, line.From.Z)
    g = (line.To.X, line.To.Y, line.To.Z)
    t0, t1 = d.T0, d.T1
    out = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        u = 1.0 if t == t1 else (t - t0) / (t1 - t0)
        if u < 0.5:
            out.append(tuple(math.fma(u, g[k] - f[k], f[k]) for k in range(3)))
        else:
            out.append(tuple(math.fma(1.0 - u, f[k] - g[k], g[k]) for k in range(3)))
    return out


native_nearest._point_at_samples = fused_point_at
native_nearest._line_state = None
try:
    check(native_nearest.line_capability().startswith("rhino3dm ")
          and native_nearest.sample(line, 128) is None,
          "an SDK that fuses multiply-adds (math.fma) fails the witness lines: PointAt stays")
finally:
    native_nearest._point_at_samples = real_point_at
    native_nearest._line_state = None
if qualified:
    start = time.perf_counter()
    native_nearest._line_binding()
    cost = time.perf_counter() - start
    check(native_nearest.line_capability() == "qualified" and cost < 0.1,
          f"the real SDK passes again; the one-time parity check took {cost * 1e3:.1f} ms")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_line_samples
D, Z = c.POINTER(c.c_double), c.c_size_t
fn.argtypes = [D, c.c_double, c.c_double, Z, D, Z]
fn.restype = c.c_int32
line = (c.c_double * 6)(0, 0, 0, 1, 2, 3)
nan = (c.c_double * 6)(0, 0, 0, float("nan"), 2, 3)
out = (c.c_double * 30)(*[-9.0] * 30)
got = {
    "reversed domain": fn(line, 1.0, 0.0, 4, out, 30),
    "empty domain": fn(line, 1.0, 1.0, 4, out, 30),
    "nan domain": fn(line, float("nan"), 1.0, 4, out, 30),
    "nan end": fn(nan, 0.0, 1.0, 4, out, 30),
    "n = 0": fn(line, 0.0, 1.0, 0, out, 30),
    "n = 2**20 + 1": fn(line, 0.0, 1.0, 2**20 + 1, out, 30),
    "n = SIZE_MAX": fn(line, 0.0, 1.0, 2**64 - 1, out, 30),
    "small buffer": fn(line, 0.0, 1.0, 9, out, 29),
    "null line": fn(None, 0.0, 1.0, 4, out, 30),
    "null out": fn(line, 0.0, 1.0, 4, None, 30),
}
print(json.dumps({"statuses": got, "untouched": list(out) == [-9.0] * 30}))
"""
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"]],
                       capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"reversed domain": -2, "empty domain": -2, "nan domain": -2, "nan end": -2,
        "n = 0": -2, "n = 2**20 + 1": -2, "n = SIZE_MAX": -2, "small buffer": -3,
        "null line": -1, "null out": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched"),
      f"{len(want)} malformed calls refused with their statuses, nothing written "
      f"(exit {child.returncode}: {result.get('statuses')})")

if qualified:
    print("\n-- cost of 129 samples (best of 5) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    slow = best(lambda: point_at_samples(line, 128), 50)
    fast = best(lambda: native_nearest.sample(line, 128), 200)
    print(f"  PointAt {slow * 1e6:7.1f} us, Rust {fast * 1e6:6.1f} us ({slow / fast:.1f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
