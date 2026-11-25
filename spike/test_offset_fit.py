#!/usr/bin/env python3
"""Free-form offsets fitted in Rust: the same fit, bit for bit, on the platforms measured.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_offset_fit.py

`offset._compact_offset` hands a free-form `NurbsCurve`'s `Encode()` record to Rust
(native/pure_core/src/offset.rs and offset_boundary.rs, through `native_nearest.offset_fit`).
Rust runs the Python route's smooth interpolate-and-check loop, and its boundary-preserving fit
for a C0 corner, a cusp or a kinked closed seam, with openNURBS's Evaluate, EvTangent,
EvCurvature, Trim, line-line intersection, SetDomain, IsValid and IsClosed (eb92af3) ported.
Parity is checked on the packed bytes of every control point, knot and deviation against
`offset._python_compact_offset`, and on its refusals. Rust leaves a degenerate tangent to the
Python route, so those offsets keep the Python route's results too. The route needs a platform in
`native_nearest.OFFSET_QUALIFIED_PLATFORMS` (Linux x86-64, where this suite measures it) AND
the loaded rhino3dm passing the parity check on the witness curves. Malformed calls go to the
export in a child process, so an abort fails a check.
"""
import base64
import importlib.util
import json
import math
import os
import platform
import random
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


for name in ("cplane", "nurbs_data", "native_nearest", "offset"):
    load(name)
native = sys.modules["narwhal3d.native_nearest"]
off = sys.modules["narwhal3d.offset"]
bridge = load("bridge")
checks = []
Z = (0.0, 0.0, 1.0)


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def bits(result):
    if result is None:
        return None
    if result.curve is None:
        return ("refused", result.warning)
    return native._offset_bits(result.curve, result.max_deviation)


def routed(curve, distance, n, tolerance):
    """(Rust-routed result, whether Rust decided it)."""
    before = native._native_offset_calls
    result = off._compact_offset(curve, distance, n, tolerance)
    return result, native._native_offset_calls == before + 1


here = (platform.system().lower(), platform.machine())
qualified = (sys.platform, platform.machine()) in native.OFFSET_QUALIFIED_PLATFORMS

print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.offset_capability(off._python_compact_offset)
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")


def random_curve(rng, kind):
    k = rng.randint(5, 14)
    if kind == "closed":
        return r3.NurbsCurve.Create(True, 3, [
            r3.Point3d(math.cos(2 * math.pi * i / k) * rng.uniform(8, 12),
                       math.sin(2 * math.pi * i / k) * rng.uniform(8, 12), 0.0)
            for i in range(k)])
    z = (lambda: rng.uniform(-1, 1)) if kind == "tilted" else (lambda: 0.0)
    curve = r3.NurbsCurve.CreateControlPointCurve(
        [r3.Point3d(i * rng.uniform(5, 12), rng.uniform(-6, 6), z()) for i in range(k)],
        2 if kind == "degree 2" else 3)
    if kind == "rational":
        for i in range(1, len(curve.Points) - 1):
            p, w = curve.Points[i], rng.uniform(0.5, 2.0)
            curve.Points[i] = r3.Point4d(p.X * w, p.Y * w, p.Z * w, w)
    return curve


print("\n-- random smooth curves: Rust's fit or no-fit is the Python route's, bit for bit --")
rng = random.Random(20261124)
KINDS = ("open", "closed", "rational", "degree 2", "tilted")
mismatches, decided, left, per_kind = [], 0, 0, {kind: 0 for kind in KINDS}
rust_s = python_s = 0.0
for trial in range(100):
    kind = KINDS[trial % len(KINDS)]
    curve = random_curve(rng, kind)
    if rng.random() < 0.5:
        curve.Domain = r3.Interval(rng.uniform(-50, 0), rng.uniform(1, 80))
    distance = rng.choice((1.0, -1.0, 0.37, 2.5, -4.2)) * rng.uniform(0.2, 1.5)
    n = Z if kind != "tilted" else off._unit((rng.uniform(-.1, .1), rng.uniform(-.1, .1), 1.0))
    tolerance = rng.choice((1e-2, 1e-3, 1e-4))
    t = time.perf_counter()
    got, by_rust = routed(curve, distance, n, tolerance)
    rust_s += time.perf_counter() - t
    t = time.perf_counter()
    want = off._python_compact_offset(curve, distance, n, tolerance)
    python_s += time.perf_counter() - t
    if bits(got) != bits(want):
        mismatches.append((trial, kind))
    if by_rust:
        decided += 1
        per_kind[kind] += 1
    else:
        left += 1
