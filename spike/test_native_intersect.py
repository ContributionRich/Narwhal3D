#!/usr/bin/env python3
"""Curve/curve intersection in Rust: the same hits as `intersect.curve_curve`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_intersect.py

Parity is exact: for two valid `NurbsCurve`s, `curve_curve` returns the same hits
(point, t_a, t_b) through Rust as through `_subdivide` + `_dedupe`, bit for bit. Rust
ports the rhino3dm calls the recursion makes (`Split`, `GetBoundingBox`, `PointAt`,
`Intersection.LineLine`) from the openNURBS rhino3dm 8.35.0 pins, and repeats the
Python helpers with CPython's float semantics. The overlap test stays in Python;
other curve types keep the Python recursion.
"""
import importlib.util
import math
import os
import random
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


load("units")
native = load("native_intersect")
ix = load("intersect")
interp = load("interpolate")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def line(p, q):
    return r3.LineCurve(r3.Point3d(*p), r3.Point3d(*q)).ToNurbsCurve()


def fields(hits):
    return [(h.point, h.t_a, h.t_b, h.overlap, h.t_a_end, h.t_b_end) for h in hits]


def python_curve_curve(a, b, **kw):
    real = ix._native_point_hits
    ix._native_point_hits = lambda *args: None
    try:
        return ix.curve_curve(a, b, **kw)
    finally:
        ix._native_point_hits = real


def same(a, b, **kw):
    """(native route taken, results equal) for `curve_curve(a, b, **kw)`."""
    before = native._native_calls
    got = fields(ix.curve_curve(a, b, **kw))
    routed = native._native_calls > before
    return routed, got == fields(python_curve_curve(a, b, **kw)), len(got)


def circle(c, r):
    return r3.Circle(r3.Point3d(*c), r).ToNurbsCurve()


def tilted_tangencies():
    """A circle in a tilted plane with a line tangent to it, and a second circle
    touching it: chords there are parallel, so `_segment_closest` seeds Newton. Built
    in XY (tangent at (R, 0, 0)), then rotated and moved off the axes together."""
    r = 2.37
    curves = [circle((0, 0, 0), r), line((r, -3.1, 0), (r, 2.3, 0)), circle((r + 1.19, 0, 0), 1.19),
              # 5e-7 clear of the circle: inside a 1e-6 tolerance, so Newton stops short of
              # a zero gap and the hit is the midpoint of the two closest points
              line((r + 5e-7, -3.1, 0), (r + 5e-7, 2.3, 0)),
              circle((r + 1.19 + 5e-7, 0, 0), 1.19)]
    turn = r3.Transform.Rotation(0.83, r3.Vector3d(0.4, -0.7, 0.2), r3.Point3d(0, 0, 0))
    move = r3.Transform.Translation(1.3, -2.7, 0.9)
    for c in curves:
        c.Transform(turn)
        c.Transform(move)
    return curves


tilted, tilted_line, tilted_twin, near_line, near_twin = tilted_tangencies()
wave = cpc([(i, math.sin(i * 0.9) * 3, 0) for i in range(20)])
FIXTURES = {
    "two 20-point cubics": (wave, cpc([(i, math.cos(i * 0.6) * 3, 0) for i in range(20)])),
    "cubic x circle": (wave, circle((8, 0, 0), 4)),
    "circle x circle (rational)": (circle((0, 0, 0), 5), circle((6, 1, 0), 4)),
    "arc x line": (r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(7, 1, 0)).ToNurbsCurve(),
                   line((-1, 2, 0), (9, 2.5, 0))),
    "line x line": (line((0, 0, 0), (4, 4, 0)), line((0, 4, 0), (4, 0, 0))),
    "tangent line on a circle": (circle((0, 0, 0), 2), line((-3, 2, 0), (3, 2, 0))),
    "shared end points": (cpc([(0, 0, 0), (1, 2, 0), (3, 1, 0), (4, 0, 0)]),
                          cpc([(4, 0, 0), (5, -2, 0), (7, 1, 0), (8, 0, 0)])),
    "crossing at the first split parameter": (line((0, 0, 0), (2, 2, 0)), line((0, 2, 0), (2, 0, 0))),
    "tilted circle and its tangent line": (tilted, tilted_line),
    "tilted circles touching": (tilted, tilted_twin),
    "a line 5e-7 clear of a tilted circle": (tilted, near_line),
    "a circle 5e-7 clear of a tilted circle": (tilted, near_twin),
    "skew 3-D, no hit": (cpc([(0, 0, 0), (1, 2, 1), (3, 1, 2), (4, 0, 3)]),
                         cpc([(0, 0, 5), (1, 2, 6), (3, 1, 7), (4, 0, 8)])),
    "quadratic x degree 5": (cpc([(0, -3, 0), (2, 4, 0), (5, -2, 0), (8, 3, 0), (11, -1, 0)], 2),
                            cpc([(i, math.sin(i) * 2, 0) for i in range(12)], 5)),
    "interpolated x closed interpolated": (
        interp.interpolate_curve([(i * 1.5, math.sin(i) * 3, 0.0) for i in range(8)]),
        interp.interpolate_closed_curve([(5 + 3 * math.cos(k), 2 * math.sin(k), 0.0)
                                         for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)])),
    "huge coordinates": (cpc([(1e5 + i * 10, 1e5 + math.sin(i) * 30, 0) for i in range(10)]),
                         circle((1e5 + 40, 1e5, 0), 25)),
}

