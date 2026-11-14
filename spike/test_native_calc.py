"""Required calculator routing plus explicitly separate compatibility ABI tests.

The production adapter owns successful AND rejected formulas. calc_reference
only decodes captured original-Python answers; it contains no evaluator. Missing/stale
calculator ABIs or malformed replies raise RuntimeError, never replay Python.
Legacy native_pure._native_calc tests below qualify compatibility only.
The unchanged original performance report is archived privately, not replaced by fixture timings.
"""
import ctypes
import importlib.util
import math
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from unittest.mock import patch


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
    for candidate in LIB_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


_LIB = _locate_lib()
if _LIB is None:
    raise RuntimeError('Required current pure-core library missing; no test-time build')
os.environ["NARWHAL_PURE_CORE"] = _LIB


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


calc = _load("narwhal_native_test_calc", "calc.py")
if hasattr(calc, "native_calc"):
    native = calc.native_calc
elif hasattr(calc, "_adapter"):
    native = calc._adapter()
else:
    raise RuntimeError("Required native_calc production candidate is not activated")
compat = _load("narwhal_native_test_calc_compat", "native_pure.py")
reference_spec = importlib.util.spec_from_file_location(
    "narwhal_native_test_calc_reference", os.path.join(ROOT, "spike", "calc_reference.py"))
reference = importlib.util.module_from_spec(reference_spec)
reference_spec.loader.exec_module(reference)

results = []
TMP = tempfile.mkdtemp(prefix="native_calc_")


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as error:  # collect every independent check
            results.append((name, "FAIL", repr(error)))
    return deco


def _reset():
    """Reset only test-visible discovery state; no production reset API needed."""
    with native._lock:
        native._signature = native._binding = native._library = None
        native._native_calc_calls = 0
    compat._reset_for_tests()


def _assert_native_ran(before, count, label):
    assert native._native_calc_calls == before + count, (
        label, "each admitted formula must make one native call", before,
        native._native_calc_calls, count)


@contextmanager
def _library_path(path):
    with patch.dict(os.environ, {"NARWHAL_PURE_CORE": path}):
        _reset()
        try:
            yield
        finally:
            _reset()


def _runtime_error(fn):
    try:
        fn()
    except RuntimeError as error:
        assert str(error), "installation/protocol errors must be actionable"
    else:
        raise AssertionError("required calculator failure was silently bypassed")


# Preserve the existing success and rejection corpus and numerical tolerances.
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
# captured original-Python result (see the check below), which is the real invariant.
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


@check("required ABI resolves through the production adapter; fixed answers have no parser")
def _():
    assert _LIB is not None
    assert callable(native._required_binding())
    assert hasattr(native, "_native_calc_calls")
    if hasattr(calc, "native_calc"):
        assert calc.native_calc is native
    else:
        assert calc._adapter() is native
    assert not hasattr(reference, '_tokenize')


@check("all successful evaluate cases are native-owned and retain expected values")
def _():
    _reset()
    before = native._native_calc_calls
    with patch.object(compat, "_native_calc", side_effect=AssertionError("legacy route used")):
        for text, unit, want in VALID:
            value, error = calc.evaluate(text, unit)
            assert error is None and value is not None, (text, unit, value, error)
            assert math.isfinite(value), (text, value)
            assert abs(value - want) <= 1e-9 * max(1.0, abs(want)), (text, value, want)
    _assert_native_ran(before, len(VALID), "valid evaluate")
    assert compat._native_calc_calls == 0, "production must not call compatibility ABI"


@check("native evaluate equals fixed original-Python answers on the SAME valid inputs")
def _():
    _reset()
    for text, unit, _ in VALID:
        before = native._native_calc_calls
        expected = reference.expected(text, unit)
        assert native._native_calc_calls == before, "oracle used production expression adapter"
        actual = calc.evaluate(text, unit)
        assert expected[1] is None and expected[0] is not None, (text, expected)
        assert actual[1] is None and actual[0] is not None, (text, actual)
        assert abs(actual[0] - expected[0]) <= 1e-9 * max(1.0, abs(expected[0])), (
            text, unit, actual, expected)
        _assert_native_ran(before, 1, text)


@check("every rejected formula is native-owned with byte-identical captured errors")
def _():
    _reset()
    with patch.object(compat, "_native_calc", side_effect=AssertionError("legacy route used")):
        for text, unit in REJECTED:
            before = native._native_calc_calls
            expected = reference.expected(text, unit)
            assert expected[0] is None and isinstance(expected[1], str) and expected[1], (
                text, "reference must be an error", expected)
            assert native._native_calc_calls == before, "oracle used production expression adapter"
            actual = calc.evaluate(text, unit)
            assert actual == expected, (text, unit, actual, "expected", expected)
            _assert_native_ran(before, 1, text)
    assert compat._native_calc_calls == 0


@check("documented rejection strings remain EXACT and increment native ownership counter")
def _():
    _reset()
    pins = [
        ("1 + 2", "no spaces in formulas"),
        ("1/0", "bad number: '1/0'"),
        ("5/(3-3)", "division by zero"),
        ("1+*2", "unexpected token: '*'"),
        ("pow(2)", "pow takes 2 argument(s), got 1"),
        ("", "empty formula"),
    ]
    for text, want in pins:
        assert calc.evaluate(text) == (None, want), (text, want)
    _assert_native_ran(0, len(pins), "contract pins")


