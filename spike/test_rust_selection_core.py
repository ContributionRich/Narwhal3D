#!/usr/bin/env python3
"""Acceptance checks for the dependency-free Rust selection-core feasibility pilot."""
from __future__ import annotations

import ctypes
import math
import os
import random
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
DoubleP = ctypes.POINTER(ctypes.c_double)
FloatP = ctypes.POINTER(ctypes.c_float)
ByteP = ctypes.POINTER(ctypes.c_ubyte)
UInt64P = ctypes.POINTER(ctypes.c_uint64)
lib.narwhal_selection_core_abi_version.argtypes = []
lib.narwhal_selection_core_abi_version.restype = ctypes.c_uint32
lib.narwhal_project_points_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_project_points_f32.restype = ctypes.c_int
lib.narwhal_any_screen_segment_crosses_rect_f64.argtypes = [
    DoubleP, ctypes.c_size_t, DoubleP,
]
lib.narwhal_any_screen_segment_crosses_rect_f64.restype = ctypes.c_int
lib.narwhal_any_projected_segment_crosses_rect_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP,
]
lib.narwhal_any_projected_segment_crosses_rect_f32.restype = ctypes.c_int
lib.narwhal_projected_segments_rect_query_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_projected_segments_rect_query_f32.restype = ctypes.c_int
lib.narwhal_projected_curves_rect_query_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, UInt64P, ctypes.c_size_t,
    ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_projected_curves_rect_query_f32.restype = ctypes.c_int
