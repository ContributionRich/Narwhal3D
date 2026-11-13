"""Native-only Join contract, checked against an independent retired oracle.

Every nonempty valid input must call native, regardless of _force_native.
Empty input verifies the binding but does not call the ABI (which rejects n=0).
Missing/stale bindings, failed calls and malformed packed output fail closed.
Policy change: nonfinite/negative tolerance, nonfinite coordinates and spatial
index-unrepresentable coordinate/tolerance combinations are explicitly refused;
the retired solver's odd acceptance is NOT a fallback contract.

Defaults use production and NARWHAL_PURE_CORE. Candidate qualification:
  python3 spike/test_native_joinchain.py --module tmp/rust13-joinchain-candidate.py
Optional --library selects an explicit library, without building shared code.
"""
import argparse
import ctypes
import importlib.util
import math
import os
import random
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
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native/pure_core/target/release/libnarwhal_pure_core.so"),
]


def _locate_lib():
    # An explicit override is authoritative, even if missing or stale.
    explicit = os.environ.get("NARWHAL_PURE_CORE")
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    return next((p for p in LIB_CANDIDATES if p and os.path.isfile(p)), None)


def _ensure_lib():
    lib = _locate_lib()
    if lib is not None or os.environ.get("NARWHAL_PURE_CORE"):
        return lib
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", os.path.join(ROOT, "native/pure_core/Cargo.toml"),
         "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True, timeout=120,
    )
    return _locate_lib()


_LIB = _ensure_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


jc = _load("narwhal_native_test_joinchain", args.module)
reference = _load("narwhal_native_joinchain_reference", "spike/joinchain_reference.py")
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


def python_chains(segs, tolerance):
    return reference.chains(segs, tolerance)


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


def ambiguous(n):
    # A backbone plus a spur meeting every third joint -> a T-junction at each.
    out = [("b%d" % i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0))
           for i in range(n)]
    for i in range(0, n, 3):
        out.append(("s%d" % i, (float(i), 0.0, 0.0), (float(i), 1.0, 0.0)))
    return out


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
    for n in (1, 2, 10, 511, 512, 513, 1000):
        segs = ordered(n)
        baseline = norm(python_chains(segs, 1e-5))
        for flag in (False, True):
            before = native._native_joinchain_calls
            assert norm(jc.chains(segs, 1e-5, _force_native=flag)) == baseline
            assert native._native_joinchain_calls == before + 1, (n, flag)


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


@check("native == python on the ordered chain")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    for n in (511, 512, 513, 1000):
        segs = ordered(n)
        py = norm(python_chains(segs, 1e-5))
        na = norm(jc.chains(segs, 1e-5, _force_native=True))
        assert py == na, "ordered n=%d: native %r != python %r" % (n, na, py)


@check("native == python on the disjoint (no-join) set")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    for n in (511, 512, 513, 1000):
        segs = disjoint(n)
        py = norm(python_chains(segs, 1e-5))
        na = norm(jc.chains(segs, 1e-5, _force_native=True))
        assert py == na, "disjoint n=%d: native != python" % n


@check("native == python on the ambiguous (T-junction) set")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    for n in (511, 512, 513, 1000):
        segs = ambiguous(n)
        py = norm(python_chains(segs, 1e-5))
        na = norm(jc.chains(segs, 1e-5, _force_native=True))
        assert py == na, "ambiguous n=%d: native != python" % n


