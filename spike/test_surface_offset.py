#!/usr/bin/env python3
"""Surface offsets (OffsetSrf) refined in Rust: the same surface, bit for bit, on the platforms measured.

Run with the bundled runtime (it carries rhino3dm):
  scripts/run_suite.sh spike/test_surface_offset.py

`offset.surface_offset` hands a `NurbsSurface`'s `Encode()` record to Rust
(native/pure_core/src/surface_offset.rs, through `native_nearest.surface_offset_fit`). Rust runs
the Python route's refinement loop (knot insertion, the control net moved along the normal at
the Greville abscissae, the 25 x 25 check against the TRUE offset, the fold test) with
openNURBS's ON_NurbsSurface::Evaluate, ON_Surface::EvNormal (with its singular-point branch)
and ON_NurbsSurface::InsertKnot (eb92af3) ported. Parity is checked on the packed bytes of
every control point, knot and deviation against `offset._python_surface_offset`, and on its
folds and approximations. The route needs a platform in
`native_nearest.SURFACE_OFFSET_QUALIFIED_PLATFORMS` (Linux x86-64, where this suite measures
it) AND the loaded rhino3dm passing the parity check on the witness surfaces. Malformed calls
go to the export in a child process, so an abort fails a check.
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


for name in ("nurbs_data", "native_nearest", "offset"):
    load(name)
native = sys.modules["narwhal3d.native_nearest"]
off = sys.modules["narwhal3d.offset"]
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def bits(result):
    if result.curve is None:
        return ("refused", result.warning)
    return native._surface_bits(result.curve, result.max_deviation), result.warning


def python_route(surface, distance, tolerance, max_refine=6):
    real = native.surface_offset_fit
    native.surface_offset_fit = lambda *args: None
    try:
        return off.surface_offset(surface, distance, tolerance, max_refine)
    finally:
        native.surface_offset_fit = real


def routed(surface, distance, tolerance, max_refine=6):
    """(Rust-routed result, whether Rust decided it)."""
    before = native._native_surface_offset_calls
    result = off.surface_offset(surface, distance, tolerance, max_refine)
    return result, native._native_surface_offset_calls == before + 1


qualified = (sys.platform, platform.machine()) in native.SURFACE_OFFSET_QUALIFIED_PLATFORMS

print("\n-- the route is live where qualified --")
start = time.perf_counter()
capability = native.surface_offset_capability(off._python_surface_offset)
cost = time.perf_counter() - start
if qualified:
    check(capability == "qualified",
          f"{sys.platform}/{platform.machine()} qualifies (one-time parity check {cost * 1e3:.0f} ms)")
else:
    check(capability.startswith("not qualified"), f"{capability!r} on an unqualified platform")


def bumpy(rng, rational):
    nu, nv = rng.randint(4, 7), rng.randint(4, 7)
    ou, ov = rng.choice((2, 3, 4)), rng.choice((3, 4))
    s = r3.NurbsSurface.Create(3, rational, ou, ov, nu, nv)
    for i in range(nu):
        for j in range(nv):
            w = rng.uniform(0.7, 1.4) if rational else 1.0
            x, y = i * rng.uniform(1.5, 2.5), j * rng.uniform(1.5, 2.5)
            z = math.sin(i * rng.uniform(0.5, 1.2)) * math.cos(j * 0.8) * rng.uniform(0.2, 0.8)
            s.Points[i, j] = r3.Point4d(x * w, y * w, z * w, w)
    for knots, o, c in ((s.KnotsU, ou, nu), (s.KnotsV, ov, nv)):
        inner = sorted(rng.uniform(0, 10) for _ in range(c - o))
        a, b = rng.uniform(-20, 0), rng.uniform(1, 30)
        for i, k in enumerate([0.0] * (o - 1) + inner + [10.0] * (o - 1)):
            knots[i] = a + (b - a) * k / 10.0
    return s


print("\n-- random surfaces: Rust's surface, fold or approximation is the Python route's, bit for bit --")
rng = random.Random(20261126)
KINDS = ("bumpy", "rational", "sphere", "cylinder")
mismatches, outcomes, left = [], {}, 0
rust_s = python_s = 0.0
for trial in range(60):
    kind = KINDS[trial % len(KINDS)]
    if kind == "sphere":
        surface = r3.Sphere(r3.Point3d(rng.uniform(-5, 5), 0, 0), rng.uniform(2, 8)).ToNurbsSurface()
    elif kind == "cylinder":
        surface = r3.Cylinder(r3.Circle(rng.uniform(1, 5)), rng.uniform(2, 9)).ToNurbsSurface()
    else:
        surface = bumpy(rng, kind == "rational")
    distance = rng.choice((1, -1)) * rng.uniform(0.1, 1.5) * (3 if rng.random() < 0.15 else 1)
    tolerance = rng.choice((1e-1, 3e-2, 1e-2, 3e-3))
    max_refine = rng.choice((6, 6, 2, 1))
    t = time.perf_counter()
    got, by_rust = routed(surface, distance, tolerance, max_refine)
    rust_s += time.perf_counter() - t
    t = time.perf_counter()
    want = python_route(surface, distance, tolerance, max_refine)
    python_s += time.perf_counter() - t
    if bits(got) != bits(want):
        mismatches.append((trial, kind))
    left += not by_rust
    outcome = ("folded" if want.curve is None else "approximate" if want.warning else "within")
    outcomes[outcome] = outcomes.get(outcome, 0) + 1
check(not mismatches, f"60 random surfaces give the Python route's bits (mismatches: {mismatches})")
check(not qualified or (left == 0 and all(outcomes.get(k, 0) >= 5
                                          for k in ("within", "approximate", "folded"))),
      f"Rust decided every one ({left} went to Python): {outcomes}")
print(f"  Rust route {rust_s * 1e3:.0f} ms, Python route {python_s * 1e3:.0f} ms in total")

print("\n-- fixtures --")
big = r3.NurbsSurface.Create(3, False, 4, 4, 10, 10)
for i in range(10):
    for j in range(10):
        big.Points[i, j] = r3.Point4d(i * 2.0, j * 2.0, math.sin(i) * math.cos(j) * 1.5, 1.0)
for knots in (big.KnotsU, big.KnotsV):
    for i in range(12):
        knots[i] = float(min(max(i - 2, 0), 7))
got, by_rust = routed(big, 0.5, 1e-3)
check((by_rust or not qualified) and got.curve.Points.CountU * got.curve.Points.CountV * 24 > (1 << 19)
      and bits(got) == bits(python_route(big, 0.5, 1e-3)),
      f"a surface larger than the first buffers ({got.curve.Points.CountU} x "
      f"{got.curve.Points.CountV} controls) is fetched again at its size")
got, by_rust = routed(big, -6.0, 1e-2)
check((by_rust or not qualified) and got.curve is None and "inverts" in got.warning
      and bits(got) == bits(python_route(big, -6.0, 1e-2)),
      "an offset past the radius of curvature is refused, with the Python route's warning")
plane = r3.NurbsSurface.Create(3, False, 2, 2, 2, 2)
for i, (x, y) in enumerate(((0, 0), (0, 3), (4, 0), (4, 3))):
    plane.Points[i // 2, i % 2] = r3.Point4d(x, y, 1.0, 1.0)
for knots in (plane.KnotsU, plane.KnotsV):
    knots[0], knots[1] = 0.0, 1.0
got, by_rust = routed(plane, 0.25, 1e-3)
check((by_rust or not qualified) and got.exact and got.max_deviation < 1e-12
      and bits(got) == bits(python_route(plane, 0.25, 1e-3)),
      "a plane offsets exactly, and is still reported exact")

print("\n-- malformed calls to the export are refused, writing nothing (child process) --")
PROBE = r"""
import ctypes as c, json, sys
fn = c.CDLL(sys.argv[1]).narwhal_pure_core_surface_offset
Z = c.c_size_t
fn.argtypes = [c.c_void_p, Z, c.c_double, c.c_double, c.c_uint32, c.c_void_p, Z, c.c_void_p,
               c.c_void_p]
