"""Native calculator parser/evaluator: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_calc.py`` (which pins the grammar and the exact public
error strings on the Python path) by proving the *native* route is the one
actually running for valid input, and that every fallback scenario runs the
*unchanged* Python evaluator with the exact error strings and with the native
counter NOT incremented.

* the Rust ABI is present and ``_calc_binding()`` is not ``None``;
* ``evaluate`` activates the native route (the ``_native_calc_calls`` counter
  must move) and, on the SAME input, returns the same value the pure-Python
  evaluator does (the "native matches the Python reference" invariant);
* every rejection falls back to the *unchanged* Python evaluator, so the public
  ``(None, error)`` is byte-for-byte identical to the pure-Python result and the
  native counter is NOT incremented (a native success that should have been a
  rejection would make these RED);
* the three fallback scenarios (missing library, an old library without the
  calculator export, a malformed native output) all run the *unchanged Python
  evaluator* with correct values/strings and with the counter NOT incremented;
* a performance guard: on the *same* valid input the native success path must
  not be *materially* slower than the pure-Python evaluator (the "never slower"
  rule -- a regression leaves a DECISION NEEDED note for the owner).

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


calc = _load("narwhal_native_test_calc", "calc.py")
# Resolve the SAME native module the evaluator uses, so we can inspect its
# counter and monkeypatch it in the fallback tests.
native = calc._native_calc()

results = []
TMP = tempfile.mkdtemp(prefix="native_calc_")


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
    assert native._native_calc_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_calc_calls))


@contextmanager
def _force_python():
    """Temporarily make the native route report unavailable so ``evaluate``
    runs its UNCHANGED Python evaluator (the "native == Python" baseline and
    the malformed-output fallback)."""
    if native is None:
        yield
        return
    real = native._native_calc
    native._native_calc = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_calc = real


# The valid formulas: (text, model_unit, expected). The expected values are the
# plain-Python results; the native route must agree to 1e-9 relative (a libm
# 1-ulp difference is allowed, a wrong value is not).
VALID = [
    ("1+2*3", "Millimeters", 7.0),
    ("(1+2)*3", "Millimeters", 9.0),
    ("10/4", "Millimeters", 2.5),
    ("2-3-4", "Millimeters", -5.0),
    ("-5+2", "Millimeters", -3.0),
    ("--5", "Millimeters", 5.0),
    ("5/16", "Millimeters", 5.0 / 16.0),
    ("1-3/4+1", "Millimeters", 2.75),
    ("1.23456e-3+1", "Millimeters", 1.00123456),
    ("pi/4", "Millimeters", 3.141592653589793 / 4),
    ("sin(0)", "Millimeters", 0.0),
    ("tan(pi/4)", "Millimeters", 1.0),
    ("atan2(1,1)", "Millimeters", 3.141592653589793 / 4),
    ("ln(exp(1))", "Millimeters", 1.0),
    ("log10(1000)", "Millimeters", 3.0),
    ("sqrt(16)", "Millimeters", 4.0),
    ("pow(2,10)", "Millimeters", 1024.0),
    ("pow(9,1/2)", "Millimeters", 3.0),
    ("10*sin(30degrees)", "Millimeters", 5.0),
    ("sin(30d)", "Millimeters", 0.5),
    ("tan(45d)", "Millimeters", 1.0),
    ("27cm", "Meters", 0.27),
    ("27cm+10cm", "Meters", 0.37),
    ("10m", "Millimeters", 10000.0),
    ("1'6\"", "Feet", 1.5),
    ("16'5\"", "Millimeters", 5003.8),
    ("16'5\"+1", "Millimeters", 5004.8),
    ("10+2'6\"", "Millimeters", 772.0),
    ("2.5'3\"", "Millimeters", 838.2),
    ("1'2-3/4\"", "Millimeters", 374.65),
    ("1'2-3/4\"+1", "Millimeters", 375.65),
    ("(16'5\")", "Millimeters", 5003.8),
    ("16'5\"*2", "Millimeters", 10007.6),
    ("(10*sin(pi/4)+63.00)/1.234e12+-56.3", "Millimeters",
     (10 * 0.7071067811865476 + 63.00) / 1.234e12 + -56.3),
]

# The rejected formulas: (text, model_unit). The exact error string is NOT
# hardcoded here -- it is asserted to be byte-for-byte identical to the
# pure-Python result (see the check below), which is the real invariant.
REJECTED = [
    ("1 + 2", "Millimeters"),
    ("abc", "Millimeters"),
    ("(1+2", "Millimeters"),
    ("1+2)", "Millimeters"),
    ("1/0", "Millimeters"),
    ("5/(3-3)", "Millimeters"),
    ("sqrt(-1)", "Millimeters"),
    ("asin(2)", "Millimeters"),
    ("ln(0)", "Millimeters"),
    ("acos(-2)", "Millimeters"),
    ("__import__('os')", "Millimeters"),
    ("", "Millimeters"),
    ("1+*2", "Millimeters"),
    ("pow(2)", "Millimeters"),
    ("foo(1)", "Millimeters"),
    ("pow(-8,1/3)", "Millimeters"),
    ("1.", "Millimeters"),
    ("pow(0,-1)", "Millimeters"),
    ("exp(1000)", "Millimeters"),
    ("2'6\"5", "Millimeters"),
    ("2'6\"mm", "Millimeters"),
]


@check("native library is built, discovered, and exposes the calculator ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._calc_binding()
    assert binding is not None and callable(binding), \
        "the calculator binding must be resolvable"
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_calc_calls")


@check("evaluate activates the native route and matches the Python reference")
def _():
    _reset()
    before = native._native_calc_calls
    for text, mu, want in VALID:
        v, e = calc.evaluate(text, mu)
        assert e is None and v is not None, (text, mu, v, e)
        assert abs(v - want) <= 1e-9 * max(1.0, abs(want)), (text, v, want)
    _assert_native_ran(before, "evaluate")


@check("native evaluate equals the pure-Python evaluate on the SAME valid input")
def _():
    _reset()
    for text, mu, _ in VALID:
        v_native, e_native = calc.evaluate(text, mu)
        with _force_python():
            v_py, e_py = calc.evaluate(text, mu)
        assert e_py is None and v_py is not None, (text, mu, v_py, e_py)
        assert e_native is None and v_native is not None, (text, mu, v_native,
                                                           e_native)
        assert abs(v_native - v_py) <= 1e-9 * max(1.0, abs(v_py)), \
            (text, mu, v_native, v_py)


@check("every rejection is byte-identical to the Python result, counter unmoved")
def _():
    _reset()
    before = native._native_calc_calls
    for text, mu in REJECTED:
        with _force_python():
            v_py, e_py = calc.evaluate(text, mu)
        assert v_py is None and isinstance(e_py, str) and e_py, \
            (text, mu, "reference must be an error", v_py, e_py)
        v_nat, e_nat = calc.evaluate(text, mu)
        assert v_nat is None, (text, mu, "must be a rejection", v_nat)
        assert e_nat == e_py, (text, mu, e_nat, "expected", e_py)
    # None of the rejections may have counted as a native SUCCESS.
    assert native._native_calc_calls == before, \
        ("rejections must not increment the native counter",
         before, native._native_calc_calls)


@check("a documented rejection keeps its EXACT public string (contract pin)")
def _():
    _reset()
    # A representative pin of the public contract (these strings embed the
    # input and come from the unchanged Python evaluator).
    for text, want in [
        ("1 + 2", "no spaces in formulas"),
        ("1/0", "bad number: '1/0'"),
        ("5/(3-3)", "division by zero"),
        ("1+*2", "unexpected token: '*'"),
        ("pow(2)", "pow takes 2 argument(s), got 1"),
        ("", "empty formula"),
    ]:
        v, e = calc.evaluate(text)
        assert v is None and e == want, (text, v, e, "expected", want)
    assert native._native_calc_calls == 0, \
        ("contract-pin rejections must not touch the native counter",
         native._native_calc_calls)


@check("fallback: a missing library uses the unchanged Python evaluator")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._calc_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_calc_calls
        # A valid input still works via the Python path (value unchanged).
        v, e = calc.evaluate("1+2*3")
        assert v == 7.0 and e is None, (v, e)
        v, e = calc.evaluate("27cm", "Meters")
        assert v == 0.27 and e is None, (v, e)
        # And a rejection keeps its exact public string.
        v, e = calc.evaluate("1/0")
        assert v is None and e == "bad number: '1/0'", (v, e)
        # The native counter must NOT have moved (the Python evaluator ran).
        assert native._native_calc_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_calc_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the calculator export uses Python")
def _():
    # A real file that is not a valid shared library models "old symbol":
    # CDLL fails (OSError) so the binding is None and the Python evaluator
    # runs with the exact strings and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._calc_binding() is None, \
            "a library without the calc export must make the binding None"
        before = native._native_calc_calls
        v, e = calc.evaluate("1+2*3")
        assert v == 7.0 and e is None, (v, e)
        v, e = calc.evaluate("1/0")
        assert v is None and e == "bad number: '1/0'", (v, e)
        assert native._native_calc_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_calc_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python evaluator")
def _():
    _reset()
    assert native is not None and native._calc_binding() is not None
    real = native._native_calc
    native._native_calc = lambda *a, **k: None  # simulate a failed native call
    try:
        before = native._native_calc_calls
        v, e = calc.evaluate("1+2*3")
        # The Python evaluator still produces the correct value.
        assert v == 7.0 and e is None, (v, e)
        v, e = calc.evaluate("1/0")
        assert v is None and e == "bad number: '1/0'", (v, e)
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_calc_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_calc_calls)
    finally:
        native._native_calc = real
        _reset()


@check("production native calculator is faster than the Python evaluator")
def _():
    # The "never slower" rule: on the *same* valid input, the native success
    # path must not be materially slower than the unchanged Python evaluator.
    # We measure the same public function twice -- once with the native route
    # active, once forced onto the pure-Python path (``_force_python``) -- so
    # the comparison isolates evaluation cost, not grammar.
    #
    # A material regression (native > 2x Python) is a DECISION NEEDED for the
    # owner, not a silent slowdown: the assertion fails and the measured
    # numbers are printed so the owner can decide whether to change the ABI.
    #
    # The comparison is HONEST: only on inputs the native route *accepts* (its
    # success path), never on rejections (a rejection + Python fallback is
    # double work and would falsely look like a native slowdown).
    perf_inputs = [(t, mu) for (t, mu, _w) in VALID]
    N = 30000

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
        t, mu = perf_inputs[i[0] % len(perf_inputs)]
        calc.evaluate(t, mu)

    _reset()
    t_native = _best(_cycle)
    with _force_python():
        _reset()
        t_py = _best(_cycle)
    r = t_native / max(t_py, 1e-9)
    print("  [perf] evaluate native=%.5fs python=%.5fs ratio=%.2fx (%d formulas)"
          % (t_native, t_py, r, N))
    assert t_native < t_py, \
        ("native evaluate is slower than the Python evaluator: native %.5fs "
         "vs python %.5fs (ratio %.2fx)" % (t_native, t_py, r))


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