lib.narwhal_nearest_projected_curve_f32.argtypes = [
    DoubleP, FloatP, ctypes.c_size_t, UInt64P, ctypes.c_size_t,
    ctypes.c_double, ctypes.c_double, DoubleP, ctypes.c_double, UInt64P, DoubleP,
]
lib.narwhal_nearest_projected_curve_f32.restype = ctypes.c_int
lib.narwhal_nearest_projected_point_f64.argtypes = [
    DoubleP, DoubleP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double,
    DoubleP, ctypes.c_double, UInt64P, DoubleP,
]
lib.narwhal_nearest_projected_point_f64.restype = ctypes.c_int
lib.narwhal_projected_points_rect_query_f64.argtypes = [
    DoubleP, DoubleP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_projected_points_rect_query_f64.restype = ctypes.c_int

checks = 0
failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    ok = bool(condition)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def darray(values):
    values = tuple(float(value) for value in values)
    return (ctypes.c_double * len(values))(*values)


def farray(values):
    values = tuple(float(value) for value in values)
    return (ctypes.c_float * len(values))(*values)


def scalar_project(matrix, point, width, height):
    x, y, z = (float(value) for value in point)
    if not all(math.isfinite(value) for value in (*matrix, x, y, z, width, height)):
        return None
    clip = tuple(
        matrix[row * 4] * x + matrix[row * 4 + 1] * y
        + matrix[row * 4 + 2] * z + matrix[row * 4 + 3]
        for row in range(4)
    )
    if not all(math.isfinite(value) for value in clip) or clip[3] <= 0.0:
        return None
    pixel = ((clip[0] / clip[3] + 1.0) * width * 0.5,
             (clip[1] / clip[3] + 1.0) * height * 0.5)
    return pixel if all(math.isfinite(value) for value in pixel) else None


def scalar_display_project(matrix, point, width, height):
    f32 = lambda value: ctypes.c_float(value).value
    x, y, z = (f32(value) for value in point)
    matrix32 = tuple(f32(value) for value in matrix)

    vector = (x, y, z, f32(1.0))

    def component(row):
        base = row * 4
        dot = 0.0
        for column in range(4):
            dot += f32(matrix32[base + column] * vector[column])
        return f32(dot)

    clip_x, clip_y, clip_w = component(0), component(1), component(3)
    if not all(math.isfinite(value) for value in (clip_x, clip_y, clip_w)) or clip_w <= 0:
        return None
    width_half, height_half = width / 2.0, height / 2.0
    return (f32(width_half + width_half * (clip_x / clip_w)),
            f32(height_half + height_half * (clip_y / clip_w)))


def projections_close(got, want, tolerance=2e-5):
    if got is None or want is None:
        return got is None and want is None
    return max(abs(got[index] - want[index]) for index in (0, 1)) < tolerance


def scalar_crosses(a, b, rect):
    x0, y0, x1, y1 = rect
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        t = q / p
        if p < 0.0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


def rust_project(matrix, points, width=800.0, height=600.0):
    flat = [coordinate for point in points for coordinate in point]
    matrix_raw = darray(matrix)
    points_raw = farray(flat)
    out = (ctypes.c_double * (len(points) * 2))()
    visible = (ctypes.c_ubyte * len(points))()
    code = lib.narwhal_project_points_f32(
        matrix_raw, points_raw, len(points), width, height, out, visible,
    )
    result = [None if not visible[index]
              else (out[index * 2], out[index * 2 + 1])
              for index in range(len(points))]
    return code, result


def rust_screen_any(segments, rect):
    raw = darray(value for segment in segments for point in segment for value in point)
    return lib.narwhal_any_screen_segment_crosses_rect_f64(
        raw, len(segments), darray(rect),
    )


def rust_projected_any(matrix, segments, rect, width=800.0, height=600.0):
    raw = farray(value for segment in segments for point in segment for value in point)
    return lib.narwhal_any_projected_segment_crosses_rect_f32(
        darray(matrix), raw, len(segments), width, height, darray(rect),
    )


def rust_projected_query(matrix, segments, rect, width=800.0, height=600.0):
    raw = farray(value for segment in segments for point in segment for value in point)
    evidence = ctypes.c_ubyte(255)
    code = lib.narwhal_projected_segments_rect_query_f32(
        darray(matrix), raw, len(segments), width, height, darray(rect), ctypes.byref(evidence),
    )
    return code, evidence.value


def packed_curves(curves):
    offsets = [0]
    flat = []
    for segments in curves:
        flat.extend(value for segment in segments for point in segment for value in point)
        offsets.append(offsets[-1] + len(segments))
    return (farray(flat), (ctypes.c_uint64 * len(offsets))(*offsets), offsets[-1])


def rust_projected_batch(matrix, curves, rect, width=800.0, height=600.0):
    raw, offset_raw, count = packed_curves(curves)
    statuses = (ctypes.c_ubyte * len(curves))()
    code = lib.narwhal_projected_curves_rect_query_f32(
        darray(matrix), raw, count, offset_raw, len(curves),
        width, height, darray(rect), statuses,
    )
    return code, tuple(statuses)


def rust_nearest_batch(matrix, curves, mouse, limit, width=800.0, height=600.0):
    raw, offset_raw, count = packed_curves(curves)
    index = ctypes.c_uint64((1 << 64) - 1)
    distance = ctypes.c_double(math.inf)
    code = lib.narwhal_nearest_projected_curve_f32(
        darray(matrix), raw, count, offset_raw, len(curves), width, height,
        darray(mouse), limit, ctypes.byref(index), ctypes.byref(distance),
    )
    return code, index.value, distance.value


def rust_nearest_point(matrix, points, mouse, limit, width=800.0, height=600.0):
    raw = darray(value for point in points for value in point)
    index = ctypes.c_uint64((1 << 64) - 1)
    distance = ctypes.c_double(math.inf)
    code = lib.narwhal_nearest_projected_point_f64(
        darray(matrix), raw, len(points), width, height, darray(mouse), limit,
        ctypes.byref(index), ctypes.byref(distance),
    )
    return code, index.value, distance.value


def rust_points_rect(matrix, points, rect, width=800.0, height=600.0):
    raw = darray(value for point in points for value in point)
    inside = (ctypes.c_ubyte * len(points))()
    code = lib.narwhal_projected_points_rect_query_f64(
        darray(matrix), raw, len(points), width, height, darray(rect), inside,
    )
    return code, tuple(inside)


IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)
PERSPECTIVE = (
    1.2, 0.1, 0.0, 0.2,
    0.0, 1.1, 0.1, -0.1,
    0.0, 0.0, 1.0, 0.0,
    0.15, -0.05, 0.2, 1.0,
)

check(lib.narwhal_selection_core_abi_version() == 1, "C ABI version is pinned")
points = [(-1.0, -1.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 0.0),
          (0.0, 0.0, -10.0), (2.0, -3.0, 4.0)]
