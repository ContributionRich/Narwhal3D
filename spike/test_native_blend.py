#!/usr/bin/env python3
"""Blend host integration: all frozen legacy records, ctypes faults and live curves.

Numeric parity and independent Bernstein verification live in blend_golden.rs.
Fixtures were captured from the original Python solver, never native answers.
--benchmark reports timings only; the original comparison remains private.
"""
import ctypes
import json
from pathlib import Path
import importlib.util
import os
import struct
import sys
import types
from unittest.mock import patch

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

def bits_match(a, b):
    """Exact (bit-for-bit) float equality, component by component."""
    if len(a) != len(b):
        return False
    return all(len(xa) == len(ya) and all(struct.pack("=d", x) == struct.pack("=d", y)
               for x, y in zip(xa, ya)) for xa, ya in zip(a, b))

check(native.available(),
      f"the pure-core library loads with the blend symbol + ABI v1 ({LIBRARY})")

class StubFunction:
    def __init__(self, result):
        self.result = result

    def __call__(self, *args):
        return self.result

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

PACKAGED_SIG = ("packaged", None)

def reset():
    native._record = None

def _raised_native():
    try:
        native.available()
    except native.NativeBlendError:
        return True
    return False

reset()
native._record = native._Record(PACKAGED_SIG, types.SimpleNamespace(), StubFunction(0))
with patch.dict(os.environ, {"NARWHAL_PURE_CORE": "<packaged>"}):
    collided = _raised_native() and native._record is not None \
        and native._record.signature == PACKAGED_SIG
check(collided,
      "env value '<packaged>' is a distinct refused signature, not served from the packaged record")
reset()

reset()
p0f, p1f = [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]
first = native.blend_bezier_points(p0f, p1f, 0, 0, 1.0, 1.0)   # resolves + caches
with patch.object(native.Path, "is_file", side_effect=AssertionError("Path touched on a warm hit")), \
        patch.object(native.ctypes, "CDLL", side_effect=AssertionError("CDLL touched on a warm hit")):
    second = native.blend_bezier_points(p0f, p1f, 0, 0, 1.0, 1.0)
check(first == second,
      "the warm cache is served before any Path / lock work (same result, no re-resolution)")
reset()

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

check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), 5, 1, 1.0, 1.0)),
      "k=5 (beyond G4) is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, 5, 1, 1.0, 1.0)),
      "k=5 (beyond G4) is an explicit NativeBlendError through the public wrapper")
check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), -1, 1, 1.0, 1.0)),
      "negative k is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, -1, 1, 1.0, 1.0)),
      "negative k is an explicit NativeBlendError through the public wrapper")
check(_raised(lambda: native.blend_bezier_points(flat(good), flat(good), 1.0, 1, 1.0, 1.0)),
      "a non-integer k is an explicit NativeBlendError")
check(_raised(lambda: blend.blend_bezier_points(good, good, 1.0, 1, 1.0, 1.0)),
      "a non-integer k is an explicit NativeBlendError through the public wrapper")
check(_raised(lambda: native.blend_bezier_points([0.0, 0.0, 0.0, 1.0], flat(good), 1, 1, 1.0, 1.0)),
      "a wrong derivative-list length is an explicit NativeBlendError")
short_p0 = [(0.0, 0.0, 0.0)]          # only P, but k0=1 needs P and D1
check(_raised(lambda: blend.blend_bezier_points(short_p0, good, 1, 1, 1.0, 1.0)),
      "a derivative list shorter than k+1 is an explicit NativeBlendError through the public wrapper")

good4 = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 3.0), (0.0, 0.0, 0.5)]
bad_p0 = [(0.0, float("nan"), 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 2.0)]
check(_raised(lambda: blend.blend_bezier_points(bad_p0, good4, 2, 2, 1.0, 1.0)),
      "a non-finite derivative at native order raises (never replayed through Python)")
check(_raised(lambda: blend.blend_bezier_points(bad_p0, good, 0, 0, 1.0, 1.0)),
      "a non-finite derivative at Position order raises (never replayed through Python)")
_status99 = native._Record(PACKAGED_SIG, types.SimpleNamespace(), StubFunction(-99))
try:
    with patch.object(native, "_blend_binding", return_value=_status99):
        blend.blend_bezier_points(good4, good4, 2, 2, 1.0, 1.0)
        execution_raised = False
except native.NativeBlendError:
    execution_raised = True
check(execution_raised, "native execution failure (status -99) raises without Python replay")

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

FIXTURES = Path(ROOT) / "native/pure_core/tests/fixtures"
def values(text):
    return [struct.unpack("=d", struct.pack("=Q", int(v, 16)))[0]
            for v in text.split(",")]

def vectors(text):
    v = values(text)
    return [tuple(v[i:i + 3]) for i in range(0, len(v), 3)]

rows = (FIXTURES / "blend_numeric.txt").read_text().splitlines()
counts = {}
for row in rows:
    kind, a, b, scales, start, end, expected, targets = row.split("|")
    k0, k1 = int(a), int(b)
    l0, l1 = values(scales)
    p0, p1, want = vectors(start), vectors(end), vectors(expected)
    assert bits_match(blend.blend_bezier_points(p0, p1, k0, k1, l0, l1), want), kind
    assert bits_match(direct_native(p0, p1, k0, k1, l0, l1), want), kind
    counts[kind] = counts.get(kind, 0) + 1
check(counts == {"RECON": 100, "PARITY": 400, "ORIENT": 120, "SIGN": 2, "FINITE": 1},
      f"all 623 frozen legacy cases replayed through wrapper and ctypes: {counts}")
sign = [vectors(row.split("|")[6]) for row in rows if row.startswith("SIGN|")]
check(sign[0] != sign[1], "negative scale genuinely changes the blend")

l1 = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(20, 0, 0))
l2 = r3.LineCurve(r3.Point3d(25, 12, 0), r3.Point3d(-3, 14, 0))
curves = json.loads((FIXTURES / "blend_curves.txt").read_text())
assert len(curves) == 5
for case in curves:
    c1, c2 = case["continuity"]
    crv, info = blend.blend_curves(l1, "end", l2, "start", c1, c2)
    want_info = case["info"]
    check(info["degree"] == want_info["degree"]
          and bits_match([info["lambda"]], [want_info["lambda"]])
          and bits_match([(p.X, p.Y, p.Z) for p in crv.Points], vectors(case["controls"]))
          and bits_match([crv.Knots.ToList()], [values(case["knots"])])
          and crv.Degree == want_info["degree"] and crv.IsValid,
          f"blend_curves {c1}/{c2}: live curve, info, controls and knots match legacy fixture")

if BENCHMARK:
    print("Timing comparison is report-only; rerun the archived original privately (see blend_provenance.txt).")
passed, total = check_calls["ok"], check_calls["n"]
print(f"\n{passed}/{total} host checks passed; 623/623 numeric host records replayed")
sys.exit(0 if passed == total else 1)
