"""Native-only Join ABI contract: the compatibility / fail-closed layer.

This is the COMPAT half of the Join qualification. The retired independent
Python oracle (``spike/joinchain_reference.py``) is retired; its decisions are
replayed by the Rust integration test ``native/pure_core/tests/joinchain_golden.rs``
against the real C ABI, and the delegate's Python mapping is exercised by
``spike/test_joinchain_owned.py``. This file owns what the ABI layer must do:

  * the binding is discovered (building the crate if the .so is absent) and the
    joinchain export is resolvable;
  * every nonempty input uses the native route under either compatibility flag,
    and the counter increments exactly once per call;
  * empty input verifies the binding but never invokes the unsupported n=0 ABI;
  * invalid numeric input (non-finite / negative tolerance, non-finite or
    index-unrepresentable coordinates, subnormal tolerance) is refused without
    replaying anything;
  * a failed ABI return or exception, and a ``None`` packed result, fail closed
    without counting or falling back;
  * the packed output validator stays strict (gaps, trailing members, flags,
    partition, non-finite / malformed ends);
  * a missing, unreadable, stale-ABI or missing-export library fails closed.

It asserts a few live native-route outputs against the golden fixture (captured
once from the retired reference) so the COMPAT layer is checked against real
expected data, but it no longer re-runs the differential / random / boundary
loops (those are Rust) nor the delegate-mapping / key / error-message contract
(that is the owned test).

Defaults use production and NARWHAL_PURE_CORE. Candidate qualification:
  python3 spike/test_native_joinchain.py --module tmp/rust13-joinchain-candidate.py
Optional --library selects an explicit library, without building shared code.
"""
import argparse
import ctypes
import importlib.util
import math
import os
import struct
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--module", default="narwhal3d/joinchain.py")
parser.add_argument("--library")
args = parser.parse_args()
if args.library:
    os.environ["NARWHAL_PURE_CORE"] = os.path.abspath(args.library)
_LIB = os.environ.get("NARWHAL_PURE_CORE")
if _LIB is None:
    _LIB = os.path.join(ROOT, "narwhal3d", "native", "libnarwhal_pure_core.so")
if not _LIB or not os.path.isfile(_LIB):
    raise RuntimeError("Required qualified Join library missing; no test-time build")
os.environ["NARWHAL_PURE_CORE"] = _LIB

FIXTURE = os.path.join(ROOT, "native", "pure_core", "tests", "fixtures",
                       "joinchain_golden.txt")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


jc = _load("narwhal_native_test_joinchain", args.module)
# This compatibility resolver must expose the SAME adapter the wrapper calls.
native = jc._native_joinchain()
results = []
TMP = tempfile.TemporaryDirectory(prefix="native_joinchain_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    if native is not None:
        native._reset_for_tests()


def norm(chains):
    return [(tuple(c.members), c.closed, tuple(c.start), tuple(c.end))
            for c in chains]


@contextmanager
def patch(obj, name, value):
    real = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, real)


def refused(segs, tolerance=1e-5):
    before = native._native_joinchain_calls
    try:
        jc.chains(segs, tolerance)
    except RuntimeError:
        pass
    else:
        raise AssertionError("native-only wrapper must raise RuntimeError, never replay Python")
    assert native._native_joinchain_calls == before


def ordered(n):
    return [("k%d" % i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0))
            for i in range(n)]


def disjoint(n):
    return [("k%d" % i, (float(i), 0.0, 0.0), (float(i) + 0.5, 0.0, 0.0))
            for i in range(n)]


# ---------------------------------------------------------------------------
# Golden-fixture decode (compact; expected values come ONLY from the fixture
# captured once from the retired Python reference -- no reference algorithm here).
# ---------------------------------------------------------------------------
def _f64s(field, count):
    if count == 0:
        return []
    return list(struct.unpack("<%dd" % count,
                              bytes.fromhex(field.replace(",", ""))))


def _chains(field):
    if not field:
        return []
    out = []
    for rec in field.split(";"):
        p = rec.split(",")
        out.append((int(p[0]), int(p[1]), bool(int(p[2])),
                    struct.unpack("<6d", bytes.fromhex("".join(p[3:9])))))
    return out


