#!/usr/bin/env python3
"""Qualification contract for the curve/curve intersection export (narwhal_pure_core_curve_curve_hits).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness hits,
refuses a library without the export, and curve_curve keeps the Rust recursion off on every
platform that has not qualified it. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_curve_hits_gate.py
"""
import importlib.util
import os
from pathlib import Path
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


def load_native_intersect():
    pkg = types.ModuleType("narwhal3d")
    pkg.__path__ = [str(ROOT / "narwhal3d")]
    sys.modules.setdefault("narwhal3d", pkg)
    spec = importlib.util.spec_from_file_location(
        "narwhal3d.native_intersect", ROOT / "narwhal3d" / "native_intersect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refused(path):
    try:
        gate.probe_pure_core_curve_hits(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness hits --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_curve_hits(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_CURVE_HITS_IDENTITY,
          "the witness hits match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- curve_curve: the Rust recursion is live only on a qualified platform --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    native = load_native_intersect()
    real_platform = sys.platform
    check(native.QUALIFIED_PLATFORMS == gate.PURE_CORE_CURVE_HITS_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    native._signature = native._binding = None
    a = r3.LineCurve(r3.Point3d(0, -1, 0), r3.Point3d(0, 1, 0)).ToNurbsCurve()
    b = r3.LineCurve(r3.Point3d(-1, 0, 0), r3.Point3d(1, 0, 0)).ToNurbsCurve()
    if real_platform in native.QUALIFIED_PLATFORMS:
        check(native.capability() == "qualified",
              f"{real_platform} is qualified and its library carries the export")
        check(native.point_hits(a, b, 1e-6, 40) == [((0.0, 0.0, 0.0), 1.0, 1.0)],
              f"{real_platform} answers through the export")
    else:
        check(native.capability().startswith("not qualified"),
              f"{real_platform} is not qualified, so the export is reported off")
    sys.platform = "win32"
    try:
        native._signature = None
        check(native.capability().startswith("not qualified on win32"),
              "win32 reports the export as not qualified")
        check(native.point_hits(a, b, 1e-6, 40) is None,
              "win32 keeps the Python recursion even with the export present")
    finally:
        sys.platform = real_platform
        native._signature = None

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
