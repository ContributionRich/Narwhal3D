"""Native coordinate text parser: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_coords.py`` (which pins the prefixes, origin shortcuts,
polar / spherical / surveyor forms, the exact public error strings, and the
``format_world`` round-trip on the Python path) by proving the *native* route is
the one actually running for valid input, and that every fallback scenario runs
the *unchanged* Python parser with the exact error strings and with the native
counter NOT incremented.

* the Rust ABI is present and ``_coords_binding()`` is not ``None``;
* ``coords.parse`` activates the native route (the ``_native_coords_calls``
  counter must move) and, on the SAME input, returns the same value the
  pure-Python parser does (the "native matches the Python reference" invariant);
* every rejection falls back to the *unchanged* Python parser, so the public
  ``(None, error)`` is byte-for-byte identical to the pure-Python result and the
  native counter is NOT incremented (a native success that should have been a
  rejection would make these RED);
* the three fallback scenarios (missing library, an old library without the
  coords export, a malformed native output) all run the *unchanged Python
  parser* with correct values/strings and with the counter NOT incremented;
* a performance guard: on the *same* valid input the native success path must
  not be *materially* slower than the pure-Python parser (the "never slower"
  rule -- a regression leaves a DECISION NEEDED note for the owner).

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "blender-patched", "5.2", "python",
                  "bin", "python3.13")
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


coords = _load("narwhal_native_test_coords", "coords.py")
# Resolve the SAME native module the parser uses, so we can inspect its counter
# and monkeypatch it in the fallback tests.
native = coords._native_coords()

results = []
TMP = tempfile.mkdtemp(prefix="native_coords_")


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


def _assert_native_ran(before, label):
    """Tripwire: the counter must have moved (the native route actually ran)."""
    assert native is not None, "adapter did not resolve the native module"
    assert native._native_coords_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_coords_calls))


@contextmanager
def _force_python():
    """Temporarily make the native route report unavailable so the parser runs
    its UNCHANGED Python path (the "native == Python" baseline and the
    malformed-output fallback)."""
    if native is None:
        yield
        return
    real_parse = native._native_parse
    native._native_parse = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_parse = real_parse


# The valid coordinates: (text, expected (x, y, z), is_world, is_relative),
# all under the default model unit (Millimeters). The expected values are the
# plain-Python results; the native route must agree to 1e-9 relative.
def _polar(r, deg, z=0.0):
    a = math.radians(deg)
    return (r * math.cos(a), r * math.sin(a), z)


def _spherical(d, deg_az, deg_el):
    az = math.radians(deg_az)
    el = math.radians(deg_el)
    return (d * math.cos(el) * math.cos(az),
            d * math.cos(el) * math.sin(az),
            d * math.sin(el))


VALID_COORDS = [
    ("1,2", (1.0, 2.0, 0.0), False, False),
    ("1,2,3", (1.0, 2.0, 3.0), False, False),
    ("w1,2", (1.0, 2.0, 0.0), True, False),
    ("r2,3", (2.0, 3.0, 0.0), False, True),
    ("rw1,2", (1.0, 2.0, 0.0), True, True),
    ("5/16,1-3/4", (0.3125, 1.75, 0.0), False, False),
    ("17<45", _polar(17, 45), False, False),
    ("5<30<45", _spherical(5, 30, 45), False, False),
    ("5,6<15", (5.0, 6.0, math.hypot(5, 6) * math.tan(math.radians(15))),
     False, False),
    ("0", (0.0, 0.0, 0.0), False, False),
    ("w0", (0.0, 0.0, 0.0), True, False),
    ("11<N30d22'54.43\"W",
     _polar(11, 90.0 + 30.0 + 22.0 / 60.0 + 54.43 / 3600.0), False, False),
    ("10*sin(30d),pow(2,3)", (5.0, 8.0, 0.0), False, False),
]


# The rejected inputs. The exact error string is NOT hardcoded here -- it is
# asserted to be byte-for-byte identical to the pure-Python result (the check
# below), which is the real invariant.
COORDS_REJECTED = [
    "5",            # a bare non-zero number is a distance constraint
    "abc,1",        # garbage component
    "1,2,3,4",      # more than three coordinates
    "w",            # a prefix with no coordinates
    "",             # empty
    "1, 2",         # a space
    ".x",           # a point filter
    ".wxy",
    "<45",          # a bare angle constraint
    "17<45<20<30",  # too many '<'
]


@check("native library is built, discovered, and exposes the coords ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._coords_binding()
    assert binding is not None and callable(binding), \
        "the coords binding must be resolvable"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_coords_calls")


@check("parse activates the native route and matches the Python reference")
def _():
    _reset()
    before = native._native_coords_calls
    for text, want, is_world, is_rel in VALID_COORDS:
        got, gw, gr, e = coords.parse(text)
        assert e is None and got is not None, (text, got, e)
        assert gw == is_world and gr == is_rel, (text, gw, gr)
        for a, b in zip(got, want):
            assert abs(a - b) <= 1e-9 * max(1.0, abs(b)), (text, a, b)
    _assert_native_ran(before, "coords")


@check("native equals the pure-Python parser on the SAME valid input")
def _():
    _reset()
    for text, _want, _w, _r in VALID_COORDS:
        got_native, gw_n, gr_n, e_native = coords.parse(text)
        with _force_python():
            got_py, gw_p, gr_p, e_py = coords.parse(text)
        assert e_py is None and got_py is not None, (text, got_py, e_py)
        assert e_native is None and got_native is not None, \
            (text, got_native, e_native)
        assert (gw_n, gr_n) == (gw_p, gr_p), (text, (gw_n, gr_n), (gw_p, gr_p))
        for a, b in zip(got_native, got_py):
            assert abs(a - b) <= 1e-9 * max(1.0, abs(b)), (text, a, b)


@check("every rejection is byte-identical to the Python result, counter unmoved")
def _():
    _reset()
    before = native._native_coords_calls
    for text in COORDS_REJECTED:
        with _force_python():
            v_py, gw_p, gr_p, e_py = coords.parse(text)
        assert v_py is None and isinstance(e_py, str) and e_py, \
            (text, "reference must be an error", v_py, e_py)
        v_nat, _gw, _gr, e_nat = coords.parse(text)
        assert v_nat is None, (text, "must be a rejection", v_nat)
        assert e_nat == e_py, (text, e_nat, "expected", e_py)
    # None of the rejections may have counted as a native SUCCESS.
    assert native._native_coords_calls == before, \
        ("rejections must not increment the native counter",
         before, native._native_coords_calls)


@check("a documented rejection keeps its EXACT public string (contract pin)")
def _():
    _reset()
    # Representative pins of the public contract (these strings embed the input
    # and come from the unchanged Python parser).
    for text, want in [
        ("5", "a bare number is a distance constraint in the reference CAD "
              "application (it locks the next pick's distance from the last "
              "point), not a coordinate; type x,y or x,y,z for a point"),
        (".x", "point filters like '.x' are not supported yet"),
        ("w", "'w' has a prefix but no coordinates"),
        ("1, 2", "a coordinate may not contain spaces"),
        ("1,2,3,4", "'1,2,3,4' has more than three coordinates"),
    ]:
        v, _gw, _gr, e = coords.parse(text)
        assert v is None and e == want, (text, v, e, "expected", want)
    assert native._native_coords_calls == 0, \
        ("contract-pin rejections must not touch the native counter",
         native._native_coords_calls)


@check("the rw prefix is not swallowed by the r prefix (native route)")
def _():
    _reset()
    before = native._native_coords_calls
    # If `r` were matched first, `rw0,5` would try to parse "w0,5" as numbers
    # (a rejection) instead of the world-relative (0,5,0).
    got, gw, gr, e = coords.parse("rw0,5")
    assert e is None and got == (0.0, 5.0, 0.0), (got, e)
    assert gw and gr, (gw, gr)
    got, gw, gr, e = coords.parse("rw1,2")
    assert e is None and got == (1.0, 2.0, 0.0), (got, e)
    assert gw and gr, (gw, gr)
    _assert_native_ran(before, "rw")


@check("fallback: a missing library uses the unchanged Python parser")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._coords_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_coords_calls
        # A valid input still works via the Python path (value unchanged).
        got, gw, gr, e = coords.parse("1,2")
        assert e is None and got == (1.0, 2.0, 0.0), (got, e)
        got, gw, gr, e = coords.parse("5/16,1-3/4")
        assert e is None and got == (0.3125, 1.75, 0.0), (got, e)
        # And a rejection keeps its exact public string.
        v, _gw, _gr, e = coords.parse("5")
        assert v is None and "distance constraint" in e, (v, e)
        # The native counter must NOT have moved (the Python parser ran).
        assert native._native_coords_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_coords_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the coords exports uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the binding is None and the Python parser runs
    # with the exact strings and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._coords_binding() is None, \
            "a library without the coords export must make the binding None"
        before = native._native_coords_calls
        got, gw, gr, e = coords.parse("1,2")
        assert e is None and got == (1.0, 2.0, 0.0), (got, e)
        v, _gw, _gr, e = coords.parse("5")
        assert v is None and "distance constraint" in e, (v, e)
        assert native._native_coords_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_coords_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python parser")
def _():
    _reset()
    assert native is not None and native._coords_binding() is not None
    real_parse = native._native_parse
    native._native_parse = lambda *a, **k: None  # simulate a failed call
    try:
        before = native._native_coords_calls
        # The Python parser still produces the correct value.
        got, gw, gr, e = coords.parse("1,2")
        assert e is None and got == (1.0, 2.0, 0.0), (got, e)
        v, _gw, _gr, e = coords.parse("5")
        assert v is None and "distance constraint" in e, (v, e)
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_coords_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_coords_calls)
    finally:
        native._native_parse = real_parse
        _reset()


@check("production native coordinate parser is faster than Python")
def _():
    # The "never slower" rule: on the *same* valid input, the native success
    # path must not be materially slower than the unchanged Python parser. We
    # measure the same public function twice -- once with the native route
    # active, once forced onto the pure-Python path (``_force_python``) -- so the
    # comparison isolates parsing cost, not grammar.
    #
    # A material regression (native > 2x Python) is a DECISION NEEDED for the
    # owner, not a silent slowdown: the assertion fails and the measured numbers
    # are printed so the owner can decide whether to change the ABI.
    #
    # The comparison is HONEST: only on inputs the native route *accepts* (its
    # success path), never on rejections (a rejection + Python fallback is double
    # work and would falsely look like a native slowdown).
    #
    # The task's measured baseline is 50,000 mixed coordinates in 508.8 ms
    # (the pure-Python path); N below matches that scale so the numbers are
    # directly comparable.
    if native is None or native._coords_binding() is None:
        print("  [perf] SKIPPED -- native coords binding not available")
        return
    perf_inputs = [t for (t, _w, _gw, _gr) in VALID_COORDS]
    N = 50000

    def _best(fn):
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for _ in range(N):
                fn()
            best = min(best, time.perf_counter() - t0)
        return best

    i = [0]

    def _cycle():
        i[0] += 1
        coords.parse(perf_inputs[i[0] % len(perf_inputs)])

    _reset()
    t_native = _best(_cycle)
    with _force_python():
        _reset()
        t_py = _best(_cycle)
    r = t_native / max(t_py, 1e-9)
    print("  [perf] coords.parse native=%.5fs python=%.5fs ratio=%.2fx (%d calls)"
          % (t_native, t_py, r, N))
    assert t_native < t_py, \
        ("native coords.parse is slower than Python: native %.5fs vs "
         "python %.5fs (ratio %.2fx)" % (t_native, t_py, r))


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
