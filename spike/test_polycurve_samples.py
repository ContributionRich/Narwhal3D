#!/usr/bin/env python3
"""PolyCurve samples without `PointAt`: the same bits, on the platforms measured.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_polycurve_samples.py

`native_nearest.sample` hands a PolyCurve's `Encode()` record, which holds every segment's
own record, to Rust (native/pure_core/src/line_samples.rs). Rust evaluates it with
openNURBS's `ON_PolyCurve::Evaluate` (eb92af3): the segment from `ON_NurbsSpanIndex` over
`m_t`, the parameter mapped onto the segment's domain as `b * s0 + a * s1`, then the
segment's own `Evaluate` (LineCurve, PolylineCurve, NurbsCurve or a nested PolyCurve), at the
parameters `curve_snap_target` and `bridge.sample_curve` use, `T0 + span * i / n`. Parity is
checked on the packed bytes of every coordinate, so a -0.0 counts. The route needs a platform
in `native_nearest.POLYCURVE_SAMPLES_QUALIFIED_PLATFORMS` (Linux x86-64, where this suite
measures it) AND the loaded rhino3dm passing the parity check on the witness polycurves;
otherwise polycurves keep `PointAt`. Malformed calls go to the export in a child process,
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


def joined_sample():
    """A line, a polyline and a cubic, end to end: what Join makes of three curves."""
    pc = r3.PolyCurve()
    pc.Append(r3.LineCurve(P(0, 0, 0), P(10, 0, 0)))
    pc.Append(r3.PolylineCurve([P(10, 0, 0), P(15, 3, 0), P(20, 0, 0), P(25, 4, 0)]))
    pc.Append(r3.NurbsCurve.CreateControlPointCurve(
        [P(25, 4, 0), P(30, 8, 0), P(35, 0, 0), P(40, 5, 0)], 3))
    return pc


joined = joined_sample()
here = (sys.platform, platform.machine())
qualified = (here in native_nearest.POLYCURVE_SAMPLES_QUALIFIED_PLATFORMS
             and native_nearest.polycurve_sample_capability() == "qualified")
print(f"platform {here}: {'qualified' if qualified else 'NOT qualified (parity skipped)'}")


def random_segment(rng, start, scale):
    kind = rng.randrange(5)
    if kind == 0:
        curve = r3.LineCurve(start, rand_point(rng, scale))
    elif kind == 1:
        curve = r3.PolylineCurve([start] + [rand_point(rng, scale)
                                            for _ in range(rng.randrange(1, 6))])
    elif kind in (2, 3):
        degree = rng.choice((2, 3, 5))
        curve = r3.NurbsCurve.CreateControlPointCurve(
            [start] + [rand_point(rng, scale) for _ in range(degree + rng.randrange(0, 4))], degree)
    else:
        curve = r3.NurbsCurve(3, True, 3, 3)       # a rational quadratic, as a conic
        for i, a in enumerate((start, rand_point(rng, scale), rand_point(rng, scale))):
            w = rng.uniform(0.3, 2.0) if i == 1 else 1.0
            curve.Points[i] = r3.Point4d(a.X * w, a.Y * w, a.Z * w, w)
        for i, k in enumerate((0.0, 0.0, 1.0, 1.0)):
            curve.Knots[i] = k
    if rng.random() < 0.5:
        t0 = rng.uniform(-50.0, 50.0)
        curve.Domain = r3.Interval(t0, t0 + rng.uniform(1e-3, 30.0))
    return curve


def rand_point(rng, scale):
    return P(*(rng.choice((rng.uniform(-1, 1) * scale, 0.0, -0.0)) if rng.random() < 0.05
               else rng.uniform(-1, 1) * scale for _ in range(3)))


def random_polycurve(rng, kinds, depth=0):
    scale = rng.choice((1.0, 1e-9, 1e-3, 1e3, 1e9, 1e15))
    pc = r3.PolyCurve()
    end = rand_point(rng, scale)
    for _ in range(rng.randrange(1, 7)):
        if depth < 2 and rng.random() < 0.1:
            inner = random_polycurve(rng, kinds, depth + 1)
            if pc.SegmentCount and not inner.SetStartPoint(pc.PointAtEnd):
                continue
            appended = pc.Append(inner)
        else:
            appended = pc.Append(random_segment(rng, end, scale))
        if appended:
            end = pc.PointAtEnd
    if pc.IsNested:
        kinds.add("nested")
    if rng.random() < 0.3:
        pc.Reverse()
        kinds.add("reversed")
    if rng.random() < 0.3:
        t0 = rng.choice((0.0, -0.0, rng.uniform(-1e4, 1e4)))
        pc.Domain = r3.Interval(t0, t0 + rng.choice((1.0, rng.uniform(1e-3, 1e4))))
        kinds.add("shifted domain")
    return pc


if qualified:
    print("\n-- the Rust evaluation gives PointAt's bits --")
    rng = random.Random(20261123)
    curves = total = differ = refused = refused_valid = 0
    kinds = set()
    while total < 400_000:
        curve = random_polycurve(rng, kinds)
        if curve.SegmentCount == 0:
            continue
        if rng.random() < 0.15:                   # uneven parameters, as Split leaves them
            d = curve.Domain
            try:
                pieces = curve.Split(d.T0 + (d.T1 - d.T0) * rng.uniform(0.2, 0.8))
            except TypeError:                     # rhino3dm's Split when it refuses
                pieces = None
            if pieces and type(pieces[0]) is r3.PolyCurve:
                curve = pieces[rng.randrange(len(pieces))]
                kinds.add("split")
        if type(curve) is not r3.PolyCurve:
            continue
        spans = curve.SpanCount
        n = rng.choice([m for m in (1, 3, 7, 64, 128, 255)
                        if native_nearest.POLYCURVE_SAMPLES_PER_SPAN * spans <= m + 1] or [1023])
        got, want = native_nearest.sample(curve, n), point_at_samples(curve, n)
        curves += 1
        total += len(want)
        if got is None:
            # A SetDomain far from the segments' lengths can round m_t intervals to zero;
            # IsValid rejects that, Rust refuses it and sample_curve calls PointAt.
            refused += 1
            refused_valid += curve.IsValid
            differ += packed(bridge.sample_curve_xyz(curve, n)) != packed(want)
        else:
            differ += packed(got) != packed(want)
    check(differ == 0 and refused_valid == 0 and refused < curves // 50,
          f"{total} points on {curves} polycurves of lines, polylines, NURBS and rational "
          f"conics (scales 1e-9..1e15, -0.0, {', '.join(sorted(kinds))}): "
          f"{differ} polycurves differ; the {refused} refused are all invalid and keep PointAt")
    keys = ["a", "b", "c"]
    pieces = [r3.LineCurve(P(0, 0, 0), P(10, 0.3, 0)),
              r3.NurbsCurve.CreateControlPointCurve([P(10, 0.3, 0), P(13, 6, 1), P(17, -2, 0),
                                                     P(21, 1.7, 0)], 3),
              r3.LineCurve(P(21, 1.7, 0), P(0, 0, 0))]
    made = bridge.join_curves(list(zip(keys, pieces)), 1e-3)
    real_join = made[0][1] if made else None
    check(type(real_join) is r3.PolyCurve
          and packed(native_nearest.sample(real_join, 128))
          == packed(point_at_samples(real_join, 128)),
          f"a closed curve made by bridge.join_curves ({type(real_join).__name__}): PointAt's bits")
    trace = load("trace_picture")
    loop = trace.bezier_loop(r3, [(math.cos(k * 0.4) * 3.7, math.sin(k * 0.4) * 2.1, 0.0)
                                  for k in range(13)])
    check(packed(native_nearest.sample(loop, 128)) == packed(point_at_samples(loop, 128)),
          f"a Trace Picture loop of {loop.SegmentCount} cubic Beziers: PointAt's bits")

print("\n-- only PolyCurve takes the route, within its span bound --")
others = {
    "LineCurve": r3.LineCurve(P(0, 0, 0), P(1, 1, 0)),
    "PolylineCurve": r3.PolylineCurve([P(0, 0, 0), P(1, 1, 0), P(2, 0, 0)]),
    "circle": r3.Circle(P(0, 0, 0), 2.0).ToNurbsCurve(),
}
before = native_nearest._native_polycurve_sample_calls
for c in others.values():
    native_nearest.sample(c, 16)
check(native_nearest._native_polycurve_sample_calls == before,
      f"{', '.join(others)}: never the polycurve route (their own evaluation)")
check(native_nearest.sample(joined, 0) is None and native_nearest.sample(joined, -3) is None,
      "a sample count below 1: None")


def chain(spans):
    """A polycurve of `spans` cubic Beziers (one span each)."""
    pc = r3.PolyCurve()
    for i in range(spans):
        pc.Append(r3.NurbsCurve.CreateControlPointCurve(
            [P(3 * i, 0, 0), P(3 * i + 1, 1, 0), P(3 * i + 2, -1, 0), P(3 * i + 3, 0, 0)], 3))
    return pc


bound = 9 // native_nearest.POLYCURVE_SAMPLES_PER_SPAN
at_bound, over = chain(bound), chain(bound + 1)
before = native_nearest._native_polycurve_sample_calls
routed = native_nearest.sample(at_bound, 8)
check(at_bound.SpanCount == bound and (routed is not None) == qualified
      and native_nearest.sample(over, 8) is None
      and native_nearest._native_polycurve_sample_calls - before == int(qualified),
      f"{bound} spans for 9 samples routes; {bound + 1} keeps PointAt (its record costs more)")

print("\n-- curve_snap_target samples a polycurve with it, the same target --")


def fields(target):
    return (packed(target.points), packed(target.vertices), target.knots, target.center,
            target.closed, target.name)


real = native_nearest._polycurve_samples
calls = {"n": 0}


def counting(curve, n):
    calls["n"] += 1
    got = real(curve, n)
    calls["routed"] = got is not None
    return got


def point_at_target(curve):
    native_nearest._polycurve_samples = lambda curve, n: None
    try:
        return bridge.curve_snap_target(curve, name="p")
    finally:
        native_nearest._polycurve_samples = counting


native_nearest._polycurve_samples = counting
try:
    target = bridge.curve_snap_target(joined, name="p")
    check(calls["n"] == 1 and calls.get("routed") == qualified
          and fields(target) == fields(point_at_target(joined)),
          f"a joined curve's target equals the PointAt target "
          f"({'routed' if qualified else 'not routed'})")
    split = joined.Split(13.7)[1]
    calls["n"] = 0
    check(type(split) is r3.PolyCurve
          and fields(bridge.curve_snap_target(split, name="p")) == fields(point_at_target(split))
          and calls["n"] == 1 and calls.get("routed") == qualified,
          "a split joined curve (trimmed first segment): the same target")
finally:
    native_nearest._polycurve_samples = real

print("\n-- unqualified platforms keep PointAt --")
real_platform, real_machine = sys.platform, platform.machine
off = False
try:
    verdicts = {}
    for plat, machine in (("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"),
                          ("darwin", "x86_64"), ("win32", "AMD64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native_nearest._polycurve_sample_state = None
        verdicts[(plat, machine)] = native_nearest.polycurve_sample_capability() == "qualified"
        if plat == "linux" and machine == "aarch64":
            off = (native_nearest.sample(joined, 128) is None
                   and fields(bridge.curve_snap_target(joined, name="p"))
                   == fields(point_at_target(joined)))
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native_nearest._polycurve_sample_state = None
check(off, "on Linux aarch64 samples answers None, even with the export present, and the "
      "target is unchanged")
check(verdicts == {("linux", "x86_64"): True, ("linux", "aarch64"): False,
                   ("darwin", "arm64"): False, ("darwin", "x86_64"): False,
                   ("win32", "AMD64"): False},
      f"the gate qualifies only Linux x86-64: {verdicts}")

print("\n-- an SDK whose PointAt rounds differently keeps PointAt --")
real_point_at = native_nearest._point_at_samples


def one_ulp_off(curve, n):
    """A stand-in for a rhino3dm build that rounds one sample differently."""
    pts = real_point_at(curve, n)
    x, y, z = pts[n // 2]
    pts[n // 2] = (math.nextafter(x, math.inf), y, z)
    return pts


def fused_point_at(curve, n):
    """ON_PolyCurve::Evaluate as a build that fuses the parameter map computes it,
    `fma(b, s0, a * s1)`, on a polycurve that nests nothing; the segments' own PointAt is
    the real one, so only the map differs."""
    if curve.IsNested:
        return real_point_at(curve, n)
    d = curve.Domain
    count = curve.SegmentCount
    ts = [curve.SegmentDomain(i).T0 for i in range(count)] + [d.T1]
    out = []
    for k in range(n + 1):
        t = d.T0 + (d.T1 - d.T0) * k / n
        i = max(0, min(count - 1, sum(1 for x in ts[1:-1] if x <= t)))
        seg = curve.SegmentCurve(i)
        sd = seg.Domain
        s0, s1, t0, t1 = sd.T0, sd.T1, ts[i], ts[i + 1]
        if s0 == t0 and s1 == t1:
            s = t
        else:
            a, b = (t - t0) / (t1 - t0), (t1 - t) / (t1 - t0)
            if b == 0.0:
                a = 1.0
            elif b == 1.0:
                a = 0.0
            elif a == 0.0:
                b = 1.0
            elif a == 1.0:
                b = 0.0
            s = math.fma(b, s0, a * s1)
        p = seg.PointAt(s)
        out.append((p.X, p.Y, p.Z))
    return out


witnesses = native_nearest._polycurve_sample_witnesses()
fused_bits = packed(fused_point_at(witnesses[0], 128))
real_bits = packed(real_point_at(witnesses[0], 128))
moved = sum(fused_bits[i:i + 24] != real_bits[i:i + 24] for i in range(0, len(real_bits), 24))
check(moved > 0, f"the fused model moves {moved} of the joined witness's 129 samples (the "
      "Trace Picture witness's segments map onto [0, 1], which no fused map can round)")
for label, stand_in in (("one ulp off on one witness sample", one_ulp_off),
                        ("an SDK that fuses the parameter map (math.fma)", fused_point_at)):
    native_nearest._point_at_samples = stand_in
    native_nearest._polycurve_sample_state = None
    try:
        text = native_nearest.polycurve_sample_capability()
        check(text.startswith("rhino3dm ") and "differs" in text
              and native_nearest.sample(joined, 128) is None
              and fields(bridge.curve_snap_target(joined, name="p"))
              == fields(point_at_target(joined)),
              f"{label}: {text!r}; the target is unchanged")
    finally:
        native_nearest._point_at_samples = real_point_at
        native_nearest._polycurve_sample_state = None
if qualified:
    sdk_witness_calls = [0]

    def counted_point_at(curve, n):
        sdk_witness_calls[0] += 1
        return real_point_at(curve, n)

    native_nearest._point_at_samples = counted_point_at
    try:
        start = time.perf_counter()
        native_nearest._polycurve_sample_binding()
        cost = time.perf_counter() - start
    finally:
        native_nearest._point_at_samples = real_point_at
    expected = len(witnesses) * len(native_nearest._POLYCURVE_SAMPLE_WITNESS_N)
    check(native_nearest.polycurve_sample_capability() == "qualified" and cost < 0.1
          and sdk_witness_calls[0] == expected,
          f"the real SDK passes again; the one-time parity check took {cost * 1e3:.1f} ms")

print("\n-- finite overflow keeps the actual SDK compatibility route --")
for t0, t1 in ((-1e308, 1e308), (0.0, 1e308)):
    wide = joined_sample()
    wide.Domain = r3.Interval(t0, t1)
    check(wide.Domain.T0 == t0 and wide.Domain.T1 == t1
          and native_nearest.sample(wide, 4) is None
          and packed(bridge.sample_curve_xyz(wide, 4)) == packed(point_at_samples(wide, 4)),
          f"finite domain {t0}, {t1} refuses native arithmetic and preserves SDK bits")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_polycurve_samples
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
                        record(joined).hex()], capture_output=True, text=True, timeout=60)
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

    slow = best(lambda: point_at_samples(joined, 128), 50)
    fast = best(lambda: native_nearest.sample(joined, 128), 200)
    print(f"  samples: PointAt {slow * 1e6:7.1f} us, Rust {fast * 1e6:6.1f} us ({slow / fast:.1f}x)")
    fast = best(lambda: bridge.curve_snap_target(joined, name="p"), 200)
    native_nearest._polycurve_samples = lambda curve, n: None
    try:
        slow = best(lambda: bridge.curve_snap_target(joined, name="p"), 50)
    finally:
        native_nearest._polycurve_samples = real
    print(f"  joined curve snap target: PointAt {slow * 1e6:7.1f} us, Rust {fast * 1e6:6.1f} us "
          f"({slow / fast:.1f}x)")
    for spans in (16, 64):
        pc = chain(spans)
        slow = best(lambda: point_at_samples(pc, 128), 20)
        fast = best(lambda: native_nearest.sample(pc, 128), 50)
        print(f"  {spans} cubic segments, 129 samples: PointAt {slow * 1e6:7.1f} us, "
              f"Rust {fast * 1e6:6.1f} us ({slow / fast:.1f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
