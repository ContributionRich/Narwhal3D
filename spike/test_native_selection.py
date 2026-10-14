#!/usr/bin/env python3
"""Acceptance for the opt-in Rust selection adapter and UI cache integration."""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from narwhal3d import native_selection, ui  # noqa: E402

if sys.platform == "win32":
    platform_name, target_name, library_name = (
        "windows-x86_64", "x86_64-pc-windows-gnu", "narwhal_selection_core.dll")
else:
    platform_name, target_name, library_name = (
        "linux-x86_64", "x86_64-unknown-linux-gnu", "libnarwhal_selection_core.so")
candidates = [
    Path(os.environ["NARWHAL_SELECTION_CORE"]) if os.environ.get("NARWHAL_SELECTION_CORE") else None,
    ROOT / "artifacts" / "selection-core" / platform_name / library_name,
    ROOT / "native" / "selection_core" / "target" / target_name / "release" / library_name,
    ROOT / "native" / "selection_core" / "target" / "release" / library_name,
]
LIBRARY = next((path for path in candidates if path is not None and path.is_file()),
               ROOT / "artifacts" / "selection-core" / platform_name / library_name)
if not LIBRARY.is_file():
    raise SystemExit(
        "Rust selection core is not built; run "
        "scripts/build_selection_core.py --reproducible"
    )

checks = 0
failures: list[str] = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def close_point(actual, expected, tolerance=2e-5):
    return max(abs(actual[index] - expected[index]) for index in (0, 1)) < tolerance


old_path = os.environ.get("NARWHAL_SELECTION_CORE")
try:
    os.environ["NARWHAL_SELECTION_CORE"] = str(LIBRARY)
    native_selection._reset_for_tests()
    check(native_selection.available(), "explicit ABI-1 Rust library is available",
          native_selection.backend_status())
    matrix = np.array((
        (1.2, 0.1, 0.0, 0.2),
        (0.0, 1.1, 0.1, -0.1),
        (0.0, 0.0, 1.0, 0.0),
        (0.15, -0.05, 0.2, 1.0),
    ), dtype=np.float64)
    lines = np.array((
        ((-1.0, -1.0, 0.0), (0.0, 0.0, 0.0)),
        ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ((0.0, 0.0, -10.0), (0.5, 0.5, -10.0)),
    ), dtype=np.float32)
    projected = native_selection.project_segments(lines, matrix, 800, 600)
    check(projected is not None and len(projected) == 2,
          "native adapter drops only segments with an unprojectable endpoint", repr(projected))
    expected_start = ((-1.1 / 0.9 + 1.0) * 400.0,
                      (-1.2 / 0.9 + 1.0) * 300.0)
    check(projected is not None and close_point(projected[0][0], expected_start),
          "adapter returns the production screen-coordinate mapping", repr(projected))
    check(native_selection.backend_status().startswith("ready:"),
          "successful call retains a diagnostic ready status", native_selection.backend_status())
    check(native_selection.project_segments(np.empty((0, 2, 3), dtype=np.float32),
                                            matrix, 800, 600) == (),
          "empty native input succeeds as an empty projected tuple")
    check(native_selection.project_segments(np.empty((2, 3), dtype=np.float32),
                                            matrix, 800, 600) is None,
          "invalid segment shape requests Python fallback")

    os.environ["NARWHAL_SELECTION_CORE"] = str(ROOT / "missing-selection-core.so")
    native_selection._reset_for_tests()
    check(not native_selection.available(), "missing explicit library remains optional")
    check(native_selection.project_segments(lines, matrix, 800, 600) is None,
          "missing library returns the Python-fallback sentinel")
finally:
    if old_path is None:
        os.environ.pop("NARWHAL_SELECTION_CORE", None)
    else:
        os.environ["NARWHAL_SELECTION_CORE"] = old_path
    native_selection._reset_for_tests()

# Production UI integration: one native call fills the same tuple and spatial index;
# a second unchanged query is a pure cache hit. A None result runs the old scalar loop.
name = "RustSelectionAcceptance"
ob = bpy.data.objects.get(name) or bpy.data.objects.new(name, None)
real_app_drawn = ui.bridge.is_app_drawn
real_record = ui._curve_record
real_native = ui.native_selection.project_segments
real_project = ui._project_2d
try:
    ui.bridge.is_app_drawn = lambda _ob: True
    record = {"n": 2, "lines": (
        ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ((2.0, 2.0, 0.0), (3.0, 3.0, 0.0)),
    )}
    ui._curve_record = lambda _ob: record
    native_calls = []
    scalar_calls = []
    native_result = (((10.0, 20.0), (30.0, 40.0)),)
    ui.native_selection.project_segments = lambda *args: native_calls.append(args) or native_result
    ui._project_2d = lambda *_args: scalar_calls.append(_args) or (99.0, 99.0)
    region = type("Region", (), {"width": 800, "height": 600})()
    rv3d = type("RV3D", (), {"perspective_matrix": tuple(
        tuple(float(row == col) for col in range(4)) for row in range(4))})()
    entry = {"segments": {}, "segment_indexes": {}}
    first = ui._projected_selection_segments(name, region, rv3d, entry)
    second = ui._projected_selection_segments(name, region, rv3d, entry)
    check(first == native_result and second is first,
          "successful native output populates the existing warm tuple cache", repr((first, second)))
    check(len(native_calls) == 1 and not scalar_calls,
          "warm cache avoids repeated native and scalar projection calls",
          repr((len(native_calls), len(scalar_calls))))
    check(entry["segment_indexes"][name]["count"] == 1,
          "native output builds the unchanged Python segment index")

    ui.native_selection.project_segments = lambda *_args: None
    fallback_entry = {"segments": {}, "segment_indexes": {}}
    scalar_calls.clear()
    fallback = ui._projected_selection_segments(name, region, rv3d, fallback_entry)
    check(len(fallback) == 2 and len(scalar_calls) == 4,
          "native-unavailable sentinel runs every original scalar endpoint projection",
          repr((fallback, len(scalar_calls))))
    check(fallback_entry["segments"][name] is fallback
          and fallback_entry["segment_indexes"][name]["count"] == 2,
          "fallback output populates the same warm cache and index")
finally:
    ui.bridge.is_app_drawn = real_app_drawn
    ui._curve_record = real_record
    ui.native_selection.project_segments = real_native
    ui._project_2d = real_project
    if ob.users == 0:
        bpy.data.objects.remove(ob)

check(not any(math.isnan(value) for segment in native_result for point in segment for value in point),
      "acceptance fixture itself contains only finite display coordinates")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} native-selection adapter checks passed")
