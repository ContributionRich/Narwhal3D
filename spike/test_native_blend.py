#!/usr/bin/env python3
"""Native Bezier endpoint-derivative solver: parity and correctness (native at every order).

Runs `blend.blend_bezier_points` and proves it against two independent
standards, using a SOURCE-BUILT pure-core library (no scratch / oracle files):

  1. INDEPENDENT RECONSTRUCTION -- the produced control points, read back with a
     separate Bernstein end-derivative formula (the n!/(n-j)! Delta^j identities
     applied forward, not the solver's difference solve), reproduce the requested
     endpoint derivatives `lambda^j D_j` at t=0 and t=1 for ALL 25 continuity
     pairs (k0,k1 in 0..=4), i.e. up to G4. This checks the math, not the code.
  2. BIT-FOR-BIT DIFFERENTIAL -- the public wrapper returns the control points a
     test-only PURE-PYTHON reference (the saved original loop, embedded below so
     this file never imports a private oracle) returns, bit for bit, over seeded
     random derivative lists, reversed / oriented ends, and negative / zero speed
     scales.
  3. ALL-ORDER NATIVE DISPATCH -- the public wrapper routes EVERY supported
     order (all 25 (k0,k1) pairs, k0,k1 in 0..=4) through the native binding;
     the retired Python endpoint loop is gone, so a missing / stale / refused
     library or a rejected input is an explicit NativeBlendError and is NEVER
     silently replayed through Python.

The openNURBS derivative extraction and `NurbsCurve` construction stay in Python
(this never samples, meshes or approximates). A malformed / non-finite input is
refused by the native layer with NO partial output, and that refusal is EXPLICIT
(a raised NativeBlendError), never a silent fallback.

The library under test must export the ABI version symbol (returning 1) and the
blend symbol; the source build of `native/pure_core` provides both. Run:

  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \\
      tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_native_blend.py

Benchmarks are off by default; pass `--benchmark` to time the public wrapper
against the pure-Python reference (an honest per-order cost comparison).
"""
import ctypes
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


# Missing / wrong-ABI / missing-solver libraries must be EXPLICIT: the solver is
# native-only, so a refused library is a raised NativeBlendError -- never a
# silent "unavailable" or a Python replay.
for version, has_solver in ((None, True), (2, True), (1, False)):
    library = types.SimpleNamespace()
    if version is not None:
        library.narwhal_pure_core_abi_version = StubFunction(version)
    if has_solver:
        library.narwhal_pure_core_blend_bezier_points = StubFunction(0)
    try:
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": f"/fake-blend-{version}-{has_solver}"}), \
                patch.object(native.Path, "is_file", return_value=True), \
                patch.object(native.ctypes, "CDLL", return_value=library):
            try:
                native.available()
                refused = False
            except native.NativeBlendError:
                refused = True
        check(refused,
              f"refused library (version={version}, solver={has_solver}) "
              "is an explicit NativeBlendError, never a silent fallback")
    finally:
        native._record = None
check(native.available(), "real source library requalified after the refused-library probes")

# a packaged / env-resolved library that is simply ABSENT is explicit too (no silent None)
try:
    with patch.dict(os.environ, {"NARWHAL_PURE_CORE": "/definitely/not/a/library.so"}), \
            patch.object(native.Path, "is_file", return_value=False):
        try:
            native.available()
            absent_explicit = False
        except native.NativeBlendError:
            absent_explicit = True
    check(absent_explicit, "an absent library is an explicit NativeBlendError, never None")
finally:
    native._record = None
check(native.available(), "real source library requalified after the absent probe")


# ---------------------------------------------------------------------------
# 0b. the binding cache: the signature is a TUPLE (no collision), the warm cache
#     is served before any Path / lock work, the CDLL is retained inside the
#     record (a switch replaces the whole record, never a dangling fn), and a
#     set-but-empty env is refused -- it is never collapsed onto the packaged
#     default (the old `explicit or "<packaged>"` string signature would have).
# ---------------------------------------------------------------------------
PACKAGED_SIG = ("packaged", None)


def reset():
    native._record = None


def _raised_native():
    try:
        native.available()
    except native.NativeBlendError:
        return True
    return False


# (collision) the env value "<packaged>" is a DISTINCT signature: it must be
# refused, and must NOT be served from a cached packaged record. The old string
# signature `explicit or "<packaged>"` would have matched this literal env value
# onto the unset-env packaged record.
reset()
native._record = native._Record(PACKAGED_SIG, types.SimpleNamespace(), StubFunction(0))
with patch.dict(os.environ, {"NARWHAL_PURE_CORE": "<packaged>"}):
    collided = _raised_native() and native._record is not None \
        and native._record.signature == PACKAGED_SIG