check(not mismatches, f"100 random curves give the Python route's bits (mismatches: {mismatches})")
check(not qualified or (all(per_kind.values()) and left == 0),
      f"Rust decided {decided} (per kind {per_kind}); {left} went to Python")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- random curves with boundaries: Rust's fit or refusal is the Python route's, bit for bit --")
COVERED = {"fill": 0, "trim": 0, "cusp": 0}
real_line_piece, real_inner_trim = off._line_piece, off._inner_trim_param
real_cusps = off._cusp_parameters


def counted_line_piece(a, b):
    COVERED["fill"] += 1
    return real_line_piece(a, b)


def counted_inner_trim(*args):
    t = real_inner_trim(*args)
    COVERED["trim"] += t is not None
    return t


def counted_cusps(*args):
    found = real_cusps(*args)
    COVERED["cusp"] += len(found)
    return found


def kinked(rng, spread, rational=False):
    """A cubic of 2-4 pieces joined at triple knots, each turning a little at its corner;
    a rational one weights the control points between the corners."""
    pieces = rng.randint(2, 4)
    pts = [r3.Point3d(i * rng.uniform(3, 4), rng.uniform(-spread, spread), 0.0)
           for i in range(3 * pieces + 1)]
    curve = r3.NurbsCurve(3, rational, 4, len(pts))
    for i, p in enumerate(pts):
        w = rng.uniform(0.7, 1.4) if rational and i % 3 else 1.0
        curve.Points[i] = r3.Point4d(p.X * w, p.Y * w, p.Z * w, w)
    for i, k in enumerate(float(j // 3) for j in range(3, 3 * pieces + 6)):
        curve.Knots[i] = k
    return curve


def boundary_curve(rng, kind):
    if kind in ("corner", "wide", "rational corner"):
        curve = kinked(rng, 0.45 if kind == "wide" else 0.15, kind == "rational corner")
        return curve, rng.choice((1.0, -1.0)) * rng.uniform(0.3, 1.5)
    if kind == "cusp":
        return (r3.NurbsCurve.CreateControlPointCurve(
            [r3.Point3d(i * 10.0, math.sin(i * rng.uniform(0.5, 0.9)) * rng.uniform(5, 9), 0.0)
             for i in range(rng.randint(6, 12))], 3),
            rng.choice((1.0, -1.0)) * rng.uniform(5, 40))
    k = rng.randint(5, 9)
    pts = [r3.Point3d(math.cos(2 * math.pi * i / k) * rng.uniform(8, 12),
                      math.sin(2 * math.pi * i / k) * rng.uniform(8, 12), 0.0) for i in range(k)]
    return (r3.NurbsCurve.CreateControlPointCurve(pts + pts[:1], 3),
            rng.choice((1.0, -1.0)) * rng.uniform(0.3, 2.0))


rng = random.Random(20261125)
BOUNDARY_KINDS = ("corner", "wide", "rational corner", "cusp", "kinked seam")
mismatches, outcomes = [], {}
rust_s = python_s = 0.0
off._line_piece, off._inner_trim_param = counted_line_piece, counted_inner_trim
off._cusp_parameters = counted_cusps
try:
    for trial in range(150):
        kind = BOUNDARY_KINDS[trial % len(BOUNDARY_KINDS)]
        curve, distance = boundary_curve(rng, kind)
        if rng.random() < 0.5:
            curve.Domain = r3.Interval(rng.uniform(-50, 0), rng.uniform(1, 80))
        tolerance = rng.choice((1e-1, 5e-2, 1e-2, 1e-3))
        t = time.perf_counter()
        got, by_rust = routed(curve, distance, Z, tolerance)
        rust_s += time.perf_counter() - t
        t = time.perf_counter()
        want = off._python_compact_offset(curve, distance, Z, tolerance)
        python_s += time.perf_counter() - t
        if bits(got) != bits(want):
            mismatches.append((trial, kind))
        outcome = ("Rust " if by_rust else "Python ") + (
            "none" if want is None else "refused" if want.curve is None else "fit")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
finally:
    off._line_piece, off._inner_trim_param = real_line_piece, real_inner_trim
    off._cusp_parameters = real_cusps
check(not mismatches, f"150 random curves give the Python route's bits (mismatches: {mismatches})")
check(not qualified or (outcomes.get("Rust fit", 0) >= 50 and outcomes.get("Rust refused", 0) >= 30
                        and sum(v for k, v in outcomes.items() if k.startswith("Python")) <= 3),
      f"Rust decided the fits and the refusals: {outcomes}")
check(all(v >= 20 for v in COVERED.values()),
      f"the Python reference took every corner branch: {COVERED} (straight fills, inner trims, cusps)")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- fixtures --")
smooth = r3.NurbsCurve.Create(True, 3, [
    r3.Point3d((4 + 0.3 * math.cos(3 * a)) * math.cos(a), (2 + 0.15 * math.sin(2 * a)) * math.sin(a), 0)
    for a in (2 * math.pi * i / 14 for i in range(14))])
for distance in (.25, -.25):
    got, by_rust = routed(smooth, distance, Z, 1e-3)
    check(bits(got) == bits(off._python_compact_offset(smooth, distance, Z, 1e-3))
          and got.curve.IsClosed and (by_rust or not qualified),
          f"the compact suite's closed cubic at {distance}: a closed fit, the Python route's bits")
wave = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(i * 10.0, math.sin(i * 0.6) * 8.0, 0.0) for i in range(12)], 3)
got, by_rust = routed(wave, 1.0, Z, 1e-300)
check(got is None and off._python_compact_offset(wave, 1.0, Z, 1e-300) is None
      and (by_rust or not qualified), "an unreachable tolerance is no fit, decided in Rust")
