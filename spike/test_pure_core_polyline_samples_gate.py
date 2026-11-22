#!/usr/bin/env python3
"""Qualification contract for the polyline samples export (narwhal_pure_core_polyline_samples).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness samples,
refuses a library without the export, and native_nearest keeps polylines on PointAt on every
platform that has not qualified the export, and on any rhino3dm whose own PointAt fails the
polyline sample parity check. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_polyline_samples_gate.py
"""
import importlib.util
import math
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
        gate.probe_pure_core_polyline_samples(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness samples --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_polyline_samples(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_POLYLINE_SAMPLES_IDENTITY,
          "the witness samples match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_nearest: the polyline sample route is live only where qualified --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    load("nurbs_data")
    native = load("native_nearest")
    here = (sys.platform, platform.machine())
    check(native.POLYLINE_SAMPLES_QUALIFIED_PLATFORMS
          == gate.PURE_CORE_POLYLINE_SAMPLES_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    rect = r3.PolylineCurve([r3.Point3d(*p) for p in
                             ((0, 0, 0), (4, 0, 0), (4, 2, 0), (0, 2, 0), (0, 0, 0))])
    # T0 + 4 * i / 8 on [0, 4]: the corners and the edge midpoints, exact either way
    expected = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 1.0, 0.0),
                (4.0, 2.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0), (0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0)]

    native._polyline_sample_state = None
    if here in native.POLYLINE_SAMPLES_QUALIFIED_PLATFORMS:
        check(native.polyline_sample_capability() == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        before = native._native_polyline_sample_calls
        check(native.sample(rect, 8) == expected
              and native._native_polyline_sample_calls == before + 1,
              f"{here} samples a Rectangle through the export")
    else:
        check(native.polyline_sample_capability().startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._polyline_sample_state = None
            check(native.polyline_sample_capability().startswith(f"not qualified on {plat}/{machine}")
                  and native.sample(rect, 8) is None
                  and native._point_at_samples(rect, 8) == expected,
                  f"{plat}/{machine} keeps PointAt, with the same samples")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._polyline_sample_state = None
    # An SDK whose own PointAt rounds differently (a fused multiply-add) gives different
    # reference samples on the witnesses: model that by nudging one.
    real_point_at = native._point_at_samples

    def nudged(curve, n):
        pts = real_point_at(curve, n)
        x, y, z = pts[1]
        pts[1] = (math.nextafter(x, math.inf), y, z)
        return pts

    native._point_at_samples = nudged
    try:
        native._polyline_sample_state = None
        check("differs" in native.polyline_sample_capability() and native.sample(rect, 8) is None,
              "an SDK that fails the witness parity keeps PointAt")
    finally:
        native._point_at_samples = real_point_at
        native._polyline_sample_state = None
    check(native.polyline_sample_capability() == "qualified"
          or here not in native.POLYLINE_SAMPLES_QUALIFIED_PLATFORMS,
          "the parity check passes again with the real SDK")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
