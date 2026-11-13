#!/usr/bin/env python3
"""One source for the unit scales: Rust's UNIT_SYSTEM_MM, read by units.py.

Run (the library defaults to the gate's release build):
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      python3 spike/test_native_unit_table.py

Compares the Rust unit table (read through the required native adapter
``narwhal3d/native_unit_table.py``) against the independent BEFORE fixture in
``spike/units_reference.py`` — the frozen original units.py, kept separately so
this comparison never reads a value the code under test produced.  Fails if a
name is missing from either table or a shared name has a different value, that
the count is checked against the 24 fixed NAMES (not the values), and that
units.py takes its scales from Rust (source ``'rust'``) in order.  The required
adapter itself must RAISE when the library is missing or stale — it has no
Python fallback to compare against.
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

    ``standalone=False`` (default): load under the ``narwhal3d`` package so
    relative imports resolve into the package.  Used for ``native_unit_table``
    and ``units`` (the code under test).

    ``standalone=True``: load as a top-level module (no package).  The
    module's relative imports (``from . import …``) fail with ImportError,
    forcing any fallback logic to use its own local data.  Used for
    ``units_reference`` so its ``_unit_scales`` falls back to its own
    ``_PYTHON_UNIT_SCALE_MM`` literal, keeping the oracle independent of the
    real package.
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
# Independent BEFORE fixture: the frozen original units.py, kept OUTSIDE the
# package so its ``_PYTHON_UNIT_SCALE_MM`` literal is an independent oracle and
# is never read from the module under test.
# Independent BEFORE fixture: the frozen original units.py, loaded STANDALONE
# (top-level, no package) so its ``from . import native_pure`` fails and
# ``_unit_scales`` falls back to its own ``_PYTHON_UNIT_SCALE_MM`` literal.
# This keeps the oracle independent of the real ``narwhal3d`` package.
reference = load("units_reference", os.path.join(ROOT, "spike", "units_reference.py"),
                 standalone=True)
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


# The 24 fixed-scale names and values, read from the independent BEFORE fixture.
BEFORE = dict(reference._PYTHON_UNIT_SCALE_MM)
BEFORE_ORDER = list(reference._PYTHON_UNIT_SCALE_MM)

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
check(all(units.convert(2.5, a, b) == 2.5 * BEFORE[a] / BEFORE[b]
          for a in BEFORE for b in BEFORE),
      f"all {len(BEFORE) ** 2} conversions are bit-identical to the BEFORE table's")
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
