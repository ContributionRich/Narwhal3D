#!/usr/bin/env python3
"""Native Bezier endpoint-derivative solver: parity and correctness.

Runs `blend.blend_bezier_points` and proves it against two independent
standards, using a SOURCE-BUILT pure-core library (no scratch / oracle files):

  1. INDEPENDENT RECONSTRUCTION -- the produced control points, read back with a
     separate Bernstein end-derivative formula (the n!/(n-j)! Delta^j identities
     applied forward, not the solver's difference solve), reproduce the requested
     endpoint derivatives `lambda^j D_j` at t=0 and t=1 for ALL 25 continuity
     pairs (k0,k1 in 0..=4), i.e. up to G4. This checks the math, not the code.
  2. BIT-FOR-BIT DIFFERENTIAL -- the native route returns the control points a
     test-only PURE-PYTHON reference (the saved original loop, embedded below so
     this file never imports a private oracle) returns, bit for bit, over seeded
     random derivative lists, reversed / oriented ends, and negative / zero speed
     scales.
  3. DISPATCH THRESHOLD -- the public wrapper routes to native only for
     k0 + k1 >= 4 (the measured break-even), so low-order blends stay on the
     Python loop and high-order blends run native; a native execution failure
     still raises and is never replayed through Python.

The openNURBS derivative extraction and `NurbsCurve` construction stay in Python
(this never samples, meshes or approximates). A malformed / non-finite input is
refused by the native layer with NO partial output.

The library under test must export the ABI version symbol (returning 1) and the
blend symbol; the source build of `native/pure_core` provides both. Run:

  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_native_blend.py

Benchmarks are off by default; pass `--benchmark` to time the public wrapper
against the pure-Python reference.
"""
import importlib.util
import math
import os
import random
import struct
import sys
import time
import types
from unittest.mock import patch

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Source-built pure-core library (exports the ABI version and the blend symbol).
# Override with NARWHAL_PURE_CORE when a different build is in play.
LIBRARY = os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "native", "pure_core", "target", "x86_64-unknown-linux-gnu",
    "release", "libnarwhal_pure_core.so"))
