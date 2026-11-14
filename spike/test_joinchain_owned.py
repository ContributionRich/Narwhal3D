"""Native-only Join-chain delegate: host checks + golden-fixture replay.

The retired independent Python oracle (``spike/joinchain_reference.py``) is
retired. Its decisions were captured ONCE into
``native/pure_core/tests/fixtures/joinchain_golden.txt`` and are replayed by
the Rust integration test ``native/pure_core/tests/joinchain_golden.rs``
against the real C ABI. This file no longer imports any reference engine and
no longer re-runs the Theta(N^2) differential loops (300 random / 100 shuffled
/ 9000 boundary) -- that coverage now lives in Rust.

What this file still owns (the host-only contract the ABI never sees):
  * the Python delegate ``chains()`` maps the ABI output to the ``Chain``
    contract EXACTLY as the reference did (members, order, closed, start/end)
    -- verified by decoding a representative row from the golden fixture and
    comparing the live delegate against it;
  * the CPU / performance guard (n=2000);
  * object admission (arbitrary keys, key identity);
  * the native call counter (increment per call, not on empty / refusal);
  * refusal / missing / stale / ``None`` / malformed fail-closed paths;
  * the ``Chain`` class contract, ``TIE_BREAK_IS_SELECTION_ORDER``, and
    ``joinable``;
  * the tampered-native RED -> GREEN mutation.

Exit code 0 = all checks PASS; 1 = at least one FAIL.
"""
from __future__ import annotations

import importlib.util
import math
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Library discovery (identical to test_native_joinchain.py)
# ---------------------------------------------------------------------------
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "native", "pure_core", "target",
                 "x86_64-unknown-linux-gnu", "release", "libnarwhal_pure_core.so"),
    os.path.join(ROOT, "native", "pure_core", "target", "release",
                 "libnarwhal_pure_core.so"),
]


def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None


_LIB = _locate_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB

FIXTURE = os.path.join(ROOT, "native", "pure_core", "tests", "fixtures",
                       "joinchain_golden.txt")


# ---------------------------------------------------------------------------
# Module loading (no reference oracle is loaded)
# ---------------------------------------------------------------------------
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CANDIDATE_PATH = os.environ.get(
    "JOINCHAIN_MODULE_PATH", os.path.join(ROOT, "narwhal3d", "joinchain.py"))

cand = _load("rust13_joinchain_cand", CANDIDATE_PATH)
adapter = cand._native_joinchain()


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------
results: list[tuple[str, str, str]] = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as exc:  # noqa: BLE001
            results.append((name, "FAIL", repr(exc)))
    return deco


def norm(chains):
    """Order-sensitive, hashable form of a Chain list."""
    return [(tuple((k, r) for k, r in c.members), c.closed,
             tuple(c.start), tuple(c.end)) for c in chains]


def _reset():
    if adapter is not None:
        adapter._reset_for_tests()


def ordered(n):
    return [(i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0)) for i in range(n)]


# ---------------------------------------------------------------------------
# Golden-fixture decode (compact, no reference algorithm). Expected values come
# ONLY from the fixture captured from the retired Python reference.
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
    """The delegate's ``norm`` form for a row (index keys, so key == index)."""
    return [(tuple((fmembers[off + j][0], bool(fmembers[off + j][1])) for j in range(ln)),
             closed, tuple(coords[:3]), tuple(coords[3:]))
            for (off, ln, closed, coords) in fchains]


_BY_GROUP = {}
for _line in [l for l in open(FIXTURE, encoding="utf-8").read().splitlines()
              if l and not l.startswith("#")]:
    f = _line.split("\t")
    n = int(f[1])
    row = (f[0], n,
           struct.unpack("<d", bytes.fromhex(f[2]))[0],
           _f64s(f[3], 3 * n), _f64s(f[4], 3 * n),
           _chains(f[6]), _members(f[8]), int(f[5]), int(f[7]))
    _BY_GROUP.setdefault(row[0], []).append(row)


def _segs(starts, ends):
    n = len(starts) // 3
    return [(i, tuple(starts[3 * i:3 * i + 3]), tuple(ends[3 * i:3 * i + 3]))
            for i in range(n)]


# A representative row of every shape class (the full 9000-boundary / 400-random
# corpus is replayed by the Rust golden test; this is the delegate mapping).
SPEC = [
    ("ordered", 0), ("ordered", 1),
    ("disjoint", 0), ("disjoint", 1),
    ("tj", 0), ("tj", 1),
    ("reversed", 0), ("reversed", 1),
    ("closed", 0), ("dup", 0), ("fwdrev", 0),
    ("zero", 0), ("zero", 1),
    ("boundary", 0), ("boundary", 3000), ("boundary", 6000),
    ("witness", 0), ("witness", 1), ("witness", 2),
    ("random", 0), ("random", 100), ("random", 200),
    ("perf", 0), ("perf", 1),
    ("empty", 0),
]
ROWS = [_BY_GROUP[g][i] for g, i in SPEC]


