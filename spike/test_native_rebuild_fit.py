#!/usr/bin/env python3
"""Rebuild end-to-end qualification over the captured golden fixture.

The numeric fit is owned by Rust (`narwhal_pure_core_rebuild_fit`); the expected
rebuild_curve outputs were captured ONCE from the retired independent Python
reference (`spike/rebuild_reference.py`) into
``native/pure_core/tests/fixtures/rebuild_e2e.txt`` and are replayed here with
the REAL native fit. This test retains the host-only concerns: rhino3dm curve
construction, arc-length sampling, the 7-native-fit parameter-correction
pipeline, the missing-library refusal, and the ctypes input-safety refusals
(each risky call in a subprocess, so a crash fails a check). No Python fit
engine remains at test time.
"""
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.environ.get("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "narwhal3d", "native", {
        "win32": "narwhal_pure_core.dll", "darwin": "libnarwhal_pure_core.dylib"
    }.get(sys.platform, "libnarwhal_pure_core.so")))
FIXTURE = os.path.join(ROOT, "native", "pure_core", "tests", "fixtures", "rebuild_e2e.txt")
PROVENANCE = "spike/rebuild_reference.py"
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


SOURCES = {
    "open cubic, 30 points": cpc([(i * 1.5, math.sin(i * 0.7) * 4, math.cos(i * 0.3)) for i in range(30)]),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "circle": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "closed InterpCrv": interp.interpolate_closed_curve(
        [(5 * math.cos(k), 4 * math.sin(k), 0.3 * math.sin(3 * k)) for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)]),
    "degree 5": cpc([(i, math.sin(i) * 4, 0.3 * i) for i in range(9)], 5),
}


def f64(field):
    """Decode one 16-hex little-endian f64 field to its exact float value."""
    return struct.unpack('<d', bytes.fromhex(field))[0]


def load_fixture(path):
    """Decode the e2e fixture, re-deriving its checksum (a tampered file fails)."""
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().splitlines()

    def field(name):
        return next(l.split('=', 1)[1] for l in lines if l.startswith(f'# {name}='))

    count = int(field('count'))
    checksum = field('checksum')
    assert field('provenance') == PROVENANCE, "fixture must trace to the retired reference"
    data = [l for l in lines if not l.startswith('#')]
    assert len(data) == count, f"fixture has {len(data)} rows, header says {count}"
    section = ('\n'.join(data) + '\n').encode('utf-8')
    h = 0xcbf29ce484222325
    for b in section:
        h ^= b
        h = (h * 0x00000100000001b3) & 0xFFFFFFFFFFFFFFFF
    assert format(h, '016x') == checksum, "fixture checksum mismatch (tampered)"

    rows = {}
    for line in data:
        f = line.split('\t')
        # group, idx, src, count, tangents, point_count, degree, closed,
        # deviation, n_ctrl, cvs, n_knots, knots, notes
        assert f[0] == 'e2e', f"unexpected group {f[0]!r}"
        rows[(f[2], int(f[3]), int(f[4]))] = {
            'point_count': int(f[5]), 'degree': int(f[6]), 'closed': bool(int(f[7])),
            'deviation': f64(f[8]),
            'cvs': [f64(x) for x in f[10].split(',')],
            'knots': [f64(x) for x in f[12].split(',')],
            'notes': json.loads(f[13]),
        }
    assert len(rows) == count, "e2e rows must be distinct by (source, count, tangents)"
    return rows


def cvs(curve):
    return [(q.X, q.Y, q.Z, q.W) for q in curve.Points], list(curve.Knots)


check(native.available(), f"the pure-core library loads ({LIBRARY})")
expected = load_fixture(FIXTURE)

print("\n-- rebuild_curve matches the captured reference, through the REAL native fit --")
for name, crv in SOURCES.items():
    for count, tangents in ((10, False), (16, True)):
        want = expected[(name, count, int(tangents))]
        before = native._native_calls
        got_curve, got_report = rebuild.rebuild_curve(crv, count, 3, preserve_tangents=tangents)
        calls = native._native_calls - before
        got_cvs, got_knots = cvs(got_curve)
        want_report = {
            'point_count': want['point_count'], 'degree': want['degree'],
            'closed': want['closed'], 'deviation': want['deviation'], 'notes': want['notes']}
        ok = (calls == rebuild.PARAM_ITERATIONS + 1
              and [v for pt in got_cvs for v in pt] == want['cvs']
              and got_knots == want['knots']
              and got_report == want_report)
        check(ok, f"{name} -> {count} points{' (PreserveTangents)' if tangents else ''}: "
                  f"identical curve and report through {calls} native fits")

print("\n-- missing library is explicitly unavailable --")
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

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