BENCHMARK = "--benchmark" in sys.argv

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name, path):
    spec = importlib.util.spec_from_file_location(f"narwhal3d.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


native = load("native_blend", os.path.join(ROOT, "narwhal3d", "native_blend.py"))
blend = load("blend", os.path.join(ROOT, "narwhal3d", "blend.py"))

check_calls = {"n": 0, "ok": 0}


def check(ok, label):
    check_calls["n"] += 1
    check_calls["ok"] += bool(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


# ---------------------------------------------------------------------------
# TEST-ONLY reference solver: the ORIGINAL pure-Python endpoint-derivative loop,
# embedded here so this file is self-contained and never imports a private
# oracle. It mirrors the historical Python implementation operation for
# operation (same scalar order) so it is bit-for-bit the reference.
# ---------------------------------------------------------------------------

def _ref_sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _ref_mul(a, s):
    return tuple(x * s for x in a)


def _reference_blend(p0, p1, k0, k1, lam0, lam1):
    """The ORIGINAL pure-Python control-point solve (test-only reference)."""
    n = k0 + k1 + 1
    pts = [None] * (n + 1)
    pts[0] = p0[0]
    pts[n] = p1[0]

    def falling(j):
        out = 1.0
        for i in range(j):
            out *= (n - i)
        return out

    for j in range(1, k0 + 1):
        target = _ref_mul(p0[j], lam0 ** j / falling(j))    # = Delta^j P_0
        acc = target
        for i in range(j):
            acc = _ref_sub(acc, _ref_mul(pts[i], (-1.0) ** (j - i) * math.comb(j, i)))
        pts[j] = acc
    for j in range(1, k1 + 1):
        target = _ref_mul(p1[j], lam1 ** j / falling(j))    # = nabla^j P_n
        acc = target
        for i in range(j):
            acc = _ref_sub(acc, _ref_mul(pts[n - i], (-1.0) ** i * math.comb(j, i)))
        pts[n - j] = _ref_mul(acc, (-1.0) ** j)
    return pts


def _reference_blend_curves(curve1, end1, curve2, end2, cont1, cont2,
                            flip1=False, flip2=False):
    """A pure-Python `blend_curves` that uses the reference solver (no native)."""
    k0, k1 = blend.ORDER[cont1], blend.ORDER[cont2]
    a = blend.end_derivatives(curve1, end1 == "end", k0, outward=True)
    b = blend.end_derivatives(curve2, end2 == "end", k1, outward=False)
    if flip1:
        a = [v if j % 2 == 0 else _ref_mul(v, -1.0) for j, v in enumerate(a)]
    if flip2:
        b = [v if j % 2 == 0 else _ref_mul(v, -1.0) for j, v in enumerate(b)]
    chord = blend._norm(blend._sub(b[0], a[0]))
    if chord <= 1e-12:
        raise ValueError("the two ends are at the same point")
    lam = []
    for derivs, k, which in ((a, k0, "first"), (b, k1, "second")):
        if k == 0:
            lam.append(1.0)
            continue
        speed = blend._norm(derivs[1])
        if speed <= 1e-14:
            raise ValueError(f"the {which} curve has no tangent at that end")
        lam.append(chord / speed)
    pts = _reference_blend(a, b, k0, k1, lam[0], lam[1])
    return blend.bezier_curve(pts), {"degree": k0 + k1 + 1, "lambda": tuple(lam)}


# ---------------------------------------------------------------------------
# independent helpers
# ---------------------------------------------------------------------------

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def bernstein_deriv_0(pts, j):
    """The j-th derivative of the Bezier at t = 0, straight from the control points.

    B^(j)(0) = [n!/(n-j)!] * sum_{i=0}^{j} (-1)^(j-i) C(j,i) P_i, n = len(pts)-1.

    An INDEPENDENT formula: it does not reuse the solver's forward / backward
    differences, so it checks the math of the produced control points.
    """
    n = len(pts) - 1
    lead = 1.0
    for i in range(j):
        lead *= (n - i)
    s = (0.0, 0.0, 0.0)
    for i in range(0, j + 1):
        s = _add(s, _mul(pts[i], lead * ((-1.0) ** (j - i)) * math.comb(j, i)))
    return s


def bernstein_deriv_1(pts, j):
    """The j-th derivative of the Bezier at t = 1, straight from the control points.

    B^(j)(1) = [n!/(n-j)!] * sum_{i=0}^{j} (-1)^i C(j,i) P_{n-i}, n = len(pts)-1.
    """
    n = len(pts) - 1
    lead = 1.0
    for i in range(j):
        lead *= (n - i)
    s = (0.0, 0.0, 0.0)
    for i in range(0, j + 1):
        s = _add(s, _mul(pts[n - i], lead * ((-1.0) ** i) * math.comb(j, i)))
    return s


def close(a, b, tol=1e-10):
    return all(abs(x - y) <= tol * max(1.0, abs(x), abs(y)) for x, y in zip(a, b))


def rand_vec(rng, scale=1.0):
    return (rng.uniform(-scale, scale), rng.uniform(-scale, scale), rng.uniform(-scale, scale))


def rand_derivs(rng, k, scale=1.0):
    """[P, D1, ..., Dk] with a nonzero tangent (realistic endpoint data)."""
    d = [rand_vec(rng, scale)]
    for _ in range(k):
        v = rand_vec(rng, scale)
        if v == (0.0, 0.0, 0.0):
            v = (scale, 0.0, 0.0)
        d.append(v)
    return d


def bits_match(a, b):
    """Exact (bit-for-bit) float equality, component by component."""
    if len(a) != len(b):
        return False
    return all(len(xa) == len(ya) and all(struct.pack("=d", x) == struct.pack("=d", y)
               for x, y in zip(xa, ya)) for xa, ya in zip(a, b))


# ---------------------------------------------------------------------------
# 0. the binding is present (ABI version 1 + blend symbol)
# ---------------------------------------------------------------------------
check(native.available(),
      f"the pure-core library loads with the blend symbol + ABI v1 ({LIBRARY})")


class StubFunction:
    def __init__(self, result):
        self.result = result

    def __call__(self, *args):
        return self.result


# Missing/wrong ABI or missing export must choose compatibility before execution.
for version, has_solver, admitted in ((None, True, False), (2, True, False),
                                     (1, False, False), (1, True, True)):
    library = types.SimpleNamespace()
    if version is not None:
        library.narwhal_pure_core_abi_version = StubFunction(version)
    if has_solver:
        library.narwhal_pure_core_blend_bezier_points = StubFunction(0)
    try:
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": f"/fake-blend-{version}-{has_solver}"}), \
                patch.object(native.Path, "is_file", return_value=True), \
                patch.object(native.ctypes, "CDLL", return_value=library):
            check(native.available() is admitted,
                  f"loader qualification: version={version}, solver={has_solver}")
    finally:
        native._signature, native._binding = None, None
check(native.available(), "real source library requalified after loader probes")


def direct_native(p0, p1, k0, k1, lam0, lam1):
    return native.blend_bezier_points([v for d in p0 for v in d],
                                      [v for d in p1 for v in d],
                                      k0, k1, lam0, lam1)

# ---------------------------------------------------------------------------
# 1. INDEPENDENT RECONSTRUCTION -- every one of the 25 continuity pairs, up to G4
# ---------------------------------------------------------------------------
print("\n-- independent reconstruction: B^(j) at both ends == lambda^j D_j, all 25 (k0,k1) --")
rng = random.Random(20261110)
recon_bad = recon_cases = 0
for k0 in range(5):
    for k1 in range(5):
        for _ in range(4):
            p0 = rand_derivs(rng, k0)
            p1 = rand_derivs(rng, k1)
            lam0 = rng.choice((0.0, -1.0, 0.5, 1.0, 1.7, 3.2))
            lam1 = rng.choice((0.0, -1.0, 0.5, 1.0, 1.7, 3.2))
            pts = direct_native(p0, p1, k0, k1, lam0, lam1)
            assert pts is not None
            recon_cases += 1
            ok = True
            for j in range(0, k0 + 1):
                ok = ok and close(bernstein_deriv_0(pts, j), _mul(p0[j], lam0 ** j))
            for j in range(0, k1 + 1):
                ok = ok and close(bernstein_deriv_1(pts, j), _mul(p1[j], lam1 ** j))
            if not ok:
                recon_bad += 1
check(recon_bad == 0,
      f"{recon_cases} endpoints over all 25 (k0,k1) pairs: {recon_bad} derivative mismatches "
      f"(max order G4)")

# ---------------------------------------------------------------------------
# 2. BIT-FOR-BIT DIFFERENTIAL against the embedded pure-Python reference (seeded)
# ---------------------------------------------------------------------------
print("\n-- bit-for-bit parity: public wrapper == pure-Python reference (seeded) --")
rng = random.Random(77)
parity_cases = parity_bad = 0
for _ in range(400):
    k0 = rng.randint(0, 4)
    k1 = rng.randint(0, 4)
    p0 = rand_derivs(rng, k0, rng.choice((0.01, 1.0, 50.0)))
    p1 = rand_derivs(rng, k1, rng.choice((0.01, 1.0, 50.0)))
    lam0 = rng.uniform(-4.0, 4.0)
    lam1 = rng.uniform(-4.0, 4.0)
    got = blend.blend_bezier_points(p0, p1, k0, k1, lam0, lam1)
    want = _reference_blend(p0, p1, k0, k1, lam0, lam1)
    parity_cases += 1
    if not bits_match(got, want) or not bits_match(
            direct_native(p0, p1, k0, k1, lam0, lam1), want):
        parity_bad += 1
check(parity_bad == 0, f"{parity_cases} seeded blends: {parity_bad} bit-for-bit differences")

# ---------------------------------------------------------------------------
# 3. orientation / reversed ends / negative / zero scales (all bit-for-bit)
# ---------------------------------------------------------------------------
print("\n-- orientation, reversed ends, negative and zero scales (bit-for-bit) --")
rng = random.Random(31337)
orient_cases = orient_bad = 0
for _ in range(120):
    k0 = rng.randint(0, 4)
    k1 = rng.randint(0, 4)
    p0 = rand_derivs(rng, k0)
    p1 = rand_derivs(rng, k1)
    lam0 = rng.choice((0.0, -1.0, 0.0, 1.0))     # negative and zero scales
    lam1 = rng.choice((0.0, -1.0, 0.0, 1.0))
    if rng.random() < 0.5:
        p1 = [v if j % 2 == 0 else _mul(v, -1.0) for j, v in enumerate(p1)]
    got = blend.blend_bezier_points(p0, p1, k0, k1, lam0, lam1)
    want = _reference_blend(p0, p1, k0, k1, lam0, lam1)
    orient_cases += 1
    if not bits_match(got, want):
        orient_bad += 1
check(orient_bad == 0, f"{orient_cases} orientation/negative/zero-scale blends: {orient_bad} differ")

# a negative scale genuinely changes the blend (it is a real supported input, not a no-op)
p0 = [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0)]
p1 = [(6.0, 0.0, 0.0), (-3.0, 0.0, 0.0)]
pos = blend.blend_bezier_points(p0, p1, 1, 1, 1.0, 1.0)
neg = blend.blend_bezier_points(p0, p1, 1, 1, -1.0, 1.0)
check(pos != neg and bits_match(pos, _reference_blend(p0, p1, 1, 1, 1.0, 1.0))
      and bits_match(neg, _reference_blend(p0, p1, 1, 1, -1.0, 1.0)),
      "a negative speed scale changes the blend and still matches the reference")

# ---------------------------------------------------------------------------
# 4. DISPATCH THRESHOLD -- native only for k0+k1 >= 4; low order stays Python
# ---------------------------------------------------------------------------
print("\n-- dispatch threshold: native used only for k0+k1 >= 4 (the measured break-even) --")
_real_native = native.blend_bezier_points
_native_hits = {"n": 0}


def _spy_native(*a, **kw):
    _native_hits["n"] += 1
    return _real_native(*a, **kw)


def _native_called(p0, p1, k0, k1, lam0=1.0, lam1=1.0):
    native.blend_bezier_points = _spy_native
    _native_hits["n"] = 0
    blend.blend_bezier_points(p0, p1, k0, k1, lam0, lam1)
    native.blend_bezier_points = _real_native
    return _native_hits["n"]


good2 = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
good4 = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 3.0), (0.0, 0.0, 0.5)]
check(not _native_called(good2, good2, 0, 0),
      "Position/Position (k0+k1=0) is routed to the Python loop, not native")
