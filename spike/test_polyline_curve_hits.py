#!/usr/bin/env python3
"""Curve/curve intersection with polylines in Rust: the same hits as `intersect.curve_curve`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_polyline_curve_hits.py

Polyline and Rectangle make `PolylineCurve`s, which the NURBS-only export left to the
Python recursion (a Trim of a 12-point cubic by a 4-point polyline: ~0.4 s). A pair with a
polyline now goes to ``narwhal_pure_core_curve_curve_record_hits``, which reads each curve
from its ``Encode()`` record and ports ``ON_PolylineCurve``'s Evaluate, bounding box and
Split. Parity is exact: every hit (point, t_a, t_b) equals `_subdivide` + `_dedupe`'s, bit
for bit, on rhino3dm's own Split and PointAt. The overlap test stays in Python.
"""
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
import time
import types

import rhino3dm as r3

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


native = load("native_intersect")
ix = load("intersect")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


P = r3.Point3d


def polyline(points, domain=None):
    curve = r3.PolylineCurve([P(*p) for p in points])
    if domain is not None:
        curve.Domain = r3.Interval(*domain)
    return curve


def cubic(points):
    return r3.NurbsCurve.CreateControlPointCurve([P(*p) for p in points], 3)


def python_only(fn, *args):
    """`fn(*args)` with the Rust recursion switched off (the reference answer)."""
    real = ix._native_point_hits
    ix._native_point_hits = lambda *a: None
    try:
        return fn(*args)
    finally:
        ix._native_point_hits = real


def xyz(p):
    return (p.X, p.Y, p.Z)


def fields(hits):
    return [(h.point, h.t_a, h.t_b, h.overlap, h.t_a_end, h.t_b_end) for h in hits]


print("\n-- the route is qualified here --")
capability = native.polyline_capability()
check(capability == "qualified", f"polyline route: {capability}")

print("\n-- a seeded sweep: every hit equals the Python recursion's, bit for bit --")
rng = random.Random(20261121)


def coord():
    k = rng.random()
    if k < 0.3:
        return round(rng.uniform(-5, 5), 1)
    if k < 0.4:
        return rng.choice([0.0, -0.0, 1.0, -1.0])
    return rng.uniform(-5, 5)


def random_polyline():
    pts = []
    flat = rng.random() < 0.7
    while len(pts) < rng.randint(2, 7):
        p = (coord(), coord(), 0.0 if flat else coord())
        if not pts or p != pts[-1]:
            pts.append(p)
    if rng.random() < 0.2:
        pts.append(pts[0])
    curve = polyline(pts)
    r = rng.random()
    if r < 0.3:
        t0 = rng.uniform(-100, 100)
        curve.Domain = r3.Interval(t0, t0 + rng.uniform(1e-3, 1e3))
    elif r < 0.5:
        d = curve.Domain
        parts = curve.Split(rng.uniform(d.T0, d.T1))
        curve = parts[rng.randint(0, 1)]   # irregular parameters, as a Split leaves them
    return curve


def random_nurbs():
    k = rng.random()
    if k < 0.4:
        return r3.NurbsCurve.Create(False, 3, [P(coord(), coord(), 0.0)
                                               for _ in range(rng.randint(4, 8))])
    if k < 0.7:
        return r3.NurbsCurve.CreateFromCircle(r3.Circle(P(coord(), coord(), 0),
                                                        rng.uniform(0.5, 4)))
    return r3.LineCurve(P(coord(), coord(), 0), P(coord(), coord(), 0)).ToNurbsCurve()


pairs = equal = hits = 0
before = native._native_record_calls
python_s = rust_s = 0.0
while pairs < 100:
    a = random_polyline()
    b = random_polyline() if rng.random() < 0.5 else random_nurbs()
    if rng.random() < 0.5:
        a, b = b, a
    if not (a.IsValid and b.IsValid):
        continue
    tol = rng.choice([1e-6, 1e-6, 1e-3, 1e-2])
    start = time.perf_counter()
    got = native.point_hits(a, b, tol, 40)
    rust_s += time.perf_counter() - start
    start = time.perf_counter()
    want = native._python_hits(a, b, tol, 40)
    python_s += time.perf_counter() - start
    pairs += 1
    if got is not None and native._hit_bits(got) == native._hit_bits(want):
        equal += 1
        hits += len(want)
calls = native._native_record_calls - before
check(equal == pairs == 100 and calls == pairs and hits >= 60,
      f"{equal}/{pairs} pairs bit-identical through Rust ({hits} hits, {calls} native calls); "
      f"Python {python_s:.1f} s, Rust {rust_s * 1e3:.0f} ms")

print("\n-- curve_curve, Split and Trim: the same answers through Rust --")
rect = polyline([(2, -1, 0), (9, -1, 0), (9, 1.5, 0), (2, 1.5, 0), (2, -1, 0)])
zig = polyline([(0, 0, 0), (3, 2, 0), (6, -1, 0), (10, 1, 0)])
wave = cubic([(i, math.sin(i * 0.9) * 2, 0) for i in range(12)])
circle = r3.NurbsCurve.CreateFromCircle(r3.Circle(P(5, 0, 0), 2))
for label, a, b in (("Rectangle x cubic", rect, wave), ("Polyline x cubic", zig, wave),
                    ("cubic x Rectangle", wave, rect), ("Rectangle x circle", rect, circle),
                    ("Rectangle x Polyline", rect, zig)):
    before = native._native_record_calls
    got = ix.curve_curve(a, b)
    taken = native._native_record_calls - before
    check(taken == 1 and fields(got) == fields(python_only(ix.curve_curve, a, b)) and got,
          f"{label}: {len(got)} hits through Rust, equal to the Python recursion")
