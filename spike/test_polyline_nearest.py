#!/usr/bin/env python3
"""Closest points on polylines in Rust: the same answers as the SDK callback search.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_polyline_nearest.py

Polyline and Rectangle make `PolylineCurve`s, which the nearest search answered through
the callback export: Rust searches, but every PointAt and DerivativeAt(t, 2) is a ctypes
callback into rhino3dm (~0.6 ms a query). ``narwhal_pure_core_nearest_on_polyline`` runs
the same search with ``ON_PolylineCurve::Evaluate`` ported, for every target at once.
Parity is exact: every answer (t, point, distance, method, end and ambiguity flags) equals
the callback search's.
"""
import base64
import contextlib
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


native = load("native_nearest")
nearest = load("nearest")
ix = load("intersect")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


P = r3.Point3d


@contextlib.contextmanager
def callbacks_only():
    """The polyline route switched off: the reference callback search answers."""
    native._polyline_binding()
    saved = native._polyline_state
    native._polyline_state = (saved[0], None, "switched off by the test")
    try:
        yield
    finally:
        native._polyline_state = saved


def fields(r):
    return (r.t, tuple(r.point), r.distance, r.method, r.at_end, r.ambiguous)


print("\n-- the route is qualified here --")
capability = native.polyline_capability()
check(capability == "qualified", f"polyline route: {capability}")

print("\n-- a seeded sweep: every answer equals the callback search's, bit for bit --")
rng = random.Random(20261122)


def random_polyline():
    pts = []
    flat = rng.random() < 0.6
    while len(pts) < rng.randint(2, 8):
        p = (rng.uniform(-5, 5), rng.uniform(-5, 5), 0.0 if flat else rng.uniform(-2, 2))
        if rng.random() < 0.2:
            p = tuple(round(v, 1) for v in p)
        if not pts or p != pts[-1]:
            pts.append(p)
    if rng.random() < 0.3 and len(pts) > 2:
        pts.append(pts[0])
    curve = r3.PolylineCurve([P(*p) for p in pts])
    r = rng.random()
    if r < 0.3:
        t0 = rng.uniform(-100, 100)
        curve.Domain = r3.Interval(t0, t0 + rng.uniform(1e-3, 1e3))
    elif r < 0.5:
        d = curve.Domain
        curve = curve.Split(rng.uniform(d.T0, d.T1))[rng.randint(0, 1)]
    return curve


def random_targets(curve, n):
    d = curve.Domain
    out = []
    for _ in range(n):
        if rng.random() < 0.6:
            q = curve.PointAt(d.T0 + (d.T1 - d.T0) * rng.random())
            out.append((q.X + rng.uniform(-0.5, 0.5), q.Y + rng.uniform(-0.5, 0.5),
                        q.Z + rng.uniform(-0.2, 0.2)))
        else:
            out.append((rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(-3, 3)))
    return out


curves = queries = equal = batch_equal = 0
before = native._native_polyline_calls
rust_s = callback_s = 0.0
while curves < 60:
    curve = random_polyline()
    if not curve.IsValid:
        continue
    curves += 1
    targets = random_targets(curve, 8)
    prepared = nearest.PreparedCurve(curve)
    start = time.perf_counter()
    got = [prepared.closest(q) for q in targets]
    many = prepared.closest_many(targets)
    rust_s += time.perf_counter() - start
    with callbacks_only():
        reference = nearest.PreparedCurve(curve)
        start = time.perf_counter()
        want = [reference.closest(q) for q in targets]
        callback_s += time.perf_counter() - start
    queries += len(targets)
    equal += sum(fields(a) == fields(b) for a, b in zip(got, want))
    batch_equal += sum(fields(a) == fields(b) for a, b in zip(many, want))
calls = native._native_polyline_calls - before
check(equal == batch_equal == queries == 480 and calls >= curves,
      f"{equal}/{queries} single and {batch_equal}/{queries} batched answers bit-identical "
      f"({calls} Rust calls); callbacks {callback_s * 1e3:.0f} ms for the single queries, "
      f"Rust {rust_s * 1e3:.0f} ms for single and batched")

print("\n-- callers: closest_point_on_curve and the overlap test --")
rect = r3.PolylineCurve([P(2, -1, 0), P(9, -1, 0), P(9, 1.5, 0), P(2, 1.5, 0), P(2, -1, 0)])
edge = r3.LineCurve(P(3, -1, 0), P(8, -1, 0))
wave = r3.NurbsCurve.CreateControlPointCurve([P(i, math.sin(i * 0.9) * 2, 0)
                                              for i in range(12)], 3)
targets = [(5.0, -1.3, 0.0), (9.4, 0.2, 0.5), (2.0, 1.5, 0.0), (5.5, 0.25, 0.0)]
got = [fields(nearest.closest_point_on_curve(rect, q)) for q in targets]
with callbacks_only():
    want = [fields(nearest.closest_point_on_curve(rect, q)) for q in targets]