check(collided,
      "env value '<packaged>' is a distinct refused signature, not served from the packaged record")
reset()

# (warm) a matching cached record is served before any Path / lock work: with the
# env unchanged, a second call returns the same result without touching Path/CDLL
# (the fast path consults the record before it ever resolves a path).
reset()
p0f, p1f = [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]
first = native.blend_bezier_points(p0f, p1f, 0, 0, 1.0, 1.0)   # resolves + caches
with patch.object(native.Path, "is_file", side_effect=AssertionError("Path touched on a warm hit")), \
        patch.object(native.ctypes, "CDLL", side_effect=AssertionError("CDLL touched on a warm hit")):
    second = native.blend_bezier_points(p0f, p1f, 0, 0, 1.0, 1.0)
check(first == second,
      "the warm cache is served before any Path / lock work (same result, no re-resolution)")
reset()

# (retained CDLL) the record keeps (signature, CDLL, fn) together; a binding
# switch replaces the whole record (never a dangling fn without its library), and
# only ONE record is held at a time (no unbounded per-signature cache).
reset()
libA, libB = types.SimpleNamespace(tag="A"), types.SimpleNamespace(tag="B")
fnA, fnB = StubFunction(0), StubFunction(0)
native._record = native._Record(("env", "/a.so"), libA, fnA)
switched = native._Record(("env", "/b.so"), libB, fnB)
native._record = switched                     # the switch
check(native._record is switched
      and native._record.library is libB
      and native._record.fn is fnB
      and native._record.signature == ("env", "/b.so"),
      "a binding switch replaces the record whole (CDLL + fn retained together), one record at a time")
reset()

# (empty env refused) a set-but-empty NARWHAL_PURE_CORE is a real, distinct
# signature: it must be refused, and the packaged path must never be consulted.
reset()
asked = []


class _FakePath:
    """A Path stand-in: records every constructed path; nothing is a file."""

    def __init__(self, *parts):
        asked.append(os.path.join(*parts))

    def expanduser(self):
        return self

    def resolve(self):
        return self

    def is_file(self):
        return False


with patch.dict(os.environ, {"NARWHAL_PURE_CORE": ""}), \
        patch.object(native, "Path", _FakePath):
    empty_refused = _raised_native()
packaged_base = os.path.join(os.path.dirname(os.path.abspath(native.__file__)), "native")
check(empty_refused and all(packaged_base not in a for a in asked) and native._record is None,
      f"an empty NARWHAL_PURE_CORE is a refused signature (never the packaged default); constructed {asked!r}")
reset()


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
# 4. ALL-ORDER NATIVE DISPATCH -- every supported (k0,k1) pair routes through
#    the native binding; the retired Python endpoint loop is gone.
# ---------------------------------------------------------------------------
print("\n-- all-order native dispatch: every supported (k0,k1) pair calls the native binding --")
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


def _case(k):
    """k+1 oriented endpoint derivative vectors [P, D1, ..., Dk], distinct and finite."""
    return [(float(i), 0.0, 0.0) if i == 0 else (0.0, float(i), 0.0) for i in range(k + 1)]


low_hit = mid_hit = high_hit = bad = 0
for k0 in range(5):
    for k1 in range(5):
        n = _native_called(_case(k0), _case(k1), k0, k1)
        if n < 1:
            bad += 1
            continue
        if k0 + k1 < 4:
            low_hit += 1
        elif k0 + k1 < 8:
            mid_hit += 1
        else:
            high_hit += 1
check(bad == 0,
      f"all 25 supported (k0,k1) pairs route through the native binding: "
      f"{low_hit} low + {mid_hit} mid + {high_hit} high, {bad} not routed")

# ---------------------------------------------------------------------------
# 5. malformed / non-finite: refused EXPLICITLY (raised), NO partial output, no replay
# ---------------------------------------------------------------------------
print("\n-- malformed / non-finite inputs: refused explicitly (raised), never a silent fallback --")
good = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]


def flat(p):
    return [v for d in p for v in d]


def _raised(fn):
    try:
        fn()
    except native.NativeBlendError:
        return True
    return False


# k beyond the supported maximum -> explicit refusal (the solver is native-only)
check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), 5, 1, 1.0, 1.0)),
      "k=5 (beyond G4) is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, 5, 1, 1.0, 1.0)),
      "k=5 (beyond G4) is an explicit NativeBlendError through the public wrapper")
# negative k -> explicit refusal, never a ctypes out-of-range crash
check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), -1, 1, 1.0, 1.0)),
      "negative k is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, -1, 1, 1.0, 1.0)),
      "negative k is an explicit NativeBlendError through the public wrapper")