code, actual = rust_project(PERSPECTIVE, points)
expected = [scalar_project(PERSPECTIVE, point, 800.0, 600.0) for point in points]
check(code == 0, "batched projection accepts valid inputs", str(code))
check([value is not None for value in actual] == [value is not None for value in expected],
      "projection preserves eye-plane visibility decisions")
check(all(projections_close(got, want) for got, want in zip(actual, expected)),
      "f32-input Rust projection matches the scalar f64 reference")

code, edge_actual = rust_project(IDENTITY, [(-1.0, -1.0, 0.0), (1.0, 1.0, 0.0)])
check(code == 0 and edge_actual == [(0.0, 0.0), (800.0, 600.0)],
      "orthographic NDC boundaries map exactly to region boundaries", repr(edge_actual))
code, nonfinite = rust_project(IDENTITY, [(math.nan, 0.0, 0.0)])
check(code == 0 and nonfinite == [None], "non-finite points are explicitly unprojectable")

rect = (4.0, 4.0, 6.0, 6.0)
check(rust_screen_any([((0.0, 5.0), (10.0, 5.0))], rect) == 1,
      "screen predicate accepts a crossing stroke")
check(rust_screen_any([((4.0, 4.0), (4.0, 4.0))], rect) == 1,
      "screen predicate includes a zero-length point on the boundary")
check(rust_screen_any([((0.0, 0.0), (10.0, 0.0)),
                       ((10.0, 0.0), (10.0, 10.0)),
                       ((10.0, 10.0), (0.0, 10.0)),
                       ((0.0, 10.0), (0.0, 0.0))], rect) == 0,
      "rectangle inside a closed stroke's empty interior remains a miss")

rng = random.Random(0x4E415257)
screen_equivalent = True
for _ in range(500):
    segments = [((rng.uniform(-20, 20), rng.uniform(-20, 20)),
                 (rng.uniform(-20, 20), rng.uniform(-20, 20)))
                for _ in range(rng.randrange(0, 24))]
    x0, x1 = sorted((rng.uniform(-10, 10), rng.uniform(-10, 10)))
    y0, y1 = sorted((rng.uniform(-10, 10), rng.uniform(-10, 10)))
    sample_rect = (x0, y0, x1, y1)
    expected_any = any(scalar_crosses(a, b, sample_rect) for a, b in segments)
    got = rust_screen_any(segments, sample_rect)
    if got not in (0, 1) or bool(got) != expected_any:
        screen_equivalent = False
        break
check(screen_equivalent, "500 deterministic randomized screen batches match Python exactly")

world_equivalent = True
classified_equivalent = True
for _ in range(250):
    segments = [((rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)),
                 (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)))
                for _ in range(rng.randrange(0, 20))]
    x0, x1 = sorted((rng.uniform(-400, 1200), rng.uniform(-400, 1200)))
    y0, y1 = sorted((rng.uniform(-300, 900), rng.uniform(-300, 900)))
    sample_rect = (x0, y0, x1, y1)
    point_values = [point for segment in segments for point in segment]
    projection_code, projected_points = rust_project(PERSPECTIVE, point_values)
    projected = list(zip(projected_points[::2], projected_points[1::2]))
    if projection_code != 0:
        classified_equivalent = False
        break
    projectable = [(a, b) for a, b in projected if a is not None and b is not None]
    expected_any = any(scalar_crosses(a, b, sample_rect) for a, b in projectable)
    got = rust_projected_any(PERSPECTIVE, segments, sample_rect)
    query_code, query_evidence = rust_projected_query(PERSPECTIVE, segments, sample_rect)
    if got not in (0, 1) or bool(got) != expected_any:
        world_equivalent = False
    if ((query_code, bool(query_evidence))
            != (int(expected_any), bool(projectable))):
        classified_equivalent = False
    if not world_equivalent or not classified_equivalent:
        break
check(world_equivalent, "250 deterministic randomized projected batches match current semantics")
check(classified_equivalent,
      "classified query matches the old native projector plus Python predicate")

boundary_segment = [((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0))]
boundary_code, boundary_points = rust_project(IDENTITY, boundary_segment[0])
boundary_rect = (800.0, 300.0, 810.0, 310.0)
check(boundary_code == 0 and boundary_points[1] == (800.0, 300.0)
      and rust_projected_query(IDENTITY, boundary_segment, boundary_rect) == (1, 1),
      "classified query includes an exact projected endpoint on the rectangle boundary")