check(got == want, "closest_point_on_curve on a Rectangle: the callback search's answers")


def overlap(a, b):
    x = ix._detect_overlap(a, b, 1e-6)
    return None if x is None else (x.point, x.t_a, x.t_b, x.t_a_end, x.t_b_end)


for label, a, b in (("a line on a Rectangle edge", edge, rect), ("a cubic on a Rectangle", wave, rect)):
    before = native._native_polyline_calls
    got = overlap(a, b)
    taken = native._native_polyline_calls - before
    with callbacks_only():
        want = overlap(a, b)
    check(got == want and taken > 0,
          f"overlap test, {label}: the same answer ({'overlap' if want else 'none'}) "
          f"through Rust ({taken} calls)")

print("\n-- routing: refusals keep the callback search and its errors --")
prepared = nearest.PreparedCurve(rect)
try:
    prepared.closest((0.0, math.nan, 0.0))
    raised = None
except Exception as exc:  # noqa: BLE001
    raised = type(exc).__name__
check(raised == "NearestInputError", f"a NaN target still raises NearestInputError ({raised})")
packed = native.pack(rect)
check(isinstance(packed, native.PolylineCallbackCurve) and packed.record is not None,
      "a valid PolylineCurve is packed with its record")
coincident = r3.PolylineCurve([P(0, 0, 0), P(1, 0, 0), P(1, 0, 0), P(2, 1, 0)])
packed = native.pack(coincident)
check(not coincident.IsValid and packed.record is None,
      "an invalid polyline (repeated point) carries no record: SDK callbacks answer it")
before = native._native_polyline_calls
nearest.closest_point_on_curve(edge, (4.0, 1.0, 0.0))
check(native._native_polyline_calls == before, "a LineCurve does not take the polyline route")

print("\n-- timing (i7-9700K: 121 queries on a Rectangle, closest_many 71 -> 0.4 ms) --")
pts = [(rng.uniform(0, 11), rng.uniform(-3, 3), 0.0) for _ in range(121)]


def best(fn, reps=3):
    out = math.inf
    for _ in range(reps):
        start = time.perf_counter()
        fn()
        out = min(out, time.perf_counter() - start)
    return out


fast = best(lambda: nearest.PreparedCurve(rect).closest_many(pts))
with callbacks_only():
    slow = best(lambda: nearest.PreparedCurve(rect).closest_many(pts))
print(f"  closest_many, 121 targets: callbacks {slow * 1e3:.1f} ms, Rust {fast * 1e3:.2f} ms "
      f"({slow / fast:.0f}x)")

print("\n-- input safety: the export runs in a subprocess, so a crash fails a check --")
script = r'''
import ctypes, json, math, os
fn = ctypes.CDLL(os.environ["NARWHAL_PURE_CORE"]).narwhal_pure_core_nearest_on_polyline
D, Z, U = ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_ubyte
fn.argtypes = [ctypes.c_char_p, Z, U, D, Z, D, ctypes.POINTER(U)]
fn.restype = ctypes.c_int32
good = bytes.fromhex(os.environ["GOOD_RECORD"])
def call(record=good, length=None, closed=0, targets=(0.0, 0.0, 0.0), n=None, out=True):
    t = (ctypes.c_double * len(targets))(*targets)
    o, e = (ctypes.c_double * 10)(), (U * 2)()
    return fn(record, len(record or b"") if length is None else length, closed, t,
              len(targets) // 3 if n is None else n, o if out else None, e)
cases = {
    "an empty record": call(record=b""),
    "a null record with a length": call(record=None, length=64),
    "a truncated record": call(record=good[:-1]),
    "garbage bytes": call(record=os.urandom(len(good))),
    "a NaN target": call(targets=(0.0, math.nan, 0.0)),
    "closed flag 2": call(closed=2),
    "a target count past the limit": call(n=100001),
    "no output": call(out=False),
}
print(json.dumps(cases))
'''
proc = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                      env={**os.environ,
                           "GOOD_RECORD": base64.b64decode(rect.Encode()["data"]).hex()},
                      timeout=60)
try:
    statuses = json.loads(proc.stdout.strip().splitlines()[-1])
except (IndexError, ValueError):
    statuses = None
check(proc.returncode == 0 and statuses is not None,
      f"malformed calls return instead of crashing (exit {proc.returncode})")
expected = {"an empty record": 1, "a null record with a length": -1, "a truncated record": 1,
            "garbage bytes": 1, "a NaN target": -2, "closed flag 2": -2,
            "a target count past the limit": -2, "no output": -1}
for name, code in (statuses or {}).items():
    check(code == expected[name], f"{name}: refused with {code}")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
