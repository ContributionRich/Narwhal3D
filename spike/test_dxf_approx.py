#!/usr/bin/env python3
"""DXF curve approximation in Rust: the same points, bit for bit.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_dxf_approx.py

Export writes a curve that is not a line, a polyline or a horizontal circle or arc to DXF as a
POLYLINE from `export_commands._approx_points`: the chord of a parameter interval is checked
at its quarter, half and three-quarter points, and the interval halved while they fall farther
than the tolerance, to a depth of 18. Rust (narwhal_pure_core_dxf_approx_points,
native/pure_core/src/dxf_approx.rs) runs it on a NurbsCurve's Encode() record with
`nearest::Nurbs` as PointAt. The route needs a platform in
`native_nearest.DXF_APPROX_QUALIFIED_PLATFORMS` AND the loaded rhino3dm passing the parity
check against `export_commands._python_approx_points` on its witness curves. Malformed calls go
to the export in a child process.
"""
import base64
import hashlib
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
from unittest.mock import patch

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


for name in ("nurbs_data", "native_nearest", "export_commands"):
    load(name)
native = sys.modules["narwhal3d.native_nearest"]
export = sys.modules["narwhal3d.export_commands"]
reference = export._python_approx_points
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def bits(points):
    return struct.pack(f"<{3 * len(points)}d", *[x for p in points for x in p])


qualified = (sys.platform, platform.machine()) in native.DXF_APPROX_QUALIFIED_PLATFORMS


def routed(curve, tol, t0, t1):
    before = native._native_dxf_approx_calls
    got = export._approx_points(curve, tol, t0, t1)
    return got, native._native_dxf_approx_calls == before + 1


def random_curve(rng, kind):
    if kind == "circle":
        return r3.Circle(r3.Point3d(rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-2, 2)),
                         rng.uniform(0.1, 50)).ToNurbsCurve()
    degree = {"cubic": 3, "quadratic": 2, "rational": 2, "linear": 1,
              "high": rng.randint(4, 7)}[kind]
    n = rng.randint(degree + 1, degree + 15)
    rational = kind == "rational"
    curve = r3.NurbsCurve(3, rational, degree + 1, n)
    for i in range(n):
        w = rng.uniform(0.4, 2.0) if rational else 1.0
        curve.Points[i] = r3.Point4d(i * rng.uniform(0.2, 3) * w, rng.uniform(-4, 4) * w,
                                     rng.uniform(-1, 1) * w, w)
    a, b = rng.uniform(-9, 0), rng.uniform(1, 9)
    inner = sorted(rng.uniform(0.01, 0.99) for _ in range(n - degree - 1))
    for i, k in enumerate([0.0] * degree + inner + [1.0] * degree):
        curve.Knots[i] = a + (b - a) * k
    return curve


print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.dxf_approx_capability(reference)
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")

print("\n-- random curves: Rust's points are the Python route's, bit for bit --")
rng = random.Random(20261125)
KINDS = ("cubic", "quadratic", "rational", "circle", "linear", "high")
mismatches, by_rust, sizes = [], 0, []
rust_s = python_s = 0.0
for trial in range(120):
    curve = random_curve(rng, KINDS[trial % len(KINDS)])
    d = curve.Domain
    tol = (0.1, 0.01, 1e-3, 1e-4)[trial % 4]
    t0, t1 = (d.T0, d.T1) if trial % 5 else sorted(rng.uniform(d.T0, d.T1) for _ in range(2))
    t = time.perf_counter()
    got, used = routed(curve, tol, t0, t1)
    rust_s += time.perf_counter() - t
    t = time.perf_counter()
    want = reference(curve, tol, t0, t1)
    python_s += time.perf_counter() - t
    if bits(got) != bits(want):
        mismatches.append(trial)
    by_rust += used
    sizes.append(len(want))
check(not mismatches, f"120 curves (cubic, quadratic, rational, circles, degree 1 and 4-7, "
      f"whole domains and inner intervals, tolerances 0.1 to 1e-4; {min(sizes)}-{max(sizes)} "
      f"points) give the Python route's bits (mismatches: {mismatches})")
check(not qualified or by_rust == 120, f"Rust approximated every one ({by_rust} of 120)")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- a second call with more room, and the depth limit --")
big = r3.Circle(r3.Point3d(3.1, -2.7, 0.4), 20.0).ToNurbsCurve()
d = big.Domain
got, used = routed(big, 1e-6, d.T0, d.T1)
want = reference(big, 1e-6, d.T0, d.T1)
check(len(want) > 4096 and bits(got) == bits(want) and (used or not qualified),
      f"a radius-20 circle at 1e-6: {len(want)} points (over the first call's 4096) through "
      f"Rust, the Python route's bits")
