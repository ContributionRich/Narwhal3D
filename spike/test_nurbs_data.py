#!/usr/bin/env python3
"""A NurbsCurve's knots and control points from one `Encode()` call: the same bits.

Run with the bundled runtime (it carries rhino3dm); the Rust adapter checks use the
gate's pure-core release build:
  scripts/run_suite.sh spike/test_nurbs_data.py

`nurbs_data.arrays` unpacks the ``ON_NurbsCurve::Write`` record instead of reading
``curve.Points`` one pybind call at a time. Parity is checked on the raw bytes
(``array.tobytes()``), so -0.0, subnormals and NaN payloads count, against the
per-point read the adapters used before. Rust (native/pure_core/src/nurbs_record.rs)
checks the record; malformed ones return None from `parse`, checked through the export in
a child process, and fall back to the per-point read. Every Rust adapter that packs a curve is checked
to take the archive route and to give the same answer as with the per-point read.
"""
import array
import base64
import importlib.util
import json
import math
import os
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


nd = load("nurbs_data")
native_nearest = load("native_nearest")
native_display = load("native_display")
native_arclength = load("native_arclength")
native_intersect = load("native_intersect")
nearest = load("nearest")
arclength = load("arclength")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def per_point(curve):
    """The adapters' read before `nurbs_data`: one call per control point and coordinate."""
    cvs = array.array("d")
    for cv in curve.Points:
        cvs.extend((cv.X, cv.Y, cv.Z, cv.W))
    return int(curve.Order), bool(curve.IsRational), array.array("d", curve.Knots.ToList()), cvs


def bits(arrays):
    order, rational, knots, cvs = arrays
    return order, rational, knots.typecode, knots.tobytes(), cvs.typecode, cvs.tobytes()


def random_curve(r, values=None):
    degree = r.randint(1, 9)
    count = r.randint(degree + 1, degree + 40)
    rational = r.random() < 0.5
    curve = r3.NurbsCurve(3, rational, degree + 1, count)
    pick = values or (lambda: r.uniform(-1e3, 1e3))
    for i in range(count):
        w = r.uniform(0.05, 20.0) if rational else 1.0
        curve.Points[i] = r3.Point4d(pick() * w, pick() * w, pick() * w, w)
    knots = sorted(r.uniform(-500, 500) for _ in range(degree + count - 1))
    for i in range(1, len(knots)):
        if r.random() < 0.2:
            knots[i] = knots[i - 1]
    for i, k in enumerate(knots):
        curve.Knots[i] = k
    return curve


print("-- the archive record gives the per-point read's bits --")
rng = random.Random(20261114)
curves = [random_curve(rng) for _ in range(400)]
odd = (0.0, -0.0, 5e-324, -2.2250738585072014e-308, 1e300, -1e-300, 1.0 / 3.0)
curves += [random_curve(rng, lambda: rng.choice(odd)) for _ in range(60)]
curves += [r3.Circle(r3.Point3d(1, 2, 3), 5.0).ToNurbsCurve(),
           r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 2), r3.Point3d(7, 1, -1)).ToNurbsCurve(),
           r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 1)).ToNurbsCurve(),
           r3.NurbsCurve.Create(True, 3, [r3.Point3d(math.cos(i), math.sin(i), 0) for i in range(9)]),
           r3.NurbsCurve.Create(False, 5, [r3.Point3d(i, i * i % 7, -i) for i in range(30)])]
negzero = r3.NurbsCurve.Create(False, 2, [r3.Point3d(-0.0, 0.0, -0.0), r3.Point3d(1, -0.0, 0),
                                           r3.Point3d(2, 3, -0.0)])
negzero.Knots[0] = -0.0
curves.append(negzero)
before = nd._encoded_reads, nd._point_reads
differ = sum(bits(nd.arrays(c)) != bits(per_point(c)) for c in curves)
rationals = sum(c.IsRational for c in curves)
check(differ == 0 and nd._encoded_reads - before[0] == len(curves) and nd._point_reads == before[1],
      f"{len(curves)} curves ({rationals} rational, degrees 1-9, -0.0, subnormals): "
      f"{differ} differ, all from the archive record")

print("\n-- records the reader does not recognise fall back to the per-point read --")

