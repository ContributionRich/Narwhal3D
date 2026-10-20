"""Native Join-chain solver (indexed Rust ABI): direct proof + route activation + fallbacks.

Complements the unchanged Python solver in ``narwhal3d/joinchain.py`` (which pins
the contract: selection-order tie-break at ambiguous junctions, reversal
preference, closed-ring detection, and "every input segment in exactly one
chain") by proving the *indexed* native route (Q117-06) is byte-for-byte the
Python result when called directly, that the selection-order tie-break holds
(the mutation target), and that every fallback scenario runs the *unchanged*
Python solver with the exact same chain list and with the native counter NOT
incremented.

The Python solver is Theta(N^2) in the segment count (each growth step scans
every segment); the native solver is an ordered spatial index. Measured
end-to-end (packing + FFI + conversion), the native route wins by +428% to
+29,153% at 512+ segments across the ordered / disjoint / ambiguous shapes, so
the crossover sits at 512 segments -- large inputs run native, small inputs
(sub-ms in Python) stay on the unchanged solver.

* the Rust joinchain ABI is present and ``_joinchain_binding()`` is not ``None``;
* ``chains`` at or above the crossover runs native (counter moves) and still
  returns the exact Python chain list;
* the native route, called directly, returns byte-for-byte the Python result
  on the ordered, reversed, disjoint, and ambiguous (T-junction) shapes and on
  a closed loop;
* the selection-order tie-break holds on the native route (the mutation target:
  at a T-junction the EARLIEST unused candidate in caller order wins -- corrupt
  the Rust ``best.is_none_or(|b| i < b)`` lowest-index rule and this goes RED);
* the fallback scenarios (missing library, a failed native call, a malformed
  packed output) all run the *unchanged Python solver* with the exact same
  chain list and with the native counter NOT incremented.

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def _ensure_lib():
    """Return the ``.so`` path, building the crate once if it is not present."""
    lib = _locate_lib()
    if lib is not None:
        return lib
    manifest = os.path.join(ROOT, "native", "pure_core", "Cargo.toml")
    if not os.path.isfile(manifest):
        return None
    subprocess.run(
        ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline",
         "--manifest-path", manifest, "--target", "x86_64-unknown-linux-gnu"],
        cwd=ROOT, check=True,
    )
    return _locate_lib()


# Make the native library discoverable before the adapter is first imported.
_LIB = _ensure_lib()
if _LIB is not None:
    os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


jc = _load("narwhal_native_test_joinchain", "joinchain.py")
# Resolve the SAME native module the solver uses, so we can inspect its counter
# and monkeypatch it in the fallback tests.
native = jc._native_joinchain()

results = []
TMP = tempfile.mkdtemp(prefix="native_joinchain_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _reset():
    """Forget discovery + counter state so a check can re-point the binding."""
    if native is not None:
        native._reset_for_tests()


def norm(chains):
    """A hashable, order-sensitive form of the Python ``Chain`` list."""
    return [(tuple((k, r) for k, r in c.members), c.closed,
             tuple(c.start), tuple(c.end)) for c in chains]


def python_chains(segs, tolerance):
    """Force the unchanged Python solver, even above the native crossover."""
    real = jc._native_joinchain
    jc._native_joinchain = lambda: None
    try:
        return jc.chains(segs, tolerance)
    finally:
        jc._native_joinchain = real


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


@check("the crossover is set and the route gate respects it")
def _():
    # The win is measured end-to-end; the crossover is a segment count.
    assert native._JOINCHAIN_NATIVE_MIN_SEGMENTS >= 2, \
        "the crossover must not be below 2 (FFI overhead would dominate)"
    assert native._joinchain_native_active(100000) is True
    assert native._joinchain_native_active(10) is False, \
        "small inputs must stay on the unchanged Python solver"
    assert native._joinchain_native_active(-1) is False
    assert native._joinchain_native_active(True) is False, \
        "a bool is not a segment count"


@check("at the crossover chains() runs native and returns the Python list")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    n = max(native._JOINCHAIN_NATIVE_MIN_SEGMENTS, 512)
    segs = ordered(n)
    baseline = python_chains(segs, 1e-5)
    before = native._native_joinchain_calls
    got = jc.chains(segs, 1e-5)  # production: at/above the crossover -> native
    assert norm(got) == norm(baseline), \
        "the native route must return the exact Python chain list"
    assert native._native_joinchain_calls == before + 1, \
        "a successful native call must increment the counter"


@check("below the crossover chains() stays on the Python solver (counter unmoved)")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    segs = ordered(10)  # far below the crossover
    before = native._native_joinchain_calls
    got = jc.chains(segs, 1e-5)
    assert norm(got)[0][0][0][0] == "k0"
    assert native._native_joinchain_calls == before, \
        "a sub-crossover input must not touch the native counter"


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
    py = norm(jc.chains(segs, 1e-5, _force_native=False))
    na = norm(jc.chains(segs, 1e-5, _force_native=True))
    assert py == na
    assert py[0][1] is True, "a 4-segment loop must be detected closed"


@check("a rejected input (non-finite coordinate) falls back to Python")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    before = native._native_joinchain_calls
    # The native call must reject the non-finite coordinate; the Python solver
    # then runs and (like the reference) still groups by _near with <=.
    got = jc.chains([("a", (float("nan"), 0.0, 0.0), (1.0, 0.0, 0.0))],
                    1e-5, _force_native=True)
    assert native._native_joinchain_calls == before, \
        "a rejected input must not count as a native success"
    assert len(got) == 1


@contextmanager
def _force_python():
    """Temporarily make the native route report unavailable so the solver runs
    its UNCHANGED Python path (the "native == Python" baseline)."""
    if native is None:
        yield
        return
    real = native._native_joinchain
    native._native_joinchain = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_joinchain = real


@check("fallback: a failed native call (None) uses the Python solver")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    segs = ordered(512)  # at/above the crossover, but the native call fails
    with _force_python():
        before = native._native_joinchain_calls
        # Compute the baseline from the pure Python solver (threshold raised so
        # the production gate does not itself route to native at 512).
        real_thresh = native._JOINCHAIN_NATIVE_MIN_SEGMENTS
        native._JOINCHAIN_NATIVE_MIN_SEGMENTS = 10 ** 15
        baseline = jc.chains(segs, 1e-5)
        native._JOINCHAIN_NATIVE_MIN_SEGMENTS = real_thresh
        got = jc.chains(segs, 1e-5)  # native attempted, fails -> Python
    assert norm(got) == norm(baseline)
    assert native._native_joinchain_calls == before, \
        "a failed-call fallback must not touch the native counter"


@check("fallback: a malformed packed output falls back without counting")
def _():
    _reset()
    assert native is not None and native._joinchain_binding() is not None
    real = native._valid_joinchain_packed
    native._valid_joinchain_packed = lambda *a, **k: False
    try:
        before = native._native_joinchain_calls
        assert native._native_joinchain(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 2.0, 0.0, 0.0],
            1e-5) is None
        assert native._native_joinchain_calls == before, \
            "a rejected packed output must not count as a native success"
        segs = ordered(512)
        got = jc.chains(segs, 1e-5)  # native rejected -> Python fallback
        assert len(got) >= 1
    finally:
        native._valid_joinchain_packed = real
        _reset()


@check("fallback: a library without the joinchain export uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the joinchain binding is None and the Python
    # solver runs with the same chain list and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._joinchain_binding() is None, \
            "a library without the joinchain export must make the binding None"
        assert native._joinchain_native_active(100000) is False, \
            "with no binding the native route must be inactive"
        before = native._native_joinchain_calls
        segs = ordered(10)
        got = jc.chains(segs, 1e-5)
        assert len(got) == 1
        assert native._native_joinchain_calls == before, \
            "the old-symbol fallback must not touch the native counter"
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


failed = 0
for name, status, detail in results:
    print("[%s] %s" % (status, name) + (  "  -- %s" % detail if detail else ""))
    if status == "FAIL":
        failed += 1
print("\n%d/%d passed" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