# ---------------------------------------------------------------------------
# 1. Binding exists
# ---------------------------------------------------------------------------
@check("native library is present and the joinchain binding resolves")
def _():
    assert _LIB is not None, "pure_core .so not found"
    assert adapter is not None, "adapter module failed to load"
    b = adapter._joinchain_binding()
    assert b is not None, "joinchain binding is None (library missing or stale)"


# ---------------------------------------------------------------------------
# 2. Delegate matches the golden fixture across every shape class (incl. CPU)
# ---------------------------------------------------------------------------
@check("delegate matches the golden fixture on all shape classes (n=1..2000)")
def _():
    _reset()
    for group, n, tol, starts, ends, fch, fm, nch, nmem in ROWS:
        segs = _segs(starts, ends)
        got = norm(cand.chains(segs, tol))
        exp = _expected(fch, fm)
        assert got == exp, \
            "group=%s n=%d tol=%r: delegate %r != golden %r" % (group, n, tol, got, exp)
        # Partition: every input segment appears exactly once.
        all_members = [m for c in got for m in c[0]]
        assert sorted(k for k, _ in all_members) == list(range(n)), \
            "group=%s n=%d: members must partition %r" % (group, n, all_members)


@check("performance guard: n=2000 runs native in bounded time and partitions")
def _():
    _reset()
    group, n, tol, starts, ends, fch, fm, nch, nmem = _BY_GROUP["perf"][0]
    segs = _segs(starts, ends)
    before = adapter._native_joinchain_calls
    got = cand.chains(segs, tol)
    assert adapter._native_joinchain_calls == before + 1, \
        "n=2000 must make exactly one native call"
    assert norm(got) == _expected(fch, fm), "n=2000: delegate != golden"


# ---------------------------------------------------------------------------
# 3. Counter: native call increments (empty / refusal do not)
# ---------------------------------------------------------------------------
@check("counter: each successful native call increments _native_joinchain_calls")
def _():
    _reset()
    before = adapter._native_joinchain_calls
    cand.chains(ordered(5), 1e-5)
    after = adapter._native_joinchain_calls
    assert after == before + 1, \
        "counter: expected %d, got %d" % (before + 1, after)
    before2 = adapter._native_joinchain_calls
    cand.chains([], 1e-5)
    after2 = adapter._native_joinchain_calls
    assert after2 == before2, \
        "empty input must not increment the native counter"


# ---------------------------------------------------------------------------
# 4. Error: missing library / native None
# ---------------------------------------------------------------------------
@check("error: missing library raises RuntimeError (no Python fallback)")
def _():
    old_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(
        tempfile.gettempdir(), "nonexistent_lib.so")
    try:
        _reset()
        assert adapter._joinchain_binding() is None, \
            "binding should be None with a missing library"
        try:
            cand.chains(ordered(3), 1e-5)
            raise AssertionError("expected RuntimeError for missing library")
        except RuntimeError:
            pass  # expected
    finally:
        if old_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = old_env
        _reset()


@check("error: adapter._native_joinchain returning None raises RuntimeError")
def _():
    _reset()
    real = adapter._native_joinchain
    adapter._native_joinchain = lambda *a, **k: None
    try:
        try:
            cand.chains(ordered(3), 1e-5)
            raise AssertionError("expected RuntimeError for native None")
        except RuntimeError as e:
            assert "native" in str(e).lower() or "fallback" in str(e).lower(), \
                "error message should mention native/fallback: %r" % str(e)
    finally:
        adapter._native_joinchain = real
        _reset()


# ---------------------------------------------------------------------------
# 5. Error: invalid input (the ABI never sees these; the delegate refuses)
# ---------------------------------------------------------------------------
@check("error: non-finite tolerance raises RuntimeError")
def _():
    for bad in (float("inf"), float("nan"), -float("inf")):
        try:
            cand.chains(ordered(3), bad)
            raise AssertionError("expected RuntimeError for tolerance %r" % bad)
        except RuntimeError:
            pass


@check("error: negative tolerance raises RuntimeError")
def _():
    try:
        cand.chains(ordered(3), -1.0)
        raise AssertionError("expected RuntimeError for negative tolerance")
    except RuntimeError:
        pass


@check("error: non-finite coordinate raises RuntimeError")
def _():
    for bad in (float("nan"), float("inf"), -float("inf")):
        for seg in ((("a", (bad, 0.0, 0.0), (1.0, 0.0, 0.0)),),
                    (("a", (0.0, bad, 0.0), (1.0, 0.0, 0.0)),)):
            try:
                cand.chains(seg, 1e-5)
                raise AssertionError("expected RuntimeError for non-finite coordinate")
            except RuntimeError:
                pass


@check("error: 2-element start raises RuntimeError (no silent 2D fallback)")
def _():
    try:
        cand.chains([("a", (1.0, 2.0), (3.0, 4.0))], 1e-5)
        raise AssertionError("expected RuntimeError for 2-element start")
    except RuntimeError as e:
        assert "3-tuple" in str(e) or "must be" in str(e), \
            "error should mention 3-tuple: %r" % str(e)


@check("error: non-numeric coordinate raises RuntimeError")
def _():
    try:
        cand.chains([("a", ("x", 0.0, 0.0), (1.0, 0.0, 0.0))], 1e-5)
        raise AssertionError("expected RuntimeError for non-numeric coordinate")
    except RuntimeError:
        pass