no_evidence = [((0.0, 0.0, math.nan), (1.0, 1.0, 0.0))]
check(rust_projected_query(IDENTITY, no_evidence, rect) == (0, 0),
      "classified query distinguishes unavailable screen evidence")
projectable_miss = [((-1.0, -1.0, 0.0), (-0.9, -0.9, 0.0))]
check(rust_projected_query(
    IDENTITY, projectable_miss, (700.0, 500.0, 710.0, 510.0)) == (0, 1),
      "classified query distinguishes an authoritative miss")

batch_curves = [boundary_segment, projectable_miss, no_evidence, []]
batch_rect = (390.0, 290.0, 410.0, 310.0)
check(rust_projected_batch(IDENTITY, batch_curves, batch_rect)
      == (0, (1, 0, 2, 2)),
      "batch query returns exact hit, miss, and no-evidence statuses in order")
batch_equivalent = True
for _ in range(100):
    curves = [[((rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)),
               (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)))
              for _segment in range(rng.randrange(0, 12))]
              for _curve in range(rng.randrange(0, 20))]
    x0, x1 = sorted((rng.uniform(-400, 1200), rng.uniform(-400, 1200)))
    y0, y1 = sorted((rng.uniform(-300, 900), rng.uniform(-300, 900)))
    sample_rect = (x0, y0, x1, y1)
    expected = []
    for segments in curves:
        projection_code, points = rust_project(
            PERSPECTIVE, [point for segment in segments for point in segment])
        if projection_code != 0:
            batch_equivalent = False
            break
        projected = list(zip(points[::2], points[1::2]))
        projectable = [(a, b) for a, b in projected if a is not None and b is not None]
        if any(scalar_crosses(a, b, sample_rect) for a, b in projectable):
            expected.append(1)
        else:
            expected.append(0 if projectable else 2)
    code, actual = rust_projected_batch(PERSPECTIVE, curves, sample_rect)
    if not batch_equivalent or code != 0 or actual != tuple(expected):
        batch_equivalent = False
        break
check(batch_equivalent,
      "100 randomized mixed-size curve batches match the old projector and predicate")

nearest_curves = [boundary_segment, boundary_segment]
check(rust_nearest_batch(IDENTITY, nearest_curves, (400.0, 300.0), 1.0)
      == (1, 1, 0.0),
      "nearest batch preserves the later-candidate exact tie rule")
no_nearest = rust_nearest_batch(
    IDENTITY, [projectable_miss], (400.0, 300.0), 1.0)
check(no_nearest[0] == 0 and no_nearest[1] == (1 << 64) - 1
      and math.isinf(no_nearest[2]),
      "nearest batch distinguishes an authoritative no-hit")

point_batch = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
check(rust_nearest_point(IDENTITY, point_batch, (400.0, 300.0), 1.0)
      == (1, 1, 0.0),
      "nearest point query preserves the later-candidate exact tie rule")
check(rust_points_rect(IDENTITY, point_batch, (400.0, 300.0, 800.0, 600.0))
      == (0, (1, 1, 1)),
      "point rectangle query includes points exactly on every rectangle edge")

point_equivalent = rect_equivalent = True
for _ in range(200):
    sample_points = [(rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6))
                     for _point in range(rng.randrange(0, 40))]
    cursor = (rng.uniform(-200, 1000), rng.uniform(-200, 800))
    limit = rng.uniform(0, 300)
    expected_index = (1 << 64) - 1
    expected_distance = limit
    projected = [scalar_display_project(PERSPECTIVE, point, 800.0, 600.0)
                 for point in sample_points]
    for index, pixel in enumerate(projected):
        if pixel is None:
            continue
        distance = math.hypot(pixel[0] - cursor[0], pixel[1] - cursor[1])
        if distance <= expected_distance:
            expected_index, expected_distance = index, distance
    actual = rust_nearest_point(PERSPECTIVE, sample_points, cursor, limit)
    if expected_index == (1 << 64) - 1:
        point_equivalent &= (actual[0] == 0 and actual[1] == expected_index
                             and math.isinf(actual[2]))
    else:
        point_equivalent &= (actual[0] == 1 and actual[1] == expected_index
                             and abs(actual[2] - expected_distance) < 1e-12)
    x0, x1 = sorted((rng.uniform(-400, 1200), rng.uniform(-400, 1200)))
    y0, y1 = sorted((rng.uniform(-300, 900), rng.uniform(-300, 900)))
    sample_rect = (x0, y0, x1, y1)
    expected_inside = tuple(int(pixel is not None and x0 <= pixel[0] <= x1
                                and y0 <= pixel[1] <= y1)
                            for pixel in projected)
    rect_equivalent &= rust_points_rect(
        PERSPECTIVE, sample_points, sample_rect) == (0, expected_inside)
    if not point_equivalent or not rect_equivalent:
        break
