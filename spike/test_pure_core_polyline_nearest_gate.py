#!/usr/bin/env python3
"""Qualification contract for the polyline nearest export (narwhal_pure_core_nearest_on_polyline).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness answers,
refuses a library without the export, and native_nearest keeps polylines on the SDK
callback search on every platform that has not qualified the export, and on any rhino3dm
whose own PointAt and DerivativeAt fail the polyline parity check. Run with the bundled
runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_polyline_nearest_gate.py
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
        gate.probe_pure_core_polyline_nearest(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness answers --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_polyline_nearest(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_POLYLINE_NEAREST_IDENTITY,
          "the witness answers match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_nearest: the polyline route is live only where qualified --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    load("nurbs_data")
    native = load("native_nearest")
    here = (sys.platform, platform.machine())
    check(native.POLYLINE_QUALIFIED_PLATFORMS
          == gate.PURE_CORE_POLYLINE_NEAREST_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    rect = r3.PolylineCurve([r3.Point3d(*p) for p in
                             ((0, 0, 0), (4, 0, 0), (4, 2, 0), (0, 2, 0), (0, 0, 0))])
    expected = (0.5, (2.0, 0.0, 0.0), 1.0, False)

    def rust_rows():
        return native._polyline_rows(native.pack(rect), [(2.0, -1.0, 0.0)])

    native._polyline_state = None
    if here in native.POLYLINE_QUALIFIED_PLATFORMS:
        check(native.polyline_capability() == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        check(rust_rows() == [expected], f"{here} answers a Rectangle query through the export")
    else:
        check(native.polyline_capability().startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._polyline_state = None
            check(native.polyline_capability().startswith(f"not qualified on {plat}/{machine}")
                  and rust_rows() is None
                  and native.search(native.pack(rect), (2.0, -1.0, 0.0)) == expected,
                  f"{plat}/{machine} keeps the SDK callback search, with the same answer")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._polyline_state = None
    # An SDK whose own PointAt rounds differently (a fused multiply-add) gives different
    # reference answers on the witnesses: model that by nudging the reference.
    real_search = native._callback_search
    native._callback_search = lambda packed, q: (lambda t, p, d, e: (t, (p[0] + 2.0 ** -40, p[1], p[2]), d, e))(*real_search(packed, q))
    try:
        native._polyline_state = None
        check("differ" in native.polyline_capability() and rust_rows() is None,
              "an SDK that fails the witness parity keeps the SDK callback search")
    finally:
        native._callback_search = real_search
        native._polyline_state = None
    check(native.polyline_capability() == "qualified" or here not in native.POLYLINE_QUALIFIED_PLATFORMS,
          "the parity check passes again with the real SDK")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