def _members(field):
    if not field:
        return []
    return [(int(a), int(b)) for a, b in (rec.split(",") for rec in field.split(";"))]


def _expected(fchains, fmembers):
    return [(tuple((fmembers[off + j][0], bool(fmembers[off + j][1])) for j in range(ln)),
             closed, tuple(coords[:3]), tuple(coords[3:]))
            for (off, ln, closed, coords) in fchains]


# Fixture rows keyed by (group, index) for the sizes the COMPAT layer exercises.
_FIX = {}
for _line in [l for l in open(FIXTURE, encoding="utf-8").read().splitlines()
              if l and not l.startswith("#")]:
    f = _line.split("\t")
    _n = int(f[1])
    _FIX.setdefault(f[0], []).append(
        (f[0], _n, struct.unpack("<d", bytes.fromhex(f[2]))[0],
         _f64s(f[3], 3 * _n), _f64s(f[4], 3 * _n),
         _chains(f[6]), _members(f[8]), int(f[5]), int(f[7])))


def _row(group, idx):
    return _FIX[group][idx]


def _segs(starts, ends):
    n = len(starts) // 3
    return [(i, tuple(starts[3 * i:3 * i + 3]), tuple(ends[3 * i:3 * i + 3]))
            for i in range(n)]


@check("native library is built, discovered, and exposes the joinchain ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    assert callable(native._joinchain_binding()), \
        "the joinchain binding must resolve"
    assert hasattr(native, "_native_joinchain_calls")


@check("all nonempty sizes use native, with either compatibility flag")
def _():
    _reset()
    # Sizes present in the ordered fixture (n -> row), so we can also assert golden.
    ordered_by_n = {row[1]: row for row in _FIX["ordered"]}
    for n in (1, 2, 10, 511, 512, 513, 1000):
        # Index-keyed segments: the fixture is index-based, so the delegate's
        # index->key mapping must come back as the integer index for the
        # partition / golden checks below.
        segs = [(i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0)) for i in range(n)]
        for flag in (False, True):
            before = native._native_joinchain_calls
            got = norm(jc.chains(segs, 1e-5, _force_native=flag))
            assert native._native_joinchain_calls == before + 1, (n, flag)
            # Every input appears exactly once (partition invariant).
            all_members = [m for c in got for m in c[0]]
            assert sorted(k for k, _ in all_members) == list(range(n)), (n, flag)
            if n in ordered_by_n:
                row = ordered_by_n[n]
                assert got == _expected(row[5], row[6]), \
                    "ordered n=%d: native != golden" % n


@check("empty input verifies binding but never invokes the unsupported n=0 ABI")
def _():
    _reset()
    binding = native._joinchain_binding()
    assert binding is not None
    seen = []

    def resolve():
        seen.append(True)
        return binding

    def forbidden(*a, **kw):
        raise AssertionError("empty input must not call native adapter")

    before = native._native_joinchain_calls
    with patch(native, "_joinchain_binding", resolve), patch(native, "_native_joinchain", forbidden):
        assert jc.chains([], 1e-5) == []
    assert seen, "empty input must verify binding"
    assert native._native_joinchain_calls == before
    with patch(native, "_joinchain_binding", lambda: None):
        refused([])


@check("invalid numeric input is refused, not replayed by the retired oracle")
def _():
    _reset()
    for tol in (-1., math.nan, math.inf, -math.inf):
        refused(ordered(2), tol)
    for value in (math.nan, math.inf, -math.inf):
        refused([("a", (value, 0., 0.), (1., 0., 0.))])
        refused([("a", (0., 0., 0.), (0., value, 0.))])
    # Finite values can still be outside the positive-tolerance spatial index.
    refused([("a", (1.e100, 0., 0.), (1.e100, 1., 0.))], 1e-5)
    refused(ordered(2), math.ulp(0.))


