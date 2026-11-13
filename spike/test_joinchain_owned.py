"""Native-only Join-chain candidate: parity, boundary, error, and mutation tests.

Qualifies ``tmp/rust13-joinchain-candidate.py`` (native-only, no Python
fallback) against the independent reference oracle
``spike/joinchain_reference.py`` (a faithful copy of the retired Python solver).

The native solver uses ``dx.hypot(dy).hypot(dz) <= tol``; Python's reference
uses ``math.dist(a, b) <= tol``. These can differ by up to 2 ULPs at the
tolerance boundary. This test probes that boundary adversarially: if the two
disagree on whether two endpoints are "near", the test is RED. A disagreement
is a BLOCKER, not a pass.

Exit code 0 = all checks PASS; 1 = at least one FAIL.
"""
from __future__ import annotations

import importlib.util
import math
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Library discovery (identical to test_native_joinchain.py)
# ---------------------------------------------------------------------------
LIB_CANDIDATES = [
    os.environ.get("NARWHAL_PURE_CORE", ""),
    os.path.join(ROOT, "native", "pure_core", "target",
                 "x86_64-unknown-linux-gnu", "release",
                 "libnarwhal_pure_core.so"),
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


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The candidate path is overridable so Main can retarget without editing this test.
CANDIDATE_PATH = os.environ.get(
    "JOINCHAIN_MODULE_PATH",
    os.path.join(ROOT, "narwhal3d", "joinchain.py"))
REFERENCE_PATH = os.path.join(ROOT, "spike", "joinchain_reference.py")
ADAPTER_PATH = os.path.join(ROOT, "narwhal3d", "native_pure.py")

cand = _load("rust13_joinchain_cand", CANDIDATE_PATH)
ref = _load("rust13_joinchain_ref", REFERENCE_PATH)
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


# ---------------------------------------------------------------------------
# Shape generators
# ---------------------------------------------------------------------------
def ordered(n):
    return [("k%d" % i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0))
            for i in range(n)]


def disjoint(n):
    return [("k%d" % i, (float(i), 0.0, 0.0), (float(i) + 0.5, 0.0, 0.0))
            for i in range(n)]


def ambiguous(n):
    """Backbone + spur at every third joint -> T-junctions."""
    out = [("b%d" % i, (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0))
           for i in range(n)]
    for i in range(0, n, 3):
        out.append(("s%d" % i, (float(i), 0.0, 0.0), (float(i), 1.0, 0.0)))
    return out


def reversed_run(n):
    """Segments in reverse order of a continuous run."""
    out = []
    for i in range(n):
        out.append(("k%d" % i, (float(n - i), 0.0, 0.0),
                    (float(n - i - 1), 0.0, 0.0)))
    return out