check(not _native_called(good2, good2, 1, 1),
      "Tangency/Tangency (k0+k1=2) is routed to the Python loop, not native")
check(_native_called(good4, good4, 2, 2) >= 1,
      "Curvature/Curvature (k0+k1=4) is routed to native")
check(_native_called(good4, good4, 4, 4) >= 1,
      "G4/G4 (k0+k1=8) is routed to native")

# ---------------------------------------------------------------------------
# 5. malformed / non-finite: refused, NO partial output, and it RAISES (no replay)
# ---------------------------------------------------------------------------
print("\n-- malformed / non-finite inputs: refused without partial output --")
good = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]


def flat(p):
    return [v for d in p for v in d]


# k beyond the supported maximum -> native declines (the Python loop may still answer)
check(native.blend_bezier_points(flat(good), flat(good), 5, 1, 1.0, 1.0) is None,
      "k=5 (beyond G4) is declined by the native route")
# negative k -> declined, never a ctypes out-of-range crash
check(native.blend_bezier_points(flat(good), flat(good), -1, 1, 1.0, 1.0) is None,
      "negative k is declined by the native route")
# non-integer k -> declined, never a ctypes out-of-range crash
check(native.blend_bezier_points(flat(good), flat(good), 1.0, 1, 1.0, 1.0) is None,
      "a non-integer k is declined by the native route")
