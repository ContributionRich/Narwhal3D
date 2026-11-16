#!/usr/bin/env python3
"""Thin real-CDLL host smoke for the Rust selection-core C ABI.

Retires the 1,612-line / 75,781-byte suite (archived unmodified at
legacy/retired-rust26/selection/original/test_rust_selection_core.py). All
algorithm, random, and ABI-capacity oracles now live in the Rust suites
(selection26_static.rs, 70 tests; selection26_random.rs, 11 tests). This host
only proves the real shared object loads and exports its C ABI: preserved
library discovery, the pinned ABI version, presence + return-type binding of
every C ABI export, and one tiny real projection call through ctypes with
argtypes and a checked output value. No fallback algorithm is added.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "selection_core"


def library_path() -> Path:
    override = os.environ.get("NARWHAL_SELECTION_CORE")
    if override:
        return Path(override)
    if sys.platform == "win32":
        platform_name, target, name = (
            "windows-x86_64", "x86_64-pc-windows-gnu", "narwhal_selection_core.dll")
    elif sys.platform == "darwin":
        platform_name, target, name = (
            "macos-unknown", "", "libnarwhal_selection_core.dylib")
    else:
        platform_name, target, name = (
            "linux-x86_64", "x86_64-unknown-linux-gnu", "libnarwhal_selection_core.so")
    candidates = [ROOT / "artifacts" / "selection-core" / platform_name / name]
    if target:
        candidates.append(CRATE / "target" / target / "release" / name)
    candidates.append(CRATE / "target" / "release" / name)
    return next((path for path in candidates if path.is_file()), candidates[0])


LIB_PATH = library_path()
if not LIB_PATH.is_file():
    raise SystemExit(
        "Rust selection core is not built; run: "
        "scripts/build_selection_core.py --reproducible"
    )

lib = ctypes.CDLL(str(LIB_PATH))

checks = 0
failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# C ABI exports bound by the original 1,612-line suite (extracted from its AST).
ORIGINAL_AST_EXPORTS = [
    "narwhal_selection_core_abi_version", "narwhal_project_points_f32",
    "narwhal_project_display_points_f64", "narwhal_projected_snap_distances_f64",
    "narwhal_any_screen_segment_crosses_rect_f64",
    "narwhal_any_projected_segment_crosses_rect_f32",
    "narwhal_projected_segments_rect_query_f32",
    "narwhal_projected_curves_rect_query_f32",
    "narwhal_nearest_projected_curve_f32", "narwhal_nearest_projected_point_f64",
    "narwhal_nearest_projected_polylines_f64", "narwhal_nearest_screen_polyline_f64",
    "narwhal_screen_boxes_from_clip_f64", "narwhal_screen_boxes_from_world_f64",
    "narwhal_world_rect_hit_mask_f64", "narwhal_screen_rects_from_aabbs_f64",
    "narwhal_projected_points_rect_query_f64", "narwhal_intersection_snaps_f64",
    "narwhal_dynamic_perp_tan_snaps_f64", "narwhal_discrete_snap_survivors_f64",
    "narwhal_oriented_bbox_center_f64", "narwhal_ordered_selection_apply_u32",
]
# Newer C ABI exports the built artifact also exposes (absent from the original AST).
NEWER_ARTIFACT_EXPORTS = [
    "narwhal_gumball_polyline_distances_f64", "narwhal_nearest_projected_polylines_clipped_f64",
    "narwhal_nearest_projected_sample_f64", "narwhal_reached_screen_rect_indices_f64",
]

lib.narwhal_selection_core_abi_version.restype = ctypes.c_uint32
check(lib.narwhal_selection_core_abi_version() == 1, "C ABI version is pinned")

for name in ORIGINAL_AST_EXPORTS + NEWER_ARTIFACT_EXPORTS:
    present = hasattr(lib, name)
    check(present, f"C ABI export present: {name}")
    if present:
        restype = ctypes.c_uint32 if name == "narwhal_selection_core_abi_version" else ctypes.c_int
        getattr(lib, name).restype = restype

DoubleP = ctypes.POINTER(ctypes.c_double)
FloatP = ctypes.POINTER(ctypes.c_float)
ByteP = ctypes.POINTER(ctypes.c_ubyte)
lib.narwhal_project_points_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP, ByteP]
lib.narwhal_project_points_f32.restype = ctypes.c_int
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
matrix = (ctypes.c_double * 16)(*IDENTITY)
point = (ctypes.c_float * 3)(-1.0, -1.0, 0.0)
out = (ctypes.c_double * 2)()
visible = (ctypes.c_ubyte * 1)()
code = lib.narwhal_project_points_f32(matrix, point, 1, 800.0, 600.0, out, visible)
check(code == 0 and visible[0] == 1 and out[0] == 0.0 and out[1] == 0.0,
      "real projection call maps NDC (-1,-1,0) to pixel (0,0) at 800x600",
      f"code={code} visible={tuple(visible)} out=({out[0]}, {out[1]})")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} Rust selection-core C-ABI host checks passed")