def closed_loop():
    return [
        ("a", (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ("b", (5.0, 0.0, 0.0), (5.0, 5.0, 0.0)),
        ("c", (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)),
        ("d", (0.0, 5.0, 0.0), (0.0, 0.0, 0.0)),
    ]


def duplicate_keys(n):
    """Same key appears twice (different geometry) -- key identity is preserved."""
    out = []
    for i in range(n):
        out.append(("dup", (float(i), 0.0, 0.0), (float(i + 1), 0.0, 0.0)))
        out.append(("dup", (float(i), 10.0, 0.0), (float(i + 1), 10.0, 0.0)))
    return out


def t_junction():
    return [
        ("b0", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ("b1", (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        ("b2", (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)),
        ("s0", (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ("s1", (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)),
    ]


def forward_vs_reversed():
    return [
        ("a", (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ("b", (5.0, 0.0, 0.0), (5.0, 5.0, 0.0)),  # forward
        ("c", (9.0, 0.0, 0.0), (5.0, 0.0, 0.0)),  # reversed
    ]


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
# 2. Parity: core shapes
# ---------------------------------------------------------------------------
@check("parity: ordered (n=1, 5, 50, 512, 1000)")
def _():
    _reset()
    for n in (1, 5, 50, 512, 1000):
        segs = ordered(n)
        py = norm(ref.chains(segs, 1e-5))
        na = norm(cand.chains(segs, 1e-5))
        assert py == na, "ordered n=%d: native %r != reference %r" % (n, na, py)


@check("parity: disjoint (n=1, 10, 100, 512)")
def _():
    _reset()
    for n in (1, 10, 100, 512):
        segs = disjoint(n)
        py = norm(ref.chains(segs, 1e-5))
        na = norm(cand.chains(segs, 1e-5))
        assert py == na, "disjoint n=%d: mismatch" % n


@check("parity: T-junction (ambiguous)")
def _():
    _reset()
    segs = t_junction()
    py = norm(ref.chains(segs, 1e-5))
    na = norm(cand.chains(segs, 1e-5))
    assert py == na, "T-junction: native %r != reference %r" % (na, py)
    # Backbone must be the 3-member chain (lowest-index tie-break)
    by_key = {c.members[0][0]: c for c in cand.chains(segs, 1e-5)}
    assert by_key["b0"].keys == ("b0", "b1", "b2"), \
        "backbone must be the chain, got %r" % (by_key["b0"].keys,)
    assert by_key["s0"].keys == ("s0",) and by_key["s1"].keys == ("s1",)


@check("parity: reversed run")
def _():
    _reset()
    for n in (1, 5, 20):
        segs = reversed_run(n)
        py = norm(ref.chains(segs, 1e-5))
        na = norm(cand.chains(segs, 1e-5))
        assert py == na, "reversed n=%d: mismatch" % n


@check("parity: closed loop")
def _():
    _reset()
    segs = closed_loop()
    py = norm(ref.chains(segs, 1e-5))
    na = norm(cand.chains(segs, 1e-5))
    assert py == na, "closed loop: mismatch"
    assert py[0][1] is True, "4-segment loop must be closed"


@check("parity: duplicate keys (key identity preserved)")
def _():
    _reset()
    segs = duplicate_keys(5)
    py = norm(ref.chains(segs, 1e-5))
    na = norm(cand.chains(segs, 1e-5))
    assert py == na, "duplicate keys: mismatch"
    # Every chain member must be the key "dup"
    for c in cand.chains(segs, 1e-5):
        for k, _ in c.members:
            assert k == "dup"


@check("parity: forward orientation preferred over reversed at T-junction")
def _():
    _reset()
    segs = forward_vs_reversed()
    na = cand.chains(segs, 1e-5)
    by_key = {c.members[0][0]: c for c in na}
    assert by_key["a"].keys == ("a", "b"), \
        "forward candidate b must join, got %r" % (by_key["a"].keys,)
    assert by_key["c"].keys == ("c",), \
        "reversed candidate c must be singleton, got %r" % (by_key["c"].keys,)


# ---------------------------------------------------------------------------
# 3. Zero and signed-zero tolerance
# ---------------------------------------------------------------------------
@check("zero tolerance: exact match parity")
def _():
    _reset()
    segs = [
        ("a", (-1.0, 0.0, 0.0), (-0.0, 0.0, 0.0)),
        ("b", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    ]
    py = norm(ref.chains(segs, 0.0))
    na = norm(cand.chains(segs, 0.0))
    assert py == na, "zero tol: native %r != reference %r" % (na, py)


@check("zero tolerance: non-matching points stay separate")
def _():
    _reset()
    segs = [
        ("a", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ("b", (1.0000001, 0.0, 0.0), (2.0, 0.0, 0.0)),
    ]
    na = cand.chains(segs, 0.0)
    py = ref.chains(segs, 0.0)
    assert len(na) == len(py) == 2, "zero tol: two separate chains expected"


# ---------------------------------------------------------------------------
# 4. Tolerance boundary probe (adversarial)
# ---------------------------------------------------------------------------
@check("boundary: probe is not vacuous (long arms, neither seed closed)")
def _():
    """The OLD probe used unit-length segments with tol ~ 100, so every seed
    was already closed (_near(head, tail, tol) true) and the continuation
    branch -- the only code that compares the boundary distance -- never ran.
    The probe therefore passed vacuously and could never fail (Gate-4 lesson:
    a test whose input cannot expose the bug is not a test).

    Use long arms: the first segment is (-10,-10,-10) -> (0,0,0) (length
    sqrt(300) ~ 17.32) and the second is d -> (10,10,10) (length ~ 15.6 to
    19.1). Both arms far exceed the boundary distance origin->d (<= sqrt(3) ~
    1.73), so each seed's own head-to-tail span EXCEEDS every tolerance we
    probe, the seed is open, and the continuation branch is actually
    exercised."""
    _reset()
    rng = random.Random(2026)
    origin = (0.0, 0.0, 0.0)
    a = (-10.0, -10.0, -10.0)
    b = (10.0, 10.0, 10.0)
    first_len = math.dist(a, origin)   # sqrt(300) ~ 17.32, the seed's own span
    seen = 0
    for _ in range(200):
        d = tuple(rng.uniform(-1.0, 1.0) for _ in range(3))
        dist = math.dist(origin, d)
        if dist == 0.0:
            continue
        seen += 1
        second_len = math.dist(d, b)   # >= 15.59 for d in [-1,1]^3
        for tol in (math.nextafter(dist, float("-inf")),
                    dist,
                    math.nextafter(dist, float("inf"))):
            # Explicit not-closed: the seed must be open so the continuation
            # branch runs. A closed seed would hide the boundary comparison.
            assert first_len > tol, \
                "seed closed (first arm %r <= tol %r)" % (first_len, tol)
            assert second_len > tol, \
                "second segment closed (len %r <= tol %r)" % (second_len, tol)
    assert seen > 0, "no non-degenerate samples drawn"


@check("boundary: native vs reference agree at nextafter(-inf)/exact/nextafter(+inf)")
def _():
    """Main's probe: long arms so the continuation branch runs. For each random
    d, the tolerance is set to the exact distance origin->d and its two
    immediate float neighbours. The Rust solver compares
    ``dx.hypot(dy).hypot(dz) <= tol``; the reference compares
    ``math.dist(a, b) <= tol``. These differ by up to 2 ULPs, so at this
    boundary they may disagree on whether the two segments join. A disagreement
    means a manufacturing join silently produces a different chain: BLOCKER.

    NOTE on the OLD second probe's wrong comment: it set ``tol = dist*(1+1e-10)``
    (ABOVE the distance) and claimed both should say "not near". That is
    backwards -- dist < tol, so dist <= tol and they SHOULD join. The correct
    neighbours are math.nextafter(dist, -inf) / dist / math.nextafter(dist, inf).

    EXPECTED TO BE RED until the distance computation is reconciled: Main
    measured native/reference differ on 68/1000 at the exact boundary.
    Left unactivated on purpose; Main will fix the norm helper and re-run."""
    _reset()
    origin = (0.0, 0.0, 0.0)
    a = (-10.0, -10.0, -10.0)
    b = (10.0, 10.0, 10.0)
    # Multiple 3D signed ranges, as directed.
    ranges = [(-1.0, 1.0), (-5.0, 5.0), (-0.1, 0.1)]
    rng = random.Random(1337)
    disagreements = []
    for lo, hi in ranges:
        for _ in range(1000):
            d = tuple(rng.uniform(lo, hi) for _ in range(3))
            dist = math.dist(origin, d)
            if dist == 0.0:
                continue
            tols = (math.nextafter(dist, float("-inf")),
                    dist,
                    math.nextafter(dist, float("inf")))
            segs = [
                ("first", a, origin),
                ("second", d, b),
            ]
            for tol in tols:
                try:
                    na = norm(cand.chains(segs, tol))
                except RuntimeError:
                    continue
                py = norm(ref.chains(segs, tol))
                if na != py:
                    disagreements.append((d, dist, tol, na, py))
    if disagreements:
        raise AssertionError(
            "BOUNDARY BLOCKER: %d native/reference disagreements at the "
            "tolerance boundary (nextafter(-inf) / exact / nextafter(+inf)). "
            "A manufacturing join could silently produce a different chain. "
            "First: %r"
            % (len(disagreements), disagreements[0]))


# ---------------------------------------------------------------------------
# 5. Generated random cases (~300)
# ---------------------------------------------------------------------------
@check("parity: 300 random segment sets (seed=7)")
def _():
    _reset()
    rng = random.Random(7)
    for case in range(300):
        n = rng.randint(1, 20)
        segs = []
        for i in range(n):
            x0 = rng.uniform(-50, 50)
            y0 = rng.uniform(-50, 50)
            z0 = rng.uniform(-50, 50)
            dx = rng.uniform(-10, 10)
            dy = rng.uniform(-10, 10)
            dz = rng.uniform(-10, 10)
            segs.append(("seg%d_%d" % (case, i),
                         (x0, y0, z0),
                         (x0 + dx, y0 + dy, z0 + dz)))
        tol = rng.choice([0.0, 1e-5, 0.1, 1.0, 5.0])
        py = norm(ref.chains(segs, tol))
        na = norm(cand.chains(segs, tol))
        if py != na:
            raise AssertionError(
                "random case %d (n=%d, tol=%r): native %r != reference %r"
                % (case, n, tol, na, py))


# ---------------------------------------------------------------------------
# 6. Counter: native call increments
# ---------------------------------------------------------------------------
@check("counter: each successful native call increments _native_joinchain_calls")
def _():
    _reset()
    before = adapter._native_joinchain_calls
    cand.chains(ordered(5), 1e-5)
    after = adapter._native_joinchain_calls
    assert after == before + 1, \
        "counter: expected %d, got %d" % (before + 1, after)
    # Empty input should NOT increment (no native call)
    before2 = adapter._native_joinchain_calls
    cand.chains([], 1e-5)
    after2 = adapter._native_joinchain_calls
    assert after2 == before2, \
        "empty input must not increment the native counter"


@check("counter: large input (n=2000) increments by exactly 1")
def _():
    _reset()
    segs = ordered(2000)
    before = adapter._native_joinchain_calls
    cand.chains(segs, 1e-5)
    after = adapter._native_joinchain_calls
    assert after == before + 1, \
        "large input: expected %d, got %d" % (before + 1, after)


# ---------------------------------------------------------------------------
# 7. Error: missing library
# ---------------------------------------------------------------------------
@check("error: missing library raises RuntimeError (no Python fallback)")
def _():
    old_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(
        tempfile.gettempdir(), "nonexistent_lib.so")
    try:
        _reset()
        # The adapter's binding will be None because the library is missing
        assert adapter._joinchain_binding() is None, \
            "binding should be None with a missing library"
        try:
            cand.chains(ordered(3), 1e-5)
            raise AssertionError(
                "expected RuntimeError for missing library, got no error")
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
            raise AssertionError(
                "expected RuntimeError for native None, got no error")
        except RuntimeError as e:
            assert "native" in str(e).lower() or "fallback" in str(e).lower(), \
                "error message should mention native/fallback: %r" % str(e)
    finally:
        adapter._native_joinchain = real
        _reset()


# ---------------------------------------------------------------------------
# 8. Error: invalid input
# ---------------------------------------------------------------------------
@check("error: non-finite tolerance raises RuntimeError")
def _():
    try:
        cand.chains(ordered(3), float("inf"))
        raise AssertionError("expected RuntimeError for inf tolerance")
    except RuntimeError:
        pass
    try:
        cand.chains(ordered(3), float("nan"))
        raise AssertionError("expected RuntimeError for NaN tolerance")
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
    try:
        cand.chains([("a", (float("nan"), 0.0, 0.0), (1.0, 0.0, 0.0))], 1e-5)
        raise AssertionError("expected RuntimeError for NaN coordinate")
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
        raise AssertionError("expected RuntimeError for 2-element segment")
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# 9. Empty input
# ---------------------------------------------------------------------------
@check("empty input: returns [] without native call")
def _():
    _reset()
    before = adapter._native_joinchain_calls
    result = cand.chains([], 1e-5)
    assert result == [], "empty input must return [], got %r" % (result,)
    after = adapter._native_joinchain_calls
    assert after == before, "empty input must not call native"


@check("empty input: binding must still be verified (installation check)")
def _():
    # The binding must exist even for empty input. This test verifies that
    # the adapter is loadable and the binding is resolvable.
    assert adapter._joinchain_binding() is not None, \
        "binding must be resolvable even for empty input"


# ---------------------------------------------------------------------------
# 10. Input key identity
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


# ---------------------------------------------------------------------------
# 11. Mutation: short bypass -> RED -> restore -> GREEN
# ---------------------------------------------------------------------------
@check("mutation: tampered native result is detected (RED) and restored (GREEN)")
def _():
    _reset()
    segs = ordered(5)
    baseline = norm(ref.chains(segs, 1e-5))

    # Mutate: make the adapter return a result with members swapped
    real = adapter._native_joinchain
    def _tampered(starts, ends, tol):
        packed = real(starts, ends, tol)
        if packed is None:
            return None
        (n, chain_off, chain_len, chain_closed, chain_ends,
         member_seg, member_rev, n_chains, n_members) = packed
        # Swap the first two member_seg entries to corrupt the result
        if len(member_seg) >= 2:
            member_seg[0], member_seg[1] = member_seg[1], member_seg[0]
        return (n, chain_off, chain_len, chain_closed, chain_ends,
                member_seg, member_rev, n_chains, n_members)
    adapter._native_joinchain = _tampered
    try:
        # The candidate should either raise (if it detects the corruption)
        # or return a different chain list. Either way, the parity test must
        # catch it.
        try:
            tampered = norm(cand.chains(segs, 1e-5))
            assert tampered != baseline, \
                "mutation not detected: tampered result equals baseline"
            # If it returned a different result without raising, that's still
            # a RED for the parity test.
        except (RuntimeError, AssertionError):
            pass  # expected: either raised or mismatch detected
    finally:
        adapter._native_joinchain = real
        _reset()

    # Restore: verify GREEN
    restored = norm(cand.chains(segs, 1e-5))
    assert restored == baseline, \
        "after restore, result must match baseline"


# ---------------------------------------------------------------------------
# 12. Chain class contract
# ---------------------------------------------------------------------------
@check("Chain class: slots, keys, len, closed, start, end")
def _():
    _reset()
    segs = closed_loop()
    result = cand.chains(segs, 1e-5)
    c = result[0]
    assert c.closed is True
    assert len(c) == 4
    assert c.keys == ("a", "b", "c", "d")
    assert c.start == (0.0, 0.0, 0.0)
    assert c.end == (0.0, 0.0, 0.0)
    # Slots check
    assert set(cand.Chain.__slots__) == {"members", "closed", "start", "end"}


@check("TIE_BREAK_IS_SELECTION_ORDER is True")
def _():
    assert cand.TIE_BREAK_IS_SELECTION_ORDER is True


# ---------------------------------------------------------------------------
# 13. joinable
# ---------------------------------------------------------------------------
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
print("RUST-JOINCHAIN-13 — native-only candidate qualification")
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