@check("zero tolerance matches signed zero exactly like Python")
def _():
    segs = [
        ("a", (-1.0, 0.0, 0.0), (-0.0, 0.0, 0.0)),
        ("b", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    ]
    assert norm(jc.chains(segs, 0.0, _force_native=True)) == \
        norm(python_chains(segs, 0.0))


@check("forward orientation is preferred over reversed at a T-junction (named invariant)")
def _():
    # At the tail (5,0,0), candidate ``b`` joins FORWARD (it STARTS there, no
    # reversal) and candidate ``c`` joins REVERSED (it ENDs there). The solver
    # must prefer the forward candidate, so the backbone is a->b and ``c`` is
    # left a singleton. Corrupting the Rust forward/reversed preference
    # (swapping the start_map/end_map or the rev bits) makes ``c`` join instead
    # of ``b`` and this goes RED.
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    segs = [
        ("a", (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ("b", (5.0, 0.0, 0.0), (5.0, 5.0, 0.0)),  # forward: starts at the tail
        ("c", (9.0, 0.0, 0.0), (5.0, 0.0, 0.0)),  # reversed: ends at the tail
    ]
    na = jc.chains(segs, 1e-5, _force_native=True)
    by_key = {c.members[0][0]: c for c in na}
    backbone = by_key["a"]
    assert backbone.keys == ("a", "b"), \
        "the forward candidate b must join, not the reversed c, got %r" \
        % (backbone.keys,)
    assert backbone.members[1][1] is False, \
        "the forward join must be non-reversed, got %r" % (backbone.members,)
    assert by_key["c"].keys == ("c",), \
        "the reversed candidate c must be left a singleton, got %r" \
        % (by_key["c"].keys,)


@check("the selection-order tie-break holds on the native route (mutation target)")
def _():
    # At a T-junction, three or more curve ends meet. The solver must take the
    # EARLIEST unused candidate in caller-supplied order (deterministic
    # selection-order behaviour). Here the backbone (b0..b2) is listed before
    # the spurs (s0), so the backbone must win the junction, not a spur.
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    segs = [
        ("b0", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ("b1", (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        ("b2", (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)),
        ("s0", (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),  # spur at the b0/b1 joint
        ("s1", (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)),  # spur at the b1/b2 joint
    ]
    na = jc.chains(segs, 1e-5, _force_native=True)
    # The backbone must form one 3-member chain; each spur joins nothing.
    by_key = {c.members[0][0]: c for c in na}
    backbone = by_key["b0"]
    assert backbone.keys == ("b0", "b1", "b2"), \
        "the backbone must be the chain (lowest-index tie-break), got %r" \
        % (backbone.keys,)
    assert backbone.closed is False
    assert by_key["s0"].keys == ("s0",) and by_key["s1"].keys == ("s1",)


@check("native == python on a closed loop (ring detection)")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    segs = [
        ("a", (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ("b", (5.0, 0.0, 0.0), (5.0, 5.0, 0.0)),
        ("c", (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)),
        ("d", (0.0, 5.0, 0.0), (0.0, 0.0, 0.0)),
    ]
    py = norm(python_chains(segs, 1e-5))
    na = norm(jc.chains(segs, 1e-5, _force_native=True))
    assert py == na
    assert py[0][1] is True, "a 4-segment loop must be detected closed"


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


@check("reversal differential corpus and first-appearance chain ordering")
def _():
    _reset()
    rng = random.Random(42)
    for n in (1, 2, 10, 511, 512, 513, 1000):
        segs = ordered(n)
        segs = [(k, e, s) if i % 2 else (k, s, e)
                for i, (k, s, e) in enumerate(segs)]
        assert norm(jc.chains(segs, 1e-5)) == norm(python_chains(segs, 1e-5))
    for _ in range(100):
        segs = ambiguous(12) + disjoint(4)
        rng.shuffle(segs)
        # Keys need not sort like selection order; give every input its own key.
        segs = [(f"key-{1000-i}", s, e) for i, (_, s, e) in enumerate(segs)]
        got = jc.chains(segs, 1e-5)
        assert norm(got) == norm(python_chains(segs, 1e-5))
        indices = {key: i for i, (key, _, _) in enumerate(segs)}
        first = [min(indices[k] for k in c.keys) for c in got]
        assert first == sorted(first)
        assert sorted(k for c in got for k in c.keys) == sorted(indices)


@check("CPython exact tolerance witness joins; adjacent below does not; never closed")
def _():
    _reset()
    d = (-.4702396670038951, -.5067449846120331, .12273626832630158)
    tol = math.dist((0., 0., 0.), d)
    segs = [("a", (-10., -10., -10.), (0., 0., 0.)),
            ("b", d, (10., 10., 10.))]
    assert math.dist(segs[0][1], segs[0][2]) > tol
    assert math.dist(segs[1][1], segs[1][2]) > tol
    for threshold, count in ((math.nextafter(tol, -math.inf), 2),
                             (tol, 1), (math.nextafter(tol, math.inf), 1)):
        before = native._native_joinchain_calls
        got = jc.chains(segs, threshold)
        assert native._native_joinchain_calls == before + 1
        assert norm(got) == norm(python_chains(segs, threshold)), threshold.hex()
        assert len(got) == count and all(not c.closed for c in got)
        if count == 1:
            assert got[0].members == (("a", False), ("b", False))


failed = 0
for name, status, detail in results:
    print("[%s] %s" % (status, name) + ("  -- %s" % detail if detail else ""))
    failed += status == "FAIL"
print("\n%d/%d passed" % (len(results) - failed, len(results)))
TMP.cleanup()
sys.exit(1 if failed else 0)
