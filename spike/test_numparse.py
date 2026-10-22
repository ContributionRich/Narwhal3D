"""Tests for numbers, fractions, and lengths with units.

Every example follows the documented accurate-modeling rules. A
failure must be an ERROR STRING, never a wrong number.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_numparse.py
"""
import importlib.util
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


np = _load("narwhal_test_numparse", "numparse.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def ok(name, text, expected, **kw):
    @check(name)
    def _():
        val, err = np.parse_number(text)
        assert err is None, (text, err)
        assert abs(val - expected) < 1e-9, (text, val, expected)
    return _


def oklen(name, text, model_unit, expected):
    @check(name)
    def _():
        val, err = np.parse_length(text, model_unit)
        assert err is None, (text, model_unit, err)
        assert abs(val - expected) < 1e-9, (text, model_unit, val, expected)
    return _


def bad(name, text, **kw):
    @check(name)
    def _():
        val, err = np.parse_number(text)
        assert val is None, (text, "expected error, got", val)
        assert isinstance(err, str) and err, (text, err)
    return _


def badlen(name, text, model_unit="Millimeters"):
    @check(name)
    def _():
        val, err = np.parse_length(text, model_unit)
        assert val is None, (text, "expected error, got", val)
        assert isinstance(err, str) and err, (text, err)
    return _


# ---------------------------------------------------------------------------
# bare numbers
# ---------------------------------------------------------------------------
ok("whole", "123", 123.0)
ok("decimal", "123.456", 123.456)
ok("leading-dot", "0.456", 0.456)
ok("dot-only", ".456", 0.456)
ok("sci-negative", "-1.23456e10", -1.23456e10)
ok("sci-uppercase", "1.23456E10", 1.23456e10)
ok("fraction 5/16", "5/16", 5.0 / 16)
ok("mixed 1-3/4", "1-3/4", 1.75)
ok("plus sign", "+123", 123.0)
ok("minus sign", "-123", -123.0)
ok("minus fraction", "-5/16", -(5.0 / 16))

# 1-3/4 is 1.75 but 1-3 is NOT -2
ok("1-3/4 is 1.75", "1-3/4", 1.75)
bad("1-3 is an error (not -2)", "1-3")
bad("1/0 is an error", "1/0")
bad("5/ is an error", "5/")
bad("abc is an error", "abc")
bad("empty is an error", "")
bad("a space is an error", "1 3")

# ---------------------------------------------------------------------------
# lengths with units (converted to the model unit)
# ---------------------------------------------------------------------------
oklen("1.235millimeters (mm model)", "1.235millimeters", "Millimeters", 1.235)
oklen("-1.234cm", "-1.234cm", "Millimeters", -12.34)
oklen("27cm in Meters = 0.27", "27cm", "Meters", 0.27)
oklen("+16'5\" in mm", "+16'5\"", "Millimeters", 5003.8)
oklen("1'2-3/4\" in mm", "1'2-3/4\"", "Millimeters", 304.8 + 2.75 * 25.4)
oklen("bare number unchanged (Meters model)", "42", "Meters", 42.0)
oklen("bare number unchanged (mm model)", "42", "Millimeters", 42.0)
oklen("10m in mm", "10m", "Millimeters", 10000.0)
oklen("2km in Meters", "2km", "Meters", 2000.0)
oklen("1000um in mm", "1000um", "Millimeters", 1.0)
oklen("1in in mm", "1in", "Millimeters", 25.4)
oklen("1ft in mm", "1ft", "Millimeters", 304.8)
oklen("1yd in mm", "1yd", "Millimeters", 914.4)
oklen("1mi in mm", "1mi", "Millimeters", 1609344.0)
oklen("case-insensitive suffix (CM)", "5CM", "Millimeters", 50.0)
oklen("case-insensitive model unit (meters)", "27cm", "meters", 0.27)
oklen("feet-inches in Feet model", "1'6\"", "Feet", 1.5)

# unknown suffix / model unit -> error, never a guess
badlen("unknown suffix is an error", "5xyz")


@check("unknown model unit is an error")
def _():
    val, err = np.parse_length("10", "Furlongs")
    assert val is None, val
    assert isinstance(err, str) and err


@check("feet without inches is an error")
def _():
    val, err = np.parse_length("16'5", "Millimeters")
    assert val is None, val
    assert isinstance(err, str) and err


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
