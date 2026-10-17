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
import rhino3dm as r3

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

    intersection_lines = (
        ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        ((8.0, -2.0, 0.0), (8.0, 2.0, 0.0)),
        ((5.0, -2.0, 0.0), (5.0, 2.0, 0.0)),
        ((2.0, -2.0, 1.0), (2.0, 2.0, 1.0)),
    )
    intersections = native_selection.intersection_points(intersection_lines, 0.0)
    packed_first = native_selection._dynamic_snap_pack_cache["packed"]
    repeated = native_selection.intersection_points(intersection_lines, 0.0)
    check(intersections == (((8.0, 0.0, 0.0), 0), ((5.0, 0.0, 0.0), 0))
          and repeated == intersections,
          "adapter returns ordered deduplicated native intersections", repr(intersections))
    check(native_selection._dynamic_snap_pack_cache["packed"] is packed_first,
          "immutable intersection polylines reuse their packed f64 buffer")
    boundary = native_selection.intersection_points(intersection_lines, 1.0)
    check(boundary is not None and ((2.0, 0.0, 0.5), 0) in boundary,
          "adapter preserves inclusive intersection tolerance", repr(boundary))
    check(native_selection.intersection_points(intersection_lines, -1.0) is None,
          "invalid intersection tolerance requests scalar fallback")

    dynamic_lines = (
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
         (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
    )
    tangent_rows = native_selection.perpendicular_tangent_points(
        dynamic_lines, (False, True), (2.0, -2.0, 0.0), perp=False)
    check(tangent_rows == (
        ((0.75, 0.25, 0.0), 1, "Tan"),
        ((-0.25, -0.75, 0.0), 1, "Tan")),
          "adapter preserves tangent crossings over a closed seam", repr(tangent_rows))
    perp_rows = native_selection.perpendicular_tangent_points(
        dynamic_lines, (False, True), (4.0, 3.0, 0.0), tan=False)
    check(perp_rows is not None and perp_rows[0] == ((4.0, 0.0, 0.0), 0, "Perp"),
          "adapter preserves strict interior Perp feet", repr(perp_rows))
    check(native_selection.perpendicular_tangent_points(
        dynamic_lines, (False,), (4.0, 3.0, 0.0)) is None,
          "invalid Perp/Tan target metadata requests scalar fallback")

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
    screen_pixels = np.array(((0.0, 0.0), (10.0, 0.0),
                              (0.0, 0.0), (10.0, 0.0)), dtype=np.float64)
    screen_visible = np.ones(4, dtype=np.uint8)
    screen_offsets = np.array((0, 2, 4), dtype=np.uint64)
    clip_values = np.array(((-1.0, -1.0, 0.0, 1.0),
                            (1.0, 1.0, 0.0, 1.0),
                            (0.0, 0.0, 0.0, -1.0),
                            (0.0, 0.0, 0.0, 1.0)), dtype=np.float64)
    clip_offsets = np.array((0, 3, 4), dtype=np.uint64)
    clip_flags = np.array((0, 1), dtype=np.uint8)
    clip_boxes = native_selection.screen_boxes_from_clip(
        clip_values, clip_offsets, clip_flags, 800, 600)
    check(clip_boxes is not None
          and tuple(clip_boxes[1]) == (1, 1)
          and tuple(clip_boxes[0][0]) == (0.0, 0.0, 800.0, 600.0)
          and tuple(clip_boxes[0][1]) == (400.0, 300.0, 400.0, 300.0),
          "clip-box adapter returns grouped rectangles without point tuples")
    world_values = np.array(((-1.0, -1.0, 0.0),
                             (1.0, 1.0, 0.0),
                             (0.0, 0.0, -10.0),
                             (0.0, 0.0, 0.0)), dtype=np.float64)
    world_boxes = native_selection.screen_boxes_from_world(
        world_values, identity, clip_offsets, clip_flags, 800, 600)
    check(world_boxes is not None
          and tuple(world_boxes[1]) == (1, 1)
          and tuple(world_boxes[0][0]) == (0.0, 0.0, 800.0, 600.0)
          and tuple(world_boxes[0][1]) == (400.0, 300.0, 400.0, 300.0),
          "fused world-box adapter projects and reduces without a NumPy clip buffer")
    mask_values = np.array(((-0.5, -0.5, 0.0), (0.5, 0.5, 0.0),
                            (0.0, 0.0, 0.0), (1.0, 1.0, 0.0)), dtype=np.float64)
    mask_offsets = np.array((0, 2, 4), dtype=np.uint64)
    mask_flags = np.array((0, 1), dtype=np.uint8)
    window_hits = native_selection.world_rect_hit_indices(
        mask_values, identity, mask_offsets, mask_flags, 800, 600,
        (300.0, 200.0, 500.0, 400.0))
    crossing_hits = native_selection.world_rect_hit_indices(
        mask_values, identity, mask_offsets, mask_flags, 800, 600,
        (600.0, 450.0, 700.0, 500.0), crossing=True)
    check(window_hits == (1,),
          "compact adapter returns only fully contained window indices", repr(window_hits))
    check(crossing_hits == (0,),
          "compact adapter preserves inclusive crossing edges", repr(crossing_hits))
    aabb_matrix = np.array(((1.0, 0.0, 0.0, 0.0),
                            (0.0, 1.0, 0.0, 0.0),
                            (0.0, 0.0, 1.0, 0.0),
                            (0.0, 0.0, 1.0, 0.0)), dtype=np.float64)
    aabb_lower = np.array(((-1.0, -1.0, 1.0),
                           (-1.0, -1.0, -2.0),
                           (-1.0, -1.0, -1.0)), dtype=np.float64)
    aabb_upper = np.array(((1.0, 1.0, 1.0),
                           (1.0, 1.0, -1.0),
                           (1.0, 1.0, 1.0)), dtype=np.float64)
    expected_aabbs = ui.osnap.boxes_screen_rects(
        aabb_lower, aabb_upper, aabb_matrix, 800, 600)
    native_aabbs = native_selection.screen_rects_from_aabbs(
        aabb_lower, aabb_upper, aabb_matrix, 800, 600)
    check(native_aabbs is not None
          and all(np.array_equal(actual, expected)
                  for actual, expected in zip(native_aabbs, expected_aabbs)),
          "AABB adapter exactly preserves bounded, behind, and straddling rectangles")
    near_polylines = (
        np.array(((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), dtype=np.float64),
        np.array(((0.25, 0.5, 0.0),), dtype=np.float64),
    )
    projected_near = native_selection.nearest_projected_polylines(
        near_polylines, identity, 800, 600, (400.0, 300.0))
    check(projected_near == ((0.0, 0.0, 0.0), None),
          "projected Near adapter reconstructs each coarse world point in source order")
    immutable_near = tuple(tuple(tuple(float(value) for value in point)
                                 for point in points) for points in near_polylines)
    native_selection.nearest_projected_polylines(
        immutable_near, identity, 800, 600, (400.0, 300.0))
    first_near_pack = native_selection._near_pack_cache["packed"]
    native_selection.nearest_projected_polylines(
        immutable_near, identity, 800, 600, (401.0, 300.0))
    reused_near_pack = native_selection._near_pack_cache["packed"]
    replacement_near = tuple(tuple(point for point in points) for points in immutable_near)
    native_selection.nearest_projected_polylines(
        replacement_near, identity, 800, 600, (400.0, 300.0))
    rebuilt_near_pack = native_selection._near_pack_cache["packed"]
    check(first_near_pack is reused_near_pack and rebuilt_near_pack is not reused_near_pack,
          "Near packing reuses immutable identities and rebuilds equal replacement tuples")
    mutable_near = np.array(((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), dtype=np.float64)
    first_mutable = native_selection.nearest_projected_polylines(
        (mutable_near,), identity, 800, 600, (400.0, 300.0))
    mutable_near[:, 0] = (0.5, 1.0)
    changed_mutable = native_selection.nearest_projected_polylines(
        (mutable_near,), identity, 800, 600, (400.0, 300.0))
    check(first_mutable == ((0.0, 0.0, 0.0),)
          and changed_mutable == ((0.5, 0.0, 0.0),),
          "mutable Near inputs repack every call and cannot return stale geometry")
    nested_mutable = ([-1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    first_nested = native_selection.nearest_projected_polylines(
        (nested_mutable,), identity, 800, 600, (400.0, 300.0))
    nested_mutable[0][0] = 0.75
    changed_nested = native_selection.nearest_projected_polylines(
        (nested_mutable,), identity, 800, 600, (400.0, 300.0))
    check(first_nested == ((0.0, 0.0, 0.0),)
          and changed_nested == ((0.75, 0.0, 0.0),),
          "tuple polylines containing mutable points are never cached")
    snap_distances = native_selection.projected_snap_distances(
        np.array(((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)), dtype=np.float64),
        identity, 800, 600, (400.0, 300.0), 1.0)
    check(snap_distances is not None
          and tuple(snap_distances[0]) == (0,)
          and tuple(snap_distances[1]) == (0.0,),
          "snap-distance adapter returns only ordered inclusive-radius survivors")
    empty_snap_distances = native_selection.projected_snap_distances(
        np.empty((0, 3), dtype=np.float64), identity, 800, 600,
        (400.0, 300.0), 1.0)
    check(empty_snap_distances is not None
          and empty_snap_distances[0].dtype == np.uint64
          and empty_snap_distances[1].dtype == np.float64,
          "empty snap-distance output preserves compact index/distance dtypes")
    check(native_selection.screen_boxes_from_clip(
        clip_values, np.array((0, 4, 3), dtype=np.uint64), clip_flags,
        800, 600) is None,
          "malformed clip-box offsets request NumPy fallback")

    screen_pick = native_selection.nearest_screen_polyline(
        screen_pixels, screen_visible, screen_offsets, (5.0, 1.0), 9.0)
    screen_boundary = native_selection.nearest_screen_polyline(
        screen_pixels, screen_visible, screen_offsets, (5.0, 9.0), 9.0)
    check(screen_pick == (0, 1.0)
          and screen_boundary == (native_selection.PICK_NO_HIT, math.inf),
          "screen-polyline adapter preserves strict earlier hover semantics",
          repr((screen_pick, screen_boundary)))
    check(native_selection.nearest_screen_polyline(
        screen_pixels, screen_visible, np.array((0, 4, 2), dtype=np.uint64),
        (5.0, 1.0), 9.0) is None,
          "malformed screen-polyline offsets request Python fallback")

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
    blender_display = native_selection.project_display_points(
        precise_point, matrix, 800, 600)
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
    adversarial_display = native_selection.project_display_points(
        adversarial_point, adversarial_matrix, 1920, 1080)
    check(blender_boundary_pick is not None and blender_boundary_pick[0] == 0
          and abs(blender_boundary_pick[1] - 9.0) < 1e-12
          and blender_boundary_rect == (0,)
          and blender_display is not None
          and tuple(blender_display[0][0]) == tuple(blender_pixel)
          and adversarial_pick == (0, 0.0) and adversarial_rect == (0,)
          and adversarial_display is not None
          and tuple(adversarial_display[0][0]) == tuple(adversarial_pixel),
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

    def malformed_display(visibility, pixel):
        def call(*args):
            output = ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_double))
            mask = ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))
            output[0] = output[1] = pixel
            mask[0] = visibility
            return 0
        return call

    def malformed_clip_boxes(validity, values):
        values = values if isinstance(values, tuple) else (values,) * 4
        def call(*args):
            output = ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_double))
            valid = ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))
            for index, value in enumerate(values):
                output[index] = value
            valid[0] = validity
            return 0
        return call

    malformed_clip_cases = (
        (malformed_clip_boxes(2, 0.0), 0),
        (malformed_clip_boxes(1, math.nan), 0),
        (malformed_clip_boxes(0, 0.0), 0),
        (malformed_clip_boxes(1, (10.0, 10.0, 0.0, 0.0)), 0),
        (malformed_clip_boxes(1, (0.0, 0.0, 1.0, 1.0)), 1),
    )
    malformed_clip_fallback = True
    for clip_call, point_flag in malformed_clip_cases:
        fake_clip_library = types.SimpleNamespace(
            narwhal_screen_boxes_from_clip_f64=clip_call)
        native_selection._native_binding = (
            fake_clip_library, None, None, None, None, Path("bad-clip-native"))
        malformed_clip_fallback &= native_selection.screen_boxes_from_clip(
            clip_values[:3], np.array((0, 3), dtype=np.uint64),
            np.array((point_flag,), dtype=np.uint8), 800, 600) is None
    check(malformed_clip_fallback,
          "malformed native clip-box masks and rectangles request NumPy fallback")

    malformed_world_fallback = True
    for world_call, point_flag in malformed_clip_cases:
        fake_world_library = types.SimpleNamespace(
            narwhal_screen_boxes_from_world_f64=world_call)
        native_selection._native_binding = (
            fake_world_library, None, None, None, None, Path("bad-world-native"))
        malformed_world_fallback &= native_selection.screen_boxes_from_world(
            world_values[:3], identity, np.array((0, 3), dtype=np.uint64),
            np.array((point_flag,), dtype=np.uint8), 800, 600) is None
    check(malformed_world_fallback,
          "malformed fused world-box masks and rectangles request clip fallback")

    def malformed_aabb_rects(unbounded_value, behind_value, values):
        def call(*args):
            output = ctypes.cast(args[-3], ctypes.POINTER(ctypes.c_double))
            unbounded_output = ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_ubyte))
            behind_output = ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))
            for index, value in enumerate(values):
                output[index] = value
            unbounded_output[0] = unbounded_value
            behind_output[0] = behind_value
            return 0
        return call

    malformed_aabb_cases = (
        lambda *_args: 0,
        malformed_aabb_rects(2, 0, (0.0, 0.0, 1.0, 1.0)),
        malformed_aabb_rects(0, 2, (0.0, 0.0, 1.0, 1.0)),
        malformed_aabb_rects(0, 0, (math.nan, 0.0, 1.0, 1.0)),
        malformed_aabb_rects(0, 0, (2.0, 0.0, 1.0, 1.0)),
        malformed_aabb_rects(0, 1, (0.0, 0.0, 1.0, 1.0)),
    )
    malformed_aabb_fallback = True
    for aabb_call in malformed_aabb_cases:
        fake_aabb_library = types.SimpleNamespace(
            narwhal_screen_rects_from_aabbs_f64=aabb_call)
        native_selection._native_binding = (
            fake_aabb_library, None, None, None, None, Path("bad-aabb-native"))
        malformed_aabb_fallback &= native_selection.screen_rects_from_aabbs(
            aabb_lower[:1], aabb_upper[:1], identity, 800, 600) is None
    check(malformed_aabb_fallback,
          "malformed native AABB masks and rectangles request NumPy fallback")

    def malformed_projected_near(validity, segment, parameter, distance):
        def call(*args):
            ctypes.cast(args[-4], ctypes.POINTER(ctypes.c_uint64))[0] = segment
            ctypes.cast(args[-3], ctypes.POINTER(ctypes.c_double))[0] = parameter
            ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_double))[0] = distance
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_ubyte))[0] = validity
            return 0
        return call

    malformed_near_calls = (
        lambda *_args: 0,
        malformed_projected_near(2, 0, 0.5, 0.0),
        malformed_projected_near(1, 99, 0.5, 0.0),
        malformed_projected_near(1, 0, 2.0, 0.0),
        malformed_projected_near(1, 0, 0.5, math.nan),
        malformed_projected_near(0, 0, 0.0, 0.0),
    )
    malformed_near_fallback = True
    for near_call in malformed_near_calls:
        fake_near_library = types.SimpleNamespace(
            narwhal_nearest_projected_polylines_f64=near_call)
        native_selection._native_binding = (
            fake_near_library, None, None, None, None, Path("bad-near-native"))
        malformed_near_fallback &= native_selection.nearest_projected_polylines(
            near_polylines[:1], identity, 800, 600, (400.0, 300.0)) is None
    check(malformed_near_fallback,
          "no-write and malformed projected Near output requests scalar fallback")

    def malformed_snap_distances(count, index, distance):
        def call(*args):
            ctypes.cast(args[-3], ctypes.POINTER(ctypes.c_uint64))[0] = index
            ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_double))[0] = distance
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint64))[0] = count
            return 0
        return call

    malformed_distance_calls = (
        lambda *_args: 0,
        malformed_snap_distances(0, 0, 0.0),
        malformed_snap_distances(2, 0, 0.0),
        malformed_snap_distances(1, 99, 0.0),
        malformed_snap_distances(1, 0, math.nan),
        malformed_snap_distances(1, 0, 11.0),
    )
    malformed_distance_fallback = True
    for distance_call in malformed_distance_calls:
        fake_distance_library = types.SimpleNamespace(
            narwhal_projected_snap_distances_f64=distance_call)
        native_selection._native_binding = (
            fake_distance_library, None, None, None, None, Path("bad-distance-native"))
        malformed_distance_fallback &= native_selection.projected_snap_distances(
            point_values[:1], identity, 800, 600, (400.0, 300.0), 10.0) is None
    check(malformed_distance_fallback,
          "no-write and malformed snap-distance output requests pixel fallback")

    def corrupt_snap_distance_tail(*args):
        indices = ctypes.cast(args[-3], ctypes.POINTER(ctypes.c_uint64))
        distances = ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_double))
        indices[0], distances[0] = 0, 0.0
        indices[1], distances[1] = 1, 1.0
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint64))[0] = 1
        return 0

    native_selection._native_binding = (
        types.SimpleNamespace(narwhal_projected_snap_distances_f64=corrupt_snap_distance_tail),
        None, None, None, None, Path("bad-distance-tail"))
    check(native_selection.projected_snap_distances(
        point_values[:2], identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "corrupted compact snap-distance tail requests pixel fallback")

    malformed_display_cases = (
        (malformed_display(2, 0.0), "undocumented native display visibility"),
        (malformed_display(1, math.nan), "visible NaN native display output"),
        (malformed_display(0, 0.0), "hidden finite native display output"),
    )
    malformed_display_fallback = True
    for display_call, _label in malformed_display_cases:
        fake_display_library = types.SimpleNamespace(
            narwhal_project_display_points_f64=display_call)
        native_selection._native_binding = (
            fake_display_library, None, None, None, None, Path("bad-display-native"))
        malformed_display_fallback &= native_selection.project_display_points(
            point_values, identity, 800, 600) is None
    check(malformed_display_fallback,
          "malformed native display masks and pixels request scalar fallback")

    def invalid_polyline_hit(*args):
        ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_uint64))[0] = 2
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_double))[0] = 0.0
        return 1

    fake_polyline_library = types.SimpleNamespace(
        narwhal_nearest_screen_polyline_f64=invalid_polyline_hit)
    native_selection._native_binding = (
        fake_polyline_library, None, None, None, None, Path("bad-polyline-native"))
    check(native_selection.nearest_screen_polyline(
        screen_pixels, screen_visible, screen_offsets, (5.0, 1.0), 9.0) is None,
          "out-of-range native screen-polyline index requests Python fallback")

    def invalid_polyline_no_hit(*args):
        ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_uint64))[0] = 0
        ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_double))[0] = math.inf
        return 0

    fake_polyline_library.narwhal_nearest_screen_polyline_f64 = invalid_polyline_no_hit
    check(native_selection.nearest_screen_polyline(
        screen_pixels, screen_visible, screen_offsets, (5.0, 1.0), 9.0) is None,
          "inconsistent native screen-polyline no-hit requests Python fallback")

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
              point_values, identity, 800, 600, (0.0, 0.0, 800.0, 600.0)) is None
          and native_selection.project_display_points(
              point_values, identity, 800, 600) is None
          and native_selection.nearest_screen_polyline(
              screen_pixels, screen_visible, screen_offsets, (5.0, 1.0), 9.0) is None
          and native_selection.screen_boxes_from_clip(
              clip_values, clip_offsets, clip_flags, 800, 600) is None
          and native_selection.screen_boxes_from_world(
              world_values, identity, clip_offsets, clip_flags, 800, 600) is None
          and native_selection.screen_rects_from_aabbs(
              aabb_lower, aabb_upper, identity, 800, 600) is None
          and native_selection.nearest_projected_polylines(
              near_polylines, identity, 800, 600, (400.0, 300.0)) is None
          and native_selection.projected_snap_distances(
              point_values, identity, 800, 600, (400.0, 300.0), 10.0) is None,
          "older ABI-1 library without later point, polyline, or box symbols uses fallbacks")

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

