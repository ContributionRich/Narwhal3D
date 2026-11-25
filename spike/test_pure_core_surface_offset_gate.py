#!/usr/bin/env python3
"""Qualification contract for the surface offset export (narwhal_pure_core_surface_offset).

scripts/check_pure_core.py probes the built library. This test pins the behaviour around it:
the probe accepts the library named by NARWHAL_PURE_CORE with the pinned witness surfaces,
refuses a library without the export, and native_nearest keeps surface offsets on the Python
route on every platform that has not qualified the export, and on any rhino3dm where the Python
route does not give Rust's surface on the witness surfaces. Run with the bundled runtime:
  NARWHAL_PURE_CORE=$PWD/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so \
      tools/blender-patched/5.2/python/bin/python3.13 spike/test_pure_core_surface_offset_gate.py
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
        gate.probe_pure_core_surface_offset(Path(path))
    except ValueError:
        return True
    return False


print("\n-- probe: the named library gives the pinned witness surfaces --")
if not (LIBRARY and Path(LIBRARY).is_file()):
    check(False, "NARWHAL_PURE_CORE names the built library")
else:
    try:
        identity = gate.probe_pure_core_surface_offset(Path(LIBRARY))
    except ValueError as exc:
        identity = str(exc)
    check(identity == gate.PURE_CORE_SURFACE_OFFSET_IDENTITY,
          "the witness surfaces match the pinned digest, and the refusals their pinned statuses")

print("\n-- a library without the export, or no library, is refused --")
check(refused(ROOT / "no-such-library.so"), "a missing library is refused")
LIBM = next((p for p in ("/lib64/libm.so.6", "/usr/lib64/libm.so.6",
                         "/usr/lib/x86_64-linux-gnu/libm.so.6") if os.path.isfile(p)), None)
if sys.platform == "linux" and LIBM:
    check(refused(LIBM), "libm, which lacks the pure-core exports, is refused")
else:
    print("  skip libm negative (not Linux, or no libm found)")

print("\n-- native_nearest: the surface offset route is live only where qualified --")
if not LIBRARY:
    check(False, "NARWHAL_PURE_CORE names the library for the routing checks")
else:
    os.environ["NARWHAL_PURE_CORE"] = LIBRARY
    load("nurbs_data")
    native = load("native_nearest")
    off = load("offset")
    here = (sys.platform, platform.machine())
    check(native.SURFACE_OFFSET_QUALIFIED_PLATFORMS
          == gate.PURE_CORE_SURFACE_OFFSET_QUALIFIED_PLATFORMS,
          "the runtime platform set equals the qualification record in check_pure_core")
    bump = r3.NurbsSurface.Create(3, False, 4, 4, 6, 6)
    for i in range(6):
        for j in range(6):
            bump.Points[i, j] = r3.Point4d(i, j, 1.0 if 2 <= i < 4 and 2 <= j < 4 else 0.0, 1.0)
    for knots in (bump.KnotsU, bump.KnotsV):
        for i, k in enumerate((0, 0, 0, 1, 2, 3, 3, 3)):
            knots[i] = float(k)
    reference = off._python_surface_offset(bump, 0.3, 1e-2, 6)

    def same(result):
        return (result is not None and result.curve is not None
                and native._surface_bits(result.curve, result.max_deviation)
                == native._surface_bits(reference[0], reference[1]))

    native._surface_offset_state = None
    if here in native.SURFACE_OFFSET_QUALIFIED_PLATFORMS:
        check(native.surface_offset_capability(off._python_surface_offset) == "qualified",
              f"{here} is qualified, its library carries the export and its rhino3dm passes parity")
        before = native._native_surface_offset_calls
        check(same(off.surface_offset(bump, 0.3, 1e-2))
              and native._native_surface_offset_calls == before + 1,
              f"{here} offsets a surface through the export, as the Python route does")
    else:
        check(native.surface_offset_capability(off._python_surface_offset).startswith("not qualified"),
              f"{here} is not qualified, so the export is reported off")
    real_platform, real_machine = sys.platform, platform.machine
    try:
        for plat, machine in (("win32", "AMD64"), ("linux", "aarch64"), ("darwin", "arm64")):
            sys.platform, platform.machine = plat, (lambda m=machine: m)
            native._surface_offset_state = None
            before = native._native_surface_offset_calls
            check(native.surface_offset_capability(off._python_surface_offset)
                  .startswith(f"not qualified on {plat}/{machine}")
                  and native.surface_offset_fit(bump, 0.3, 1e-2, 6, off._python_surface_offset) is None
                  and same(off.surface_offset(bump, 0.3, 1e-2))
                  and native._native_surface_offset_calls == before,
                  f"{plat}/{machine} keeps the Python route, with the same surface")
    finally:
        sys.platform, platform.machine = real_platform, real_machine
        native._surface_offset_state = None

    # An SDK that evaluates differently (a fused multiply-add) gives the Python route a
    # different surface on the witnesses: model that by nudging one control point.
    def nudged(surface, distance, tolerance, max_refine):
        result, dev, within = off._python_surface_offset(surface, distance, tolerance, max_refine)
        p = result.Points[1, 1]
        result.Points[1, 1] = r3.Point4d(math.nextafter(p.X, math.inf), p.Y, p.Z, p.W)
        return result, dev, within

    native._surface_offset_state = None
    check("differs" in native.surface_offset_capability(nudged)
          and native.surface_offset_fit(bump, 0.3, 1e-2, 6, nudged) is None,
          "an SDK that fails the witness parity keeps the Python route")
    native._surface_offset_state = None
    check(native.surface_offset_capability(off._python_surface_offset) == "qualified"
          or here not in native.SURFACE_OFFSET_QUALIFIED_PLATFORMS,
          "the parity check passes again with the real SDK")

failed = sum(not ok for ok, _ in checks)
print(f"\n{len(checks) - failed}/{len(checks)} passed")
raise SystemExit(1 if failed else 0)