record = bytearray(base64.b64decode(curves[0].Encode()["data"]))
check(nd.parse(bytes(record)) is not None, "an unmodified record parses")


def mutated(offset, value):
    data = bytearray(record)
    data[offset:offset + len(value)] = value
    return bytes(data)


cases = {
    "a different class id": mutated(24, b"\x00" * 16),
    "a different outer typecode": mutated(0, struct.pack("<I", 0x00027FFB)),
    "chunk version 2.0": mutated(56, b"\x20"),
    "dimension 2": mutated(57, struct.pack("<i", 2)),
    "is_rat 2": mutated(61, struct.pack("<i", 2)),
    "order 1": mutated(65, struct.pack("<i", 1)),
    "a knot count off by one": mutated(129, struct.pack("<i", struct.unpack_from("<i", record, 129)[0] + 1)),
    "a CV count of 2**31 - 1": mutated(69, struct.pack("<i", 2**31 - 1)),
    "a data chunk longer than the record": mutated(48, struct.pack("<q", 10**9)),
    # The outer framing faults found on review: each was accepted by the Python reader.
    "a zeroed outer length": mutated(4, struct.pack("<q", 0)),
    "trailing bytes": bytes(record) + b"extra",
    "a record missing its last four bytes": bytes(record[:-4]),
    "a truncated record": bytes(record[:150]),
    "an empty record": b"",
}
# Through the Rust export in a child process, so an abort fails a check instead of the run.
PROBE = r"""
import importlib.util, json, os, sys, types
pkg = types.ModuleType("narwhal3d"); pkg.__path__ = [sys.argv[1]]; sys.modules["narwhal3d"] = pkg
spec = importlib.util.spec_from_file_location("narwhal3d.nurbs_data", os.path.join(sys.argv[1], "nurbs_data.py"))
nd = importlib.util.module_from_spec(spec); spec.loader.exec_module(nd)
cases = json.load(sys.stdin)
print(json.dumps({name: nd.parse(bytes.fromhex(data)) is None for name, data in cases.items()}))
"""
child = subprocess.run([sys.executable, "-I", "-c", PROBE, os.path.join(ROOT, "narwhal3d")],
                       input=json.dumps({k: v.hex() for k, v in cases.items()}),
                       capture_output=True, text=True, env=dict(os.environ), timeout=60)
refused = [k for k, v in json.loads(child.stdout or "{}").items() if v]
check(child.returncode == 0 and len(refused) == len(cases),
      f"the Rust export refuses {len(refused)}/{len(cases)} malformed records in a child process "
      f"(exit {child.returncode}; missing: {sorted(set(cases) - set(refused))})")


class Unencodable:
    """A curve whose `Encode` fails: `arrays` must answer from `Points`."""

    def __init__(self, curve):
        self._curve = curve
        self.Points, self.Order = curve.Points, curve.Order
        self.IsRational, self.Knots = curve.IsRational, curve.Knots

    def Encode(self):
        raise RuntimeError("no archive")


before = nd._point_reads
got = nd.arrays(Unencodable(curves[1]))
check(nd._point_reads == before + 1 and bits(got) == bits(per_point(curves[1])),
      "a failing Encode() reads point by point, with the same answer")
two_d = r3.NurbsCurve(2, False, 3, 4)
for i in range(4):
    two_d.Points[i] = r3.Point4d(i, i * 0.5, 0.0, 1.0)
for i, k in enumerate((0, 0, 1, 2, 2)):
    two_d.Knots[i] = k
before = nd._point_reads
got = nd.arrays(two_d)
check(nd._point_reads == before + 1 and bits(got) == bits(per_point(two_d)),
      "a 2-D curve's record is refused and read point by point, with the same answer")

print("\n-- every Rust adapter packs through the archive, with unchanged answers --")


def per_point_only(fn):
    """`fn()` with the archive route switched off, as the adapters read before."""
    real = nd._from_archive
    nd._from_archive = lambda curve: None
    try:
        return fn()
    finally:
        nd._from_archive = real


def through_archive(label, fn):
    before = nd._encoded_reads
    got = fn()
    used = nd._encoded_reads - before
    want = per_point_only(fn)
    check(got is not None and used > 0 and got == want,
          f"{label}: {used} archive read(s), the per-point answer")