check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: curve_curve through Rust == the Python recursion, bit for bit --")
for name, (a, b) in FIXTURES.items():
    results = [same(a, b, tol=tol, max_depth=depth) for tol, depth in ((1e-6, 40), (1e-3, 40), (1e-9, 40), (1e-6, 12))]
    check(all(r[0] and r[1] for r in results),
          f"{name}: {[r[2] for r in results]} hits at 4 tolerance/depth settings, all equal")

print("\n-- random curve pairs: degrees 1-5, rational, multiple knots, 2-D and 3-D --")


def random_curve(r):
    degree = r.randint(1, 5)
    count = r.randint(degree + 1, degree + 10)
    rational = r.random() < 0.4
    curve = r3.NurbsCurve(3, rational, degree + 1, count)
    flat = r.random() < 0.7
    for i in range(count):
        x, y = r.uniform(-10, 10), r.uniform(-10, 10)
        z = 0.0 if flat else r.uniform(-2, 2)
        w = r.uniform(0.3, 3.0) if rational else 1.0
        curve.Points[i] = r3.Point4d(x * w, y * w, z * w, w)
    t0 = r.choice((0.0, -2.5, 7.0))
    t1 = t0 + r.choice((1.0, 10.0, r.uniform(0.5, 40)))
    interior = sorted(r.uniform(t0, t1) for _ in range(count - degree - 1))
    for i in range(1, len(interior)):
        if r.random() < 0.25:
            interior[i] = interior[i - 1]
    knots = [t0] * degree + interior + [t1] * degree
    for i, k in enumerate(knots):
        curve.Knots[i] = k
    return curve


rng = random.Random(20261108)
pairs = differ = routed = hits = rationals = 0
while pairs < 200:
    a, b = random_curve(rng), random_curve(rng)
    if not (a.IsValid and b.IsValid):
        continue
    pairs += 1
    rationals += a.IsRational or b.IsRational
    took, equal, n = same(a, b)
    routed += took
    differ += not equal
    hits += n
check(pairs == 200 and routed == 200 and rationals > 50 and hits > 300 and differ == 0,
      f"{pairs} pairs ({rationals} with a rational curve), {hits} hits: {differ} differ, {routed} routed")

print("\n-- what stays in Python --")
poly = r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 0), r3.Point3d(6, 0, 0)])
took, equal, n = same(poly, circle((3, 1, 0), 2))
check(not took and equal, f"a PolylineCurve keeps the Python recursion ({n} hits)")
bent = cpc([(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)])
straight = line((1, -1, 0), (1, 0, 0))
over_a, over_b = line((0, 0, 0), (4, 0, 0)), line((1, 0, 0), (6, 0, 0))
before = native._native_calls
got = ix.curve_curve(over_a, over_b)
check(native._native_calls == before and len(got) == 1 and got[0].overlap,
      "an overlap is found by the Python overlap test before any recursion")
check(same(bent, straight)[1], "a collinear cubic touching a line end gives the same answer")
try:
    ix.curve_curve(wave, wave)
    refused = False
except ValueError:
    refused = True
check(refused, "self-intersection (a is b) is still refused")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    a, b = FIXTURES["cubic x circle"]
    before = native._native_calls
    check(native._native_calls == before and fields(ix.curve_curve(a, b)) == fields(python_curve_curve(a, b)),
          "without the library the Python recursion answers")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- Newton's fixed-point exit (main's witness) --")
# Main's `_newton` stops once an iterate repeats; the Rust loop mirrors it. The skew lines
# of test_trim_split_once.py never meet, and their iterate settles at a clamp after one
# step. With tol 2 the boxes overlap, so the recursion reaches Newton on them (curve_curve's
# overlap test would claim the pair first, so the recursion is called directly).
skew_a, skew_b = line((0, 0, 0), (10, 0, 0)), line((5, 1, 0), (5, 11, 0))
calls = 0
real_velocity = ix._velocity


def counting_velocity(c, t):
    global calls
    calls += 1
    return real_velocity(c, t)