@check("failed ABI return and exception raise without counting or Python replay")
def _():
    _reset()
    for n in (1, 10, 512):
        with patch(native, "_joinchain_binding", lambda: (lambda *a: -1)):
            refused(ordered(n))

        def broken(*a):
            raise OSError("injected native call failure")

        with patch(native, "_joinchain_binding", lambda: broken):
            refused(ordered(n))
        with patch(native, "_native_joinchain", lambda *a: None):
            refused(ordered(n))


def packed_valid(p):
    n, off, lengths, closed, ends, members, rev, nc, nm = p
    return native._valid_joinchain_packed(n, nc, nm, off, lengths,
                                          closed, ends, members, rev)


@check("packed shape, gaps, trailing members, flags and partition stay strict")
def _():
    _reset()
    p = native._native_joinchain(
        [0., 0., 0., 5., 0., 0.], [1., 0., 0., 6., 0., 0.], 1e-5)
    assert p is not None and len(p) == 9
    assert packed_valid(p)
    converted = jc._chains_from_packed(disjoint(2), p)
    assert [c.keys for c in converted] == [("k0",), ("k1",)]
    # All mutations are copied Python output metadata, never live Rust edits.
    cases = [(1, [1, 1]), (1, [0, 2]), (2, [1, 0]), (2, [1, 2]),
             (3, [0, 2]), (5, [0, 0]), (5, [0, 2]), (6, [0, 2]),
             (7, 1), (8, 1), (4, ([0., 0.],) * 5),
             (4, ([math.nan, 0.],) * 6), (4, ([],) * 6)]
    for field, bad in cases:
        malformed = list(p)
        malformed[field] = bad
        assert not packed_valid(malformed), (field, bad)
    before = native._native_joinchain_calls
    with patch(native, "_valid_joinchain_packed", lambda *a: False):
        assert native._native_joinchain([0., 0., 0.], [1., 0., 0.], 1e-5) is None
        refused(ordered(1))
        refused(ordered(512))
    assert native._native_joinchain_calls == before


@check("missing, unreadable, stale ABI and missing export fail closed")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    unreadable = os.path.join(TMP.name, "empty.so")
    open(unreadable, "wb").close()
    try:
        for path in (os.path.join(TMP.name, "missing.so"), unreadable):
            os.environ["NARWHAL_PURE_CORE"] = path
            _reset()
            assert native._joinchain_binding() is None
            refused(ordered(10))
            refused([])

        # Model a loadable old library independently of host compiler presence.
        class Version:
            def __init__(self, value): self.value = value
            def __call__(self): return self.value

        class Library:
            def __init__(self, version):
                self.narwhal_pure_core_abi_version = Version(version)

        for version in (0, 1):  # wrong ABI; correct ABI but missing Join export
            _reset()
            with patch(ctypes, "CDLL", lambda *a, v=version: Library(v)):
                assert native._joinchain_binding() is None
                refused(ordered(10))
                refused([])
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("native route is correct on the golden witness (boundary + closed loop)")
def _():
    _reset()
    # Witness: CPython exact tolerance joins; adjacent below does not (2 chains).
    d = (-.4702396670038951, -.5067449846120331, .12273626832630158)
    tol = math.dist((0., 0., 0.), d)
    segs = [("a", (-10., -10., -10.), (0., 0., 0.)),
            ("b", d, (10., 10., 10.))]
    # Index-keyed rows from the fixture (the witness group).
    for idx, (thr, count) in enumerate([
            (math.nextafter(tol, -math.inf), 2), (tol, 1), (math.nextafter(tol, math.inf), 1)]):
        got = jc.chains(segs, thr, _force_native=True)
        assert len(got) == count and all(not c.closed for c in got), thr.hex()
    # Closed loop from the fixture must come back closed under the native route.
    group, n, ctol, s, e, fch, fm, _, _ = _row("closed", 0)
    cl = jc.chains(_segs(s, e), ctol, _force_native=True)
    assert norm(cl) == _expected(fch, fm) and cl[0].closed is True


failed = 0
for name, status, detail in results:
    print("[%s] %s" % (status, name) + ("  -- %s" % detail if detail else ""))
    failed += status == "FAIL"
print("\n%d/%d passed" % (len(results) - failed, len(results)))
TMP.cleanup()
sys.exit(1 if failed else 0)