corner = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(3, 2, 0), r3.Point3d(6, 0, 0), r3.Point3d(6, 0, 0),
     r3.Point3d(6, 0, 0), r3.Point3d(9, 3, 0), r3.Point3d(12, 1, 0)], 3)
cusped = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(i * 10.0, math.sin(i * 0.6) * 8.0, 0.0) for i in range(12)], 3)
stalled = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 0), r3.Point3d(4, 3, 0), r3.Point3d(9, -1, 0),
     r3.Point3d(13, 2, 0)], 3)
for label, curve, distance in (("a doubled control point (a stalled tangent)", corner, 0.3),
                               ("a zero first derivative at the start", stalled, 0.4)):
    got, by_rust = routed(curve, distance, Z, 1e-3)
    check(not by_rust and bits(got) == bits(off._python_compact_offset(curve, distance, Z, 1e-3)),
          f"{label} goes to the Python route, with its result")
got, by_rust = routed(cusped, 200.0, Z, 1e-3)
check((by_rust or not qualified) and bits(got) == bits(off._python_compact_offset(cusped, 200.0, Z, 1e-3)),
      "an offset past the radius of curvature (cusps) is decided in Rust, with the Python route's result")
square = r3.NurbsCurve(3, False, 4, 7)
for i, (x, y) in enumerate(((0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2), (3, 3))):
    square.Points[i] = r3.Point4d(x, y, 0.0, 1.0)
for i, k in enumerate((0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0)):
    square.Knots[i] = k
got, by_rust = routed(square, 0.5, Z, 1e-3)
want = off._python_compact_offset(square, 0.5, Z, 1e-3)
check((by_rust or not qualified) and got.curve is None and want.curve is None
      and got.warning == want.warning == off._BOUNDARY_REFUSAL,
      "a right-angle corner is refused in Rust with the Python route's warning")
zigzag = r3.NurbsCurve(3, False, 4, 181)
at, heading = (0.0, 0.0), 0.0
zigzag.Points[0] = r3.Point4d(0.0, 0.0, 0.0, 1.0)
for piece in range(60):
    heading += math.radians(0.4 if piece % 2 == 0 else -0.4)
    for j in range(3):
        at = (at[0] + math.cos(heading), at[1] + math.sin(heading))
        zigzag.Points[3 * piece + j + 1] = r3.Point4d(at[0], at[1], 0.0, 1.0)
