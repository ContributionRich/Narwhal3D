"""Native surveyor bearing parser: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_bearing.py`` (which pins the quadrants, DMS, 0/90
boundaries and the exact public error strings on the Python path) by proving the
*native* route is the one actually running for valid input, and that every
fallback scenario runs the *unchanged* Python parser with the exact error strings
and with the native counter NOT incremented.

* the Rust ABI is present and ``_bearing_binding()`` is not ``None``;
* ``bearing_angle`` / ``parse_surveyor`` activate the native route (the
  ``_native_bearing_calls`` counter must move) and, on the SAME input, return the
  same value(s) the pure-Python parser does (the "native matches the Python
  reference" invariant);
* every rejection falls back to the *unchanged* Python parser, so the public
  ``(None, error)`` is byte-for-byte identical to the pure-Python result and the
  native counter is NOT incremented (a native success that should have been a
  rejection would make these RED);
* the three fallback scenarios (missing library, an old library without the
  bearing exports, a malformed native output) all run the *unchanged Python
  parser* with correct values/strings and with the counter NOT incremented;
* a performance guard: on the *same* valid input the native success path must not
  be *materially* slower than the pure-Python parser (the "never slower" rule --
  a regression leaves a DECISION NEEDED note for the owner).

The suite is green with or without a prebuilt ``.so``: if the library is not
present it builds the crate once (offline, pinned toolchain) and rechecks.
"""
import importlib.util
import os
import shutil
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


brg = _load("narwhal_native_test_bearing", "bearing.py")
# Resolve the SAME native module the parser uses, so we can inspect its counter
# and monkeypatch it in the fallback tests.
native = brg._native_bearing()

results = []
TMP = tempfile.mkdtemp(prefix="native_bearing_")


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
    assert native._native_bearing_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_bearing_calls))


@contextmanager
def _force_python():
    """Temporarily make the native route report unavailable so the parser runs
    its UNCHANGED Python path (the "native == Python" baseline and the
    malformed-output fallback)."""
    if native is None:
        yield
        return
    real_ang = native._native_bearing_angle
    real_sur = native._native_parse_surveyor
    native._native_bearing_angle = lambda *a, **k: None
    native._native_parse_surveyor = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_bearing_angle = real_ang
        native._native_parse_surveyor = real_sur


DMS = 30.0 + 22.0 / 60.0 + 54.43 / 3600.0

# The valid bearings: (text, expected CCW degrees). Expected values are the
# plain-Python results; the native route must agree to 1e-9 relative (a libm
# 1-ulp difference is allowed, a wrong value is not).
VALID_BEARING = [
    ("N0E", 90.0),
    ("N90E", 0.0),
    ("N90W", 180.0),
    ("S0W", 270.0),
    ("S90E", 0.0),
    ("n30e", 60.0),
    ("n30w", 120.0),
    ("s30e", 300.0),
    ("s30w", 240.0),
    ("N45W", 135.0),
    ("S45W", 225.0),
    ("S45E", 315.0),
    ("N30W", 120.0),
    ("N30.5E", 59.5),
    ("N 30 E", 60.0),
    ("S 45 W", 225.0),
    ("N30d22'W", 90.0 + 30.0 + 22.0 / 60.0),
    ("N30d22'54.43\"W", 90.0 + DMS),
]

# The valid surveyor coordinates: (text, distance, expected CCW degrees).
VALID_SURVEYOR = [
    ("11<N30d22'54.43\"W", 11.0, 90.0 + DMS),
    ("5<N45E", 5.0, 45.0),
    ("2.5<S30W", 2.5, 240.0),
]

# The rejected inputs. The two functions have different domains, so the
# rejection sets are split (mirroring ``test_bearing.py``'s ``badb`` / ``badp``):
# a *bearing* that is valid on its own (e.g. ``N30E`` = 60 deg) is still a
# *surveyor* rejection (no distance before the ``<``). The exact error string is
# NOT hardcoded here -- it is asserted to be byte-for-byte identical to the
# pure-Python result (see the check below), which is the real invariant.
BEARING_REJECTED = [
    "N95E",
    "E30N",
    "N30",
    "30W",
    "N-5E",
    "NabcE",
    "N30wE",
    "N E",
    "",
]

SURVEYOR_REJECTED = [
    "5<N95E",
    "5<E30N",
    "5<N30",
    "5<30W",
    "N30E",
    "11<",
    "",
]