# wrong span length -> declined
check(native.blend_bezier_points([0.0, 0.0, 0.0, 1.0], flat(good), 1, 1, 1.0, 1.0) is None,
      "a wrong derivative-list length is declined by the native route")
# non-finite lambda -> the native route refuses, and blend raises (never silently
# replays the failure through the Python loop) -- but only when it is actually
# routed to native (k0+k1 >= 4); below the threshold the Python loop is used.
bad_p0 = [(0.0, float("nan"), 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 2.0)]
nan_raised = False
try:
    blend.blend_bezier_points(bad_p0, good4, 2, 2, 1.0, 1.0)   # k0+k1=4 -> native
except native.NativeBlendError:
    nan_raised = True
check(nan_raised, "a non-finite derivative at native order raises (never replayed through Python)")
with patch.object(native, "_blend_binding", return_value=StubFunction(-99)):
    try:
        blend.blend_bezier_points(good4, good4, 2, 2, 1.0, 1.0)
    except native.NativeBlendError:
        execution_raised = True
    else:
        execution_raised = False
check(execution_raised, "native execution failure raises without Python replay")
# and the reference still answers the *finite* case, so the refusal is the NaN path
check(_reference_blend(good, good, 1, 1, 1.0, 1.0) is not None,
      "the pure-Python reference still answers the finite case")