for i in range(183):
    zigzag.Knots[i] = float((i + 3) // 3 - 1)
got, by_rust = routed(zigzag, 1.0, Z, 0.05)
check((by_rust or not qualified) and len(got.curve.Points) > native.OFFSET_MAX_FIT_CVS
      and bits(got) == bits(off._python_compact_offset(zigzag, 1.0, Z, 0.05)),
      f"a fit larger than the first buffers ({len(got.curve.Points)} controls) is fetched again "
      "at its size")
check(off._compact_offset(r3.NurbsCurve.CreateControlPointCurve(
          [r3.Point3d(0, 0, 0), r3.Point3d(2, 3, 0), r3.Point3d(5, 0, 0)], 1), 1.0, Z, 1e-3) is None,
      "a degree-1 curve is not a compact offset, as before")

print("\n-- bridge.offset_curve: the Offset command's result is unchanged --")
real = native.offset_fit
for curve in (wave, smooth):
    for distance in (0.5, -1.5):
        a = bridge.offset_curve(curve, distance, tolerance=1e-3)
        native.offset_fit = lambda *args: None
        try:
            b = bridge.offset_curve(curve, distance, tolerance=1e-3)
        finally:
            native.offset_fit = real
        check(bits(a) == bits(b) and a.warning == b.warning,
              f"{'closed' if curve.IsClosed else 'open'} curve at {distance}: "
              f"{len(a.curve.Points)} controls, deviation {a.max_deviation:.2e}")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_offset_fit
D, Z = c.POINTER(c.c_double), c.c_size_t
fn.argtypes = [c.c_void_p, Z, c.c_double, c.c_void_p, c.c_double, c.c_void_p, Z, c.c_void_p, Z,
               c.c_void_p, c.c_void_p]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
cvs, knots = (c.c_double * 375)(*[-9.0] * 375), (c.c_double * 127)(*[-9.0] * 127)
counts, dev = (c.c_uint64 * 2)(7, 7), c.c_double(-9.0)
n, nan = (c.c_double * 3)(0.0, 0.0, 1.0), (c.c_double * 3)(float("nan"), 0.0, 1.0)
shared = (c.c_double * 600)()
c.memmove(shared, rec, min(len(rec), 4800))
cd, kd, sd, dd = (c.cast(x, c.c_void_p) for x in (cvs, knots, counts, c.pointer(dev)))
odd = c.c_void_p(c.addressof(shared) + 1)


def call(record=buf, size=len(rec), distance=0.5, normal=n, tolerance=0.01, out_cvs=cd,
         cv_capacity=375, out_knots=kd, knot_capacity=127, out_counts=sd, out_dev=dd):
    return fn(record, size, distance, normal, tolerance, out_cvs, cv_capacity, out_knots,
              knot_capacity, out_counts, out_dev)


got = {
    "zero distance": call(distance=0.0),
    "infinite distance": call(distance=float("inf")),
    "NaN tolerance": call(tolerance=float("nan")),
    "NaN normal": call(normal=nan),
    "small control buffer": call(cv_capacity=374),
    "small knot buffer": call(knot_capacity=126),
    "not a record": call(record=b"not a record", size=12),
    "truncated record": call(size=len(rec) - 1),
    "empty record": call(record=None, size=0),
    "null record": call(record=None),
    "null normal": call(normal=None),
    "null controls": call(out_cvs=None),
    "null counts": call(out_counts=None),
    "null deviation": call(out_dev=None),
    "misaligned controls": call(out_cvs=odd, cv_capacity=375),
    "controls overlapping the record": call(record=shared, out_cvs=shared, cv_capacity=375),
    "knots overlapping the controls": call(out_knots=c.c_void_p(c.addressof(cvs) + 8 * 300)),
}
untouched = (list(cvs) == [-9.0] * 375 and list(knots) == [-9.0] * 127
             and list(counts) == [7, 7] and dev.value == -9.0)
print(json.dumps({"statuses": got, "untouched": untouched}))
"""
record = base64.b64decode(wave.Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"zero distance": -2, "infinite distance": -2, "NaN tolerance": -2, "NaN normal": -2,
        "small control buffer": -3, "small knot buffer": -3, "not a record": 1,
        "truncated record": 1, "empty record": 1, "null record": -1, "null normal": -1,
        "null controls": -1, "null counts": -1, "null deviation": -1,
        "misaligned controls": -1, "controls overlapping the record": -1,
        "knots overlapping the controls": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched"),
      f"{len(want)} malformed calls refused with their statuses, nothing written "
      f"(exit {child.returncode}: {result.get('statuses')} {child.stderr[-300:]})")

if qualified:
    print("\n-- cost of one free-form offset (best of 3) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    witnesses = native._offset_witnesses()
    for label, curve, distance, tolerance in (("12-point open cubic, tolerance 1e-2", wave, 1.0, 1e-2),
                                              ("12-point open cubic, tolerance 1e-3", wave, 1.0, 1e-3),
                                              ("14-point closed cubic, tolerance 1e-3", smooth, .25, 1e-3),
                                              ("two C0 corners, a trim and a fill", *witnesses[2][:2], 0.05),
                                              ("a wave through two cusps", *witnesses[3][:2], 0.05)):
        slow = best(lambda: off._python_compact_offset(curve, distance, Z, tolerance), 1)
        fast = best(lambda: off._compact_offset(curve, distance, Z, tolerance), 10)
        print(f"  {label}: Python {slow * 1e3:6.1f} ms, Rust {fast * 1e3:5.2f} ms ({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
