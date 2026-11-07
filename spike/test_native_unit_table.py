#!/usr/bin/env python3
"""One source for the unit scales: Rust's UNIT_SYSTEM_MM, read by units.py.

Run (the library defaults to the gate's release build):
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      python3 spike/test_native_unit_table.py

Enumerates every name in the Rust table and in units.py's Python fallback and fails
if a name is missing from either or a shared name has a different value; checks
that units.py takes its scales from Rust when the library loads and from the
identical fallback when it does not; and that every unit name units.py accepted
before still resolves to the same scale.
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.environ.setdefault("NARWHAL_PURE_CORE", os.path.join(
    ROOT, "native", "pure_core", "target", "x86_64-unknown-linux-gnu", "release",
    "libnarwhal_pure_core.so"))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        f"narwhal3d.{name}", os.path.join(ROOT, "narwhal3d", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


native_pure = load("native_pure")
units = load("units")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


# The 24 fixed-scale names units.py accepted before this change, with their values.
BEFORE = {
    "Angstroms": 1e-7, "Nanometers": 1e-6, "Microns": 1e-3, "Millimeters": 1.0,
    "Centimeters": 10.0, "Decimeters": 100.0, "Meters": 1000.0, "Dekameters": 10_000.0,
    "Hectometers": 100_000.0, "Kilometers": 1_000_000.0, "Megameters": 1_000_000_000.0,
    "Gigameters": 1_000_000_000_000.0, "Microinches": 25.4e-6, "Mils": 25.4e-3,
    "Inches": 25.4, "Feet": 304.8, "Yards": 914.4, "Miles": 1_609_344.0,
    "PrinterPoints": 25.4 / 72.0, "PrinterPicas": 25.4 / 12.0, "NauticalMiles": 1_852_000.0,
    "AstronomicalUnits": 1.495978707e11, "LightYears": 9.4607304725808e15,
    "Parsecs": 3.0856775814913673e16,
}

print("-- both tables, name by name --")
rust = native_pure.unit_table()
python = units._PYTHON_UNIT_SCALE_MM
check(rust is not None, f"the Rust unit table loads ({LIBRARY})")
rust = rust or {}
only_rust = sorted(set(rust) - set(python))
only_python = sorted(set(python) - set(rust))
check(not only_rust and not only_python,
      f"the same {len(rust)} names in both (only Rust: {only_rust}, only Python: {only_python})")
differ = {name: (rust[name], python[name]) for name in set(rust) & set(python)
          if rust[name] != python[name]}
check(not differ, f"every shared name has the identical value {differ}")
check(list(rust) == list(python), "and the same order")

print("\n-- units.py reads Rust when it can --")
check(units.UNIT_SCALE_SOURCE == "rust", f"with the library: source is {units.UNIT_SCALE_SOURCE!r}")
check(units.UNIT_SCALE_MM == BEFORE and list(units.UNIT_SCALE_MM) == list(BEFORE),
      "every name units.py accepted still resolves to the same scale, in the same order")
check(all(units.unit_scale_mm(name) == mm for name, mm in BEFORE.items()),
      "unit_scale_mm agrees for all 24")
check(all(units.convert(2.5, a, b) == 2.5 * BEFORE[a] / BEFORE[b] for a in BEFORE for b in BEFORE),
      f"all {len(BEFORE) ** 2} conversions are bit-identical to the old table's")

os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    fallback = load("units")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
check(fallback.UNIT_SCALE_SOURCE == "python" and fallback.UNIT_SCALE_MM == BEFORE,
      "without the library the Python copy gives the same scales")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
