#!/usr/bin/env python3
"""Qualification contract for the line samples export (narwhal_pure_core_line_samples).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness bits and
refusals, refuses a library without the export, and native_nearest keeps lines on PointAt
on every platform that has not qualified it. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_line_samples_gate.py
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
        gate.probe_pure_core_line_samples(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness bits --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_line_samples(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_LINE_SAMPLES_IDENTITY,
          "the witness samples match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_nearest: the line route is live only on a qualified platform --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    native = load_native_nearest()
    here = (sys.platform, platform.machine())
    check(native.LINE_QUALIFIED_PLATFORMS == gate.PURE_CORE_LINE_SAMPLES_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    native._line_state = None
    line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(2, 4, 8))
    if here in native.LINE_QUALIFIED_PLATFORMS:
        check(native.line_capability() == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        check(native.sample(line, 4) == [(0.0, 0.0, 0.0), (0.5, 1.0, 2.0), (1.0, 2.0, 4.0),
                                          (1.5, 3.0, 6.0), (2.0, 4.0, 8.0)],
              f"{here} answers through the export")
    else:
        check(native.line_capability().startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._line_state = None
            check(native.line_capability().startswith(f"not qualified on {plat}/{machine}")
                  and native.sample(line, 4) is None,
                  f"{plat}/{machine} keeps PointAt even with the export present")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._line_state = None

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
