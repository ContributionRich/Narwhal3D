#!/usr/bin/env python3
"""Qualification contract for the NURBS record export (narwhal_pure_core_nurbs_curve_record).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness doubles and
refusals, refuses a library without the export, and nurbs_data keeps the per-point read on
every platform that has not qualified it. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_nurbs_record_gate.py
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


def load_nurbs_data():
    pkg = types.ModuleType("narwhal3d")
    pkg.__path__ = [str(ROOT / "narwhal3d")]
    sys.modules.setdefault("narwhal3d", pkg)
    spec = importlib.util.spec_from_file_location(
        "narwhal3d.nurbs_data", ROOT / "narwhal3d" / "nurbs_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refused(path):
    try:
        gate.probe_pure_core_nurbs_record(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness doubles --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_nurbs_record(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_NURBS_RECORD_IDENTITY,
          "the witness doubles match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- nurbs_data: the record route is live only on a qualified platform --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    nd = load_nurbs_data()
    real_platform = sys.platform
    check(nd.QUALIFIED_PLATFORMS == gate.PURE_CORE_NURBS_RECORD_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    nd._signature = nd._binding = None
    curve = r3.Circle(r3.Point3d(1, 2, 3), 5.0).ToNurbsCurve()
    want = (int(curve.Order), bool(curve.IsRational), list(curve.Knots.ToList()),
            [v for cv in curve.Points for v in (cv.X, cv.Y, cv.Z, cv.W)])

    def read():
        order, rational, knots, cvs = nd.arrays(curve)
        return order, rational, list(knots), list(cvs)

    if real_platform in nd.QUALIFIED_PLATFORMS:
        check(nd.capability() == "qualified",
              f"{real_platform} is qualified and its library carries the export")
        before = nd._encoded_reads
        check(read() == want and nd._encoded_reads == before + 1,
              f"{real_platform} reads the curve through the export")
    else:
        check(nd.capability().startswith("not qualified"),
              f"{real_platform} is not qualified, so the export is reported off")
    sys.platform = "win32"
    try:
        nd._signature = None
        check(nd.capability().startswith("not qualified on win32"),
              "win32 reports the export as not qualified")
        before = nd._point_reads
        check(read() == want and nd._point_reads == before + 1,
              "win32 reads point by point even with the export present")
    finally:
        sys.platform = real_platform
        nd._signature = None

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