check(all(native_selection._near_pack_cache[key] is None
          for key in ("source", "arrays", "packed", "offsets"))
      and native_selection._near_pack_cache["total"] == 0,
      "native test reset clears every retained Near cache field")

real_snap_settings = ui._snap_settings
real_get_cplane = ui.get_cplane
real_snap_reach = ui._snap_reach
real_snap_targets = ui._snap_targets
real_best_on_screen = ui.osnap.best_on_screen
real_apply_projection = ui.osnap.apply_projection
real_location_3d_to_region_2d = v3u.location_3d_to_region_2d
real_snap_display_projection = ui.native_selection.project_display_points
real_snap_near_projection = ui.native_selection.nearest_projected_polylines
real_snap_distance_projection = ui.native_selection.projected_snap_distances
try:
    ui._snap_settings = types.SimpleNamespace(
        active=lambda **_kwargs: ("End",), radius_px=10.0,
        snap_to_locked=False, project=False)
    ui.get_cplane = lambda _area: object()
    ui._snap_reach = lambda *_args: "native-snap-reach"
    ui._snap_targets = lambda _excluded, *, reach=None: ("target", reach)
    v3u.location_3d_to_region_2d = (
        lambda _region, _rv3d, _point: types.SimpleNamespace(x=400.0, y=300.0))
    snap_region = types.SimpleNamespace(width=800, height=600)
    snap_rv3d = types.SimpleNamespace(
        perspective_matrix=tuple(tuple(float(row == column) for column in range(4))
                                 for row in range(4)))
    native_snap_calls = []
    ui.native_selection.project_display_points = (
        lambda *args: native_snap_calls.append(args)
        or (np.array(((400.0, 300.0), (800.0, 300.0))),
            np.array((1, 1), dtype=np.uint8)))
    native_distance_calls = []
    ui.native_selection.projected_snap_distances = (
        lambda *args: native_distance_calls.append(args)
        or (np.array((0,), dtype=np.uint64), np.array((0.0,))))
    native_near_calls = []
    ui.native_selection.nearest_projected_polylines = (
        lambda *args: native_near_calls.append(args) or ((0.5, 0.0, 0.0),))
    routed_snap_batches = []

    def capture_best(targets, cursor, modes, project, radius, plane, **kwargs):
        routed_snap_batches.append((targets, cursor, modes, radius, plane,
                                    project(cursor),
                                    kwargs["project_batch"](((0.0, 0.0, 0.0),
                                                             (1.0, 0.0, 0.0))),
                                    kwargs["project_near_batch"](
                                        (((0.0, 0.0, 0.0),
                                          (1.0, 0.0, 0.0)),), (400.0, 300.0)),
                                    kwargs["project_distance_batch"](
                                        ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
                                        (400.0, 300.0), 10.0)))
        return "native-snap"

    ui.osnap.best_on_screen = capture_best
    ui.osnap.apply_projection = lambda snap, _plane, _enabled: snap
    routed_snap = ui.snap_at(
        None, types.SimpleNamespace(region_3d=snap_rv3d), snap_region,
        (0.0, 0.0, 0.0))
    check(routed_snap == "native-snap" and len(routed_snap_batches) == 1
          and routed_snap_batches[0][0] == ("target", "native-snap-reach")
          and routed_snap_batches[0][5] == (400.0, 300.0)
          and len(native_snap_calls) == 1 and len(native_near_calls) == 1
          and len(native_distance_calls) == 1
          and routed_snap_batches[0][7] == ((0.5, 0.0, 0.0),)
          and tuple(routed_snap_batches[0][8][0]) == (0,)
          and native_snap_calls[0][1].shape == (4, 4)
          and native_snap_calls[0][2:] == (800, 600),
          "snap_at supplies one viewport-shaped native candidate projection batch")

    captured_batch = {}

    def capture_decline(_targets, _cursor, _modes, _project, _radius, _plane, **kwargs):
        captured_batch["result"] = kwargs["project_batch"](((0.0, 0.0, 0.0),))
        return None

    ui.osnap.best_on_screen = capture_decline
    ui.native_selection.project_display_points = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("native projector unavailable"))
    ui.native_selection.nearest_projected_polylines = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("native Near unavailable"))
    ui.native_selection.projected_snap_distances = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("native snap distance unavailable"))
    declined_snap = ui.snap_at(
        None, types.SimpleNamespace(region_3d=snap_rv3d), snap_region,
        (0.0, 0.0, 0.0))
    check(declined_snap is None and captured_batch.get("result") is None,
          "snap_at turns native batch exceptions into the scalar-fallback sentinel")