@check("domain/overflow/arity/nonfinite rejections retain native ownership and parity")
def _():
    _reset()
    texts = ["sin()", "sin(1,2)", "atan2(1)", "pow(1,2,3)", "sinh(1000)",
             "cosh(1000)", "pow(1e308,2)", "pow(-1e308,1.5)", "1e308*2",
             "sin(1e308*2)", "ln(1e308*2)", "1e999", "sqrt(-1)@"]
    for text in texts:
        expected = reference.expected(text)
        assert expected[0] is None and expected[1], (text, expected)
        before = native._native_calc_calls
        assert calc.evaluate(text) == expected, (text, expected)
        _assert_native_ran(before, 1, text)


@check("missing and unloadable required ABIs raise RuntimeError without Python replay")
def _():
    empty = os.path.join(TMP, "empty.so")
    with open(empty, "wb"):
        pass
    for path in [os.path.join(TMP, "no_such_lib.so"), empty]:
        with _library_path(path):
            for text, unit in [("pi", "Millimeters"), ("1+2*3", "Millimeters"),
                               ("27cm", "Meters"), ("1/0", "Millimeters"),
                               ("sqrt(-1)", "Millimeters"), ("", "Millimeters")]:
                _runtime_error(lambda: calc.evaluate(text, unit))
            assert native._native_calc_calls == 0


@check("stale identity or missing calculator export raises RuntimeError, never replay")
def _():
    class Identity:
        def __init__(self, value):
            self.value = value
        def __call__(self):
            return self.value
    class Stale:
        narwhal_pure_core_abi_version = Identity(1)
        narwhal_pure_core_calc_abi2 = Identity(1)
    class Missing:
        narwhal_pure_core_abi_version = Identity(1)
    for library in [Stale(), Missing()]:
        with _library_path(os.path.join(TMP, "stale.so")):
            with patch.object(native.ctypes, "CDLL", return_value=library):
                for text in ["1+2*3", "1/0", "pi", "sqrt(-1)"]:
                    _runtime_error(lambda: calc.evaluate(text))
                assert native._native_calc_calls == 0


@check("unwritten, invalid, nonfinite or failed native replies raise RuntimeError")
def _():
    def fault(**changes):
        def call(*args):
            reply = ctypes.cast(args[-2], ctypes.POINTER(native.CalcReply)).contents
            reply.code = 0
            for key, value in changes.items():
                if key != "rc":
                    setattr(reply, key, value)
            return changes.get("rc", 0)
        return call
    def broken(*args):
        raise OSError("injected native call failure")
    faults = [lambda *args: 0, broken, fault(rc=1), fault(code=100), fault(reserved=1),
              fault(value=float("nan")), fault(value=float("inf")),
              fault(start=2, end=1), fault(end=100), fault(aux=1),
              fault(code=3, value=1, start=0, end=1), fault(code=5, start=0, end=0)]
    _reset()
    for binding in faults:
        with patch.object(native, "_required_binding", return_value=binding):
            for text in ["1+2*3", "1/0"]:
                _runtime_error(lambda: calc.evaluate(text))
    assert native._native_calc_calls == 0, "invalid replies must not count as classified results"


@check("compatibility ONLY: legacy direct success ABI retains values and finite results")
def _():
    compat._reset_for_tests()
    assert callable(compat._calc_binding()), "compatibility calculator ABI missing"
    before = compat._native_calc_calls
    for text, unit, want in VALID:
        value = compat._native_calc(text, unit)
        assert value is not None and math.isfinite(value), (text, value)
        assert abs(value - want) <= 1e-9 * max(1.0, abs(want)), (text, value, want)
    assert compat._native_calc_calls == before + len(VALID)


@check("compatibility ONLY: legacy direct ABI declines errors, domains and bad arities")
def _():
    compat._reset_for_tests()
    texts = [(text, unit) for text, unit in REJECTED]
    texts += [(text, "Millimeters") for text in ["sin()", "sin(1,2)", "atan2(1)",
              "pow(1,2,3)", "1e308*2", "sinh(1000)", "cosh(1000)", "1e999"]]
    for text, unit in texts:
        assert compat._native_calc(text, unit) is None, (text, unit)
    assert compat._native_calc_calls == 0


@check("compatibility ONLY: malformed ABI spans clear finite outputs and reject")
def _():
    binding = compat._calc_binding()
    assert callable(binding)
    model = b"Millimeters"
    for text, size in [(None, 1), (b"1", 1025), (b"\xff", 1)]:
        out = ctypes.c_double(42)
        status = binding(text, size, model, len(model), ctypes.byref(out))
        assert status != 0, (text, size, status)
        assert out.value == 0.0 and math.isfinite(out.value), (text, out.value)
    assert binding(b"1", 1, model, len(model), None) != 0
    out = ctypes.c_double(42)
    assert binding(b"1", 1, None, 1, ctypes.byref(out)) != 0
    assert out.value == 0.0 and math.isfinite(out.value)


failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
