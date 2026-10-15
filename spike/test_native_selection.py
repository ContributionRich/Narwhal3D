#!/usr/bin/env python3
"""Acceptance for the opt-in Rust selection adapter and UI cache integration."""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import sys
import types

import bpy
import bpy_extras.view3d_utils as v3u
from mathutils import Matrix, Vector
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
    check(native_selection.project_segments(np.empty((3, 3), dtype=np.float32),
                                            matrix, 800, 600) is None,
          "invalid segment shape requests Python fallback")

    identity = np.identity(4, dtype=np.float64)
    query_lines = np.array((((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),), dtype=np.float32)
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (390.0, 290.0, 410.0, 310.0))
          == native_selection.RECT_HIT,
          "native rectangle query returns an exact crossing hit")
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (0.0, 0.0, 10.0, 10.0))
          == native_selection.RECT_MISS,
          "native rectangle query returns an authoritative projectable miss")
    no_evidence = query_lines.copy()
    no_evidence[0, 0, 0] = np.nan
    check(native_selection.projected_segments_rect_query(
        no_evidence, identity, 800, 600, (0.0, 0.0, 10.0, 10.0))
          == native_selection.RECT_NO_EVIDENCE,
          "native rectangle query preserves no-projectable-evidence state")
    check(native_selection.projected_segments_rect_query(
        np.empty((0, 2, 3), dtype=np.float32), identity, 800, 600,
        (0.0, 0.0, 10.0, 10.0)) == native_selection.RECT_NO_EVIDENCE,
          "empty native rectangle input has no projectable evidence")
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (10.0, 0.0, 0.0, 10.0)) is None,
          "unordered rectangle requests Python fallback")
    miss_lines = np.array((((-1.0, -1.0, 0.0), (-0.9, -0.9, 0.0)),),
                          dtype=np.float32)
    check(native_selection.projected_curves_rect_query(
        (query_lines, miss_lines, no_evidence, np.empty((0, 2, 3), dtype=np.float32)),
        identity, 800, 600, (390.0, 290.0, 410.0, 310.0))
          == (native_selection.RECT_HIT, native_selection.RECT_MISS,
              native_selection.RECT_NO_EVIDENCE, native_selection.RECT_NO_EVIDENCE),
          "native batch query returns one ordered three-state result per curve")
    check(native_selection.projected_curves_rect_query(
        (query_lines, np.empty((3, 3), dtype=np.float32)),
        identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "one malformed curve rejects the complete native batch")
    nearest = native_selection.nearest_projected_curve(
        (miss_lines, query_lines, query_lines), identity, 800, 600,
        (400.0, 300.0), 10.0)
    check(nearest is not None and nearest[0] == 2 and nearest[1] < 1e-9,
          "native nearest-curve query preserves later-candidate exact ties", repr(nearest))
    no_pick = native_selection.nearest_projected_curve(
        (miss_lines,), identity, 800, 600, (400.0, 300.0), 1.0)
    check(no_pick is not None and no_pick[0] == native_selection.PICK_NO_HIT
          and math.isinf(no_pick[1]),
          "native nearest-curve query distinguishes a successful no-hit", repr(no_pick))
    check(native_selection.nearest_projected_curve(
        (query_lines, np.empty((3, 3), dtype=np.float32)), identity, 800, 600,
        (400.0, 300.0), 10.0) is None,
          "one malformed curve rejects the complete native pick batch")
    eye_matrix = np.array((
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
    ), dtype=np.float64)
    eye_crossing = np.array((((0.0, 0.0, 1.0), (0.0, 0.0, -1.0)),),
                            dtype=np.float32)
    clipped_pick = native_selection.nearest_projected_curve(
        (eye_crossing,), eye_matrix, 800, 600, (400.0, 300.0), 1.0)
    check(clipped_pick is not None and clipped_pick[0] == 0 and clipped_pick[1] < 1e-6,
          "native nearest query clips a segment crossing the eye plane", repr(clipped_pick))
    finite_and_nan = np.concatenate((query_lines, no_evidence), axis=0)
    mixed_pick = native_selection.nearest_projected_curve(
        (finite_and_nan,), identity, 800, 600, (400.0, 300.0), 1.0)
    numpy_mixed = ui.osnap.segments_screen_distance(
        finite_and_nan, identity, 800, 600, (400.0, 300.0))
    check(mixed_pick is not None and mixed_pick[0] == 0
          and abs(mixed_pick[1] - numpy_mixed) < 1e-9,
          "one NaN segment does not hide another finite segment in its curve",
          repr((mixed_pick, numpy_mixed)))
    point_values = np.array(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                             (1.0, 1.0, 0.0)), dtype=np.float64)
    nearest_point = native_selection.nearest_projected_point(
        point_values, identity, 800, 600, (400.0, 300.0), 1.0)
    check(nearest_point == (1, 0.0),
          "native f64 point picker preserves later-candidate exact ties",
          repr(nearest_point))
    point_rect = native_selection.projected_points_in_rect(
        point_values, identity, 800, 600, (400.0, 300.0, 800.0, 600.0))
    check(point_rect == (0, 1, 2),
          "native f64 point rectangle includes exact boundary points in order",
          repr(point_rect))
    precise_point = np.array(((0.123456789123, -0.456789123456, 0.2),),
                             dtype=np.float64)
    blender_region = type("Region", (), {"width": 800, "height": 600})()
    blender_rv3d = type("RV3D", (), {"perspective_matrix": Matrix(matrix.tolist())})()
    blender_pixel = v3u.location_3d_to_region_2d(
        blender_region, blender_rv3d, Vector(precise_point[0]))
    blender_boundary_pick = native_selection.nearest_projected_point(
        precise_point, matrix, 800, 600,
        (blender_pixel.x + 9.0, blender_pixel.y), 9.0)
    blender_boundary_rect = native_selection.projected_points_in_rect(
        precise_point, matrix, 800, 600,
        (blender_pixel.x, blender_pixel.y, blender_pixel.x, blender_pixel.y))
    adversarial_matrix = np.array((
        (1.027998685836792, -0.42524489760398865, -0.466378778219223, 0.6437168717384338),
        (0.3514747619628906, 1.8906302452087402, -0.9491537809371948, 9.998909950256348),
        (-0.4969795048236847, -0.3138795793056488, -0.8092533946037292, 6.88346529006958),
        (-0.49688011407852173, -0.3138168156147003, -0.8090915679931641, 7.08206844329834),
    ), dtype=np.float64)
    adversarial_point = np.array(((6.764359507293101, -9.856076443526845,
                                   0.48392038233675194),), dtype=np.float64)
    adversarial_region = type("Region", (), {"width": 1920, "height": 1080})()
    adversarial_rv3d = type(
        "RV3D", (), {"perspective_matrix": Matrix(adversarial_matrix.tolist())})()
    adversarial_pixel = v3u.location_3d_to_region_2d(
        adversarial_region, adversarial_rv3d, Vector(adversarial_point[0]))
    adversarial_pick = native_selection.nearest_projected_point(
        adversarial_point, adversarial_matrix, 1920, 1080,
        (adversarial_pixel.x, adversarial_pixel.y), 0.0)
    adversarial_rect = native_selection.projected_points_in_rect(
        adversarial_point, adversarial_matrix, 1920, 1080,
        (adversarial_pixel.x, adversarial_pixel.y,
         adversarial_pixel.x, adversarial_pixel.y))
    check(blender_boundary_pick is not None and blender_boundary_pick[0] == 0
          and abs(blender_boundary_pick[1] - 9.0) < 1e-12
          and blender_boundary_rect == (0,)
          and adversarial_pick == (0, 0.0) and adversarial_rect == (0,),
          "native point queries match Blender's mixed-precision projection at exact boundaries",
          repr((blender_pixel, blender_boundary_pick, blender_boundary_rect,
                adversarial_pixel, adversarial_pick, adversarial_rect)))
    check(native_selection.nearest_projected_point(
        np.empty((2, 2), dtype=np.float64), identity, 800, 600,
        (400.0, 300.0), 1.0) is None,
          "malformed point shape requests scalar picker fallback")
    check(native_selection.projected_points_in_rect(
        point_values, identity, 800, 600, (800.0, 300.0, 400.0, 600.0)) is None,
          "unordered point rectangle requests scalar selection fallback")

    rng = np.random.default_rng(0x5049434B)
    nearest_equivalent = True
    saw_hit = saw_miss = 0
    for _ in range(100):
        curves = [rng.uniform(-6.0, 6.0, size=(int(rng.integers(0, 16)), 2, 3))
                  .astype(np.float32)
                  for _curve in range(int(rng.integers(0, 16)))]
        cursor = (float(rng.uniform(-200.0, 1000.0)),
                  float(rng.uniform(-200.0, 800.0)))
        limit = float(rng.uniform(0.0, 300.0))
        expected_index = native_selection.PICK_NO_HIT
        expected_distance = limit
        for index, curve in enumerate(curves):
            distance = ui.osnap.segments_screen_distance(
                curve, matrix, 800, 600, cursor)
            if distance <= expected_distance:
                expected_index, expected_distance = index, distance
        actual = native_selection.nearest_projected_curve(
            curves, matrix, 800, 600, cursor, limit)
        if expected_index == native_selection.PICK_NO_HIT:
            saw_miss += 1
            matches = (actual is not None
                       and actual[0] == native_selection.PICK_NO_HIT
                       and math.isinf(actual[1]))
        else:
            saw_hit += 1
            matches = (actual is not None and actual[0] == expected_index
                       and abs(actual[1] - expected_distance)
                       <= 1e-7 * max(1.0, expected_distance))
        if not matches:
            nearest_equivalent = False
            break
    check(nearest_equivalent and saw_hit and saw_miss,
          "100 randomized nearest batches match the actual NumPy picker",
          repr((saw_hit, saw_miss)))

    signature = os.environ["NARWHAL_SELECTION_CORE"]
    native_selection._signature = signature
    native_selection._native_binding = (
        None, None, lambda *_args: -2, None, None, Path("fake-native"))
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "negative native status cannot become a truthy hit")
    native_selection._native_binding = (
        None, None, lambda *_args: 2, None, None, Path("fake-native"))
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "unexpected positive native status cannot become a truthy hit")
    native_selection._native_binding = (
        None, None, None, None, None, Path("old-abi1-native"))
    check(native_selection.projected_segments_rect_query(
        query_lines, identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "older ABI-1 library without additive query uses Python fallback")
    native_selection._native_binding = (
        None, None, None, lambda *_args: 7, None, Path("bad-batch-native"))
    check(native_selection.projected_curves_rect_query(
        (query_lines,), identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "unexpected native batch return code rejects the complete result")

    def undocumented_batch_status(*args):
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))[0] = 3
        return 0

    native_selection._native_binding = (
        None, None, None, undocumented_batch_status, None, Path("bad-batch-native"))
    check(native_selection.projected_curves_rect_query(
        (query_lines,), identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "undocumented per-curve batch status rejects the complete result")
    native_selection._native_binding = (
        None, None, None, None, None, Path("old-abi1-native"))
    check(native_selection.projected_curves_rect_query(
        (query_lines,), identity, 800, 600, (0.0, 0.0, 10.0, 10.0)) is None,
          "older ABI-1 library without batch query uses per-curve fallback")

    def invalid_no_hit(*args):
        ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_uint64))[0] = 0
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_double))[0] = math.inf
        return 0

    native_selection._native_binding = (
        None, None, None, None, invalid_no_hit, Path("bad-nearest-native"))
    check(native_selection.nearest_projected_curve(
        (query_lines,), identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "inconsistent native no-hit output requests NumPy fallback")

    def negative_infinity_no_hit(*args):
        ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_uint64))[0] = (1 << 64) - 1
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_double))[0] = -math.inf
        return 0

    native_selection._native_binding = (
        None, None, None, None, negative_infinity_no_hit, Path("bad-nearest-native"))
    check(native_selection.nearest_projected_curve(
        (query_lines,), identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "negative infinity no-hit distance requests NumPy fallback")
    native_selection._native_binding = (
        None, None, None, None, None, Path("old-abi1-native"))
    check(native_selection.nearest_projected_curve(
        (query_lines,), identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "older ABI-1 library without nearest query uses NumPy fallback")

    def invalid_point_hit(*args):
        ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_uint64))[0] = len(point_values)
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_double))[0] = 0.0
        return 1

    fake_point_library = types.SimpleNamespace(
        narwhal_nearest_projected_point_f64=invalid_point_hit)
    native_selection._native_binding = (
        fake_point_library, None, None, None, None, Path("bad-point-native"))
    check(native_selection.nearest_projected_point(
        point_values, identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "out-of-range native point index requests scalar fallback")

    def undocumented_point_mask(*args):
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))[0] = 2
        return 0

    fake_rect_library = types.SimpleNamespace(
        narwhal_projected_points_rect_query_f64=undocumented_point_mask)
    native_selection._native_binding = (
        fake_rect_library, None, None, None, None, Path("bad-point-native"))
    check(native_selection.projected_points_in_rect(
        point_values, identity, 800, 600, (0.0, 0.0, 800.0, 600.0)) is None,
          "undocumented native point mask requests scalar fallback")
    native_selection._native_binding = (
        types.SimpleNamespace(), None, None, None, None, Path("old-abi1-native"))
    check(native_selection.nearest_projected_point(
        point_values, identity, 800, 600, (400.0, 300.0), 10.0) is None
          and native_selection.projected_points_in_rect(
              point_values, identity, 800, 600, (0.0, 0.0, 800.0, 600.0)) is None,
          "older ABI-1 library without point symbols uses scalar fallbacks")

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