curve = random_curve(random.Random(7))
while not curve.IsValid:
    curve = random_curve(random.Random(rng.random()))
other = r3.NurbsCurve.Create(False, 3, [r3.Point3d(-400, -400, 0), r3.Point3d(0, 600, 0),
                                         r3.Point3d(400, -300, 0), r3.Point3d(600, 500, 0)])
through_archive("native_nearest.sample", lambda: native_nearest.sample(curve, 200))


def packed_search():
    packed = native_nearest.pack(curve)
    return [native_nearest.search(packed, p) for p in ((1.0, 2.0, 3.0), (-300.0, 50.0, 7.5))]


through_archive("native_nearest.pack + search", packed_search)
through_archive("nearest.near_point (Rust search)",
                lambda: nearest.near_point(curve, (10.0, -20.0, 5.0), nearest.NearCache(curve)))
through_archive("native_display.display_samples", lambda: native_display.display_samples(curve, 0.01))
through_archive("native_intersect.point_hits", lambda: native_intersect.point_hits(curve, other, 1e-6, 40))
through_archive("arclength.PreparedLength (Rust inversion)",
                lambda: arclength.PreparedLength(curve).parameters_at([0.0, 1.5, 40.0, 100.0]))

print("\n-- bridge.control_points reads NURBS grips through the archive --")
bridge = load("bridge")


def legacy_control_points(curve):
    """`bridge.control_points` on a NurbsCurve before the change: five calls per point."""
    pts = curve.Points
    n = max(0, len(pts) - bridge._periodic_wrap(curve))
    out = []
    for j in range(n):
        cp = pts[j]
        w = getattr(cp, "W", 1.0) or 1.0
        out.append((cp.X / w, cp.Y / w, cp.Z / w))
    return out


def as_bits(points):
    return [struct.pack("<3d", *p) for p in points]


grip_curves = [c for c in curves if c.IsValid]
periodic = [c for c in grip_curves if c.IsPeriodic]
before = nd._encoded_reads
differ = sum(as_bits(bridge.control_points(c)) != as_bits(legacy_control_points(c))
             for c in grip_curves)
check(differ == 0 and nd._encoded_reads - before == len(grip_curves) and periodic,
      f"{len(grip_curves)} NurbsCurves ({len(periodic)} periodic): {differ} grip lists differ "
      f"from the per-point read, bit for bit")
joined = r3.PolyCurve()
for piece in (r3.NurbsCurve.Create(False, 3, [r3.Point3d(0, 0, 0), r3.Point3d(1, 2, 0),
                                               r3.Point3d(3, 2, 0), r3.Point3d(4, 0, 0)]),
              r3.LineCurve(r3.Point3d(4, 0, 0), r3.Point3d(6, -1, 0)),
              r3.Arc(r3.Point3d(6, -1, 0), r3.Point3d(7, 0.5, 0), r3.Point3d(8, -1, 0)).ToNurbsCurve()):
    joined.Append(piece)
leaves = bridge._polycurve_leaves(joined)
want = [legacy_control_points(leaf) if isinstance(leaf, r3.NurbsCurve)
        else bridge._leaf_points(leaf) for leaf in leaves]
check(leaves and [as_bits(bridge._leaf_points(leaf)) for leaf in leaves]
      == [as_bits(points) for points in want] and len(bridge.control_points(joined)) > 0,
      "a joined curve's NURBS leaves (one rational) give the per-point grips, bit for bit")

print("\n-- cost of one read (best of 5) --")


def best(fn, reps):
    out = math.inf
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


for n in (4, 16, 67, 200):
    c = r3.NurbsCurve.Create(False, 3, [r3.Point3d(math.cos(i), math.sin(0.7 * i), 0.1 * i)
                                        for i in range(n)])
    fast, slow = best(lambda: nd.arrays(c), 200), best(lambda: per_point(c), 50)
    print(f"  {n:3d} control points: per point {slow * 1e6:7.1f} us, archive {fast * 1e6:6.1f} us "
          f"({slow / fast:.1f}x)")
    fast, slow = best(lambda: bridge.control_points(c), 200), best(lambda: legacy_control_points(c), 50)
    print(f"      bridge.control_points: per point {slow * 1e6:7.1f} us, archive {fast * 1e6:6.1f} us "
          f"({slow / fast:.1f}x)")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
