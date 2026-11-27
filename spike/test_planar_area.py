#!/usr/bin/env python3
"""Closed planar curve area and centroid in Rust: the same results, bit for bit.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_planar_area.py

Area, AreaCentroid and the object details measure a closed planar curve with
`analysis.planar_curve_area_centroid`: Newell's normal of 64 points on the curve, then Green's
theorem at 12- and 24-point Gauss-Legendre per knot span, the answer the 24-point one and the
error the difference. Rust (narwhal_pure_core_planar_area_centroid,
native/pure_core/src/planar_area.rs) runs it on Encode() records: the plane on the curve itself
(a NurbsCurve, PolyCurve or PolylineCurve) and Green's theorem on its NURBS form, with
`nearest::Nurbs` and the polycurve / polyline evaluators as PointAt / DerivativeAt; IsClosed and
IsPlanar stay rhino3dm queries. The
route needs a platform in `native_nearest.PLANAR_AREA_QUALIFIED_PLATFORMS` AND the loaded
rhino3dm passing the parity check against `analysis._python_planar_curve_area_centroid` on its
witness curves. Malformed calls go to the export in a child process.
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


for name in ("nurbs_data", "native_nearest", "arclength", "analysis"):
    load(name)
native = sys.modules["narwhal3d.native_nearest"]
analysis = sys.modules["narwhal3d.analysis"]
reference = analysis._python_planar_curve_area_centroid
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def bits(result):
    if result is None:
        return None
    area, err, c, c_err = result
    return struct.pack("<8d", area, err, *c, *c_err)


qualified = (sys.platform, platform.machine()) in native.PLANAR_AREA_QUALIFIED_PLATFORMS


def routed(curve):
    before = native._native_planar_area_calls
    got = analysis.planar_curve_area_centroid(curve)
    return got, native._native_planar_area_calls == before + 1


def tilted_plane(rng):
    return r3.Plane(r3.Point3d(rng.uniform(-9, 9), rng.uniform(-9, 9), rng.uniform(-9, 9)),
                    r3.Vector3d(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.1, 1)))


def random_curve(rng, kind):
    plane = tilted_plane(rng)
    n = rng.randint(4, 40)
    pts = [plane.PointAt(math.cos(2 * math.pi * k / n) * rng.uniform(2, 6),
                         math.sin(2 * math.pi * k / n) * rng.uniform(2, 6)) for k in range(n)]
    if kind == "circle":
        curve = r3.Circle(plane.Origin, rng.uniform(0.1, 30)).ToNurbsCurve()
        curve.Rotate(rng.uniform(0, 3), r3.Vector3d(rng.uniform(-1, 1), rng.uniform(-1, 1),
                                                    rng.uniform(-1, 1)), plane.Origin)
        return curve
    if kind == "periodic":
        return r3.NurbsCurve.Create(True, rng.randint(2, 5), pts)
    if kind == "polygon":
        return r3.NurbsCurve.Create(False, 1, pts + [pts[0]])
    if kind == "polyline":
        return r3.PolylineCurve(pts + [pts[0]])
    if kind == "joined":
        joined, m = r3.PolyCurve(), len(pts) // 3
        first = (r3.NurbsCurve.Create(False, 3, pts[:m + 1]) if m >= 3
                 else r3.LineCurve(pts[0], pts[m]))
        first.Domain = r3.Interval(rng.uniform(-5, 5), rng.uniform(6, 20))
        joined.Append(first)
        joined.Append(r3.PolylineCurve(pts[m:2 * m + 1]))
        for p, q in zip(pts[2 * m:], pts[2 * m + 1:] + [pts[0]]):
            joined.Append(r3.LineCurve(p, q))
        return joined
    if kind == "rational":
        pts.append(pts[0])
        curve = r3.NurbsCurve(3, True, 3, len(pts))
        for i, p in enumerate(pts):
            w = 1.0 if i in (0, len(pts) - 1) else rng.uniform(0.5, 2.0)
            curve.Points[i] = r3.Point4d(p.X * w, p.Y * w, p.Z * w, w)
        inner = sorted(rng.uniform(0.02, 0.98) for _ in range(len(pts) - 3))
        for i, k in enumerate([0.0] * 2 + inner + [1.0] * 2):
            curve.Knots[i] = 2.0 + 5.0 * k
        return curve
    if kind == "non-planar":
        pts = [r3.Point3d(p.X, p.Y, p.Z + rng.uniform(-1, 1)) for p in pts]
    if kind != "open":
        pts.append(pts[0])
    return r3.NurbsCurve.Create(False, rng.randint(2, 5), pts)


print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.planar_area_capability(reference)
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")

print("\n-- random curves: Rust's results are the Python route's, bit for bit --")
rng = random.Random(20261127)
KINDS = ("periodic", "clamped", "rational", "circle", "polygon", "polyline", "joined", "open",
         "non-planar")
mismatches, by_rust, answered, nones = [], 0, 0, 0
rust_s = python_s = 0.0
for trial in range(180):
    curve = None
    while curve is None or not curve.IsValid:
        curve = random_curve(rng, KINDS[trial % len(KINDS)])
    t = time.perf_counter()
    got, used = routed(curve)
    rust_s += time.perf_counter() - t
    t = time.perf_counter()
    want = reference(curve)
    python_s += time.perf_counter() - t
    if bits(got) != bits(want):
        mismatches.append(trial)
    by_rust += used
    answered += want is not None
    nones += want is None
check(not mismatches, f"180 curves (periodic of degrees 2-5, clamped, rational, circles, "
      f"polygons, closed PolylineCurves and PolyCurves with a shifted segment domain, in tilted "
      f"planes; open and non-planar ones answer None: {answered} areas, {nones} None) give the "
      f"Python route's bits (mismatches: {mismatches})")
check(not qualified or by_rust == 180, f"Rust answered every one ({by_rust} of 180)")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- what keeps the Python route --")
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(2, 1, 0))
before = native._native_planar_area_calls
got = analysis.planar_curve_area_centroid(line)
check(native._native_planar_area_calls == before and got is None and reference(line) is None,
      "a LineCurve (another class) is measured by the Python route")
check(native.planar_area_centroid((0, 0, 0), reference) is None,
      "something that is not a curve is left to the Python route")

print("\n-- qualification: the probe, the platform set, and the parity check --")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pure_core as gate  # noqa: E402

library = os.environ["NARWHAL_PURE_CORE"]
try:
    identity = gate.probe_pure_core_planar_area(gate.Path(library))
except ValueError as exc:
    identity = str(exc)
check(identity == gate.PURE_CORE_PLANAR_AREA_IDENTITY,
      "the probe gives the pinned witness results, and the refusals their pinned statuses")
witness_bits = b"".join(bits(reference(c)) for c in native._planar_area_witnesses())
check(hashlib.sha256(witness_bits).hexdigest()
      == gate.PURE_CORE_PLANAR_AREA_IDENTITY["results_sha256"],
      "the pinned digest is the Python route's on the loaded rhino3dm")
probe_text = gate.probe_witness_records("PURE_CORE_PLANAR_AREA_PROBE").replace('"\n            "', "")
records = [base64.b64decode(c.Encode()["data"]).hex() for c in native._planar_area_witnesses()]
records += [base64.b64decode(c.ToNurbsCurve().Encode()["data"]).hex()
            for c in native._planar_area_witnesses() if not isinstance(c, r3.NurbsCurve)]
check(all(r in probe_text for r in records),
      "the probe's records are the runtime witnesses (and NURBS forms) as rhino3dm encodes them")
libm = next((q for q in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(q)), None)
if libm:
    try:
        gate.probe_pure_core_planar_area(gate.Path(libm))
        refused = False
    except ValueError:
        refused = True
    check(refused, "libm, which lacks the pure-core exports, is refused by the probe")
check(native.PLANAR_AREA_QUALIFIED_PLATFORMS == gate.PURE_CORE_PLANAR_AREA_QUALIFIED_PLATFORMS,
      "the runtime platform set equals the qualification record in check_pure_core")
blob = None
while blob is None or not blob.IsValid:
    blob = random_curve(rng, "periodic")
real_platform, real_machine = sys.platform, platform.machine
try:
    for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
        sys.platform, platform.machine = plat, (lambda m=machine: m)
        native._planar_area_state = None
        check(native.planar_area_capability(reference).startswith(
                  f"not qualified on {plat}/{machine}")
              and native.planar_area_centroid(blob, reference) is None,
              f"{plat}/{machine} keeps the Python route")
finally:
    sys.platform, platform.machine = real_platform, real_machine
    native._planar_area_state = None


def nudged(curve):
    area, err, c, c_err = reference(curve)
    return math.nextafter(area, math.inf), err, c, c_err


check("differs" in native.planar_area_capability(nudged)
      and native.planar_area_centroid(blob, nudged) is None,
      "an SDK whose area rounds differently on a witness keeps the Python route")
native._planar_area_state = None
check(native.planar_area_capability(reference) == "qualified" or not qualified,
      "the parity check passes again with the real SDK")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_planar_area_centroid
fn.argtypes = [c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
out = (c.c_double * 16)(*[-9.0] * 16)
od = c.cast(out, c.c_void_p)


def call(record=buf, size=len(rec), result=od, shape=None, shape_size=0):
    return fn(shape, shape_size, record, size, result)


got = {
    "not a record": call(record=b"not a record", size=12),
    "a shape that is not a record": call(shape=b"not a record", shape_size=12),
    "a null shape with a length": call(shape_size=8),
    "result over the shape": call(shape=c.cast(out, c.c_void_p), shape_size=64),
    "truncated record": call(size=len(rec) - 1),
    "null record": call(record=None),
    "null result": call(result=None),
    "misaligned result": call(result=c.c_void_p(c.addressof(out) + 1)),
    "result over the record": call(result=c.cast(buf, c.c_void_p)),
}
untouched = list(out) == [-9.0] * 16
good = call()
print(json.dumps({"statuses": got, "untouched": untouched, "good": good,
                  "written": -9.0 not in list(out)[:8] and list(out)[8:] == [-9.0] * 8}))
"""
record = base64.b64decode(blob.Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"not a record": 1, "a shape that is not a record": 1, "a null shape with a length": -1,
        "result over the shape": -1, "truncated record": 1, "null record": -1,
        "null result": -1, "misaligned result": -1, "result over the record": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched")
      and result.get("good") == 0 and result.get("written"),
      f"{len(want)} malformed calls refused with their statuses, nothing written; a good call "
      f"writes exactly its 8 values (exit {child.returncode}: {result} {child.stderr[-300:]})")

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

    for n in (12, 60, 300):
        pts = [r3.Point3d(math.cos(2 * math.pi * i / n) * (5 + math.sin(14 * math.pi * i / n)),
                          math.sin(2 * math.pi * i / n) * (5 + math.sin(14 * math.pi * i / n)), 0)
               for i in range(n)]
        flower = r3.NurbsCurve.Create(True, 3, pts)
        slow = best(lambda: reference(flower), 1)
        fast = best(lambda: analysis.planar_curve_area_centroid(flower), 10)
        print(f"  periodic cubic through {n} points: Python route {slow * 1e3:6.1f} ms, Rust "
              f"{fast * 1e3:5.2f} ms ({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