check(point_equivalent, "200 randomized nearest-point batches match scalar f64 projection")
check(rect_equivalent, "200 randomized point-rectangle batches match inclusive scalar projection")

bad_matrix = list(IDENTITY)
bad_matrix[0] = math.nan
matrix_raw = darray(bad_matrix)
point_raw = farray((0.0, 0.0, 0.0))
out_raw = (ctypes.c_double * 2)()
visible_raw = (ctypes.c_ubyte * 1)()
check(lib.narwhal_project_points_f32(
    matrix_raw, point_raw, 1, 800.0, 600.0, out_raw, visible_raw) == -2,
    "invalid matrices fail closed with an explicit input error")
check(lib.narwhal_project_points_f32(
    None, point_raw, 1, 800.0, 600.0, out_raw, visible_raw) == -1,
    "null matrix pointers fail closed")
check(rust_screen_any([], rect) == 0, "empty screen-segment batches are a clean miss")
check(rust_projected_any(IDENTITY, [], rect) == 0,
      "empty world-segment batches are a clean miss")
check(rust_projected_query(IDENTITY, [], rect) == (0, 0),
      "empty classified batches report no projectable evidence")
query_raw = farray((0.0, 0.0, 0.0, 1.0, 1.0, 0.0))
check(lib.narwhal_projected_segments_rect_query_f32(
    darray(IDENTITY), query_raw, 1, 800.0, 600.0, darray(rect), None) == -1,
    "classified query rejects a null evidence pointer")
for label, matrix_ptr, segments_ptr, count, width, height, rect_ptr, expected in (
    ("null matrix", None, query_raw, 1, 800.0, 600.0, darray(rect), -1),
    ("null segments", darray(IDENTITY), None, 1, 800.0, 600.0, darray(rect), -1),
    ("null rectangle", darray(IDENTITY), query_raw, 1, 800.0, 600.0, None, -1),
    ("invalid matrix", matrix_raw, query_raw, 1, 800.0, 600.0, darray(rect), -2),
    ("invalid dimensions", darray(IDENTITY), query_raw, 1, -1.0, 600.0, darray(rect), -2),
    ("unordered rectangle", darray(IDENTITY), query_raw, 1, 800.0, 600.0,
     darray((6.0, 4.0, 4.0, 6.0)), -2),
    ("non-finite rectangle", darray(IDENTITY), query_raw, 1, 800.0, 600.0,
     darray((4.0, 4.0, math.nan, 6.0)), -2),
):
    evidence = ctypes.c_ubyte(7)
    result = lib.narwhal_projected_segments_rect_query_f32(
        matrix_ptr, segments_ptr, count, width, height, rect_ptr, ctypes.byref(evidence))
    check(result == expected and evidence.value == 0,
          f"classified query rejects {label} and clears evidence",
          repr((result, evidence.value)))

batch_segments = farray((
    -1.0, 0.0, 0.0, 1.0, 0.0, 0.0,
    -1.0, -1.0, 0.0, -0.9, -0.9, 0.0,
))
invalid_offsets = (ctypes.c_uint64 * 3)(0, 2, 1)
batch_statuses = (ctypes.c_ubyte * 2)(9, 9)
check(lib.narwhal_projected_curves_rect_query_f32(
    darray(IDENTITY), batch_segments, 2, invalid_offsets, 2,
    800.0, 600.0, darray(rect), batch_statuses) == -2
      and tuple(batch_statuses) == (2, 2),
      "batch query rejects non-monotonic offsets before publishing partial results")
