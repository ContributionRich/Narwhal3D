#!/usr/bin/env python3
"""PolylineCurve samples without `PointAt`: the same bits, on the platforms measured.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_polyline_samples.py

`native_nearest.sample` hands a PolylineCurve's `Encode()` record to Rust
(native/pure_core/src/line_samples.rs), which evaluates it with openNURBS's
`ON_PolylineCurve::Evaluate` (eb92af3, ported in curvex.rs) at the parameters
`curve_snap_target` and `bridge.sample_curve` use, `T0 + span * i / n`. Parity is checked
on the packed bytes of every coordinate, so a -0.0 counts. The route needs a platform in
`native_nearest.POLYLINE_SAMPLES_QUALIFIED_PLATFORMS` (Linux x86-64, where this suite
measures it) AND the loaded rhino3dm passing the parity check on the witness polylines;
otherwise polylines keep `PointAt`. Malformed calls go to the export in a child process,
so an abort fails a check.
"""
import base64
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


for name in ("nurbs_data", "native_nearest", "osnap"):
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


def record(curve):
    return base64.b64decode(curve.Encode()["data"])


P = r3.Point3d
rect = r3.PolylineCurve([P(0, 0, 0), P(3.1, 0, 0), P(3.1, 2.2, 0), P(0, 2.2, 0), P(0, 0, 0)])
here = (sys.platform, platform.machine())
qualified = (here in native_nearest.POLYLINE_SAMPLES_QUALIFIED_PLATFORMS
             and native_nearest.polyline_sample_capability() == "qualified")
print(f"platform {here}: {'qualified' if qualified else 'NOT qualified (parity skipped)'}")

if qualified:
    print("\n-- the Rust evaluation gives PointAt's bits --")
    rng = random.Random(20261121)
    curves = total = differ = 0
    kinds = set()
    while total < 400_000:
        scale = rng.choice((1.0, 1e-9, 1e-3, 1e3, 1e9, 1e15))
        count = rng.choice((2, 3, 4, 5, 8, 17, 40))
        points = [[rng.uniform(-1, 1) * scale for _ in range(3)] for _ in range(count)]
        for p in points:                          # signed zeros, flat and repeated points
            for k in range(3):
                if rng.random() < 0.1:
                    p[k] = rng.choice((0.0, -0.0))
        if rng.random() < 0.2:
            for p in points:
                p[2] = -0.0
            kinds.add("planar -0.0")
        if rng.random() < 0.1 and count > 2:
            i = rng.randrange(count - 1)
            points[i + 1] = list(points[i])
            kinds.add("repeated point")
        if rng.random() < 0.2:
            points[-1] = list(points[0])
            kinds.add("closed")
        curve = r3.PolylineCurve([P(*p) for p in points])
        if rng.random() < 0.6:
            t0 = rng.choice((0.0, -0.0, rng.uniform(-1e4, 1e4)))
            curve.Domain = r3.Interval(t0, t0 + rng.choice((1.0, 3.0, rng.uniform(1e-6, 1e6))))
            kinds.add("shifted domain")
        if rng.random() < 0.25:                   # uneven parameters, as Split leaves them
            d = curve.Domain
            pieces = curve.Split(d.T0 + (d.T1 - d.T0) * rng.uniform(0.2, 0.8))
            if pieces:
                curve = pieces[rng.randrange(len(pieces))]
                kinds.add("split")
        bound = native_nearest.POLYLINE_SAMPLES_POINTS_PER_SAMPLE
        n = rng.choice([m for m in (1, 2, 3, 7, 64, 128, 255) if curve.PointCount <= bound * (m + 1)])
        got, want = native_nearest.sample(curve, n), point_at_samples(curve, n)
        curves += 1
        total += len(want)
        differ += got is None or packed(got) != packed(want)
    check(differ == 0,
          f"{total} points on {curves} polylines (scales 1e-9..1e15, {', '.join(sorted(kinds))}): "
          f"{differ} polylines differ")
    past = native_nearest._polyline_sample_witnesses()[-1]
    d = past.Domain
    check(d.T0 + (d.T1 - d.T0) * 128 / 128 > d.T1
          and packed(native_nearest.sample(past, 128)) == packed(point_at_samples(past, 128)),
          "a last parameter one ulp past T1 extrapolates the last segment, as PointAt does")
    dup = r3.PolylineCurve([P(0.1, 0.2, 0.3), P(0.1, 0.2, 0.3), P(1.7, -2.9, 0.3)])
    check(not dup.IsValid
          and packed(native_nearest.sample(dup, 9)) == packed(point_at_samples(dup, 9)),
          "a polyline with a repeated point (IsValid false) evaluates as PointAt does")