# A real curve-pass record is flat GL_LINES points, not the shaped adapter fixture above.
production_mesh = bpy.data.meshes.new("RustProductionRecordMesh")
production_mesh.from_pydata(((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), ((0, 1),), ())
production_object = bpy.data.objects.new("RustProductionRecord", production_mesh)
production_record = ui._curve_record(production_object)
check(production_record is not None and production_record["lines"].shape == (2, 3),
      "real Blender curve record uses flat consecutive XYZ endpoint pairs",
      repr(None if production_record is None else production_record["lines"].shape))
try:
    os.environ["NARWHAL_SELECTION_CORE"] = str(LIBRARY)
    native_selection._reset_for_tests()
    production_pick = native_selection.nearest_projected_curve(
        (production_record["lines"],), np.identity(4), 800, 600,
        (400.0, 300.0), 1.0)
    check(production_pick is not None and production_pick[0] == 0
          and production_pick[1] < 1e-9,
          "native picker accepts the real flat curve-record layout", repr(production_pick))
    production_rect = native_selection.projected_curves_rect_query(
        (production_record["lines"],), np.identity(4), 800, 600,
        (390.0, 290.0, 410.0, 310.0))
    check(production_rect == (native_selection.RECT_HIT,),
          "native crossing batch accepts the real flat curve-record layout",
          repr(production_rect))
finally:
    if old_path is None:
        os.environ.pop("NARWHAL_SELECTION_CORE", None)
    else:
        os.environ["NARWHAL_SELECTION_CORE"] = old_path
    native_selection._reset_for_tests()
    bpy.data.objects.remove(production_object)
    bpy.data.meshes.remove(production_mesh)

# Production UI integration: one native call fills the same tuple and spatial index;
# a second unchanged query is a pure cache hit. A None result runs the old scalar loop.
name = "RustSelectionAcceptance"
ob = bpy.data.objects.get(name) or bpy.data.objects.new(name, None)
real_app_drawn = ui.bridge.is_app_drawn
real_record = ui._curve_record
real_native = ui.native_selection.project_segments
real_native_query = ui.native_selection.projected_segments_rect_query
real_native_batch = ui.native_selection.projected_curves_rect_query
real_native_nearest = ui.native_selection.nearest_projected_curve
real_curve_crosses = ui._curve_crosses_selection_rect
real_curve_sync = ui._curve_sync
real_unpickable = ui._unpickable_names_cached
real_boxes_reach = ui.osnap.boxes_reach
real_segment_distance = ui.osnap.segments_screen_distance
real_projected_segments = ui._projected_selection_segments
real_screen_crosses = ui._screen_segment_crosses_rect
real_project = ui._project_2d
real_native_available = ui.native_selection.available
real_native_point = ui.native_selection.nearest_projected_point
real_native_points_rect = ui.native_selection.projected_points_in_rect
real_cp_rows = ui._cp_rows
real_cp_locations = ui._control_point_locations
real_document_points = ui._document_point_locations
real_locked_names = ui._locked_names_cached
real_doc_key = ui._doc_key
real_point_loc_cache = dict(ui._point_loc_cache)
try:
    ui.bridge.is_app_drawn = lambda _ob: True
    record = {"n": 2, "lines": np.array((
        (0.0, 0.0, 0.0), (1.0, 1.0, 0.0),
        (2.0, 2.0, 0.0), (3.0, 3.0, 0.0),
    ), dtype=np.float32)}
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

    query_calls = []
    materialized = []
    ui.native_selection.projected_segments_rect_query = (
        lambda *_args: query_calls.append(_args) or native_selection.RECT_HIT)
    ui._projected_selection_segments = (
        lambda *_args: materialized.append(_args) or (((0.0, 0.0), (1.0, 1.0)),))
    crossing_entry = {"segments": {}, "segment_indexes": {}}
    check(ui._curve_crosses_selection_rect(
        name, region, rv3d, (10.0, 10.0, 20.0, 20.0), crossing_entry),
          "authoritative native hit keeps the crossing candidate")
    check(len(query_calls) == 1 and not materialized,
          "native hit bypasses Python tuple and index materialization")

    ui.native_selection.projected_segments_rect_query = lambda *_args: native_selection.RECT_MISS
    check(not ui._curve_crosses_selection_rect(
        name, region, rv3d, (10.0, 10.0, 20.0, 20.0),
        {"segments": {}, "segment_indexes": {}}),
          "authoritative native miss rejects the crossing candidate")
    check(not materialized, "native miss also bypasses Python materialization")

    ui.native_selection.projected_segments_rect_query = (
        lambda *_args: native_selection.RECT_NO_EVIDENCE)
    check(ui._curve_crosses_selection_rect(
        name, region, rv3d, (10.0, 10.0, 20.0, 20.0),
        {"segments": {}, "segment_indexes": {}}),
          "native no-evidence result remains conservatively selected")

    ui.native_selection.projected_segments_rect_query = lambda *_args: None
    ui._screen_segment_crosses_rect = lambda *_args: False
    check(not ui._curve_crosses_selection_rect(
        name, region, rv3d, (10.0, 10.0, 20.0, 20.0),
        {"segments": {}, "segment_indexes": {}})
          and len(materialized) == 1,
          "native error enters the unchanged Python refinement path")

    cached_entry = {
        "segments": {name: (((0.0, 0.0), (1.0, 1.0)),)},
        "segment_indexes": {name: None},
    }
    query_calls.clear()
    ui.native_selection.projected_segments_rect_query = (
        lambda *_args: query_calls.append(_args) or native_selection.RECT_HIT)
    check(not ui._curve_crosses_selection_rect(
        name, region, rv3d, (10.0, 10.0, 20.0, 20.0), cached_entry)
          and not query_calls,
          "existing warm Python cache remains authoritative without a native call")

    batch_names = ["RustBatchMiss", "RustBatchHit", "RustBatchNoEvidence"]
    batch_objects = [bpy.data.objects.get(item) or bpy.data.objects.new(item, None)
                     for item in batch_names]
    batch_calls = []
    ui.native_selection.projected_curves_rect_query = (
        lambda *args: batch_calls.append(args) or (
            native_selection.RECT_MISS,
            native_selection.RECT_HIT,
            native_selection.RECT_NO_EVIDENCE,
        ))
    batch_entry = {"segments": {}, "segment_indexes": {}}
    filtered = ui._curves_cross_selection_rect(
        [*batch_names, "MissingRustBatchObject"], region, rv3d,
        (10.0, 10.0, 20.0, 20.0), batch_entry)
    check(filtered == [batch_names[1], batch_names[2], "MissingRustBatchObject"],
          "one native batch preserves ordered hit, miss, no-evidence, and non-curve semantics",
          repr(filtered))
    check(len(batch_calls) == 1 and len(batch_calls[0][0]) == 3
          and not batch_entry["segments"],
          "uncached curves cross one native boundary without Python materialization")

    fallback_calls = []
    ui.native_selection.projected_curves_rect_query = lambda *_args: None
    ui._curve_crosses_selection_rect = (
        lambda item, *_args: fallback_calls.append(item) or item != batch_names[0])
    fallback_filtered = ui._curves_cross_selection_rect(
        batch_names, region, rv3d, (10.0, 10.0, 20.0, 20.0),
        {"segments": {}, "segment_indexes": {}})
    check(fallback_filtered == batch_names[1:] and fallback_calls == batch_names,
          "batch failure reruns the established per-curve route for every candidate",
          repr((fallback_filtered, fallback_calls)))

    many_calls = []
    single_calls = []
    broad_candidates = [(item, (0.0, 0.0, 5.0, 5.0)) for item in batch_names]
    many_hits = ui._rectangle_selection_hits(
        broad_candidates, (0.0, 0.0, 10.0, 10.0), ui.select.CROSSING,
        curve_crosses=lambda item: single_calls.append(item) or False,
        curve_crosses_many=lambda items: many_calls.append(tuple(items)) or items[1:])
    check(many_hits == batch_names[1:] and many_calls == [tuple(batch_names)]
          and not single_calls,
          "crossing selection chooses one many-curve refinement over per-curve calls")

    pick_names = ["RustPickFirst", "RustPickSecond"]
    pick_lines = {
        item: np.array(((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), dtype=np.float32)
        for item in pick_names
    }
    pick_state = {
        "hidden": frozenset(), "dirty": frozenset(), "pick_names": pick_names,
        "pick_lo": np.full((2, 3), -1.0), "pick_hi": np.full((2, 3), 1.0),
        "recs": {item: {"n": 1, "lines": pick_lines[item]} for item in pick_names},
    }
    ui._curve_sync = lambda: pick_state
    ui._unpickable_names_cached = lambda: frozenset()
    ui.osnap.boxes_reach = lambda *_args: np.array((True, True))
    nearest_calls = []
    distance_calls = []
    ui.native_selection.nearest_projected_curve = (
        lambda *args: nearest_calls.append(args) or (1, 0.5))
    ui.osnap.segments_screen_distance = (
        lambda *args: distance_calls.append(args) or 0.5)
    pick_space = type("Space", (), {"region_3d": rv3d})()
    check(ui.pick_curve_object(None, pick_space, region, 400.0, 300.0) == pick_names[1]
          and len(nearest_calls) == 1 and not distance_calls,
          "curve picker uses one native nearest batch and bypasses per-curve NumPy")

    ui.native_selection.nearest_projected_curve = (
        lambda *_args: (native_selection.PICK_NO_HIT, math.inf))
    check(ui.pick_curve_object(None, pick_space, region, 400.0, 300.0) is None,
          "authoritative native no-hit does not enter the NumPy fallback")

    ui.native_selection.nearest_projected_curve = lambda *_args: None
    distance_calls.clear()
    check(ui.pick_curve_object(None, pick_space, region, 400.0, 300.0) == pick_names[1]
          and len(distance_calls) == 2,
          "native picker failure preserves per-curve NumPy and later-wins ties")

    point_object_rows = [("", (-1.0, 0.0, 0.0)),
                         ("LockedPoint", (0.0, 0.0, 0.0)),
                         ("PointFirst", (0.0, 0.0, 0.0)),
                         ("PointSecond", (0.0, 0.0, 0.0))]
    point_object_native = []
    point_object_scalar = []
    doc_key = [object()]
    locked_state = [frozenset(("LockedPoint",))]
    ui._doc_key = lambda: doc_key[0]
    ui._point_loc_cache.update(key=doc_key[0], out=point_object_rows)
    ui._document_point_locations = lambda: list(ui._point_loc_cache["out"])
    ui._locked_names_cached = lambda: locked_state[0]
    ui.native_selection.available = lambda: True

    def native_last(*args):
        point_object_native.append(args)
        return (len(args[0]) - 1, 0.0)

    ui.native_selection.nearest_projected_point = native_last
    ui._project_2d = (
        lambda *_args: point_object_scalar.append(_args) or (400.0, 300.0))
    ui._native_point_objects.update(source=None, locked=None, rows=[], points=None)
    point_object_first = ui.pick_point_object(None, pick_space, region, 400.0, 300.0)
    point_object_second = ui.pick_point_object(None, pick_space, region, 400.0, 300.0)
    first_packed = point_object_native[0][0]
    check(point_object_first == point_object_second == "PointSecond"
          and len(point_object_native) == 2 and not point_object_scalar
          and first_packed is point_object_native[1][0]
          and first_packed.shape == (2, 3) and first_packed.dtype == np.float64,
          "point-object clicks reuse the production row cache and one filtered f64 batch")

    locked_state[0] = frozenset(("LockedPoint", "PointFirst"))
    locked_pick = ui.pick_point_object(None, pick_space, region, 400.0, 300.0)
    locked_packed = point_object_native[-1][0]
    locked_state[0] = frozenset()
    unlocked_pick = ui.pick_point_object(None, pick_space, region, 400.0, 300.0)
    unlocked_packed = point_object_native[-1][0]
    check(locked_pick == unlocked_pick == "PointSecond"
          and locked_packed.shape == (1, 3) and unlocked_packed.shape == (3, 3)
          and locked_packed is not first_packed and unlocked_packed is not locked_packed,
          "lock and unlock changes rebuild point-object eligibility and packed coordinates")

    doc_key[0] = object()
    edited_rows = point_object_rows + [("PointThird", (0.0, 0.0, 0.0))]
    locked_state[0] = frozenset(("LockedPoint",))
    ui._point_loc_cache.update(key=doc_key[0], out=edited_rows)
    edited_pick = ui.pick_point_object(None, pick_space, region, 400.0, 300.0)
    edited_packed = point_object_native[-1][0]
    check(edited_pick == "PointThird" and edited_packed.shape == (3, 3)
          and edited_packed is not unlocked_packed,
          "a new document/edit row list invalidates the native point-object cache")

    ui.native_selection.nearest_projected_point = (
        lambda *_args: (native_selection.PICK_NO_HIT, math.inf))
    check(ui.pick_point_object(None, pick_space, region, 400.0, 300.0) is None
          and not point_object_scalar,
          "authoritative native point-object no-hit does not enter scalar fallback")
    for bad_index in (-2, True, 99):
        ui.native_selection.nearest_projected_point = (
            lambda *_args, bad_index=bad_index: (bad_index, 0.0))
        malformed_point_object = ui.pick_point_object(
            None, pick_space, region, 400.0, 300.0)
        check(malformed_point_object == "PointThird",
              f"malformed point-object index {bad_index!r} uses scalar fallback")
    check(len(point_object_scalar) == 9,
          "malformed point-object outputs preserve unnamed/locked filtering")
    ui.native_selection.nearest_projected_point = lambda *_args: None
    point_object_scalar.clear()
    check(ui.pick_point_object(None, pick_space, region, 400.0, 300.0) == "PointThird"
          and len(point_object_scalar) == 3,
          "native point-object failure runs the unchanged scalar picker")

    grip_rows = [("Grip", 0, (0.0, 0.0, 0.0)),
                 ("Grip", 1, (0.0, 0.0, 0.0)),
                 ("Grip", 2, (1.0, 1.0, 0.0))]
    scalar_grips = []
    point_calls = []
    rect_calls = []
    ui._cp_rows = lambda: grip_rows
    ui._control_point_locations = lambda: scalar_grips.append(True) or list(grip_rows)
    ui.native_selection.available = lambda: True
    ui.native_selection.nearest_projected_point = (
        lambda *args: point_calls.append(args) or (1, 0.0))
    ui.native_selection.projected_points_in_rect = (
        lambda *args: rect_calls.append(args) or (0, 2))
    ui._native_cp_points.update(rows=None, points=None)
    packed_first = ui._packed_control_points(grip_rows)
    packed_again = ui._packed_control_points(grip_rows)
    check(packed_first is packed_again and packed_first.dtype == np.float64
          and packed_first.shape == (3, 3),
          "control-point coordinates are packed as cached f64 without rebuilding")
    check(ui.pick_control_point(None, pick_space, region, 400.0, 300.0) == grip_rows[1]
          and len(point_calls) == 1 and not scalar_grips,
          "control-point click uses one native point query and bypasses scalar projection")
    check(ui._control_points_in_rect(region, rv3d, (0.0, 0.0, 800.0, 600.0))
          == [("Grip", 0), ("Grip", 2)]
          and len(rect_calls) == 1 and not scalar_grips,
          "control-point rectangle maps ordered native indices without scalar projection")
    ui.native_selection.nearest_projected_point = (
        lambda *_args: (native_selection.PICK_NO_HIT, math.inf))
    check(ui.pick_control_point(None, pick_space, region, 400.0, 300.0) is None
          and not scalar_grips,
          "authoritative native point no-hit does not enter scalar fallback")

    ui._project_2d = lambda _region, _rv3d, world: (
        400.0 + 400.0 * world[0], 300.0 + 300.0 * world[1])
    ui.native_selection.nearest_projected_point = lambda *_args: (99, 0.0)
    malformed_pick = ui.pick_control_point(None, pick_space, region, 400.0, 300.0)
    ui.native_selection.projected_points_in_rect = lambda *_args: (99,)
    malformed_rect = ui._control_points_in_rect(
        region, rv3d, (400.0, 300.0, 800.0, 600.0))

    def broken_available():
        raise RuntimeError("discovery failure")

    ui.native_selection.available = broken_available
    discovery_pick = ui.pick_control_point(None, pick_space, region, 400.0, 300.0)
    check(malformed_pick == grip_rows[1]
          and malformed_rect == [("Grip", 0), ("Grip", 1), ("Grip", 2)]
          and discovery_pick == grip_rows[1] and len(scalar_grips) == 3,
          "malformed UI native results and discovery exceptions use scalar fallbacks")

    ui.native_selection.available = lambda: True
    ui.native_selection.nearest_projected_point = lambda *_args: None
    ui.native_selection.projected_points_in_rect = lambda *_args: None
    scalar_grips.clear()
    fallback_grip = ui.pick_control_point(None, pick_space, region, 400.0, 300.0)
    fallback_rect = ui._control_points_in_rect(
        region, rv3d, (400.0, 300.0, 800.0, 600.0))
    check(fallback_grip == grip_rows[1] and fallback_rect == [
        ("Grip", 0), ("Grip", 1), ("Grip", 2)] and len(scalar_grips) == 2,
          "native point failures preserve scalar later ties and inclusive rectangle edges")
finally:
    ui.bridge.is_app_drawn = real_app_drawn
    ui._curve_record = real_record
    ui.native_selection.project_segments = real_native
    ui.native_selection.projected_segments_rect_query = real_native_query
    ui.native_selection.projected_curves_rect_query = real_native_batch
    ui.native_selection.nearest_projected_curve = real_native_nearest
    ui._curve_crosses_selection_rect = real_curve_crosses
    ui._curve_sync = real_curve_sync
    ui._unpickable_names_cached = real_unpickable
    ui.osnap.boxes_reach = real_boxes_reach
    ui.osnap.segments_screen_distance = real_segment_distance
    ui._projected_selection_segments = real_projected_segments
    ui._screen_segment_crosses_rect = real_screen_crosses
    ui._project_2d = real_project
    ui.native_selection.available = real_native_available
    ui.native_selection.nearest_projected_point = real_native_point
    ui.native_selection.projected_points_in_rect = real_native_points_rect
    ui._cp_rows = real_cp_rows
    ui._control_point_locations = real_cp_locations
    ui._document_point_locations = real_document_points
    ui._locked_names_cached = real_locked_names
    ui._doc_key = real_doc_key
    ui._point_loc_cache.clear()
    ui._point_loc_cache.update(real_point_loc_cache)
    ui._native_cp_points.update(rows=None, points=None)
    ui._native_point_objects.update(source=None, locked=None, rows=[], points=None)
    if ob.users == 0:
        bpy.data.objects.remove(ob)
    for item in locals().get("batch_objects", ()):
        if item.users == 0:
            bpy.data.objects.remove(item)

check(not any(math.isnan(value) for segment in native_result for point in segment for value in point),
      "acceptance fixture itself contains only finite display coordinates")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} native-selection adapter checks passed")