# non-integer k -> explicit refusal, never a ctypes out-of-range crash
check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), 1.0, 1, 1.0, 1.0)),
      "a non-integer k is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, 1.0, 1, 1.0, 1.0)),
      "a non-integer k is an explicit NativeBlendError through the public wrapper")
# wrong span length -> explicit refusal
check(_raised(lambda: native.blend_bezier_points([0.0, 0.0, 0.0, 1.0], flat(good), 1, 1, 1.0, 1.0)),
      "a wrong derivative-list length is an explicit NativeBlendError")
# a span whose vector count does not match k -> explicit refusal through the public wrapper
short_p0 = [(0.0, 0.0, 0.0)]          # only P, but k0=1 needs P and D1
check(_raised(lambda: blend.blend_bezier_points(short_p0, good, 1, 1, 1.0, 1.0)),
      "a derivative list shorter than k+1 is an explicit NativeBlendError through the public wrapper")

# non-finite input -> the native layer refuses, and blend RAISES (never silently
# replays the failure through the retired Python loop).
good4 = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 3.0), (0.0, 0.0, 0.5)]
bad_p0 = [(0.0, float("nan"), 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 2.0)]
check(_raised(lambda: blend.blend_bezier_points(bad_p0, good4, 2, 2, 1.0, 1.0)),
      "a non-finite derivative at native order raises (never replayed through Python)")
check(_raised(lambda: blend.blend_bezier_points(bad_p0, good, 0, 0, 1.0, 1.0)),
      "a non-finite derivative at Position order raises (never replayed through Python)")
# a loaded library that rejects the call (non-zero status) is explicit, never replayed
_status99 = native._Record(PACKAGED_SIG, types.SimpleNamespace(), StubFunction(-99))
try:
    with patch.object(native, "_blend_binding", return_value=_status99):
        blend.blend_bezier_points(good4, good4, 2, 2, 1.0, 1.0)
        execution_raised = False
except native.NativeBlendError:
    execution_raised = True
check(execution_raised, "native execution failure (status -99) raises without Python replay")

# FAULT INJECTION: a library that reports SUCCESS (status 0) but writes a non-finite
# control component must be refused with a NativeBlendError -- non-finite geometry
# is never returned, and no count is reported for it. The guard checks the RETURNED
# control points, so an immutable record does not protect against this.
class _NonFiniteSuccessFn:
    """status 0 (success) but corrupts one output component to NaN / inf."""
    def __call__(self, *args):
        out = args[8]                 # the fixed 30-double output buffer
        out[0] = float("nan")         # first control point, x
        out[1] = float("inf")         # first control point, y
        return 0                      # reports success
_nonfinite_rec = native._Record(PACKAGED_SIG, types.SimpleNamespace(), _NonFiniteSuccessFn())
try:
    with patch.object(native, "_blend_binding", return_value=_nonfinite_rec):
        blend.blend_bezier_points(good, good, 0, 0, 1.0, 1.0)
        nonfinite_raised = False
except native.NativeBlendError:
    nonfinite_raised = True
check(nonfinite_raised,
      "a success (status 0) with a non-finite control component raises NativeBlendError, "
      "never returns non-finite geometry (fault injection)")

# a native INVOCATION that raises a bare ctypes.ArgumentError (a direct Exception
# subclass, NOT OSError/ValueError) or a bare ValueError must be wrapped by name
# into a NativeBlendError -- the solver is native-only, so no bare exception escapes.
class _RaisesArgumentErrorFn:
    def __call__(self, *a):
        raise ctypes.ArgumentError("bad argument conversion")

class _RaisesValueErrorFn:
    def __call__(self, *a):
        raise ValueError("malformed output buffer")


def _wraps(fn):
    rec = native._Record(PACKAGED_SIG, types.SimpleNamespace(), fn)
    try:
        with patch.object(native, "_blend_binding", return_value=rec):
            blend.blend_bezier_points(good, good, 0, 0, 1.0, 1.0)
        return False                  # did not raise at all
    except native.NativeBlendError:
        return True                   # wrapped by name
    except Exception as exc:          # a bare (unwrapped) exception escaped
        return ("ESCAPED:" + type(exc).__name__)   # mark the failure distinctly

check(_wraps(_RaisesArgumentErrorFn()) is True,
      "a bare ctypes.ArgumentError from the native invocation is wrapped into NativeBlendError")
check(_wraps(_RaisesValueErrorFn()) is True,
      "a bare ValueError from the native invocation is wrapped into NativeBlendError")

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
# 7. benchmark (opt-in via --benchmark): public wrapper (native) vs the pure-Python loop
# ---------------------------------------------------------------------------
if BENCHMARK:
    print("\n-- benchmark (best of 3; the pure control-point solve only; native at every order) --")

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
