"""Tests for unit-format display. Pure Python, no bpy.

Pins the three "Distance display" modes from
the documented distance-display settings:

* Decimal -- "Sets the display to decimal (1.25)."
* Fractional -- "Sets the display to fractional (1-1/4)."
* Feet & Inches -- "Sets the display to feet and inches (1'-3")."

plus the task's required checks:
* decimal rounding at several precisions;
* 1.75 Fractional @ 1/16 -> ``1-3/4``;
* 30.5 Feet & Inches -> ``2'-6 1/2"``;
* negative values;
* rounding that carries: 0.99999 -> ``1"`` (not ``0-16/16"`` / ``0-16/16``);
* bad mode / bad precision -> ValueError (never a guess).
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


uf = _load("narwhal_unitformat", "unitformat.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def F(value, mode, precision):
    return uf.format_length(value, "mm", mode, precision)


@check("module loads without bpy and format_length exists")
def _():
    assert "bpy" not in sys.modules
    assert callable(uf.format_length)


@check("Decimal: 1.25 at 2 places is '1.25' (the help's own example)")
def _():
    assert F(1.25, "Decimal", 2) == "1.25"
    assert F(1.25, "decimal", 2) == "1.25"      # case-insensitive


@check("Decimal: rounding at several precisions (round-half-up)")
def _():
    assert F(1.2345, "Decimal", 0) == "1"
    assert F(1.5, "Decimal", 0) == "2"          # half-up, not banker's
    assert F(1.2345, "Decimal", 1) == "1.2"
    assert F(1.2345, "Decimal", 2) == "1.23"
    assert F(1.2345, "Decimal", 3) == "1.235"   # 1.2345 -> 1.235 (4th digit 5)
    assert F(1.23456, "Decimal", 4) == "1.2346"
    assert F(0.1, "Decimal", 2) == "0.10"       # trailing zeros kept
    assert F(100.0, "Decimal", 0) == "100"


@check("Decimal: negative values keep the sign, round the magnitude")
def _():
    assert F(-1.2345, "Decimal", 2) == "-1.23"
    assert F(-0.00001, "Decimal", 2) == "-0.00"


@check("Fractional: 1.75 @ 1/16 -> 1-3/4 (12/16 reduced to 3/4)")
def _():
    assert F(1.75, "Fractional", 16) == "1-3/4"


@check("Fractional: exact halves and quarters at 1/4")
def _():
    assert F(1.25, "Fractional", 4) == "1-1/4"   # the help's own example
    assert F(1.5, "Fractional", 4) == "1-1/2"
    assert F(1.75, "Fractional", 4) == "1-3/4"
    assert F(2.0, "Fractional", 4) == "2"        # whole -> no fraction
    assert F(0.25, "Fractional", 4) == "1/4"     # sub-whole -> bare fraction


@check("Fractional: denominator 1 rounds to whole numbers")
def _():
    assert F(1.4, "Fractional", 1) == "1"
    assert F(1.6, "Fractional", 1) == "2"
    assert F(1.5, "Fractional", 1) == "2"        # half-up


@check("Feet & Inches: 30.5 in -> 2'-6 1/2\" (8/16 reduced to 1/2)")
def _():
    assert F(30.5, "Feet & Inches", 16) == "2'-6 1/2\""


@check("Feet & Inches: sub-foot prints a bare inch fraction")
def _():
    assert F(3.5, "Feet & Inches", 16) == "3 1/2\""
    assert F(1.0, "Feet & Inches", 16) == "1\""
    assert F(12.0, "Feet & Inches", 16) == "1'-0\""
    assert F(12.25, "Feet & Inches", 16) == "1'-1/4\""


@check("Feet & Inches: negative values keep the sign")
def _():
    assert F(-30.5, "Feet & Inches", 16) == "-2'-6 1/2\""
    assert F(-1.5, "Feet & Inches", 16) == "-1 1/2\""


@check("carry: 0.99999 Fractional @1/16 -> '1', NOT '0-16/16'")
def _():
    got = F(0.99999, "Fractional", 16)
    assert got == "1", got
    assert got != "0-16/16"


@check("carry: 0.99999 Feet & Inches @1/16 -> '1\"', NOT '0-16/16\"'")
def _():
    got = F(0.99999, "Feet & Inches", 16)
    assert got == "1\"", got
    assert "16/16" not in got


@check("carry across the foot boundary: 11.99999 in -> '1'-11 15/16\"'")
def _():
    # 11.99999 rounds to 12 in at 1/16 = exactly 1'-0"
    assert F(11.99999, "Feet & Inches", 16) == "1'-0\""


@check("zero follows each mode's own formatting")
def _():
    assert F(0, "Decimal", 2) == "0.00"
    assert F(0, "Fractional", 16) == "0"
    assert F(0, "Feet & Inches", 16) == "0\""


@check("bad mode -> ValueError (never a guess)")
def _():
    for bad in ("Imperial", "metric", "", None, 5):
        try:
            F(1.0, bad, 2)
            raise AssertionError(f"expected ValueError for mode {bad!r}")
        except ValueError:
            pass


@check("bad precision -> ValueError")
def _():
    for bad in (-1, "x", None, 4096):
        try:
            F(1.0, "Decimal", bad)
            raise AssertionError(f"expected ValueError for Decimal prec {bad!r}")
        except ValueError:
            pass
    for bad in (0, -4, "x", None, 4096):
        try:
            F(1.0, "Fractional", bad)
            raise AssertionError(f"expected ValueError for Fractional prec {bad!r}")
        except ValueError:
            pass


@check("non-numeric value -> ValueError")
def _():
    for bad in ("1.5", None, [1]):
        try:
            F(bad, "Decimal", 2)
            raise AssertionError(f"expected ValueError for value {bad!r}")
        except ValueError:
            pass


@check("model_unit is accepted but does not change the rendering")
def _():
    assert uf.format_length(1.75, "mm", "Fractional", 16) == \
        uf.format_length(1.75, "in", "Fractional", 16) == "1-3/4"


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
