#!/usr/bin/env python3
"""Rebuild's least-squares fit in Rust: the same control points as `rebuild._fit`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
adjacent packaged build unless explicitly selected:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_rebuild_fit.py

Parity is exact: `native_rebuild.fit` returns the control points and knots of
`_fit_open` / `_fit_closed`, bit for bit, because Rust repeats `_basis`,
`_least_squares` and `_solve` in their operation order with Python's float semantics
(first-largest pivot, `if v` skipping zeros, float `%`, compensated `sum()`). The
fit IS the rebuilt curve, so nothing weaker than equality is accepted.
"""
import importlib.util
import math
import os
import random
import sys
import time
import types

import rhino3dm as r3
import rebuild_reference as reference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.environ.get("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "narwhal3d", "native", {
        "win32": "narwhal_pure_core.dll", "darwin": "libnarwhal_pure_core.dylib"
    }.get(sys.platform, "libnarwhal_pure_core.so")))
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
native = load("native_rebuild")
rebuild = load("rebuild")
interp = load("interpolate")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def ends_of(crv, n, p, tangent):
    start, end = rebuild._xyz(crv.PointAtStart), rebuild._xyz(crv.PointAtEnd)
    if tangent and p >= 2 and n >= 4:
        return (start, end, rebuild._xyz(crv.TangentAtStart), rebuild._xyz(crv.TangentAtEnd))
    return (start, end)


def python_fit(pts, params, n, p, closed, ends):
    try:
        if closed:
            ctrl, knots = reference._fit_closed(pts, params, n, p)
        else:
            ctrl, knots = reference._fit_open(pts, params, n, p, ends)
        return [tuple(c) for c in ctrl], list(knots)
    except ValueError as exc:
        return ("error", str(exc))


def native_fit(pts, params, n, p, closed, ends):
    got = native.fit([q for _t, q in pts], params, n, p, closed, ends)
    if got == "singular":
        return ("error", rebuild._SINGULAR)
    return got


def case(crv, n, p, closed, tangent, m, jitter=None):
    pts = rebuild._samples(crv, m, closed)
    d = crv.Domain
    params = [(t - d.T0) / (d.T1 - d.T0) for t, _q in pts]
    if jitter is not None:                      # a later fit's corrected parameters
        params = [min(1.0, max(0.0, u + jitter.uniform(-0.01, 0.01))) for u in params]
        if not closed:
            params[0], params[-1] = 0.0, 1.0
    ends = () if closed else ends_of(crv, n, p, tangent)
    return pts, params, n, p, closed, ends


SOURCES = {
    "open cubic, 30 points": cpc([(i * 1.5, math.sin(i * 0.7) * 4, math.cos(i * 0.3)) for i in range(30)]),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "circle": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "closed InterpCrv": interp.interpolate_closed_curve(
        [(5 * math.cos(k), 4 * math.sin(k), 0.3 * math.sin(3 * k)) for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)]),
    "degree 5": cpc([(i, math.sin(i) * 4, 0.3 * i) for i in range(9)], 5),
}

check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- parity: native fit == _fit_open / _fit_closed, bit for bit --")
rng = random.Random(5)
for name, crv in SOURCES.items():
    closed = bool(crv.IsClosed)
    runs = differ = 0
    for n, p in ((4, 3), (8, 3), (12, 2), (20, 3), (7, 5), (5, 1)):
        if (closed and n < 3) or (not closed and n < p + 1):
            continue
        for tangent in ((False,) if closed else (False, True)):
            for jitter in (None, rng):
                args = case(crv, n, p, closed, tangent, max(20 * n, 400), jitter)
                runs += 1
                differ += native_fit(*args) != python_fit(*args)
    check(differ == 0, f"{name}: {runs} fits, {differ} differ")

print("\n-- random sources, counts and degrees --")
rng = random.Random(20261108)
runs = differ = closed_runs = tangent_runs = 0
for _ in range(120):
    k = rng.randint(4, 25)
    pts = [(rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-3, 3)) for _ in range(k)]
    closed = rng.random() < 0.35
    crv = (interp.interpolate_closed_curve(pts[:max(3, k)]) if closed
           else cpc(pts, min(rng.randint(1, 5), k - 1)))
    p = rng.randint(1, 5)
    n = rng.randint(3 if closed else p + 1, 30)
    tangent = not closed and rng.random() < 0.5
    args = case(crv, n, p, closed, tangent, rng.choice((60, 400, 20 * n)), rng if rng.random() < 0.5 else None)
    runs += 1
    closed_runs += closed
    tangent_runs += len(args[5]) == 4
    differ += native_fit(*args) != python_fit(*args)