ix._velocity = counting_velocity
try:
    python_skew = []
    ix._subdivide(skew_a, skew_b, 2.0, 0, 40, python_skew)
    python_skew = [(h.point, h.t_a, h.t_b) for h in ix._dedupe(python_skew, 2.0)]
finally:
    ix._velocity = real_velocity
rust_skew = native.point_hits(skew_a, skew_b, 2.0, 40)
check(len(python_skew) == 1 and rust_skew == python_skew and 0 < calls <= 4,
      f"skew lines 1 apart at tol 2: one hit, the same through Rust, and the Python stops "
      f"at the fixed point ({calls} velocity evaluations; {rust_skew} vs {python_skew})")

print("\n-- input safety: each risky call runs in a subprocess, so a crash fails a check --")
import json  # noqa: E402
import subprocess  # noqa: E402


def isolated(body):
    """Run `body` in a fresh interpreter; ``(exit code, last stdout line as JSON)``."""
    script = (
        "import ctypes, json, math, sys, types, importlib.util, os\n"
        f"ROOT = {ROOT!r}\n"
        "pkg = types.ModuleType('narwhal3d'); pkg.__path__ = [os.path.join(ROOT, 'narwhal3d')]\n"
        "sys.modules['narwhal3d'] = pkg\n"
        "def load(name):\n"
        "    spec = importlib.util.spec_from_file_location('narwhal3d.' + name, os.path.join(ROOT, 'narwhal3d', name + '.py'))\n"
        "    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; setattr(pkg, name, mod)\n"
        "    spec.loader.exec_module(mod); return mod\n"
        + body)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=dict(os.environ), timeout=120)
    lines = proc.stdout.strip().splitlines()
    try:
        return proc.returncode, (json.loads(lines[-1]) if lines else None)
    except ValueError:
        return proc.returncode, None


# Two crossing lines in openNURBS form; each case changes one argument of the first.
direct = isolated(
    "lib = ctypes.CDLL(os.environ['NARWHAL_PURE_CORE'])\n"
    "fn = lib.narwhal_pure_core_curve_curve_hits\n"
    "D = ctypes.POINTER(ctypes.c_double); Z = ctypes.c_size_t\n"
    "curve = [Z, D, Z, D, Z, ctypes.c_ubyte]\n"
    "fn.argtypes = curve + curve + [ctypes.c_double, Z, D, Z, ctypes.POINTER(Z)]\n"
    "fn.restype = ctypes.c_int32\n"
    "def arr(v): return (ctypes.c_double * len(v))(*v) if v is not None else None\n"
    "B = (2, arr([0.0, 1.0]), 2, arr([-1.0, 0, 0, 1, 1, 0, 0, 1]), 2, 0)\n"
    "def call(order=2, knots=(0.0, 1.0), cvs=(0, -1, 0, 1, 0, 1, 0, 1), rational=0,\n"
    "         knot_count=None, cv_count=None, tol=1e-6, depth=40, out=5, cap=1, count=True):\n"
    "    k = arr(list(knots) if knots is not None else None)\n"
    "    c = arr([float(x) for x in cvs] if cvs is not None else None)\n"
    "    n = ctypes.c_size_t(99)\n"
    "    code = fn(order, k, len(knots or ()) if knot_count is None else knot_count, c,\n"
    "              len(cvs or ()) // 4 if cv_count is None else cv_count, rational, *B, tol, depth,\n"
    "              arr([0.0] * out) if out else None, cap, ctypes.byref(n) if count else None)\n"
    "    return code\n"
    "inf, nan = math.inf, math.nan\n"
    "cases = {\n"
    "  'decreasing knots': call(knots=(1.0, 0.0)),\n"
    "  'an empty domain': call(knots=(1.0, 1.0)),\n"
    "  'a NaN knot': call(knots=(0.0, nan)),\n"
    "  'an infinite control point': call(cvs=(inf, -1, 0, 1, 0, 1, 0, 1)),\n"
    "  'a zero weight': call(cvs=(0, -1, 0, 0, 0, 1, 0, 1), rational=1),\n"
    "  'a negative weight': call(cvs=(0, -1, 0, 1, 0, 1, 0, -1), rational=1),\n"
    "  'a knot too many': call(knots=(0.0, 1.0, 2.0)),\n"
    "  'a knot count past the buffer': call(knot_count=1 << 40),\n"
    "  'a point count past the buffer': call(cv_count=1 << 40),\n"
    "  'order 1': call(order=1, knots=()),\n"
    "  'more order than points': call(order=3, knots=(0.0, 0.0, 1.0)),\n"
    "  'order 33': call(order=33),\n"
    "  'rational flag 2': call(rational=2),\n"
    "  'max_depth 65': call(depth=65),\n"
    "  'max_depth 2**63': call(depth=1 << 63),\n"
    "  'a NaN tolerance': call(tol=nan),\n"
    "  'an infinite tolerance': call(tol=inf),\n"
    "  'null knots with a count': call(knots=None, knot_count=2),\n"
    "  'null points with a count': call(cvs=None, cv_count=2),\n"
    "  'no count pointer': call(count=False),\n"
    "  'a hit with nowhere to write it': call(out=0, cap=1),\n"
    "}\n"
    "print(json.dumps({'cases': cases, 'valid': call(), 'depth 64': call(depth=64),\n"
    "                  'no room': call(cap=0)}))\n")