valid_offsets = (ctypes.c_uint64 * 3)(0, 1, 2)
check(lib.narwhal_projected_curves_rect_query_f32(
    darray(IDENTITY), batch_segments, 2, valid_offsets, 2,
    800.0, 600.0, darray(rect), None) == -1,
      "batch query rejects a null status output")
check(lib.narwhal_projected_curves_rect_query_f32(
    darray(IDENTITY), None, 2, valid_offsets, 2,
    800.0, 600.0, darray(rect), batch_statuses) == -1,
      "batch query rejects null non-empty segments")
empty_offsets = (ctypes.c_uint64 * 3)(0, 0, 0)
empty_statuses = (ctypes.c_ubyte * 2)(9, 9)
check(lib.narwhal_projected_curves_rect_query_f32(
    darray(IDENTITY), None, 0, empty_offsets, 2,
    800.0, 600.0, darray(rect), empty_statuses) == 0
      and tuple(empty_statuses) == (2, 2),
      "batch query accepts a null segment pointer for all-empty curves without UB")
nearest_index = ctypes.c_uint64(7)
nearest_distance = ctypes.c_double(7.0)
check(lib.narwhal_nearest_projected_curve_f32(
    darray(IDENTITY), None, 0, empty_offsets, 2, 800.0, 600.0,
    darray((400.0, 300.0)), 6.0,
    ctypes.byref(nearest_index), ctypes.byref(nearest_distance)) == 0
      and nearest_index.value == (1 << 64) - 1 and math.isinf(nearest_distance.value),
      "nearest batch safely accepts multiple empty curves")
check(lib.narwhal_nearest_projected_curve_f32(
    darray(IDENTITY), batch_segments, 2, invalid_offsets, 2, 800.0, 600.0,
    darray((400.0, 300.0)), 6.0,
    ctypes.byref(nearest_index), ctypes.byref(nearest_distance)) == -2,
      "nearest batch rejects non-monotonic offsets")
check(lib.narwhal_nearest_projected_curve_f32(
    darray(IDENTITY), batch_segments, 2, valid_offsets, 2, 800.0, 600.0,
    darray((400.0, 300.0)), 6.0, None, ctypes.byref(nearest_distance)) == -1,
      "nearest batch rejects a null index output")
point_index = ctypes.c_uint64(7)
point_distance = ctypes.c_double(7.0)
check(lib.narwhal_nearest_projected_point_f64(
    darray(IDENTITY), None, 0, 800.0, 600.0, darray((400.0, 300.0)), 6.0,
    ctypes.byref(point_index), ctypes.byref(point_distance)) == 0
      and point_index.value == (1 << 64) - 1 and math.isinf(point_distance.value),
      "nearest point query safely accepts empty null input")
check(lib.narwhal_nearest_projected_point_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), 1, 800.0, 600.0,
    darray((400.0, 300.0)), 6.0, None, ctypes.byref(point_distance)) == -1,
      "nearest point query rejects a null index output")
point_inside = (ctypes.c_ubyte * 2)(9, 9)
check(lib.narwhal_projected_points_rect_query_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0, 1.0, 1.0, 0.0)), 2,
    800.0, 600.0, darray((800.0, 300.0, 400.0, 600.0)), point_inside) == -2
      and tuple(point_inside) == (0, 0),
      "point rectangle query rejects unordered bounds and clears every result")
check(lib.narwhal_projected_points_rect_query_f64(
    darray(IDENTITY), None, 0, 800.0, 600.0, darray(rect), None) == 0,
      "point rectangle query safely accepts empty null input and output")
oversized_count = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 3) + 1
check(lib.narwhal_nearest_projected_point_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), oversized_count,
    800.0, 600.0, darray((400.0, 300.0)), 6.0,
    ctypes.byref(point_index), ctypes.byref(point_distance)) == -3,
      "nearest point query rejects a slice whose byte span exceeds isize::MAX")
point_inside = (ctypes.c_ubyte * 2)(9, 9)
check(lib.narwhal_projected_points_rect_query_f64(
    None, darray((0.0, 0.0, 0.0, 1.0, 1.0, 0.0)), 2,
    800.0, 600.0, darray(rect), point_inside) == -1
      and tuple(point_inside) == (0, 0),
      "point rectangle query clears output before rejecting a null matrix")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} Rust selection-core checks passed")