wiggle = random_curve(random.Random(7), "high")
d = wiggle.Domain
got, used = routed(wiggle, -1.0, d.T0, d.T1)
ends = [(p.X, p.Y, p.Z) for p in (wiggle.PointAt(d.T0), wiggle.PointAt(d.T1))]
check(not qualified or (used and len(got) == (1 << 18) + 1
                        and bits([got[0], got[-1]]) == bits(ends)),
      f"a negative tolerance halves to depth 18 and stops: {len(got)} points from PointAt(T0) "
      f"to PointAt(T1) (the Python route's 1.3 million PointAt calls are not repeated here)")

print("\n-- what keeps the Python route --")
polyline = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(1, 2, 0), r3.Point3d(3, 1, 1)])
check(native.dxf_approx_points(polyline, 0.01, 0.0, 2.0, reference) is None,
      "a PolylineCurve (not a NURBS record) is left to the Python route")
check(native.dxf_approx_points((0, 0, 0), 0.01, 0.0, 1.0, reference) is None,
      "something that is not a curve is left to the Python route")

print("\n-- export_commands._dxf_entities: the DXF entities are unchanged --")
joined = r3.PolyCurve()
joined.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(2, 0, 0)))
joined.Append(r3.NurbsCurve.Create(False, 3, [r3.Point3d(2, 0, 0), r3.Point3d(3, 2, 0),
                                              r3.Point3d(5, -1, 1), r3.Point3d(7, 0, 0)]))
tilted = r3.Circle(r3.Point3d(1, 2, 3), 4.0).ToNurbsCurve()
tilted.Rotate(0.7, r3.Vector3d(1, 0, 0), r3.Point3d(0, 0, 0))
for label, curve in (("free-form cubic", random_curve(rng, "cubic")),
                     ("rational quadratic", random_curve(rng, "rational")),
                     ("tilted circle", tilted), ("line + cubic polycurve", joined)):
    before = native._native_dxf_approx_calls
    fast = export._dxf_entities(curve, 1e-3, 25.4)
    used = native._native_dxf_approx_calls > before
    with patch.object(native, "dxf_approx_points", lambda *args: None):
        slow = export._dxf_entities(curve, 1e-3, 25.4)
    count = sum(len(e.get("points", ())) for e in fast[0])
    check(fast == slow and (used or not qualified),
          f"{label}: {len(fast[0])} entities, {count} polyline points, the same as through Python")

print("\n-- qualification: the probe, the platform set, and the parity check --")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pure_core as gate  # noqa: E402

library = os.environ["NARWHAL_PURE_CORE"]
try:
    identity = gate.probe_pure_core_dxf_approx(gate.Path(library))
except ValueError as exc:
    identity = str(exc)
check(identity == gate.PURE_CORE_DXF_APPROX_IDENTITY,
      "the probe gives the pinned witness points, and the refusals their pinned statuses")
witness_bits = b""
for curve, tol, t0, t1 in native._dxf_approx_witnesses():
    points = reference(curve, tol, t0, t1)
    witness_bits += struct.pack(f"<Q{3 * len(points)}d", len(points),
                                *[x for p in points for x in p])
check(hashlib.sha256(witness_bits).hexdigest()
      == gate.PURE_CORE_DXF_APPROX_IDENTITY["points_sha256"],
      "the pinned digest is the Python route's on the loaded rhino3dm")
probe_text = gate.probe_witness_records("PURE_CORE_DXF_APPROX_PROBE").replace('"\n    "', "")
check(all(base64.b64decode(w[0].Encode()["data"]).hex() in probe_text
          for w in native._dxf_approx_witnesses()),
      "the probe's records are the runtime witnesses as rhino3dm encodes them")
libm = next((q for q in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(q)), None)
if libm:
    try:
        gate.probe_pure_core_dxf_approx(gate.Path(libm))
        refused = False
    except ValueError:
        refused = True
    check(refused, "libm, which lacks the pure-core exports, is refused by the probe")
check(native.DXF_APPROX_QUALIFIED_PLATFORMS == gate.PURE_CORE_DXF_APPROX_QUALIFIED_PLATFORMS,
      "the runtime platform set equals the qualification record in check_pure_core")
cubic = random_curve(rng, "cubic")
cd = cubic.Domain
real_platform, real_machine = sys.platform, platform.machine
try:
    for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native._dxf_approx_state = None
        check(native.dxf_approx_capability(reference).startswith(
                  f"not qualified on {plat}/{machine}")
              and native.dxf_approx_points(cubic, 0.01, cd.T0, cd.T1, reference) is None,
              f"{plat}/{machine} keeps the Python route")
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native._dxf_approx_state = None
huge = r3.NurbsCurve.Create(False, 2, [r3.Point3d(-1e200, 0, 0), r3.Point3d(0, 1, 0),
                                      r3.Point3d(1e200, 0, 0)])