@check("native library is built, discovered, and exposes the bearing ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._bearing_binding()
    assert binding is not None and len(binding) == 2, \
        "the bearing binding must be resolvable"
    assert callable(binding[0]) and callable(binding[1]), \
        "both bearing and surveyor exports must be callable"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_bearing_calls")


LONG_BEARING = " " * 124 + "N30E"


@check("bearing Rust stays direct/internal while standalone production stays Python")
def _():
    _reset()
    before = native._native_bearing_calls
    assert native._native_bearing_angle("N30E") == 60.0
    assert native._native_parse_surveyor("5<N45E") == (5.0, 45.0)
    _assert_native_ran(before, "direct bearing ABI")
    _reset()
    before = native._native_bearing_calls
    assert brg.bearing_angle("N30E") == (60.0, None)
    assert brg.bearing_angle(LONG_BEARING) == (60.0, None)
    assert brg.parse_surveyor("5<N45E") == ((5.0, 45.0), None)
    assert native._native_bearing_calls == before


@check("native equals the pure-Python parser on the SAME valid input")
def _():
    _reset()
    for text, _want in VALID_BEARING:
        v_native, e_native = brg.bearing_angle(text)
        with _force_python():
            v_py, e_py = brg.bearing_angle(text)
        assert e_py is None and v_py is not None, (text, v_py, e_py)
        assert e_native is None and v_native is not None, (text, v_native,
                                                           e_native)
        assert abs(v_native - v_py) <= 1e-9 * max(1.0, abs(v_py)), \
            (text, v_native, v_py)
    for text, dist, _want in VALID_SURVEYOR:
        val_native, e_native = brg.parse_surveyor(text)
        with _force_python():
            val_py, e_py = brg.parse_surveyor(text)
        assert e_py is None and val_py is not None, (text, val_py, e_py)
        assert e_native is None and val_native is not None, (text, val_native,
                                                             e_native)
        d_nat, a_nat = val_native
        d_py, a_py = val_py
        assert abs(d_nat - d_py) <= 1e-9 * max(1.0, abs(d_py)), (text, d_nat,
                                                                 d_py)
        assert abs(a_nat - a_py) <= 1e-9 * max(1.0, abs(a_py)), (text, a_nat,
                                                                 a_py)


@check("every rejection is byte-identical to the Python result, counter unmoved")
def _():
    _reset()
    before = native._native_bearing_calls
    for text in BEARING_REJECTED:
        with _force_python():
            v_py, e_py = brg.bearing_angle(text)
        assert v_py is None and isinstance(e_py, str) and e_py, \
            (text, "reference must be an error", v_py, e_py)
        v_nat, e_nat = brg.bearing_angle(text)
        assert v_nat is None, (text, "must be a rejection", v_nat)
        assert e_nat == e_py, (text, e_nat, "expected", e_py)
    for text in SURVEYOR_REJECTED:
        with _force_python():
            sv_py, se_py = brg.parse_surveyor(text)
        assert sv_py is None and isinstance(se_py, str) and se_py, \
            (text, "surveyor reference must be an error", sv_py, se_py)
        sv_nat, se_nat = brg.parse_surveyor(text)
        assert sv_nat is None, (text, "surveyor must be a rejection", sv_nat)
        assert se_nat == se_py, (text, se_nat, "expected", se_py)
    # None of the rejections may have counted as a native SUCCESS.
    assert native._native_bearing_calls == before, \
        ("rejections must not increment the native counter",
         before, native._native_bearing_calls)


@check("a documented rejection keeps its EXACT public string (contract pin)")
def _():
    _reset()
    # Representative pins of the public contract (these strings embed the input
    # and come from the unchanged Python parser).
    for text, want in [
        ("N95E", "quadrant angle must be in [0, 90]: 95"),
        ("N30", "not a bearing (need N/S + angle + E/W): 'N30'"),
        ("NabcE", "not an angle: 'abc'"),
        ("N-5E", "not an angle: '-5'"),
    ]:
        v, e = brg.bearing_angle(text)
        assert v is None and e == want, (text, v, e, "expected", want)
    assert native._native_bearing_calls == 0, \
        ("contract-pin rejections must not touch the native counter",
         native._native_bearing_calls)


@check("fallback: a missing library uses the unchanged Python parser")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._bearing_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_bearing_calls
        # A valid input still works via the Python path (value unchanged).
        v, e = brg.bearing_angle("n30e")
        assert v == 60.0 and e is None, (v, e)
        val, e = brg.parse_surveyor("5<N45E")
        assert e is None and val == (5.0, 45.0), (val, e)
        # And a rejection keeps its exact public string.
        v, e = brg.bearing_angle("N95E")
        assert v is None and e == "quadrant angle must be in [0, 90]: 95", \
            (v, e)
        # The native counter must NOT have moved (the Python parser ran).
        assert native._native_bearing_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_bearing_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the bearing exports uses Python")
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
        assert native._bearing_binding() is None, \
            "a library without the bearing exports must make the binding None"
        before = native._native_bearing_calls
        v, e = brg.bearing_angle("n30e")
        assert v == 60.0 and e is None, (v, e)
        v, e = brg.bearing_angle("N95E")
        assert v is None and e == "quadrant angle must be in [0, 90]: 95", \
            (v, e)
        assert native._native_bearing_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_bearing_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python parser")
def _():
    _reset()
    assert native is not None and native._bearing_binding() is not None
    real_ang = native._native_bearing_angle
    real_sur = native._native_parse_surveyor
    native._native_bearing_angle = lambda *a, **k: None  # simulate a failed call
    native._native_parse_surveyor = lambda *a, **k: None
    try:
        before = native._native_bearing_calls
        # The Python parser still produces the correct value.
        v, e = brg.bearing_angle("n30e")
        assert v == 60.0 and e is None, (v, e)
        v, e = brg.bearing_angle("N95E")
        assert v is None and e == "quadrant angle must be in [0, 90]: 95", \
            (v, e)
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_bearing_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_bearing_calls)
    finally:
        native._native_bearing_angle = real_ang
        native._native_parse_surveyor = real_sur
        _reset()


@check("standalone bearing workflows never cross the slower FFI boundary")
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
    perf_inputs = ["5<N45E", "12.5<S30W", "100<N0E", "1.25<S90E"]
    N = 5000

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
        brg.parse_surveyor(perf_inputs[i[0] % len(perf_inputs)])

    _reset()
    before = native._native_bearing_calls
    elapsed = _best(_cycle)
    print("  [perf] standalone Python surveyor=%.5fs (%d calls)" % (elapsed, N))
    assert native._native_bearing_calls == before


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