check(runs == 120 and closed_runs > 25 and tangent_runs >= 15 and differ == 0,
      f"{runs} random fits ({closed_runs} closed, {tangent_runs} with end tangents): {differ} differ")

print("\n-- a singular fit raises the Python's error --")
# Every parameter at 0: only the first basis function is nonzero, so no sample
# constrains the free control points and their normal-equation columns are zero.
pts = [(0.0, (float(i), 0.0, 0.0)) for i in range(50)]
for closed, ends in ((False, ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0))), (True, ())):
    args = (pts, [0.0] * len(pts), 8, 3, closed, ends)
    want = python_fit(*args)
    check(want[0] == "error" and native_fit(*args) == want,
          f"{'closed' if closed else 'open'}, all samples at one parameter: {want[1]!r}")

print("\n-- rebuild_curve matches the independent oracle; missing native refuses --")


def cvs(curve):
    return [(q.X, q.Y, q.Z, q.W) for q in curve.Points], list(curve.Knots)


for name, crv in SOURCES.items():
    for count, tangents in ((10, False), (16, True)):
        before = native._native_calls
        got = rebuild.rebuild_curve(crv, count, 3, preserve_tangents=tangents)
        calls = native._native_calls - before
        real = rebuild._native_fit
        rebuild._native_fit = lambda pts, params, n, p, closed, ends: (reference._fit_closed(pts, params, n, p) if closed else reference._fit_open(pts, params, n, p, ends))
        try:
            want = rebuild.rebuild_curve(crv, count, 3, preserve_tangents=tangents)
        finally:
            rebuild._native_fit = real
        check(calls == rebuild.PARAM_ITERATIONS + 1 and cvs(got[0]) == cvs(want[0]) and got[1] == want[1],
              f"{name} -> {count} points{' (PreserveTangents)' if tangents else ''}: "
              f"identical curve and report through {calls} native fits")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    try:
        native.fit([(0, 0, 0)], [0.0], 4, 3, False, ((0, 0, 0), (1, 0, 0)))
    except native.NativeRebuildError as exc:
        check("unavailable" in str(exc), "missing library is explicitly unavailable")
    else:
        check(False, "missing library must refuse")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

print("\n-- input safety: each risky call runs in a subprocess, so a crash fails a check --")
import json  # noqa: E402
import subprocess  # noqa: E402