fn.restype = c.c_int32
rec = bytes.fromhex(sys.argv[2])
buf = c.create_string_buffer(rec, len(rec))
out = (c.c_ubyte * 65536)(*[0xA5] * 65536)
length, dev = c.c_uint64(7), c.c_double(-9.0)
shared = (c.c_double * 8192)()
c.memmove(shared, rec, min(len(rec), 8 * 8192))
od, ld, dd = (c.cast(x, c.c_void_p) for x in (out, c.pointer(length), c.pointer(dev)))
odd = c.c_void_p(c.addressof(shared) + 1)


def call(record=buf, size=len(rec), distance=0.3, tolerance=0.01, max_refine=6, out_record=od,
         capacity=65536, out_len=ld, out_dev=dd):
    return fn(record, size, distance, tolerance, max_refine, out_record, capacity, out_len,
              out_dev)


got = {
    "zero distance": call(distance=0.0),
    "infinite distance": call(distance=float("inf")),
    "NaN tolerance": call(tolerance=float("nan")),
    "eleven rounds": call(max_refine=11),
    "not a record": call(record=b"not a record", size=12),
    "truncated record": call(size=len(rec) - 1),
    "empty record": call(record=None, size=0),
    "null record": call(record=None),
    "null output": call(out_record=None),
    "null length": call(out_len=None),
    "null deviation": call(out_dev=None),
    "misaligned length": call(out_len=odd),
    "output overlapping the record": call(record=shared, out_record=shared, capacity=4096),
    "length inside the output": call(out_len=c.c_void_p(c.addressof(out) + 64)),
}
untouched = list(out) == [0xA5] * 65536 and length.value == 7 and dev.value == -9.0
print(json.dumps({"statuses": got, "untouched": untouched}))
"""
record = base64.b64decode(native._surface_offset_witnesses()[1][0].Encode()["data"])
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.environ["NARWHAL_PURE_CORE"],
                        record.hex()], capture_output=True, text=True, timeout=60)
result = json.loads(child.stdout or "{}")
want = {"zero distance": -2, "infinite distance": -2, "NaN tolerance": -2, "eleven rounds": -2,
        "not a record": 1, "truncated record": 1, "empty record": 1, "null record": -1,
        "null output": -1, "null length": -1, "null deviation": -1, "misaligned length": -1,
        "output overlapping the record": -1, "length inside the output": -1}
check(child.returncode == 0 and result.get("statuses") == want and result.get("untouched"),
      f"{len(want)} malformed calls refused with their statuses, nothing written "
      f"(exit {child.returncode}: {result.get('statuses')} {child.stderr[-300:]})")

if qualified:
    print("\n-- cost of one surface offset (best of 3) --")

    def best(fn, reps):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    six = r3.NurbsSurface.Create(3, False, 4, 4, 6, 6)
    for i in range(6):
        for j in range(6):
            six.Points[i, j] = r3.Point4d(i * 2.0, j * 2.0, math.sin(i) * math.cos(j) * 1.5, 1.0)
    for knots in (six.KnotsU, six.KnotsV):
        for i in range(8):
            knots[i] = float(min(max(i - 2, 0), 3))
    sphere = r3.Sphere(r3.Point3d(0, 0, 0), 5.0).ToNurbsSurface()
    for label, surface, distance, tolerance in (("6x6 bump, tolerance 1e-2", six, 0.5, 1e-2),
                                                ("6x6 bump, tolerance 1e-3", six, 0.5, 1e-3),
                                                ("10x10 bump, tolerance 1e-2", big, 0.5, 1e-2),
                                                ("sphere, tolerance 1e-2", sphere, 0.5, 1e-2)):
        slow = best(lambda: python_route(surface, distance, tolerance), 1)
        fast = best(lambda: off.surface_offset(surface, distance, tolerance), 3)
        print(f"  {label}: Python {slow * 1e3:6.1f} ms, Rust {fast * 1e3:5.2f} ms ({slow / fast:.0f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