expected = {"decreasing knots": -2, "an empty domain": -2, "a NaN knot": -2,
            "an infinite control point": -2, "a zero weight": -2, "a negative weight": -2,
            "a knot too many": -2, "a knot count past the buffer": -2,
            "a point count past the buffer": -2, "order 1": -2, "more order than points": -2,
            "order 33": -2, "rational flag 2": -2, "max_depth 65": -2, "max_depth 2**63": -2,
            "a NaN tolerance": -2, "an infinite tolerance": -2, "null knots with a count": -1,
            "null points with a count": -1, "no count pointer": -1,
            "a hit with nowhere to write it": -1}
got = (direct[1] or {}).get("cases", {})
wrong = {k: got.get(k) for k in expected if got.get(k) != expected[k]}
check(direct[0] == 0 and not wrong and direct[1]["valid"] == 0 and direct[1]["depth 64"] == 0
      and direct[1]["no room"] == -3,
      f"direct ABI: {len(expected)} malformed calls are refused, a valid one (and depth 64) "
      f"answers, a full buffer reports its size, no crash (exit {direct[0]}; wrong: {wrong})")

adapter = isolated(
    "import rhino3dm as r3\n"
    "native = load('native_intersect')\n"
    "def line(p, q): return r3.LineCurve(r3.Point3d(*p), r3.Point3d(*q)).ToNurbsCurve()\n"
    "a, b = line((0, -1, 0), (0, 1, 0)), line((-1, 0, 0), (1, 0, 0))\n"
    "broken = r3.NurbsCurve(3, False, 3, 4)\n"
    "cases = {\n"
    "  'max_depth -1': native.point_hits(a, b, 1e-6, -1),\n"
    "  'max_depth 65': native.point_hits(a, b, 1e-6, 65),\n"
    "  'max_depth 2**70': native.point_hits(a, b, 1e-6, 2 ** 70),\n"
    "  'max_depth 40.5': native.point_hits(a, b, 1e-6, 40.5),\n"
    "  'max_depth \"40\"': native.point_hits(a, b, 1e-6, '40'),\n"
    "  'max_depth None': native.point_hits(a, b, 1e-6, None),\n"
    "  'tol NaN': native.point_hits(a, b, math.nan, 40),\n"
    "  'tol inf': native.point_hits(a, b, math.inf, 40),\n"
    "  'tol \"x\"': native.point_hits(a, b, 'x', 40),\n"
    "  'tol None': native.point_hits(a, b, None, 40),\n"
    "  'a point, not a curve': native.point_hits(r3.Point3d(0, 0, 0), b, 1e-6, 40),\n"
    "  'an invalid NurbsCurve': native.point_hits(broken, b, 1e-6, 40),\n"
    "}\n"
    "ok = native.point_hits(a, b, 1e-6, 40)\n"
    "print(json.dumps({'refused': {k: v is None for k, v in cases.items()},\n"
    "                  'valid call answers': ok == [((0.0, 0.0, 0.0), 1.0, 1.0)]}))\n")
refused = (adapter[1] or {}).get("refused", {})
check(adapter[0] == 0 and len(refused) == 12 and all(refused.values())
      and adapter[1]["valid call answers"],
      f"the adapter refuses {sum(refused.values())} of 12 malformed calls before ctypes and "
      f"still answers a valid one (exit {adapter[0]}; not refused: "
      f"{[k for k, v in refused.items() if not v]})")

print("\n-- timing: the recursion alone (best of 3) --")


def best(fn, reps=1):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


faster = True
for name in ("two 20-point cubics", "cubic x circle", "circle x circle (rational)"):
    a, b = FIXTURES[name]

    def python_recursion():
        h = []
        ix._subdivide(a, b, 1e-6, 0, 40, h)
        return ix._dedupe(h, 1e-6)

    python = best(python_recursion)
    rust = best(lambda: native.point_hits(a, b, 1e-6, 40), 10)
    faster &= rust < python
    print(f"  {name}: python {python * 1e3:6.1f} ms, rust {rust * 1e3:5.2f} ms ({python / rust:.0f}x)")
check(faster, "the Rust recursion is faster end to end")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
