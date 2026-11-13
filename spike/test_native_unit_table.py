#!/usr/bin/env python3
"""Required Rust unit-table/public-host contract against static BEFORE bits.

The 24 independent values were captured from the old Python oracle using
bundled Python 3.13, before retiring that implementation to ignored local backup.
Pure 576-pair numeric checks live in Rust unit_golden; this suite retains all
12 table/metadata/admission checks and explicit live Python boundary tuples.
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


def load(name, path=None, standalone=False):
    """Load a module by file path.

    Package loading exercises relative adapter imports without importing Blender.
    """
    if path is None:
        path = os.path.join(ROOT, "narwhal3d", f"{name}.py")
    module_name = name if standalone else f"narwhal3d.{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if not standalone:
        setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


adapter = load("native_unit_table")
units = load("units")
# Static independently captured values; no retired Python engine is imported.
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


# The 24 fixed-scale names and values, read from the independent BEFORE fixture.
from pathlib import Path
import struct
BEFORE = {name: struct.unpack('!d', bytes.fromhex(value))[0]
          for name, value in (line.split() for line in
          (Path(ROOT) / 'native/pure_core/tests/fixtures/unit_table.txt').read_text().splitlines())}
BEFORE_ORDER = list(BEFORE)

print("-- Rust table (via required adapter) vs independent BEFORE fixture --")
result = adapter.unit_table()
rust = result["table"]
check(result["status"] == "ok" and result["count"] == 24,
      f"the Rust unit table loads via the adapter ({LIBRARY})")
only_rust = sorted(set(rust) - set(BEFORE))
only_before = sorted(set(BEFORE) - set(rust))
check(not only_rust and not only_before,
      f"the same {len(rust)} names in both (only Rust: {only_rust}, only BEFORE: {only_before})")
differ = {name: (rust[name], BEFORE[name]) for name in set(rust) & set(BEFORE)
          if rust[name] != BEFORE[name]}
check(not differ, f"every shared name has the identical value {differ}")
check(list(rust) == BEFORE_ORDER, "and the same order")
# Count is checked against the NAMES list (24), not the value count.
check(result["count"] == len(BEFORE_ORDER) and len(result["names"]) == len(BEFORE_ORDER),
      f"count {result['count']} equals the {len(BEFORE_ORDER)} fixed NAMES (names, not values)")

print("\n-- units.py reads Rust when it can --")
check(units.UNIT_SCALE_SOURCE == "rust",
      f"with the library: source is {units.UNIT_SCALE_SOURCE!r}")
check(units.UNIT_SCALE_MM == BEFORE and list(units.UNIT_SCALE_MM) == BEFORE_ORDER,
      "every name units.py accepted still resolves to the same scale, in the same order")
check(all(units.unit_scale_mm(name) == mm for name, mm in BEFORE.items()),
      "unit_scale_mm agrees for all 24")
check(all(units.convert(v, a, b) == expected for v, a, b, expected in [
    (2.5, 'Millimeters', 'Inches', 0.0984251968503937),
    (2.5, 'Inches', 'Millimeters', 63.5),
    (2.5, 'Meters', 'Millimeters', 2500.0)]),
      'live public conversion boundary tuples (576 numeric pairs in Rust unit_golden)')
check(len(units.UNIT_MEMBERS) == 27, "UNIT_MEMBERS still has 27 public names")

print("\n-- the required adapter has no Python fallback --")
# 1) Missing library (env set to a non-existent path) must raise.
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "no-such-library.so")
try:
    adapter2 = load("native_unit_table")
    adapter2._binding = None
    raised = False
    try:
        adapter2.unit_table()
    except RuntimeError:
        raised = True
    check(raised, "missing library: the required adapter raises (not replayed in Python)")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

# 2) Empty env must refuse — never fall through to packaged.
os.environ["NARWHAL_PURE_CORE"] = ""
try:
    adapter3 = load("native_unit_table")
    raised = False
    try:
        adapter3.unit_table()
    except RuntimeError:
        raised = True
    check(raised, "empty NARWHAL_PURE_CORE: the required adapter refuses (no packaged fallback)")
finally:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