finally:
    ui._snap_settings = real_snap_settings
    ui.get_cplane = real_get_cplane
    ui._snap_reach = real_snap_reach
    ui._snap_targets = real_snap_targets
    ui.osnap.best_on_screen = real_best_on_screen
    ui.osnap.apply_projection = real_apply_projection
    v3u.location_3d_to_region_2d = real_location_3d_to_region_2d
    ui.native_selection.project_display_points = real_snap_display_projection
    ui.native_selection.nearest_projected_polylines = real_snap_near_projection
    ui.native_selection.projected_snap_distances = real_snap_distance_projection

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
real_native_screen = ui.native_selection.nearest_screen_polyline
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
real_hover_native_data = ui._hover_curve_native_data
real_hover_world_data = ui._hover_curve_world_data
real_hover_curve_object = ui._hover_curve_object
real_native_screen_boxes = ui._native_selection_screen_boxes
real_native_rect_names = ui._native_selection_rect_names
real_world_rect_indices = ui.native_selection.world_rect_hit_indices
real_selection_box_inputs = ui._selection_world_box_inputs
real_object_screen_bboxes = ui._object_screen_bboxes
real_rectangle_selection_hits = ui._rectangle_selection_hits
real_world_boxes = ui.native_selection.screen_boxes_from_world
real_clip_boxes = ui.native_selection.screen_boxes_from_clip
real_project_many = ui._project_many_2d
real_curve_candidates = ui._curve_selection_candidates
real_unpickable_for_boxes = ui._unpickable_names_cached
real_doc_rows = ui._doc_rows
real_curve_points = ui._curve_points
real_display_visible = ui.bridge.display_visible
real_geom_by_name = ui.geom_by_name_in_document
real_ui_bpy = ui.bpy
real_region_origin = v3u.region_2d_to_origin_3d
real_region_vector = v3u.region_2d_to_vector_3d
real_model = ui.app_state.model
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

    hover_pixels = np.array(((0.0, 0.0), (10.0, 0.0),
                             (0.0, 0.0), (10.0, 0.0)), dtype=np.float64)
    hover_visible = np.ones(4, dtype=np.uint8)
    hover_offsets = np.array((0, 2, 4), dtype=np.uint64)
    ui.native_selection.available = lambda: True
    ui._hover_curve_native_data = lambda *_args: (
        ("HoverFirst", "HoverSecond"), hover_offsets, hover_pixels, hover_visible)
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) == "HoverFirst",
          "native hover projection preserves the earlier-curve exact tie")
    check(ui._hover_curve_object(
        region, rv3d, 5.0, ui.SUBOBJECT_PICK_PX) is None,
          "native hover projection preserves strict distance < tolerance")
    saved_hover_pixel = hover_pixels[1].copy()
    hover_visible[1] = 0
    hover_pixels[1] = (math.nan, math.nan)
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) == "HoverSecond",
          "native hover drops only a segment with an unprojectable endpoint")

    hover_visible[:] = 1
    hover_pixels[1] = saved_hover_pixel
    hover_native_calls = []
    ui.native_selection.nearest_screen_polyline = (
        lambda *args: hover_native_calls.append(args) or (0, 1.0))
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) == "HoverFirst"
          and len(hover_native_calls) == 1
          and hover_native_calls[0][0] is hover_pixels
          and hover_native_calls[0][1] is hover_visible
          and hover_native_calls[0][2] is hover_offsets,
          "curve hover maps one native screen-polyline result without Python segments")
    ui.native_selection.nearest_screen_polyline = (
        lambda *_args: (native_selection.PICK_NO_HIT, math.inf))
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) is None,
          "authoritative native screen-polyline miss bypasses Python distance")
    ui.native_selection.nearest_screen_polyline = lambda *_args: (-2, 0.0)
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) == "HoverFirst",
          "malformed UI screen-polyline result uses projected-array fallback")
    ui.native_selection.nearest_screen_polyline = lambda *_args: None
    check(ui._hover_curve_object(region, rv3d, 5.0, 1.0) == "HoverFirst",
          "missing or failed screen-polyline query preserves projected-array fallback")
    ui.native_selection.nearest_screen_polyline = real_native_screen

    ray_curve_calls = []
    ray_object = types.SimpleNamespace(name="RayFirstSurface")
    ui._hover_curve_object = (
        lambda *_args: ray_curve_calls.append(_args) or "CurveFallback")
    ui.geom_by_name_in_document = lambda name: object() if name == ray_object.name else None
    ui.bpy = types.SimpleNamespace(context=types.SimpleNamespace(
        scene=types.SimpleNamespace(ray_cast=lambda *_args: (
            True, None, None, None, ray_object, None)),
        evaluated_depsgraph_get=lambda: object()))
    v3u.region_2d_to_origin_3d = lambda *_args: (0.0, 0.0, 0.0)
    v3u.region_2d_to_vector_3d = lambda *_args: (0.0, 0.0, -1.0)
    ray_result = ui._hover_object(
        None, types.SimpleNamespace(region_3d=rv3d), region, 5.0, 1.0)
    check(ray_result == ray_object.name and not ray_curve_calls,
          "surface and solid ray hits remain authoritative before curve hover")
    ui._hover_curve_object = real_hover_curve_object
    ui.geom_by_name_in_document = real_geom_by_name
    ui.bpy = real_ui_bpy
    v3u.region_2d_to_origin_3d = real_region_origin
    v3u.region_2d_to_vector_3d = real_region_vector

    ui._hover_curve_native_data = real_hover_native_data
    projection_calls = []
    local_hover_cache = {
        "key": object(), "names": ("HoverFirst",),
        "offsets": np.array((0, 2), dtype=np.uint64),
        "points": np.array(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), dtype=np.float64),
        "view": None, "pixels": None, "visible": None,
    }
    ui._hover_curve_world_data = lambda: local_hover_cache
    ui.native_selection.project_display_points = (
        lambda *args: projection_calls.append(args)
        or (np.array(((400.0, 300.0), (800.0, 300.0))), np.ones(2, dtype=np.uint8)))
    ui._hover_curve_native_data(region, rv3d)
    ui._hover_curve_native_data(region, rv3d)
    changed_rv3d = type("RV3D", (), {"perspective_matrix": (
        (1.0, 0.0, 0.0, 0.1), (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))})()
    ui._hover_curve_native_data(region, changed_rv3d)
    changed_width = types.SimpleNamespace(
        width=region.width + 1, height=region.height)
    ui._hover_curve_native_data(changed_width, changed_rv3d)
    changed_height = types.SimpleNamespace(
        width=changed_width.width, height=region.height + 1)
    ui._hover_curve_native_data(changed_height, changed_rv3d)
    check(len(projection_calls) == 4
          and projection_calls[0][0] is local_hover_cache["points"],
          "hover pixels cache by matrix, width, and height independently")

    ui._hover_curve_world_data = real_hover_world_data

    box_points = [(-1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (0.0, 0.0, 0.0)]
    box_slices = [("BoxFirst", 0, 2, False), ("PointSecond", 2, 3, True)]
    fused_route_calls = []
    ui.native_selection.screen_boxes_from_world = (
        lambda *args: fused_route_calls.append(args)
        or (np.array(((0.0, 0.0, 800.0, 600.0),
                      (400.0, 300.0, 400.0, 300.0))),
            np.array((1, 1), dtype=np.uint8)))
    ui.native_selection.screen_boxes_from_clip = lambda *_args: (_ for _ in ()).throw(
        AssertionError("clip reducer should be bypassed by fused projection"))
    native_box_rows = ui._native_selection_screen_boxes(
        region, rv3d, box_points, box_slices)
    check(native_box_rows == [
        ("BoxFirst", (0.0, 0.0, 800.0, 600.0)),
        ("PointSecond", (400.0, 300.0, 400.0, 300.0))]
          and len(fused_route_calls) == 1,
          "UI box helper routes fused world projection and preserves source names")

    compact_route_calls = []
    ui.native_selection.world_rect_hit_indices = (
        lambda *args, **kwargs: compact_route_calls.append((args, kwargs)) or (0, 1))
    compact_names = ui._native_selection_rect_names(
        region, rv3d, box_points, box_slices,
        (390.0, 290.0, 410.0, 310.0), ui.select.CROSSING)
    check(compact_names == ["BoxFirst", "PointSecond"]
          and len(compact_route_calls) == 1
          and compact_route_calls[0][1].get("crossing") is True,
          "UI compact mask maps multiple hit indices back to ordered names")
    ui.native_selection.world_rect_hit_indices = lambda *_args, **_kwargs: (1, 0)
    check(ui._native_selection_rect_names(
        region, rv3d, box_points, box_slices,
        (390.0, 290.0, 410.0, 310.0), ui.select.CROSSING) is None,
          "descending compact indices request the complete box fallback")
    ui.native_selection.world_rect_hit_indices = real_world_rect_indices

    compact_production_calls = []
    ui._selection_world_box_inputs = lambda _projection: (tuple(box_points), tuple(box_slices))
    ui._native_selection_rect_names = (
        lambda *args: compact_production_calls.append(args) or ["PointSecond"])
    ui._object_screen_bboxes = lambda *_args: [
        ("WrongFallback", (0.0, 0.0, 1.0, 1.0))]
    ui._rectangle_selection_hits = (
        lambda candidates, _rect, _mode, **kwargs:
        [tuple(candidates), tuple(kwargs.get("preclassified") or ())])
    compact_production = ui._object_rectangle_hits(
        None, types.SimpleNamespace(region_3d=rv3d), region, rv3d,
        {"boxes": None, "box_inputs": None},
        (390.0, 290.0, 410.0, 310.0), ui.select.WINDOW)
    check(compact_production == [(), ("PointSecond",)]
          and len(compact_production_calls) == 1,
          "selection release consumes compact native names without materializing boxes")
    ui._native_selection_rect_names = lambda *_args: None
    compact_decline = ui._object_rectangle_hits(
        None, types.SimpleNamespace(region_3d=rv3d), region, rv3d,
        {"boxes": None, "box_inputs": None},
        (0.0, 0.0, 2.0, 2.0), ui.select.WINDOW)
    check(compact_decline == [
        (("WrongFallback", (0.0, 0.0, 1.0, 1.0)),), ()],
          "compact-mask decline enters the connected screen-box fallback branch")
    ui._selection_world_box_inputs = real_selection_box_inputs
    ui._native_selection_rect_names = real_native_rect_names
    ui._object_screen_bboxes = real_object_screen_bboxes
    ui._rectangle_selection_hits = real_rectangle_selection_hits

    clip_route_calls = []
    ui.native_selection.screen_boxes_from_world = lambda *_args: None
    ui.native_selection.screen_boxes_from_clip = (
        lambda *args: clip_route_calls.append(args)
        or (np.array(((0.0, 0.0, 800.0, 600.0),
                      (400.0, 300.0, 400.0, 300.0))),
            np.array((1, 1), dtype=np.uint8)))
    clip_fallback_rows = ui._native_selection_screen_boxes(
        region, rv3d, box_points, box_slices)
    check(clip_fallback_rows == native_box_rows and len(clip_route_calls) == 1
          and clip_route_calls[0][0].shape == (3, 4),
          "fused decline uses the existing NumPy-clip native reducer")

    ui.native_selection.screen_boxes_from_clip = lambda *_args: None
    check(ui._native_selection_screen_boxes(region, rv3d, box_points, box_slices) is None,
          "both native box stages declining requests the complete tuple fallback")
    ui.native_selection.screen_boxes_from_world = real_world_boxes
    ui.native_selection.screen_boxes_from_clip = real_clip_boxes
    box_route_calls = []
    ui._curve_selection_candidates = lambda *_args: (
        [], set(bpy.data.objects.keys()))
    ui._document_point_locations = lambda: []
    ui._unpickable_names_cached = lambda: frozenset()
    ui._native_selection_screen_boxes = (
        lambda *args: box_route_calls.append(args)
        or [("NativeBox", (1.0, 2.0, 3.0, 4.0))])
    ui._project_many_2d = lambda *_args: (_ for _ in ()).throw(
        AssertionError("tuple projection should be bypassed"))
    routed_projection = {"boxes": None}
    routed_boxes = ui._object_screen_bboxes(
        None, types.SimpleNamespace(region_3d=rv3d), region, routed_projection)
    check(routed_boxes == [("NativeBox", (1.0, 2.0, 3.0, 4.0))]
          and len(box_route_calls) == 1
          and routed_projection["boxes"] == tuple(routed_boxes),
          "object screen boxes route one native reduction and cache its result")
    ui._native_selection_screen_boxes = lambda *_args: None
    fallback_record = {"n": 1, "lo": (-1.0, -1.0, 0.0),
                       "hi": (1.0, 1.0, 0.0)}
    ui._curve_selection_candidates = lambda *_args: (
        [("FallbackBox", object(), fallback_record)],
        set(bpy.data.objects.keys()) | {"FallbackBox"})
    ui._project_many_2d = lambda _region, _rv3d, points: (
        [(0.0, 0.0), (10.0, 10.0)] * (len(points) // 2))
    fallback_projection = {"boxes": None}
    fallback_boxes = ui._object_screen_bboxes(
        None, types.SimpleNamespace(region_3d=rv3d), region,
        fallback_projection)
    check(fallback_boxes == [("FallbackBox", (0.0, 0.0, 10.0, 10.0))]
          and fallback_projection["boxes"] == tuple(fallback_boxes),
          "native clip-box decline runs the complete tuple/reduction fallback")
    ui._native_selection_screen_boxes = real_native_screen_boxes
    ui._native_selection_rect_names = real_native_rect_names
    ui.native_selection.world_rect_hit_indices = real_world_rect_indices
    ui._selection_world_box_inputs = real_selection_box_inputs
    ui._object_screen_bboxes = real_object_screen_bboxes
    ui._rectangle_selection_hits = real_rectangle_selection_hits
    ui._project_many_2d = real_project_many
    ui._curve_selection_candidates = real_curve_candidates
    ui._document_point_locations = real_document_points
    ui._unpickable_names_cached = real_unpickable_for_boxes

    hover_object_names = ("RustHoverCacheA", "RustHoverCacheB")
    hover_objects = [bpy.data.objects.get(item) or bpy.data.objects.new(item, None)
                     for item in hover_object_names]
    hover_file_objects = [types.SimpleNamespace(
        Geometry=r3.LineCurve(r3.Point3d(index, 0.0, 0.0),
                              r3.Point3d(index + 1.0, 0.0, 0.0)),
        Attributes=types.SimpleNamespace(Name=item, Id=f"hover-{index}"))
        for index, item in enumerate(hover_object_names)]
    ui.app_state.model = types.SimpleNamespace(
        file=types.SimpleNamespace(Objects=hover_file_objects))
    hover_rows = [(item, None, None, fobj.Geometry, fobj.Attributes)
                  for item, fobj in zip(hover_object_names, hover_file_objects)]
    hover_row_source = [hover_rows]
    hover_doc_key = [object()]
    hidden_hover = set()
    hover_curve_reads = []
    ui._doc_rows = lambda: hover_row_source[0]
    ui._doc_key = lambda: hover_doc_key[0]
    ui.bridge.display_visible = lambda ob: ob.name not in hidden_hover
    ui._curve_points = lambda curve: hover_curve_reads.append(curve) or [
        (curve.PointAtStart.X, curve.PointAtStart.Y, curve.PointAtStart.Z),
        (curve.PointAtEnd.X, curve.PointAtEnd.Y, curve.PointAtEnd.Z)]
    ui._hover_curve_cache.update(
        key=None, rows=None, names=(), offsets=None, points=None,
        view=None, pixels=None, visible=None)
    world_first = ui._hover_curve_world_data()
    first_names = world_first["names"]
    world_again = ui._hover_curve_world_data()
    hidden_hover.add(hover_object_names[1])
    world_hidden = ui._hover_curve_world_data()
    hover_row_source[0] = list(hover_rows)
    world_same_key_edit = ui._hover_curve_world_data()
    hover_doc_key[0] = object()
    world_changed = ui._hover_curve_world_data()
    check(world_first is world_again is world_hidden is world_same_key_edit is world_changed
          and len(hover_curve_reads) == 5
          and first_names == hover_object_names
          and world_first["names"] == (hover_object_names[0],),
          "hover world cache preserves source order and rebuilds for visibility, "
          "same-key invalidation, and document keys")

    hidden_hover.clear()
    hover_scalar_calls = []
    ui.native_selection.project_display_points = lambda *_args: None
    ui._project_2d = lambda _region, _rv3d, world: (
        hover_scalar_calls.append(world) or (float(world[0]) * 10.0, 0.0))
    check(ui._hover_curve_object(region, rv3d, 5.0, 0.0) == hover_object_names[0]
          and len(hover_scalar_calls) == 4,
          "native hover projection failure runs the complete scalar curve path")

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
    ui.native_selection.nearest_screen_polyline = real_native_screen
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
    ui._hover_curve_native_data = real_hover_native_data
    ui._hover_curve_world_data = real_hover_world_data
    ui._hover_curve_object = real_hover_curve_object
    ui._native_selection_screen_boxes = real_native_screen_boxes
    ui.native_selection.screen_boxes_from_world = real_world_boxes
    ui.native_selection.screen_boxes_from_clip = real_clip_boxes
    ui._project_many_2d = real_project_many
    ui._curve_selection_candidates = real_curve_candidates
    ui._document_point_locations = real_document_points
    ui._unpickable_names_cached = real_unpickable_for_boxes
    ui._doc_rows = real_doc_rows
    ui._curve_points = real_curve_points
    ui.bridge.display_visible = real_display_visible
    ui.geom_by_name_in_document = real_geom_by_name
    ui.bpy = real_ui_bpy
    v3u.region_2d_to_origin_3d = real_region_origin
    v3u.region_2d_to_vector_3d = real_region_vector
    ui.app_state.model = real_model
    ui._hover_curve_cache.update(
        key=None, rows=None, names=(), offsets=None, points=None,
        view=None, pixels=None, visible=None)
    ui._native_cp_points.update(rows=None, points=None)
    ui._native_point_objects.update(source=None, locked=None, rows=[], points=None)
    if ob.users == 0:
        bpy.data.objects.remove(ob)
    for item in (*locals().get("batch_objects", ()),
                 *locals().get("hover_objects", ())):
        if item.users == 0:
            bpy.data.objects.remove(item)

check(not any(math.isnan(value) for segment in native_result for point in segment for value in point),
      "acceptance fixture itself contains only finite display coordinates")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} native-selection adapter checks passed")