hd = huge.Domain
raised = []
for route in (reference, export._approx_points):
    try:
        route(huge, 0.01, hd.T0, hd.T1)
    except OverflowError as exc:
        raised.append(str(exc))
check(native.dxf_approx_points(huge, 0.01, hd.T0, hd.T1, reference) is None
      and len(raised) == 2 and raised[0] == raised[1],
      "a chord whose `** 2` overflows goes to the Python route, which raises OverflowError")
native._dxf_approx_state = None


def nudged(curve, tol, t0, t1):
    points = reference(curve, tol, t0, t1)
    x, y, z = points[1]
    points[1] = (x, math.nextafter(y, math.inf), z)
    return points


check("differs" in native.dxf_approx_capability(nudged)
      and native.dxf_approx_points(cubic, 0.01, cd.T0, cd.T1, nudged) is None,
      "an SDK whose PointAt rounds one coordinate differently keeps the Python route")
native._dxf_approx_state = None
check(native.dxf_approx_capability(reference) == "qualified" or not qualified,
      "the parity check passes again with the real SDK")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_dxf_approx_points
Z = c.c_size_t
fn.argtypes = [c.c_void_p, Z, c.c_double, c.c_double, c.c_double, c.c_void_p, Z, c.c_void_p]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
t0, t1 = float(sys.argv[3]), float(sys.argv[4])
buf = c.create_string_buffer(rec, len(rec))
out = (c.c_double * 3000)(*[-9.0] * 3000)
count = (Z * 2)(7, 7)
od, cd = c.cast(out, c.c_void_p), c.cast(count, c.c_void_p)


def call(record=buf, size=len(rec), tol=0.01, a=t0, b=t1, points=od, capacity=1000, n=cd):
    return fn(record, size, tol, a, b, points, capacity, n)


got = {
    "a NaN tolerance": call(tol=float("nan")),
    "an infinite start": call(a=float("inf")),
    "a NaN end": call(b=float("nan")),
    "not a record": call(record=b"not a record", size=12),
    "truncated record": call(size=len(rec) - 1),
    "null record": call(record=None),
    "null points": call(points=None),
    "null count": call(n=None),
    "a capacity that overflows": call(capacity=(1 << 63) + 1),
    "misaligned points": call(points=c.c_void_p(c.addressof(out) + 1), capacity=8),
    "misaligned count": call(n=c.c_void_p(c.addressof(count) + 1)),
    "count inside the points": call(n=c.c_void_p(c.addressof(out) + 8 * 8)),
    "points over the record": call(points=c.cast(buf, c.c_void_p), capacity=2),
}
untouched = list(out) == [-9.0] * 3000 and list(count) == [7, 7]
room = call(capacity=3)
room_ok = list(out) == [-9.0] * 3000 and count[0] > 3 and count[1] == 7
need = count[0]
good = call()
print(json.dumps({"statuses": got, "untouched": untouched, "room": room, "room_ok": room_ok,
                  "good": good, "written": count[0] == need and -9.0 not in list(out)[:3 * need]
                  and list(out)[3 * need:] == [-9.0] * (3000 - 3 * need)}))
"""
record = base64.b64decode(cubic.Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex(), repr(cd.T0), repr(cd.T1)],
                       capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"a NaN tolerance": -2, "an infinite start": -2, "a NaN end": -2, "not a record": 1,
        "truncated record": 1, "null record": -1, "null points": -1, "null count": -1,
        "a capacity that overflows": -1, "misaligned points": -1, "misaligned count": -1,
        "count inside the points": -1, "points over the record": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched")
      and result.get("room") == 4 and result.get("room_ok") and result.get("good") == 0
      and result.get("written"),
      f"{len(want)} malformed calls refused with their statuses, nothing written; a short buffer "
      f"gets only the count, and a good call writes exactly its points (exit "
      f"{child.returncode}: {result} {child.stderr[-300:]})")

if qualified:
    print("\n-- cost of one curve (best of 3) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    wave = r3.NurbsCurve.Create(False, 3, [r3.Point3d(i, math.sin(i * 0.8) * 3, 0)
                                            for i in range(20)])
    wd = wave.Domain
    for tol in (1e-2, 1e-3):
        slow = best(lambda: reference(wave, tol, wd.T0, wd.T1), 2)
        fast = best(lambda: export._approx_points(wave, tol, wd.T0, wd.T1), 20)
        print(f"  20-point cubic at {tol:g}: Python route {slow * 1e3:5.1f} ms, Rust "
              f"{fast * 1e3:5.2f} ms ({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