print("\n-- only PolylineCurve takes the route, within its length bound --")
others = {
    "LineCurve": r3.LineCurve(P(0, 0, 0), P(1, 1, 0)),
    "degree-1 NurbsCurve": r3.NurbsCurve.Create(False, 1, [P(0, 0, 0), P(1, 1, 0)]),
    "circle": r3.Circle(P(0, 0, 0), 2.0).ToNurbsCurve(),
}
before = native_nearest._native_polyline_sample_calls
for c in others.values():
    native_nearest.sample(c, 16)
check(native_nearest._native_polyline_sample_calls == before,
      f"{', '.join(others)}: never the polyline route (their own evaluation)")
check(native_nearest.sample(rect, 0) is None and native_nearest.sample(rect, -3) is None,
      "a sample count below 1: None")
bound = native_nearest.POLYLINE_SAMPLES_POINTS_PER_SAMPLE * (8 + 1)
at_bound = r3.PolylineCurve([P(i * 0.25, (i % 5) * 0.1, 0.0) for i in range(bound)])
over = r3.PolylineCurve([P(i * 0.25, (i % 5) * 0.1, 0.0) for i in range(bound + 1)])
before = native_nearest._native_polyline_sample_calls
routed = native_nearest.sample(at_bound, 8)
check((routed is not None) == qualified and native_nearest.sample(over, 8) is None
      and native_nearest._native_polyline_sample_calls - before == int(qualified),
      f"{bound} points for 9 samples routes; {bound + 1} keeps PointAt (its record costs more)")

print("\n-- curve_snap_target samples a polyline with it, the same target --")


def fields(target):
    return (packed(target.points), packed(target.vertices), target.knots, target.center,
            target.closed, target.name)


real = native_nearest._polyline_samples
calls = {"n": 0}


def counting(curve, n):
    calls["n"] += 1
    got = real(curve, n)
    calls["routed"] = got is not None
    return got


def point_at_target(curve):
    native_nearest._polyline_samples = lambda curve, n: None
    try:
        return bridge.curve_snap_target(curve, name="p")
    finally:
        native_nearest._polyline_samples = counting


native_nearest._polyline_samples = counting
try:
    target = bridge.curve_snap_target(rect, name="p")
    check(calls["n"] == 1 and calls.get("routed") == qualified
          and fields(target) == fields(point_at_target(rect)),
          f"a Rectangle's target equals the PointAt target ({'routed' if qualified else 'not routed'})")
    split = rect.Split(1.37)[1]
    calls["n"] = 0
    check(fields(bridge.curve_snap_target(split, name="p")) == fields(point_at_target(split))
          and calls["n"] == 1 and calls.get("routed") == qualified,
          "a split Rectangle (uneven parameters): the same target")
finally:
    native_nearest._polyline_samples = real