# ---------------------------------------------------------------------------
# 6. the public Blend consumer path still works and matches the pure-Python reference
# ---------------------------------------------------------------------------
print("\n-- public Blend consumer (blend_curves) vs the pure-Python reference --")
l1 = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(20, 0, 0))
l2 = r3.LineCurve(r3.Point3d(25, 12, 0), r3.Point3d(-3, 14, 0))
for c1, c2 in (("Position", "Position"), ("Tangency", "Curvature"),
               ("Curvature", "Curvature"), ("G3", "G4"), ("G4", "G3")):
    crv, info = blend.blend_curves(l1, "end", l2, "start", c1, c2)
    oc, oi = _reference_blend_curves(l1, "end", l2, "start", c1, c2)
    got = [(p.X, p.Y, p.Z) for p in crv.Points]
    want = [(p.X, p.Y, p.Z) for p in oc.Points]
    check(info["degree"] == oi["degree"] and bits_match(got, want)
          and crv.Knots.ToList() == oc.Knots.ToList(),
          f"blend_curves {c1}/{c2}: degree {info['degree']}, control points and knots match")

# ---------------------------------------------------------------------------
# 7. benchmark (opt-in via --benchmark): public wrapper vs the pure-Python loop
# ---------------------------------------------------------------------------
if BENCHMARK:
    print("\n-- benchmark (best of 3; the pure control-point solve only) --")

    def best(fn, reps=400):
        out = math.inf
        for _ in range(3):
            start = time.perf_counter()
            for _ in range(reps):
                fn()
            out = min(out, (time.perf_counter() - start) / reps)
        return out

    def make_case(k):
        r = random.Random(k)
        p0 = rand_derivs(r, k)
        p1 = rand_derivs(r, k)
        return p0, p1, 1.0, 1.0

    for label, k in (("Position (k0=k1=0)", 0), ("Tangency (k0=k1=1)", 1),
                     ("Curvature (k0=k1=2)", 2), ("G4 (k0=k1=4)", 4)):
        p0, p1, l0, l1 = make_case(k)
        pub_t = best(lambda: blend.blend_bezier_points(p0, p1, k, k, l0, l1))
        py_t = best(lambda: _reference_blend(p0, p1, k, k, l0, l1))
        check(bits_match(blend.blend_bezier_points(p0, p1, k, k, l0, l1),
                         _reference_blend(p0, p1, k, k, l0, l1)),
              f"{label}: public {pub_t * 1e6:6.1f} us  python {py_t * 1e6:6.1f} us "
              f"({py_t / pub_t:.1f}x, bit-for-bit equal)")

passed = check_calls["ok"]
total = check_calls["n"]
print(f"\n{passed}/{total} passed")
sys.exit(0 if passed == total else 1)