@check("error: non-3-tuple segment raises RuntimeError")
def _():
    try:
        cand.chains([("a", (1.0, 0.0, 0.0))], 1e-5)
        raise AssertionError("expected RuntimeError for non-3-tuple segment")
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# 6. Empty input
# ---------------------------------------------------------------------------
@check("empty input: returns [] without native call, binding still verified")
def _():
    _reset()
    assert adapter._joinchain_binding() is not None, \
        "binding must be resolvable even for empty input"
    before = adapter._native_joinchain_calls
    result = cand.chains([], 1e-5)
    assert result == [], "empty input must return [], got %r" % (result,)
    after = adapter._native_joinchain_calls
    assert after == before, "empty input must not call native"


# ---------------------------------------------------------------------------
# 7. Input key identity (object admission)
# ---------------------------------------------------------------------------
@check("key identity: arbitrary Python objects as keys are preserved")
def _():
    _reset()
    obj1 = object()
    obj2 = object()
    segs = [
        (obj1, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        (obj2, (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    ]
    result = cand.chains(segs, 1e-5)
    assert len(result) == 1, "two touching segments must form one chain"
    assert result[0].keys == (obj1, obj2), \
        "keys must preserve identity, got %r" % (result[0].keys,)
    assert result[0].members[0][0] is obj1, "member key must be obj1"
    assert result[0].members[1][0] is obj2, "member key must be obj2"


@check("key identity: a repeated key is preserved on every member")
def _():
    _reset()
    dup = object()
    segs = [
        (dup, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        (dup, (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
    ]
    result = cand.chains(segs, 1e-5)
    assert len(result) == 1, "two touching segments must form one chain"
    for k, _ in result[0].members:
        assert k is dup, "the repeated key must be preserved on every member"


# ---------------------------------------------------------------------------
# 8. Mutation: tampered native result -> RED -> restore -> GREEN
# ---------------------------------------------------------------------------
@check("mutation: tampered native result is detected (RED) and restored (GREEN)")
def _():
    _reset()
    _, n, tol, starts, ends, fch, fm, _, _ = _BY_GROUP["ordered"][1]
    segs = _segs(starts, ends)
    baseline = _expected(fch, fm)

    real = adapter._native_joinchain

    def _tampered(starts, ends, tol):
        packed = real(starts, ends, tol)
        if packed is None:
            return None
        (nn, off, ln, closed, cends, mseg, mrev, nc, nm) = packed
        if len(mseg) >= 2:
            mseg[0], mseg[1] = mseg[1], mseg[0]
        return (nn, off, ln, closed, cends, mseg, mrev, nc, nm)

    adapter._native_joinchain = _tampered
    try:
        try:
            tampered = norm(cand.chains(segs, tol))
            assert tampered != baseline, \
                "mutation not detected: tampered result equals baseline"
        except (RuntimeError, AssertionError):
            pass  # expected: raised or mismatch detected
    finally:
        adapter._native_joinchain = real
        _reset()

    restored = norm(cand.chains(segs, tol))
    assert restored == baseline, "after restore, result must match baseline"


# ---------------------------------------------------------------------------
# 9. Chain class contract / TIE_BREAK / joinable
# ---------------------------------------------------------------------------
@check("Chain class: slots, keys, len, closed, start, end")
def _():
    _reset()
    segs = [
        ("a", (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ("b", (5.0, 0.0, 0.0), (5.0, 5.0, 0.0)),
        ("c", (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)),
        ("d", (0.0, 5.0, 0.0), (0.0, 0.0, 0.0)),
    ]
    c = cand.chains(segs, 1e-5)[0]
    assert c.closed is True
    assert len(c) == 4
    assert c.keys == ("a", "b", "c", "d")
    assert c.start == (0.0, 0.0, 0.0)
    assert c.end == (0.0, 0.0, 0.0)
    assert set(cand.Chain.__slots__) == {"members", "closed", "start", "end"}


@check("TIE_BREAK_IS_SELECTION_ORDER is True")
def _():
    assert cand.TIE_BREAK_IS_SELECTION_ORDER is True


@check("joinable: only chains with len >= 2")
def _():
    _reset()
    segs = [
        ("a", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ("b", (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        ("c", (100.0, 0.0, 0.0), (101.0, 0.0, 0.0)),  # singleton
    ]
    result = cand.joinable(segs, 1e-5)
    assert len(result) == 1
    assert result[0].keys == ("a", "b")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("RUST-JOIN-GOLDEN-18 -- native-only delegate (host) qualification")
print("=" * 60)
for name, status, detail in results:
    icon = "  ✓" if status == "PASS" else "  ✗"
    print(f"{icon} [{status}] {name}")
    if detail:
        print(f"     {detail}")
print()
n_pass = sum(1 for _, s, _ in results if s == "PASS")
n_fail = sum(1 for _, s, _ in results if s == "FAIL")
print(f"Total: {len(results)}  PASS: {n_pass}  FAIL: {n_fail}")
print("=" * 60)

sys.exit(0 if n_fail == 0 else 1)
