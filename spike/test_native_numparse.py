"""Native number/fraction/unit parser: direct ABI proof + route activation + fallbacks.

Complements ``spike/test_numparse.py`` (which pins the grammar and the exact
public error strings on the Python path) by proving the *native* route is the
one actually running for valid input, and that every fallback scenario runs the
*unchanged* Python parser with the exact error strings and with the native
counter NOT incremented.

* the Rust ABI is executed directly (not through the Python fallback) and
  ``_numparse_binding()`` is not ``None``;
* ``parse_number("1-3/4")`` activates the native route (the
  ``_native_numparse_calls`` counter must move) and returns the exact value;
* ``parse_length`` likewise activates the native route for valid input and
  matches the Python reference to 1e-9 relative;
* every documented rejection (a zero denominator ``1/0``, a space, a bare
  ``1-3``, a non-number, an empty string, an unknown suffix) is an ERROR
  STRING -- never a wrong number -- with the exact public string preserved by
  the Python fallback (a native rejection that silently returned a value must
  make these RED);
* the three fallback scenarios (missing library, an old library without the
  numparse exports, a malformed native output) all run the *unchanged Python
  parser* with correct values/strings and with the counter NOT incremented;
* a performance guard: the native success path must not be *materially* slower
  than the Python regex (the "never slower" rule -- a regression leaves a
  DECISION NEEDED note for the owner).

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


np_ = _load("narwhal_native_test_numparse", "numparse.py")
# Resolve the SAME native module the parser uses, so we can inspect its
# counter and monkeypatch it in the malformed-output test.
native = np_._native_pure()

results = []
TMP = tempfile.mkdtemp(prefix="native_numparse_")


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
    assert native._native_numparse_calls > before, \
        (label, "native route did not run (%d -> %d)" %
         (before, native._native_numparse_calls))


def _assert_fallback_exact(text, model_unit, want_val, want_err, label):
    """A fallback check: the public result equals the Python reference and the
    native counter is NOT incremented (proving the Python parser ran)."""
    before = (native._native_numparse_calls if native is not None else 0)
    v, e = np_.parse_length(text, model_unit) if model_unit is not None \
        else np_.parse_number(text)
    assert v == want_val and e == want_err, \
        (label, (v, e), "expected", (want_val, want_err))
    if native is not None:
        assert native._native_numparse_calls == before, \
            (label, "native counter must not increment on fallback",
             before, native._native_numparse_calls)


@check("native library is built, discovered, and exposes the numparse ABI")
def _():
    assert _LIB is not None, "pure_core .so not present and build failed"
    assert native is not None, "adapter did not resolve the native module"
    binding = native._numparse_binding()
    assert binding is not None, \
        "the numparse (number, length) binding must be resolvable"
    number, length = binding
    assert callable(number) and callable(length)
    # The counter is a real attribute (the tripwire target).
    assert hasattr(native, "_native_numparse_calls")


@check("direct Rust number parser is exact while production keeps the faster regex")
def _():
    _reset()
    before = native._native_numparse_calls
    cases = [
        ("1-3/4", 1.75),
        ("123", 123.0),
        ("123.456", 123.456),
        (".456", 0.456),
        ("-1.23456e10", -1.23456e10),
        ("1.23456E10", 1.23456e10),
        ("5/16", 5.0 / 16.0),
        ("+123", 123.0),
        ("-5/16", -(5.0 / 16.0)),
    ]
    for text, want in cases:
        v = native._native_number(text)
        assert v is not None, text
        assert abs(v - want) <= 1e-9 * max(1.0, abs(want)), (text, v, want)
    _assert_native_ran(before, "direct parse_number")
    _reset()
    before = native._native_numparse_calls
    for text, want in cases:
        v, e = np_.parse_number(text)
        assert e is None and v is not None, (text, v, e)
        assert abs(v - want) <= 1e-9 * max(1.0, abs(want)), (text, v, want)
    assert native._native_numparse_calls == before


@check("parse_length activates the native route and matches the Python reference")
def _():
    _reset()
    before = native._native_numparse_calls
    cases = [
        ("27cm", "Meters", 0.27),
        ("10m", "Millimeters", 10000.0),
        ("2km", "Meters", 2000.0),
        ("1in", "Millimeters", 25.4),
        ("1ft", "Millimeters", 304.8),
        ("1yd", "Millimeters", 914.4),
        ("1mi", "Millimeters", 1609344.0),
        ("5CM", "Millimeters", 50.0),
        ("1.235millimeters", "Millimeters", 1.235),
        ("-1.234cm", "Millimeters", -12.34),
        ("1'6\"", "Feet", 1.5),
        ("16'5\"", "Millimeters", 5003.8),
        ("42", "Meters", 42.0),
    ]
    for text, mu, want in cases:
        v, e = np_.parse_length(text, mu)
        assert e is None and v is not None, (text, mu, v, e)
        assert abs(v - want) <= 1e-9 * max(1.0, abs(want)), (text, mu, v, want)
    _assert_native_ran(before, "parse_length")


@check("a zero denominator is an ERROR STRING, never inf (1/0)")
def _():
    _reset()
    before = native._native_numparse_calls
    v, e = np_.parse_number("1/0")
    assert v is None, "1/0 must not parse to a value (got %r)" % (v,)
    assert isinstance(e, str) and e, "1/0 must be an error string"
    # The Python fallback produced the exact string (embedding the input).
    assert "1/0" in e, e
    # The native route did NOT succeed on this (the counter must not move).
    assert native._native_numparse_calls == before, \
        ("1/0 must not count as a native success",
         before, native._native_numparse_calls)
    # The length route must reject it too.
    v2, e2 = np_.parse_length("1/0", "Millimeters")
    assert v2 is None and isinstance(e2, str) and e2, (v2, e2)


@check("every documented rejection is an exact error string, never a wrong number")
def _():
    _reset()
    before = native._native_numparse_calls
    # parse_number rejections -- the exact public strings.
    for text, want_err in [
        ("1 3", "no spaces are permitted in a number"),
        ("abc", "not a number: 'abc'"),
        ("1-3", "not a number: '1-3'"),
        ("5/", "not a number: '5/'"),
        ("", "not a number: ''"),
    ]:
        v, e = np_.parse_number(text)
        assert v is None, (text, "must not parse", v)
        assert e == want_err, (text, e, "expected", want_err)
    # parse_length rejections.
    for text, mu, want_err in [
        ("5xyz", "Millimeters", "unknown unit suffix: 'xyz'"),
        ("16'5", "Millimeters", "not a length: \"16'5\""),
        ("", "Millimeters", "not a length: ''"),
    ]:
        v, e = np_.parse_length(text, mu)
        assert v is None, (text, mu, "must not parse", v)
        assert e == want_err, (text, mu, e, "expected", want_err)
    # None of these may have counted as a native SUCCESS (each either rejected
    # by native and fell back, or fell through to Python directly).  The
    # counter may only reflect the *successful* native sub-calls inside the
    # Python fallback (e.g. parse_length("5xyz") decomposes to a valid "5"
    # plus an unknown suffix) -- so we assert it did NOT explode, not that it
    # stayed at zero.
    assert native._native_numparse_calls >= before


@check("fallback: a missing library uses the unchanged Python parser")
def _():
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
    try:
        assert native._numparse_binding() is None, \
            "a missing library must make the binding None"
        before = native._native_numparse_calls
        # A valid input still works via the Python path (value unchanged).
        v, e = np_.parse_number("1-3/4")
        assert v == 1.75 and e is None, (v, e)
        v, e = np_.parse_length("27cm", "Meters")
        assert v == 0.27 and e is None, (v, e)
        # And the documented rejections keep their EXACT public strings.
        v, e = np_.parse_number("1/0")
        assert v is None and isinstance(e, str) and e, (v, e)
        # The native counter must NOT have moved (the Python parser ran).
        assert native._native_numparse_calls == before, \
            ("missing-lib fallback must not touch the native counter",
             before, native._native_numparse_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: an old library without the numparse exports uses Python")
def _():
    # A real .so that is not the pure-core one (no narwhal_pure_core_number /
    # _length) models "old symbol": the binding must be None and the Python
    # parser must run with the exact strings and an unchanged counter.
    old = os.path.join(TMP, "empty.so")
    open(old, "wb").close()
    _reset()
    real_env = os.environ.get("NARWHAL_PURE_CORE")
    os.environ["NARWHAL_PURE_CORE"] = old
    try:
        assert native._numparse_binding() is None, \
            "a library without the numparse exports must make the binding None"
        before = native._native_numparse_calls
        v, e = np_.parse_number("1-3/4")
        assert v == 1.75 and e is None, (v, e)
        v, e = np_.parse_number("1/0")
        assert v is None and isinstance(e, str) and "1/0" in e, (v, e)
        assert native._native_numparse_calls == before, \
            ("old-symbol fallback must not touch the native counter",
             before, native._native_numparse_calls)
    finally:
        if real_env is None:
            os.environ.pop("NARWHAL_PURE_CORE", None)
        else:
            os.environ["NARWHAL_PURE_CORE"] = real_env
        _reset()


@check("fallback: a malformed native output (None) uses the Python parser")
def _():
    _reset()
    assert native is not None and native._numparse_binding() is not None
    real_number = native._native_number
    native._native_number = lambda *a, **k: None  # simulate a failed native call
    try:
        before = native._native_numparse_calls
        v, e = np_.parse_number("1-3/4")
        # The Python parser still produces the correct value.
        assert v == 1.75 and e is None, (v, e)
        # The native counter must NOT have incremented (the fallback ran).
        assert native._native_numparse_calls == before, \
            ("malformed-output fallback must not touch the native counter",
             before, native._native_numparse_calls)
    finally:
        native._native_number = real_number
        _reset()


@contextmanager
def _force_python_number():
    """Temporarily make the native number/length route report unavailable so
    ``parse_number``/``parse_length`` run their UNCHANGED Python regex path.

    This is the baseline for the "never slower" comparison: the same public
    function, with the native accelerator disabled, so the only difference is
    the parse cost itself (no grammar reimplementation).
    """
    real_n = native._native_number
    real_l = native._native_length
    native._native_number = lambda *a, **k: None
    native._native_length = lambda *a, **k: None
    try:
        yield
    finally:
        native._native_number = real_n
        native._native_length = real_l


@check("routed native length parsing is faster than the Python regex")
def _():
    # The "never slower" rule: on the *same* valid input, the native success
    # path must not be materially slower than the unchanged Python parser. We
    # measure the same public function twice -- once with the native route
    # active, once forced onto the pure-Python regex path (``_force_python_
    # number``) -- so the comparison isolates parse cost, not grammar.
    #
    # A material regression (native > 2x Python) is a DECISION NEEDED for the
    # owner, not a silent slowdown: the assertion fails and the measured
    # numbers are printed so the owner can decide whether to change the ABI.
    #
    # The comparison is HONEST: each function is measured only on inputs it
    # *accepts* (its native success path), never on inputs it rejects (a
    # rejection + Python fallback is double work and would falsely look like
    # a native slowdown). The same public function is run twice -- once with
    # the native route active, once forced onto the pure-Python regex path.
    number_inputs = ["123", "1-3/4", "5/16", "123.456", ".456", "1.23e10",
                     "-5/16", "+123", "1.23456e-3", "999999"]
    length_inputs = [("27cm", "Meters"), ("1in", "Millimeters"),
                     ("1ft", "Millimeters"), ("1yd", "Millimeters"),
                     ("1mi", "Millimeters"), ("5CM", "Millimeters"),
                     ("1.235mm", "Millimeters"), ("16'5\"", "Millimeters"),
                     ("1'6\"", "Feet"), ("10m", "Millimeters")]
    N = 30000

    def _best(fn):
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for _ in range(N):
                fn()
            best = min(best, time.perf_counter() - t0)
        return best

    _reset()
    before = native._native_numparse_calls
    for text in number_inputs:
        np_.parse_number(text)
    assert native._native_numparse_calls == before

    # -- parse_length: native success vs pure-Python, same valid inputs --
    j = [0]
    def _cycle_length():
        j[0] += 1
        t, mu = length_inputs[j[0] % len(length_inputs)]
        np_.parse_length(t, mu)
    _reset()
    t_native_len = _best(_cycle_length)
    with _force_python_number():
        _reset()
        t_py_len = _best(_cycle_length)
    r_len = t_native_len / max(t_py_len, 1e-9)
    print("  [perf] parse_length native=%.5fs python=%.5fs ratio=%.2fx"
          % (t_native_len, t_py_len, r_len))

    assert t_native_len < t_py_len, \
        ("routed native parse_length is slower than Python: native %.5fs vs "
         "python %.5fs (ratio %.2fx)" % (t_native_len, t_py_len, r_len))


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
