#!/usr/bin/env python3
"""Qualification contract for the record curve/curve export (narwhal_pure_core_curve_curve_record_hits).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness hits,
refuses a library without the export, and native_intersect keeps pairs with a polyline in
the Python recursion on every platform that has not qualified the export, and on any
rhino3dm whose own Split and PointAt fail the polyline parity check. Run with the bundled
runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_curve_record_hits_gate.py
"""
import importlib.util
import os
from pathlib import Path
import platform
import sys
import types

import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_pure_core as gate  # noqa: E402

LIBRARY = os.environ.get("NARWHAL_PURE_CORE", "")
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def load(name):
    pkg = sys.modules.setdefault("narwhal3d", types.ModuleType("narwhal3d"))
    pkg.__path__ = [str(ROOT / "narwhal3d")]
    spec = importlib.util.spec_from_file_location(
        f"narwhal3d.{name}", ROOT / "narwhal3d" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


def refused(path):
    try:
        gate.probe_pure_core_curve_record_hits(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness hits --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_curve_record_hits(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_CURVE_RECORD_HITS_IDENTITY,
          "the witness hits match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_intersect: the polyline route is live only where qualified --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    load("nurbs_data")
    native = load("native_intersect")
    load("intersect")
    here = (sys.platform, platform.machine())
    check(native.POLYLINE_QUALIFIED_PLATFORMS
          == gate.PURE_CORE_CURVE_RECORD_HITS_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    rect = r3.PolylineCurve([r3.Point3d(*p) for p in
                             ((0, 0, 0), (4, 0, 0), (4, 2, 0), (0, 2, 0), (0, 0, 0))])
    cross = r3.LineCurve(r3.Point3d(2, -1, 0), r3.Point3d(2, 3, 0)).ToNurbsCurve()
    expected = [((2.0, 0.0, 0.0), 0.5, 1.0), ((2.0, 2.0, 0.0), 2.5, 3.0)]  # the line runs 0..4
    native._record_state = None
    if here in native.POLYLINE_QUALIFIED_PLATFORMS:
        check(native.polyline_capability() == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        check(native.point_hits(rect, cross, 1e-6, 40) == expected,
              f"{here} answers a Rectangle crossed by a line through the export")
    else:
        check(native.polyline_capability().startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._record_state = None
            check(native.polyline_capability().startswith(f"not qualified on {plat}/{machine}")
                  and native.point_hits(rect, cross, 1e-6, 40) is None,
                  f"{plat}/{machine} keeps the Python recursion even with the export present")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._record_state = None
    # An SDK whose own Split/PointAt round differently (a fused multiply-add) gives
    # different reference hits on the witnesses: model that by nudging the reference.
    real_hits = native._python_hits
    native._python_hits = lambda *a: [((x + 2.0 ** -40, y, z), ta, tb)
                                      for (x, y, z), ta, tb in real_hits(*a)]
    try:
        native._record_state = None
        check(native.polyline_capability().endswith("polylines recurse in Python")
              and "differ" in native.polyline_capability()
              and native.point_hits(rect, cross, 1e-6, 40) is None,
              "an SDK that fails the witness parity keeps the Python recursion")
    finally:
        native._python_hits = real_hits
        native._record_state = None
    check(native.polyline_capability() == "qualified" or here not in native.POLYLINE_QUALIFIED_PLATFORMS,
          "the parity check passes again with the real SDK")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
