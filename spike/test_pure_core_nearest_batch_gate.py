#!/usr/bin/env python3
"""Qualification contract for the batch closest-point export (narwhal_pure_core_nearest_on_nurbs_many).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness answers,
refuses a library without the export, and Near keeps the batch route off on every platform
that has not qualified it. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_nearest_batch_gate.py
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


def load_native_nearest():
    pkg = types.ModuleType("narwhal3d")
    pkg.__path__ = [str(ROOT / "narwhal3d")]
    sys.modules.setdefault("narwhal3d", pkg)
    spec = importlib.util.spec_from_file_location(
        "narwhal3d.native_nearest", ROOT / "narwhal3d" / "native_nearest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refused(path):
    try:
        gate.probe_pure_core_nearest_batch(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library answers the batch export exactly as the single export --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_nearest_batch(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_NEAREST_BATCH_IDENTITY,
          "batch answers equal single answers bit for bit, and match the pinned witness digest")

print("\n-- a library without the batch export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- Near: the batch route is live only on a qualified platform --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the Near checks")
else:
    native = load_native_nearest()
    real_platform = sys.platform
    check(native.BATCH_QUALIFIED_PLATFORMS == gate.PURE_CORE_BATCH_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    native._many_signature = native._many_binding = None
    curve = r3.NurbsCurve.Create(False, 3, [r3.Point3d(0, 0, 0), r3.Point3d(1, 2, 0),
                                            r3.Point3d(3, 2, 0), r3.Point3d(4, 0, 0)])
    packed = native.pack(curve)
    target = (2.0, 1.0, 0.0)
    if real_platform in native.BATCH_QUALIFIED_PLATFORMS:
        check(native.batch_capability() == "qualified",
              f"{real_platform} is qualified and its library carries the batch export")
    else:
        check(native.batch_capability().startswith("not qualified"),
              f"{real_platform} is not qualified, so the batch is reported off")
    sys.platform = "win32"
    try:
        native._many_signature = None
        check(native.batch_capability().startswith("not qualified on win32"),
              "win32 reports the batch as not qualified")
        check(native.search_many(packed, [target]) is None,
              "win32 keeps the batch off even with the export present")
        check(native.search(packed, target) is not None,
              "win32 keeps the single search live (the complete fallback)")
    finally:
        sys.platform = real_platform
        native._many_signature = None

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