def isolated(body):
    """Run `body` in a fresh interpreter; ``(exit code, last stdout line as JSON)``."""
    script = (
        "import ctypes, json, sys, types, importlib.util, os\n"
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
    return proc.returncode, (json.loads(lines[-1]) if lines else None)


direct = isolated(
    "lib = ctypes.CDLL(os.environ['NARWHAL_PURE_CORE'])\n"
    "fn = lib.narwhal_pure_core_rebuild_fit\n"
    "D = ctypes.POINTER(ctypes.c_double)\n"
    "fn.argtypes = [D, D, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ubyte,\n"
    "               ctypes.c_ubyte, D, D, D, ctypes.POINTER(ctypes.c_size_t)]\n"
    "fn.restype = ctypes.c_int32\n"
    "import math\n"
    "def call(n, p, samples=40):\n"
    "    prm = [i / samples for i in range(samples)]\n"
    "    pts = [c for u in prm for c in (math.cos(6.283185307179586 * u), math.sin(6.283185307179586 * u), 0.0)]\n"
    "    P = (ctypes.c_double * len(pts))(*pts); U = (ctypes.c_double * samples)(*prm)\n"
    "    C = (ctypes.c_double * (3 * (n + p)))(); K = (ctypes.c_double * (n + 2 * p))(); m = ctypes.c_size_t()\n"
    "    return fn(P, U, samples, n, p, 1, 0, (ctypes.c_double * 1)(), C, K, ctypes.byref(m)), m.value\n"
    "print(json.dumps({'degree above count': call(3, 5), 'degree equals count': call(3, 3),\n"
    "                  'too large': call(1366, 3, 4)}))\n")
check(direct[0] == 0 and direct[1] == {"degree above count": [-2, 0], "degree equals count": [0, 6],
                                       "too large": [-5, 0]},
      f"direct ABI: a closed degree-5 fit of 3 points is refused, degree == count fits, "
      f"1366 closed points are refused before allocating, no crash ({direct})")

adapter = isolated(
    "native = load('native_rebuild')\n"
    "pts = [(float(i), float(i % 3), 0.0) for i in range(30)]\n"
    "prm = [i / 29 for i in range(30)]\n"
    "ends = ((0.0, 0.0, 0.0), (29.0, 2.0, 0.0))\n"
    "def refused(*args):\n"
    "    try: native.fit(*args); return False\n"
    "    except (ValueError, native.NativeRebuildError): return True\n"
    "cases = {\n"
    "  'fewer parameters than samples': refused(pts, prm[:-5], 6, 3, False, ends),\n"
    "  'more parameters than samples': refused(pts[:-5], prm, 6, 3, False, ends),\n"
    "  'two-coordinate samples': refused([q[:2] for q in pts], prm, 6, 3, False, ends),\n"
    "  'open fit without ends': refused(pts, prm, 6, 3, False, ()),\n"
    "  'open fit with one end': refused(pts, prm, 6, 3, False, ends[:1]),\n"
    "  'two-coordinate end': refused(pts, prm, 6, 3, False, ((0.0, 0.0), (29.0, 2.0))),\n"
    "  'closed fit given ends': refused(pts, prm, 6, 3, True, ends),\n"
    "  'closed degree above count': refused(pts, prm, 3, 5, True, ()),\n"
    "  'tangents at degree 1': refused(pts, prm, 6, 1, False, ends + ends),\n"
    "}\n"
    "ok = native.fit(pts, prm, 6, 3, False, ends)\n"
    "print(json.dumps({'refused': cases,\n"
    "                  'valid call answers': isinstance(ok, tuple)}))\n")
refused = (adapter[1] or {}).get("refused", {})
check(adapter[0] == 0 and refused and all(refused.values()) and adapter[1]["valid call answers"],
      f"the adapter refuses {sum(refused.values())} of 9 malformed calls before ctypes and still "
      f"answers a valid one (exit {adapter[0]}; not refused: {[k for k, v in refused.items() if not v]})")

pts = [(0.0, (math.cos(i / 8), math.sin(i / 8), 0.0)) for i in range(50)]
try:
    rebuild._fit(None, pts, [i / 50 for i in range(50)], 1366, 3, True, False, [])
    too_large = None
except ValueError as exc:
    too_large = str(exc)
check(too_large is not None and "too large" in too_large,
      f"a fit past the bound is refused by name, not solved in Python ({too_large!r})")

if "--no-benchmark" not in sys.argv:
    print("\n-- timing (best of 3) --")


    def best(fn):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            fn()
            out = min(out, time.perf_counter() - start)
        return out


    args = case(SOURCES["open cubic, 30 points"], 40, 3, False, False, 800)
    python = best(lambda: python_fit(*args))
    rust = best(lambda: native_fit(*args))
    print(f"  one 40-point fit of 801 samples: python {python * 1e3:6.1f} ms, rust {rust * 1e3:5.2f} ms "
          f"({python / rust:.0f}x)")
    crv = SOURCES["open cubic, 30 points"]
    fast = best(lambda: rebuild.rebuild_curve(crv, 40, 3))
    real = rebuild._native_fit
    rebuild._native_fit = lambda pts, params, n, p, closed, ends: (reference._fit_closed(pts, params, n, p) if closed else reference._fit_open(pts, params, n, p, ends))
    try:
        slow = best(lambda: rebuild.rebuild_curve(crv, 40, 3))
    finally:
        rebuild._native_fit = real
    print(f"  rebuild_curve to 40 points: python fit {slow * 1e3:6.0f} ms, rust fit {fast * 1e3:6.0f} ms")
    print("  timing is informational, not a correctness gate")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