print("\n-- unqualified platforms keep PointAt --")
real_platform, real_machine = sys.platform, platform.machine
off = False
try:
    verdicts = {}
    for plat, machine in (("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"),
                          ("darwin", "x86_64"), ("win32", "AMD64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native_nearest._polyline_sample_state = None
        verdicts[(plat, machine)] = native_nearest.polyline_sample_capability() == "qualified"
        if plat == "linux" and machine == "aarch64":
            off = (native_nearest.sample(rect, 128) is None
                   and fields(bridge.curve_snap_target(rect, name="p")) == fields(point_at_target(rect)))
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native_nearest._polyline_sample_state = None
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


def fused_point_at(curve, n):
    """ON_PolylineCurve::Evaluate as an FMA-contracting build computes it, for a polyline
    whose parameters are still the constructor's 0, 1, 2, ... (the witness Rectangle);
    other witnesses answer with the real PointAt."""
    d = curve.Domain
    count = curve.PointCount
    if (d.T0, d.T1) != (0.0, float(count - 1)):
        return real_point_at(curve, n)
    pts = [curve.Point(i) for i in range(count)]
    out = []
    for i in range(n + 1):
        t = d.T0 + (d.T1 - d.T0) * i / n
        k = min(int(t), count - 2)
        s = 1.0 if t == k + 1 else (t - k) / 1.0
        a, b = pts[k], pts[k + 1]
        out.append(tuple(math.fma(1.0 - s, u, s * v) for u, v in ((a.X, b.X), (a.Y, b.Y), (a.Z, b.Z))))
    return out


for label, stand_in in (("one ulp off on one witness sample", one_ulp_off),
                        ("an SDK that fuses multiply-adds (math.fma) on the witness Rectangle",
                         fused_point_at)):
    native_nearest._point_at_samples = stand_in
    native_nearest._polyline_sample_state = None
    try:
        text = native_nearest.polyline_sample_capability()
        check(text.startswith("rhino3dm ") and "differs" in text
              and native_nearest.sample(rect, 128) is None
              and fields(bridge.curve_snap_target(rect, name="p")) == fields(point_at_target(rect)),
              f"{label}: {text!r}; the target is unchanged")
    finally:
        native_nearest._point_at_samples = real_point_at
        native_nearest._polyline_sample_state = None
if qualified:
    sdk_witness_calls = [0]

    def counted_point_at(curve, n):
        sdk_witness_calls[0] += 1
        return real_point_at(curve, n)

    native_nearest._point_at_samples = counted_point_at
    try:
        start = time.perf_counter()
        native_nearest._polyline_sample_binding()
        cost = time.perf_counter() - start
    finally:
        native_nearest._point_at_samples = real_point_at
    witnesses = (len(native_nearest._polyline_sample_witnesses())
                 * len(native_nearest._POLYLINE_SAMPLE_WITNESS_N))
    check(native_nearest.polyline_sample_capability() == "qualified" and cost < 0.1
          and sdk_witness_calls[0] == witnesses,
          f"the real SDK passes again; the one-time parity check took {cost * 1e3:.1f} ms")

print("\n-- finite overflow keeps the actual SDK compatibility route --")
for t0, t1 in ((-1e308, 1e308), (0.0, 1e308)):
    wide = r3.PolylineCurve([P(0, 0, 0), P(2, 4, 8), P(3, 1, 0)])
    wide.Domain = r3.Interval(t0, t1)
    check(wide.Domain.T0 == t0 and wide.Domain.T1 == t1
          and native_nearest.sample(wide, 4) is None
          and packed(bridge.sample_curve_xyz(wide, 4)) == packed(point_at_samples(wide, 4)),
          f"finite domain {t0}, {t1} refuses native arithmetic and preserves SDK bits")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_polyline_samples
D, Z = c.POINTER(c.c_double), c.c_size_t
fn.argtypes = [c.c_void_p, Z, Z, D, Z]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
out = (c.c_double * 30)(*[-9.0] * 30)
shared = (c.c_double * (len(rec) // 8 + 31))()
c.memmove(shared, rec, len(rec))
got = {
    "n = 0": fn(buf, len(rec), 0, out, 30),
    "n = 2**20 + 1": fn(buf, len(rec), 2**20 + 1, out, 30),
    "n = SIZE_MAX": fn(buf, len(rec), 2**64 - 1, out, 30),
    "small buffer": fn(buf, len(rec), 9, out, 29),
    "not a record": fn(b"not a record", 12, 4, out, 30),
    "truncated record": fn(buf, len(rec) - 1, 4, out, 30),
    "empty record": fn(None, 0, 4, out, 30),
    "null record": fn(None, len(rec), 4, out, 30),
    "null out": fn(buf, len(rec), 4, None, 30),
    "out overlapping the record": fn(shared, len(rec), 4, shared, 30),
}
print(json.dumps({"statuses": got, "untouched": list(out) == [-9.0] * 30}))
"""
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record(rect).hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"n = 0": -2, "n = 2**20 + 1": -2, "n = SIZE_MAX": -2, "small buffer": -3,
        "not a record": 1, "truncated record": 1, "empty record": 1, "null record": -1,
        "null out": -1, "out overlapping the record": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched"),
      f"{len(want)} malformed calls refused with their statuses, nothing written "
      f"(exit {child.returncode}: {result.get('statuses')})")

if qualified:
    print("\n-- cost of 129 samples and of a snap target (best of 5) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    slow = best(lambda: point_at_samples(rect, 128), 50)
    fast = best(lambda: native_nearest.sample(rect, 128), 200)
    print(f"  samples: PointAt {slow * 1e6:7.1f} us, Rust {fast * 1e6:6.1f} us ({slow / fast:.1f}x)")
    fast = best(lambda: bridge.curve_snap_target(rect, name="p"), 200)
    native_nearest._polyline_samples = lambda curve, n: None
    try:
        slow = best(lambda: bridge.curve_snap_target(rect, name="p"), 50)
    finally:
        native_nearest._polyline_samples = real
    print(f"  Rectangle snap target: PointAt {slow * 1e6:7.1f} us, Rust {fast * 1e6:6.1f} us "
          f"({slow / fast:.1f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
