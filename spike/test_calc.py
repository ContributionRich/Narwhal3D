"""Tests for the command-line calculator.

A recursive-descent parser; it must NEVER use eval/exec (proven by refusing
``__import__('os')``). Every listed example follows the documented reference rules from
user_interface/accurate_modeling. A failure must be an ERROR STRING, never a
number.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_calc.py
"""
import importlib.util
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


calc = _load("narwhal_test_calc", "calc.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def ok(name, text, expected, model_unit="Millimeters", tol=1e-9):
    @check(name)
    def _():
        val, err = calc.evaluate(text, model_unit)
        assert err is None, (text, err)
        assert abs(val - expected) <= tol, (text, val, expected)
    return _


# ---------------------------------------------------------------------------
# Documented reference examples
# ---------------------------------------------------------------------------
ok("10*sin(30degrees) = 5", "10*sin(30degrees)", 5.0)
ok("sin(pi/6) = 0.5", "sin(pi/6)", 0.5)
ok("pi/2radians = pi/2", "pi/2radians", math.pi / 2)
ok("documented example (computed with math)",
   "(10*sin(pi/4)+63.00)/1.234e12+-56.3",
   (10 * math.sin(math.pi / 4) + 63.00) / 1.234e12 + -56.3, tol=1e-6)

# ---------------------------------------------------------------------------
# arithmetic + precedence + unary
# ---------------------------------------------------------------------------
ok("1+2*3", "1+2*3", 7)
ok("(1+2)*3", "(1+2)*3", 9)
ok("10/4", "10/4", 2.5)
ok("2-3-4 left-associative", "2-3-4", -5)
ok("unary minus", "-5+2", -3)
ok("unary plus", "+5", 5)
ok("double unary", "--5", 5)
ok("fraction in formula 5/16", "5/16", 5 / 16)
ok("mixed number 1-3/4+1 = 2.75", "1-3/4+1", 2.75)
ok("pi constant", "pi", math.pi)
ok("pi/4", "pi/4", math.pi / 4)

# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
ok("sin(0)=0", "sin(0)", 0.0)
ok("cos(pi)=-1", "cos(pi)", -1.0, tol=1e-12)
ok("tan(pi/4)=1", "tan(pi/4)", 1.0, tol=1e-12)
ok("asin(1)=pi/2", "asin(1)", math.pi / 2, tol=1e-12)
ok("acos(0)=pi/2", "acos(0)", math.pi / 2, tol=1e-12)
ok("atan(1)=pi/4", "atan(1)", math.pi / 4, tol=1e-12)
ok("atan2(1,1)=pi/4", "atan2(1,1)", math.pi / 4, tol=1e-12)
ok("ln(e)=1", "ln(exp(1))", 1.0, tol=1e-12)
ok("log10(1000)=3", "log10(1000)", 3.0, tol=1e-12)
ok("exp(0)=1", "exp(0)", 1.0)
ok("sinh(0)=0", "sinh(0)", 0.0)
ok("cosh(0)=1", "cosh(0)", 1.0)
ok("tanh(0)=0", "tanh(0)", 0.0)
ok("sqrt(16)=4", "sqrt(16)", 4.0)
ok("sqrt(2)", "sqrt(2)", math.sqrt(2))
ok("pow(2,10)=1024", "pow(2,10)", 1024.0)
ok("pow(9,1/2)=3", "pow(9,1/2)", 3.0)

# ---------------------------------------------------------------------------
# angle units
# ---------------------------------------------------------------------------
ok("sin(30d)=0.5", "sin(30d)", 0.5)
ok("sin(30degrees)=0.5", "sin(30degrees)", 0.5)
ok("sin(30grade) = sin(27deg)", "sin(30grade)", math.sin(math.radians(27.0)), tol=1e-9)
ok("cos(90d)=0", "cos(90d)", 0.0, tol=1e-12)
ok("sin(pi)~0 (radians default)", "sin(pi)", 0.0, tol=1e-12)
ok("tan(45d)=1", "tan(45d)", 1.0, tol=1e-12)

# ---------------------------------------------------------------------------
# length units converted to the model unit
# ---------------------------------------------------------------------------
ok("27cm in Meters = 0.27", "27cm", 0.27, model_unit="Meters")
ok("27cm + 10cm in Meters = 0.37", "27cm+10cm", 0.37, model_unit="Meters")
ok("10m in mm = 10000", "10m", 10000.0, model_unit="Millimeters")
ok("1'6\" in Feet = 1.5", "1'6\"", 1.5, model_unit="Feet")

# ---------------------------------------------------------------------------
# errors (never a number)
@check("unknown name 'abc' is an error")
def _():
    val, err = calc.evaluate("abc")
    assert val is None, val
    assert isinstance(err, str) and err


@check("unbalanced parens ( missing ) is an error")
def _():
    val, err = calc.evaluate("(1+2")
    assert val is None, val
    assert isinstance(err, str) and err


@check("unbalanced parens ) missing ( is an error")
def _():
    val, err = calc.evaluate("1+2)")
    assert val is None, val


@check("1/0 is an error")
def _():
    val, err = calc.evaluate("1/0")
    assert val is None, val
    assert isinstance(err, str) and err


@check("5/(3-3) division by zero is an error")
def _():
    val, err = calc.evaluate("5/(3-3)")
    assert val is None, val
    assert isinstance(err, str) and err


@check("sqrt(-1) is an error")
def _():
    val, err = calc.evaluate("sqrt(-1)")
    assert val is None, val
    assert isinstance(err, str) and err


@check("asin(2) is an error")
def _():
    val, err = calc.evaluate("asin(2)")
    assert val is None, val
    assert isinstance(err, str) and err


@check("ln(0) is an error")
def _():
    val, err = calc.evaluate("ln(0)")
    assert val is None, val
    assert isinstance(err, str) and err


@check("acos(-2) is an error")
def _():
    val, err = calc.evaluate("acos(-2)")
    assert val is None, val


@check("__import__('os') is refused (no eval)")
def _():
    val, err = calc.evaluate("__import__('os')")
    assert val is None, ("expected refusal, got", val)
    assert isinstance(err, str) and err


@check("a space is an error")
def _():
    val, err = calc.evaluate("1 + 2")
    assert val is None, val
    assert isinstance(err, str) and err


@check("empty formula is an error")
def _():
    val, err = calc.evaluate("")
    assert val is None, val


@check("a bare operator is an error")
def _():
    val, err = calc.evaluate("1+*2")
    assert val is None, val


@check("pow with one arg is an error")
def _():
    val, err = calc.evaluate("pow(2)")
    assert val is None, val


@check("unknown function is an error")
def _():
    val, err = calc.evaluate("foo(1)")
    assert val is None, val


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