# An edge of the rectangle lying on a line overlaps it: the overlap test (Python) answers
# first and the recursion is not reached, as before.
edge = r3.LineCurve(P(3, -1, 0), P(8, -1, 0)).ToNurbsCurve()
got = ix.curve_curve(rect, edge)
check(len(got) == 1 and got[0].overlap and fields(got) == fields(python_only(ix.curve_curve, rect, edge)),
      "a collinear edge is still one overlap, from the Python overlap test")
pieces = ix.split_curve_by(wave, [rect, zig])
reference = python_only(ix.split_curve_by, wave, [rect, zig])
check(len(pieces) == len(reference) > 1
      and all(xyz(p.PointAtStart) == xyz(q.PointAtStart) and p.Domain.T1 == q.Domain.T1
              for p, q in zip(pieces, reference)),
      f"Split of a cubic by a Rectangle and a Polyline: the same {len(pieces)} pieces")
kept = ix.trim_curve_by(rect, [wave], (5.5, -1, 0))
reference = python_only(ix.trim_curve_by, rect, [wave], (5.5, -1, 0))
check(len(kept) == len(reference) >= 1
      and all(p.Domain.T0 == q.Domain.T0 and p.Domain.T1 == q.Domain.T1
              for p, q in zip(kept, reference)),
      f"Trim of a Rectangle by a cubic: the same {len(kept)} surviving pieces")

print("\n-- routing: only valid NURBS and polyline curves take the route --")
line_curve = r3.LineCurve(P(0, -5, 0), P(6, 5, 0))
check(native.point_hits(rect, line_curve, 1e-6, 40) is None,
      "a LineCurve beside a polyline keeps the Python recursion")
check(native.point_hits(rect, rect.Duplicate(), 1e-6, float("nan")) is None
      and native.point_hits(rect, zig, float("inf"), 40) is None
      and native.point_hits(rect, zig, 1e-6, 65) is None,
      "a non-integer depth, an infinite tolerance or a depth over 64 keeps the Python")
before = (native._native_calls, native._native_record_calls)
native.point_hits(wave, circle, 1e-6, 40)
check((native._native_calls - before[0], native._native_record_calls - before[1]) == (1, 0),
      "two NURBS curves still take the NURBS export")

print("\n-- timing (i7-9700K: Rectangle x cubic 21 -> 8 ms, Polyline x cubic 442 -> 9 ms; "
      "the rest is the Python overlap test) --")


def best(fn, reps=3):
    out = math.inf
    for _ in range(reps):
        start = time.perf_counter()
        fn()
        out = min(out, time.perf_counter() - start)
    return out


for label, a, b in (("Rectangle x cubic", rect, wave), ("Polyline x cubic", zig, wave)):
    slow = best(lambda: python_only(ix.curve_curve, a, b))
    fast = best(lambda: ix.curve_curve(a, b))
    print(f"  {label}: Python {slow * 1e3:.1f} ms, Rust {fast * 1e3:.2f} ms "
          f"({slow / fast:.0f}x)")

print("\n-- input safety: the record export runs in a subprocess, so a crash fails a check --")
script = r'''
import ctypes, json, math, os
fn = ctypes.CDLL(os.environ["NARWHAL_PURE_CORE"]).narwhal_pure_core_curve_curve_record_hits
D, Z = ctypes.POINTER(ctypes.c_double), ctypes.c_size_t
fn.argtypes = [ctypes.c_char_p, Z, ctypes.c_char_p, Z, ctypes.c_double, Z, D, Z, ctypes.POINTER(Z)]
fn.restype = ctypes.c_int32
good = bytes.fromhex(os.environ["GOOD_RECORD"])
def call(a=good, b=good, a_len=None, tol=1e-6, depth=40, cap=4, count=True):
    out, n = (ctypes.c_double * 20)(), Z(99)
    return fn(a, len(a or b"") if a_len is None else a_len, b, len(b), tol, depth, out, cap,
              ctypes.byref(n) if count else None)
cases = {
    "an empty record": call(a=b""),
    "a null record with a length": call(a=None, a_len=64),
    "a truncated record": call(a=good[:-1]),
    "garbage bytes": call(a=os.urandom(len(good))),
    "a NaN tolerance": call(tol=math.nan),
    "a depth of 65": call(depth=65),
    "no count": call(count=False),
}
print(json.dumps(cases))
'''
proc = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                      env={**os.environ, "GOOD_RECORD": native._encoded(rect).hex()}, timeout=60)
try:
    statuses = json.loads(proc.stdout.strip().splitlines()[-1])
except (IndexError, ValueError):
    statuses = None
check(proc.returncode == 0 and statuses is not None,
      f"malformed calls return instead of crashing (exit {proc.returncode})")
for name, code in (statuses or {}).items():
    want = -1 if name in ("a null record with a length", "no count") else -2
    check(code == want, f"{name}: refused with {code}")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
