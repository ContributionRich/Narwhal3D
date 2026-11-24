#!/usr/bin/env python3
"""Qualification contract for the offset fit export (narwhal_pure_core_offset_fit).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness fits,
refuses a library without the export, and native_nearest keeps offsets on the Python route on
every platform that has not qualified the export, and on any rhino3dm where the Python route
does not give Rust's fit on the witness curves. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_offset_fit_gate.py
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
        gate.probe_pure_core_offset_fit(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness fits --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_offset_fit(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_OFFSET_FIT_IDENTITY,
          "the witness fits match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_nearest: the offset fit route is live only where qualified --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    load("nurbs_data")
    native = load("native_nearest")
    off = load("offset")
    here = (sys.platform, platform.machine())
    check(native.OFFSET_QUALIFIED_PLATFORMS == gate.PURE_CORE_OFFSET_FIT_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    wave = r3.NurbsCurve.CreateControlPointCurve(
        [r3.Point3d(i * 10.0, math.sin(i * 0.6) * 8.0, 0.0) for i in range(12)], 3)
    n = (0.0, 0.0, 1.0)
    reference = off._python_compact_offset(wave, 1.0, n, 0.01)

    def same(result):
        return (result is not None and result.curve is not None
                and native._offset_bits(result.curve, result.max_deviation)
                == native._offset_bits(reference.curve, reference.max_deviation))

    native._offset_state = None
    if here in native.OFFSET_QUALIFIED_PLATFORMS:
        check(native.offset_capability(off._python_compact_offset) == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        before = native._native_offset_calls
        check(same(off._compact_offset(wave, 1.0, n, 0.01))
              and native._native_offset_calls == before + 1,
              f"{here} fits a smooth offset through the export, as the Python route does")
    else:
        check(native.offset_capability(off._python_compact_offset).startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._offset_state = None
            before = native._native_offset_calls
            check(native.offset_capability(off._python_compact_offset)
                  .startswith(f"not qualified on {plat}/{machine}")
                  and native.offset_fit(wave, 1.0, n, 0.01, off._python_compact_offset) is None
                  and same(off._compact_offset(wave, 1.0, n, 0.01))
                  and native._native_offset_calls == before,
                  f"{plat}/{machine} keeps the Python route, with the same fit")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._offset_state = None

    # An SDK that evaluates differently (a fused multiply-add) gives the Python route a
    # different fit on the witnesses: model that by nudging one control point.
    def nudged(curve, distance, normal, tolerance):
        result = off._python_compact_offset(curve, distance, normal, tolerance)
        p = result.curve.Points[1]
        result.curve.Points[1] = r3.Point4d(math.nextafter(p.X, math.inf), p.Y, p.Z, p.W)
        return result

    native._offset_state = None
    check("differs" in native.offset_capability(nudged)
          and native.offset_fit(wave, 1.0, n, 0.01, nudged) is None,
          "an SDK that fails the witness parity keeps the Python route")
    native._offset_state = None
    check(native.offset_capability(off._python_compact_offset) == "qualified"
          or here not in native.OFFSET_QUALIFIED_PLATFORMS,
          "the parity check passes again with the real SDK")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
