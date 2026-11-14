#!/usr/bin/env python3
"""Point at arc length in Rust: the same parameters as `arclength._invert`.

Run with the bundled runtime (it carries rhino3dm); the library defaults to the
gate's release build:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_arclength.py

Bit-parity against `_invert` (the full 5315-row golden corpus: named fixtures, 125
random curves, rational/non-rational, shifted domains, all tolerances) is now enforced
by the compiled Rust suite `native/pure_core/tests/arclength_golden.rs`. This host test
keeps the host-only boundary checks Rust cannot see: the library loads, packing, the
document tolerance, non-`NurbsCurve` fallback, refusal/error presentation, rebuild
control-point + report parity, the explicit missing-fit refusal, and the existing
`rust < python` timing criterion.
"""
import importlib.util
import math
import os
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
native = load("native_arclength")
arclength = load("arclength")
interp = load("interpolate")
rebuild = load("rebuild")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def cpc(points, degree=3):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in points], degree)


def python_params(prepared, lengths, tol):
    return [arclength._invert(prepared._curve, prepared._table, s, tol) for s in lengths]


def outcome(fn):
    try:
        return ("value", fn())
    except Exception as exc:  # noqa: BLE001 -- the error itself is compared
        return ("error", type(exc).__name__, str(exc))


FIXTURES = {
    "open cubic, 30 points": cpc([(i * 1.5, math.sin(i * 0.7) * 4, math.cos(i * 0.3)) for i in range(30)]),
    "open cubic, 4 points": cpc([(0, 0, 0), (3, 5, 0), (6, -2, 1), (10, 3, 0)]),
    "open quadratic": cpc([(0, 0, 0), (2, 4, 0), (5, -1, 0), (8, 3, 2), (11, 0, 0)], 2),
    "circle (rational, analytic total)": r3.Circle(r3.Point3d(1, 2, 0), 5.0).ToNurbsCurve(),
    "tilted arc": r3.Arc(r3.Point3d(0, 0, 0), r3.Point3d(3, 4, 2), r3.Point3d(7, 1, -1)).ToNurbsCurve(),
    "straight cubic (analytic total)": cpc([(0, 0, 0), (1, 1, 1), (2, 2, 2), (5, 5, 5)]),
    "InterpCrv, 10 points": interp.interpolate_curve([(i * 2.0, math.sin(i) * 3, 0.0) for i in range(10)]),
    "closed InterpCrv": interp.interpolate_closed_curve(
        [(5 * math.cos(k), 4 * math.sin(k), 0.0) for k in (0.0, 1.1, 2.0, 3.3, 4.1, 5.2)]),
    "degree 5": cpc([(i, math.sin(i) * 4, 0.3 * i) for i in range(9)], 5),
}

check(native.available(), f"the pure-core library loads ({LIBRARY})")

print("\n-- packing: a NurbsCurve packs, the native table is built --")
prepared = arclength.PreparedLength(FIXTURES["open cubic, 30 points"])
total = prepared.total
check(prepared._packed is not None, "a NurbsCurve packs (the native span table is built)")
for s in (-1.0, total + 1.0, total * 2.0):
    got = outcome(lambda: prepared.parameter_at(s))
    want = outcome(lambda: arclength._invert(prepared._curve, prepared._table, s, None))
    check(got == want and got[0] == "error", f"length {s:.4g}: the Python's {got[1]} ({got[2][:48]}...)")
lengths = [total * 0.31, total * 0.77]
check(prepared.parameters_at(lengths) == python_params(prepared, lengths, None),
      "the document tolerance is used when none is given")
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(4, 3, 0))
poly = r3.PolyCurve()
poly.Append(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(4, 0, 0)))
poly.Append(cpc([(4, 0, 0), (5, -3, 1), (8, -1, 0), (10, 2, 0)]))
for name, curve in (("a LineCurve", line), ("a PolyCurve", poly)):
    p = arclength.PreparedLength(curve)
    lengths = [p.total * k / 7 for k in range(8)]
    check(p._packed is None and p.parameters_at(lengths) == python_params(p, lengths, None),
          f"{name} (not a NurbsCurve) keeps the Python inversion")

print("\n-- Rebuild parity with Python inversion; missing fit explicitly refuses --")
source = FIXTURES["open cubic, 30 points"]
before = native._native_calls
with_rust = rebuild.rebuild_curve(source, 12, 3)
calls = native._native_calls - before
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    check(arclength.PreparedLength(source)._packed is None, "without the library nothing is packed")
    from narwhal3d import native_rebuild
    try:
        rebuild.rebuild_curve(source, 12, 3)
    except native_rebuild.NativeRebuildError as error:
        check("unavailable" in str(error), "missing library explicitly refuses native-only fitting")
    else:
        check(False, "missing library must not replay Python fitting")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

# Isolate inversion parity without disabling the now-required numeric fit.
real_pack = arclength._native_pack
arclength._native_pack = lambda curve, table: None
try:
    plain = rebuild.rebuild_curve(source, 12, 3)
finally:
    arclength._native_pack = real_pack


def cvs(curve):
    return [(p.X, p.Y, p.Z, p.W) for p in curve.Points]


check(calls >= 3 and cvs(with_rust[0]) == cvs(plain[0]) and with_rust[1] == plain[1],
      f"rebuild_curve gives the same control points and report ({calls} native batches)")

print("\n-- timing (best of 3) --")


def best(fn, reps=1):
    out = math.inf
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(reps):
            fn()
        out = min(out, (time.perf_counter() - start) / reps)
    return out


lengths = [total * i / 800 for i in range(1, 800)]
python = best(lambda: python_params(prepared, lengths, None))
rust = best(lambda: prepared.parameters_at(lengths))
print(f"  799 lengths on a 30-point cubic: python {python * 1e3:7.1f} ms, rust {rust * 1e3:6.1f} ms "
      f"({python / rust:.1f}x)")
fast = best(lambda: rebuild.rebuild_curve(source, 40, 3))
real_pack = arclength._native_pack
arclength._native_pack = lambda curve, table: None   # only the inversion in Python
try:
    slow = best(lambda: rebuild.rebuild_curve(source, 40, 3))
finally:
    arclength._native_pack = real_pack
print(f"  rebuild_curve to 40 points (Near search native in both): python inversion "
      f"{slow * 1e3:7.1f} ms, rust {fast * 1e3:6.1f} ms ({slow / fast:.1f}x)")
check(rust < python and fast < slow, "the Rust route is faster end to end")

passed = sum(ok for ok, _ in checks)
print(f"\n{passed}/{len(checks)} passed")
sys.exit(0 if passed == len(checks) else 1)
