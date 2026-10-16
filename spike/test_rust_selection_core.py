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
lib.narwhal_project_display_points_f64.argtypes = [
    DoubleP, DoubleP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_project_display_points_f64.restype = ctypes.c_int
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
lib.narwhal_nearest_projected_polylines_f64.argtypes = [
    DoubleP, DoubleP, ctypes.c_size_t, UInt64P, ctypes.c_size_t,
    ctypes.c_double, ctypes.c_double, DoubleP, UInt64P, DoubleP, DoubleP, ByteP,
]
lib.narwhal_nearest_projected_polylines_f64.restype = ctypes.c_int
lib.narwhal_nearest_screen_polyline_f64.argtypes = [
    DoubleP, ByteP, ctypes.c_size_t, UInt64P, ctypes.c_size_t,
    DoubleP, ctypes.c_double, UInt64P, DoubleP,
]
lib.narwhal_nearest_screen_polyline_f64.restype = ctypes.c_int
lib.narwhal_screen_boxes_from_clip_f64.argtypes = [
    DoubleP, ctypes.c_size_t, UInt64P, ctypes.c_size_t, ByteP,
    ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_screen_boxes_from_clip_f64.restype = ctypes.c_int
lib.narwhal_screen_boxes_from_world_f64.argtypes = [
    DoubleP, DoubleP, ctypes.c_size_t, UInt64P, ctypes.c_size_t, ByteP,
    ctypes.c_double, ctypes.c_double, DoubleP, ByteP,
]
lib.narwhal_screen_boxes_from_world_f64.restype = ctypes.c_int
lib.narwhal_screen_rects_from_aabbs_f64.argtypes = [
    DoubleP, DoubleP, DoubleP, ctypes.c_size_t, ctypes.c_double, ctypes.c_double,
    DoubleP, ByteP, ByteP,
]
lib.narwhal_screen_rects_from_aabbs_f64.restype = ctypes.c_int
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


def scalar_segment_distance(point, a, b):
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    if denominator < 1.0e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / denominator
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def scalar_clip_boxes(clip, offsets, point_flags, width=800.0, height=600.0):
    boxes = []
    for object_index, is_point in enumerate(point_flags):
        visible = []
        for point in clip[offsets[object_index]:offsets[object_index + 1]]:
            if not all(math.isfinite(value) for value in point) or point[3] <= 0.0:
                continue
            pixel = ((point[0] / point[3] + 1.0) * width * 0.5,
                     (point[1] / point[3] + 1.0) * height * 0.5)
            if all(math.isfinite(value) for value in pixel):
                visible.append(pixel)
        if not visible:
            boxes.append(None)
        elif is_point:
            boxes.append((*visible[0], *visible[0]))
        else:
            boxes.append((min(point[0] for point in visible),
                          min(point[1] for point in visible),
                          max(point[0] for point in visible),
                          max(point[1] for point in visible)))
    return boxes


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


def rust_display_project(matrix, points, width=800.0, height=600.0):
    raw = darray(value for point in points for value in point)
    pixels = (ctypes.c_double * (len(points) * 2))()
    visible = (ctypes.c_ubyte * len(points))()
    code = lib.narwhal_project_display_points_f64(
        darray(matrix), raw, len(points), width, height, pixels, visible)
    result = [None if not visible[index]
              else (pixels[index * 2], pixels[index * 2 + 1])
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


def rust_screen_polyline(pixels, visible, offsets, mouse, limit):
    index = ctypes.c_uint64((1 << 64) - 1)
    distance = ctypes.c_double(math.inf)
    code = lib.narwhal_nearest_screen_polyline_f64(
        darray(value for point in pixels for value in point),
        (ctypes.c_ubyte * len(visible))(*visible),
        len(pixels),
        (ctypes.c_uint64 * len(offsets))(*offsets),
        len(offsets) - 1,
        darray(mouse),
        limit,
        ctypes.byref(index),
        ctypes.byref(distance),
    )
    return code, index.value, distance.value


def rust_clip_boxes(clip, offsets, point_flags, width=800.0, height=600.0):
    boxes = (ctypes.c_double * (len(point_flags) * 4))()
    valid = (ctypes.c_ubyte * len(point_flags))()
    code = lib.narwhal_screen_boxes_from_clip_f64(
        darray(value for point in clip for value in point),
        len(clip),
        (ctypes.c_uint64 * len(offsets))(*offsets),
        len(point_flags),
        (ctypes.c_ubyte * len(point_flags))(*point_flags),
        width,
        height,
        boxes,
        valid,
    )
    result = [None if not valid[index] else tuple(boxes[index * 4:index * 4 + 4])
              for index in range(len(point_flags))]
    return code, result


def rust_world_boxes(matrix, points, offsets, point_flags, width=800.0, height=600.0):
    boxes = (ctypes.c_double * (len(point_flags) * 4))()
    valid = (ctypes.c_ubyte * len(point_flags))()
    code = lib.narwhal_screen_boxes_from_world_f64(
        darray(matrix),
        darray(value for point in points for value in point),
        len(points),
        (ctypes.c_uint64 * len(offsets))(*offsets),
        len(point_flags),
        (ctypes.c_ubyte * len(point_flags))(*point_flags),
        width,
        height,
        boxes,
        valid,
    )
    result = [None if not valid[index] else tuple(boxes[index * 4:index * 4 + 4])
              for index in range(len(point_flags))]
    return code, result


def rust_aabb_rects(matrix, lower, upper, width=800.0, height=600.0):
    count = len(lower)
    rects = (ctypes.c_double * (count * 4))()
    unbounded = (ctypes.c_ubyte * count)()
    all_behind = (ctypes.c_ubyte * count)()
    code = lib.narwhal_screen_rects_from_aabbs_f64(
        darray(matrix),
        darray(value for point in lower for value in point),
        darray(value for point in upper for value in point),
        count, width, height, rects, unbounded, all_behind,
    )
    return (code,
            [tuple(rects[index * 4:index * 4 + 4]) for index in range(count)],
            tuple(unbounded), tuple(all_behind))


def scalar_aabb_rects(matrix, lower, upper, width=800.0, height=600.0):
    rows = [matrix[index:index + 4] for index in range(0, 16, 4)]
    result = []
    for low, high in zip(lower, upper):
        bounds = [math.inf, math.inf, -math.inf, -math.inf]
        behind = 0
        any_nonfront = False
        for corner in range(8):
            x = high[0] if corner & 4 else low[0]
            y = high[1] if corner & 2 else low[1]
            z = high[2] if corner & 1 else low[2]
            w_terms = (rows[3][0] * x, rows[3][1] * y, rows[3][2] * z)
            w = w_terms[0] + w_terms[1] + w_terms[2] + rows[3][3]
            if not w > 0.0:
                any_nonfront = True
                scale = (abs(w_terms[0]) + abs(w_terms[1]) + abs(w_terms[2])
                         + abs(rows[3][3]))
                behind += int(w < -1.0e-5 * scale)
                continue
            clip_x = rows[0][0] * x + rows[0][1] * y + rows[0][2] * z + rows[0][3]
            clip_y = rows[1][0] * x + rows[1][1] * y + rows[1][2] * z + rows[1][3]
            px = width / 2.0 + width / 2.0 * (clip_x / w)
            py = height / 2.0 + height / 2.0 * (clip_y / w)
            if px < bounds[0]:
                bounds[0] = px
            if py < bounds[1]:
                bounds[1] = py
            if px > bounds[2]:
                bounds[2] = px
            if py > bounds[3]:
                bounds[3] = py
        all_behind = behind == 8
        finite = all(math.isfinite(value) for value in bounds)
        result.append((tuple(bounds), int((any_nonfront and not all_behind) or not finite),
                       int(all_behind)))
    return result


def rust_projected_near(matrix, polylines, mouse, width=800.0, height=600.0):
    points = [point for polyline in polylines for point in polyline]
    offsets = [0]
    for polyline in polylines:
        offsets.append(offsets[-1] + len(polyline))
    count = len(polylines)
    segments = (ctypes.c_uint64 * count)()
    parameters = (ctypes.c_double * count)()
    distances = (ctypes.c_double * count)()
    valid = (ctypes.c_ubyte * count)()
    code = lib.narwhal_nearest_projected_polylines_f64(
        darray(matrix), darray(value for point in points for value in point), len(points),
        (ctypes.c_uint64 * len(offsets))(*offsets), count, width, height,
        darray(mouse), segments, parameters, distances, valid,
    )
    result = []
    for index in range(count):
        result.append(None if not valid[index]
                      else (segments[index], parameters[index], distances[index]))
    return code, result


def scalar_projected_near(matrix, polylines, mouse, width=800.0, height=600.0):
    result = []
    for polyline in polylines:
        projected = [scalar_display_project(matrix, point, width, height)
                     for point in polyline]
        best = None
        for index, (a, b) in enumerate(zip(projected, projected[1:])):
            if a is None or b is None:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            denominator = dx * dx + dy * dy
            if denominator <= 0.0:
                parameter = 0.0
            else:
                parameter = max(0.0, min(
                    1.0, ((mouse[0] - a[0]) * dx + (mouse[1] - a[1]) * dy)
                    / denominator))
            distance = math.hypot(a[0] + dx * parameter - mouse[0],
                                  a[1] + dy * parameter - mouse[1])
            if best is None or distance < best[2]:
                best = (index, parameter, distance)
        result.append(best)
    return result


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
adversarial_matrix = (
    1.027998685836792, -0.42524489760398865, -0.466378778219223, 0.6437168717384338,
    0.3514747619628906, 1.8906302452087402, -0.9491537809371948, 9.998909950256348,
    -0.4969795048236847, -0.3138795793056488, -0.8092533946037292, 6.88346529006958,
    -0.49688011407852173, -0.3138168156147003, -0.8090915679931641, 7.08206844329834,
)
adversarial_points = [(6.764359507293101, -9.856076443526845, 0.48392038233675194),
                      (math.nan, 0.0, 0.0)]
display_code, display_actual = rust_display_project(
    adversarial_matrix, adversarial_points, 1920.0, 1080.0)
display_expected = [scalar_display_project(
    adversarial_matrix, point, 1920.0, 1080.0) for point in adversarial_points]
check(display_code == 0 and display_actual == display_expected,
      "f64 display export matches adversarial mathutils mixed-precision projection")

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

screen_pixels = [(0.0, 0.0), (10.0, 0.0), (0.0, 0.0), (10.0, 0.0)]
screen_offsets = [0, 2, 4]
check(rust_screen_polyline(screen_pixels, [1, 1, 1, 1], screen_offsets, (5.0, 1.0), 9.0)
      == (1, 0, 1.0),
      "screen polyline query preserves earlier exact ties")
cancellation_pixels = [(-1.0, 0.5), (1.0, 0.5), (1.0e16, 0.0), (1.0, 0.0)]
check(rust_screen_polyline(
    cancellation_pixels, [1, 1, 1, 1], [0, 2, 4], (0.0, 0.0), 0.75)
      == (1, 1, 0.0),
      "hover broad rejection preserves floating interpolation outside endpoint AABBs")
screen_boundary = rust_screen_polyline(
    screen_pixels, [1, 1, 1, 1], screen_offsets, (5.0, 9.0), 9.0)
check(screen_boundary[0] == 0 and screen_boundary[1] == (1 << 64) - 1
      and math.isinf(screen_boundary[2]),
      "screen polyline query preserves strict distance < tolerance")
hidden_pixels = [(0.0, 0.0), (math.nan, math.nan), (0.0, 0.0), (10.0, 0.0)]
check(rust_screen_polyline(hidden_pixels, [1, 0, 1, 1], screen_offsets, (5.0, 1.0), 9.0)
      == (1, 1, 1.0),
      "screen polyline query drops only segments with a hidden endpoint")
adversarial_mouse = (4.364241123199463, 4.749826908111572)
adversarial_distance = math.hypot(*adversarial_mouse)
adversarial_pixels = [(0.0, 0.0), (0.0, 0.0)]
adversarial_offsets = [0, 2]
adversarial_miss = rust_screen_polyline(
    adversarial_pixels, [1, 1], adversarial_offsets,
    adversarial_mouse, adversarial_distance)
adversarial_hit = rust_screen_polyline(
    adversarial_pixels, [1, 1], adversarial_offsets,
    adversarial_mouse, math.nextafter(adversarial_distance, math.inf))
check(adversarial_miss[0] == 0 and adversarial_miss[1] == (1 << 64) - 1
      and math.isinf(adversarial_miss[2])
      and adversarial_hit == (1, 0, adversarial_distance),
      "screen polyline query is bit-exact with adversarial Python math.hypot")
threshold_dx = math.sqrt(1.0e-9)
below_threshold_dx = math.nextafter(threshold_dx, 0.0)
epsilon_mouse = (threshold_dx * 2.0, 1.0)
at_distance = scalar_segment_distance(
    epsilon_mouse, (0.0, 0.0), (threshold_dx, 0.0))
below_distance = scalar_segment_distance(
    epsilon_mouse, (0.0, 0.0), (below_threshold_dx, 0.0))
epsilon_limit = (at_distance + below_distance) / 2.0
at_epsilon = rust_screen_polyline(
    [(0.0, 0.0), (threshold_dx, 0.0)], [1, 1], [0, 2],
    epsilon_mouse, epsilon_limit)
below_epsilon = rust_screen_polyline(
    [(0.0, 0.0), (below_threshold_dx, 0.0)], [1, 1], [0, 2],
    epsilon_mouse, epsilon_limit)
check(threshold_dx * threshold_dx == 1.0e-9
      and at_epsilon == (1, 0, at_distance)
      and below_epsilon[0] == 0 and below_epsilon[1] == (1 << 64) - 1,
      "screen polyline query preserves the exact 1e-9 degenerate threshold")
extreme = 1.4e154
check(rust_screen_polyline(
    [(0.0, 0.0), (extreme, extreme)], [1, 1], [0, 2],
    (extreme, extreme), 9.0) == (1, 0, 0.0),
      "screen polyline query matches Python max/min when the parameter is NaN")

box_clip = [(-1.0, -1.0, 0.0, 1.0), (1.0, 1.0, 0.0, 1.0),
            (99.0, 99.0, 0.0, -1.0), (-0.5, 0.25, 0.0, 1.0),
            (0.75, -0.75, 0.0, 1.0), (math.nan, 0.0, 0.0, 1.0)]
box_offsets = [0, 3, 5, 6]
box_flags = [0, 1, 0]
check(rust_clip_boxes(box_clip, box_offsets, box_flags) == (
    0, [(0.0, 0.0, 800.0, 600.0), (200.0, 375.0, 200.0, 375.0), None]),
      "clip-box reduction preserves grouping, hidden rows, and first point pixels")
box_equivalent = True
for _ in range(250):
    object_count = rng.randrange(0, 24)
    clip = []
    offsets = [0]
    point_flags = []
    for _object in range(object_count):
        point_flags.append(rng.randrange(0, 2))
        for _point in range(rng.randrange(0, 10)):
            point = [rng.uniform(-20.0, 20.0) for _component in range(4)]
            if rng.random() < 0.12:
                point[rng.randrange(0, 4)] = math.nan
            clip.append(tuple(point))
        offsets.append(len(clip))
    expected = scalar_clip_boxes(clip, offsets, point_flags)
    code, actual = rust_clip_boxes(clip, offsets, point_flags)
    box_equivalent &= code == 0 and actual == expected
    if not box_equivalent:
        break
check(box_equivalent, "250 randomized grouped clip batches match Python screen boxes exactly")

world_matrix = (
    1.2, 0.1, 0.0, 0.2,
    0.0, 1.1, 0.1, -0.1,
    0.0, 0.0, 1.0, 0.0,
    0.15, -0.05, 0.2, 1.0,
)
world_points = [(-1.0, -1.0, 0.0), (1.0, 1.0, 0.0),
                (0.0, 0.0, -10.0), (0.25, -0.5, 0.75)]
world_offsets = [0, 3, 4]
world_flags = [0, 1]
world_clip = [tuple(
    world_matrix[row * 4] * point[0]
    + world_matrix[row * 4 + 1] * point[1]
    + world_matrix[row * 4 + 2] * point[2]
    + world_matrix[row * 4 + 3]
    for row in range(4)) for point in world_points]
check(rust_world_boxes(world_matrix, world_points, world_offsets, world_flags)
      == (0, scalar_clip_boxes(world_clip, world_offsets, world_flags)),
      "fused world-box query matches fixed-order f64 projection and reduction")
world_box_equivalent = True
for _ in range(250):
    matrix = tuple(rng.uniform(-2.0, 2.0) for _value in range(16))
    object_count = rng.randrange(0, 20)
    points = []
    offsets = [0]
    flags = []
    for _object in range(object_count):
        flags.append(rng.randrange(0, 2))
        points.extend(tuple(rng.uniform(-8.0, 8.0) for _coordinate in range(3))
                      for _point in range(rng.randrange(0, 10)))
        offsets.append(len(points))
    clip = [tuple(
        matrix[row * 4] * point[0]
        + matrix[row * 4 + 1] * point[1]
        + matrix[row * 4 + 2] * point[2]
        + matrix[row * 4 + 3]
        for row in range(4)) for point in points]
    expected = scalar_clip_boxes(clip, offsets, flags)
    code, actual = rust_world_boxes(matrix, points, offsets, flags)
    world_box_equivalent &= code == 0 and actual == expected
    if not world_box_equivalent:
        break
check(world_box_equivalent,
      "250 randomized fused world-box batches match fixed-order Python exactly")

aabb_matrix = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
)
aabb_lower = [(-1.0, -1.0, 1.0), (-1.0, -1.0, -2.0), (-1.0, -1.0, -1.0)]
aabb_upper = [(1.0, 1.0, 1.0), (1.0, 1.0, -1.0), (1.0, 1.0, 1.0)]
aabb_expected = scalar_aabb_rects(aabb_matrix, aabb_lower, aabb_upper)
aabb_code, aabb_actual_rects, aabb_unbounded, aabb_behind = rust_aabb_rects(
    aabb_matrix, aabb_lower, aabb_upper)
check(aabb_code == 0
      and list(zip(aabb_actual_rects, aabb_unbounded, aabb_behind)) == aabb_expected,
      "AABB rectangles preserve bounded, all-behind, and eye-straddling states")
near_eye_matrix = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    1.0, 0.0, 0.0, 1.0,
)
near_eye = rust_aabb_rects(
    near_eye_matrix, [(-1.000001, -1.0, -1.0)],
    [(-1.000001, 1.0, 1.0)])
check(near_eye[0] == 0 and near_eye[2:] == ((1,), (0,)),
      "relative cancellation near w=0 remains unbounded rather than clearly behind")
overflow_w_matrix = (
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    1.0e308, 0.0, 0.0, 0.0,
)
overflow_w = rust_aabb_rects(
    overflow_w_matrix, [(2.0, 0.0, 0.0)], [(2.0, 0.0, 0.0)])
check(overflow_w == (0, [(400.0, 300.0, 400.0, 300.0)], (0,), (0,)),
      "positive-infinite w remains front-facing exactly like the NumPy predicate")
aabb_equivalent = True
for _ in range(250):
    matrix = tuple(rng.uniform(-2.0, 2.0) for _value in range(16))
    count = rng.randrange(0, 30)
    lower = []
    upper = []
    for _box in range(count):
        centre = tuple(rng.uniform(-10.0, 10.0) for _coordinate in range(3))
        extent = tuple(rng.uniform(0.0, 2.0) for _coordinate in range(3))
        lower.append(tuple(centre[index] - extent[index] for index in range(3)))
        upper.append(tuple(centre[index] + extent[index] for index in range(3)))
    expected = scalar_aabb_rects(matrix, lower, upper, 1920.0, 1080.0)
    code, actual_rects, actual_unbounded, actual_behind = rust_aabb_rects(
        matrix, lower, upper, 1920.0, 1080.0)
    actual = list(zip(actual_rects, actual_unbounded, actual_behind))
    if code != 0 or actual != expected:
        aabb_equivalent = False
        break
check(aabb_equivalent,
      "250 randomized AABB batches match fixed-order Python rectangles exactly")

near_polylines = [
    [(-1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
    [(0.25, 0.5, 0.0), (0.25, 0.5, 0.0)],
    [(0.0, 0.0, 0.0)],
]
near_expected = scalar_projected_near(IDENTITY, near_polylines, (400.0, 300.0))
check(rust_projected_near(IDENTITY, near_polylines, (400.0, 300.0))
      == (0, near_expected),
      "projected Near batches preserve segment parameter, degenerates, and empty curves")
projected_near_equivalent = True
for _ in range(250):
    matrix = tuple(rng.uniform(-2.0, 2.0) for _value in range(16))
    polylines = [[tuple(rng.uniform(-8.0, 8.0) for _coordinate in range(3))
                  for _point in range(rng.randrange(0, 14))]
                 for _curve in range(rng.randrange(0, 20))]
    mouse = (rng.uniform(-2000.0, 2000.0), rng.uniform(-1200.0, 1200.0))
    expected = scalar_projected_near(matrix, polylines, mouse, 1920.0, 1080.0)
    code, actual = rust_projected_near(matrix, polylines, mouse, 1920.0, 1080.0)
    if code != 0 or actual != expected:
        projected_near_equivalent = False
        break
check(projected_near_equivalent,
      "250 randomized projected Near batches match Python segment search exactly")

screen_polyline_equivalent = True
for _ in range(250):
    curve_count = rng.randrange(0, 24)
    offsets = [0]
    pixels = []
    visibility = []
    for _curve in range(curve_count):
        for _point in range(rng.randrange(0, 10)):
            shown = rng.random() >= 0.18
            visibility.append(int(shown))
            pixels.append((rng.uniform(-400, 1200), rng.uniform(-300, 900))
                          if shown else (math.nan, math.nan))
        offsets.append(len(pixels))
    cursor = (rng.uniform(-200, 1000), rng.uniform(-200, 800))
    limit = rng.uniform(0.0, 300.0)
    expected_index = (1 << 64) - 1
    expected_distance = limit
    for curve_index in range(curve_count):
        start, end = offsets[curve_index:curve_index + 2]
        curve_distance = math.inf
        for point_index in range(start, max(start, end - 1)):
            if not visibility[point_index] or not visibility[point_index + 1]:
                continue
            curve_distance = min(curve_distance, scalar_segment_distance(
                cursor, pixels[point_index], pixels[point_index + 1]))
        if curve_distance < expected_distance:
            expected_index, expected_distance = curve_index, curve_distance
    actual = rust_screen_polyline(pixels, visibility, offsets, cursor, limit)
    if expected_index == (1 << 64) - 1:
        screen_polyline_equivalent &= (
            actual[0] == 0 and actual[1] == expected_index and math.isinf(actual[2]))
    else:
        screen_polyline_equivalent &= (
            actual[0] == 1 and actual[1] == expected_index
            and actual[2] == expected_distance)
    if not screen_polyline_equivalent:
        break
check(screen_polyline_equivalent,
      "250 randomized grouped screen polylines match strict-earlier Python distance")

point_batch = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
check(rust_nearest_point(IDENTITY, point_batch, (400.0, 300.0), 1.0)
      == (1, 1, 0.0),
      "nearest point query preserves the later-candidate exact tie rule")
check(rust_points_rect(IDENTITY, point_batch, (400.0, 300.0, 800.0, 600.0))
      == (0, (1, 1, 1)),
      "point rectangle query includes points exactly on every rectangle edge")

point_equivalent = rect_equivalent = display_equivalent = True
for _ in range(200):
    sample_points = [(rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6))
                     for _point in range(rng.randrange(0, 40))]
    cursor = (rng.uniform(-200, 1000), rng.uniform(-200, 800))
    limit = rng.uniform(0, 300)
    expected_index = (1 << 64) - 1
    expected_distance = limit
    projected = [scalar_display_project(PERSPECTIVE, point, 800.0, 600.0)
                 for point in sample_points]
    display_code, display_points = rust_display_project(PERSPECTIVE, sample_points)
    display_equivalent &= display_code == 0 and display_points == projected
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
    if not point_equivalent or not rect_equivalent or not display_equivalent:
        break
check(display_equivalent, "200 randomized f64 display batches match scalar mixed precision")
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
box_out = (ctypes.c_double * 8)(*([7.0] * 8))
box_valid = (ctypes.c_ubyte * 2)(7, 7)
box_offsets_raw = (ctypes.c_uint64 * 3)(0, 1, 2)
box_flags_raw = (ctypes.c_ubyte * 2)(0, 1)
box_clip_raw = darray((-1.0, -1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0))
check(lib.narwhal_screen_boxes_from_clip_f64(
    None, 2, box_offsets_raw, 2, box_flags_raw, 800.0, 600.0,
    box_out, box_valid) == -1
      and tuple(box_valid) == (0, 0) and all(math.isnan(value) for value in box_out),
      "clip-box reduction clears outputs before rejecting null nonempty clip input")
box_out[:] = [7.0] * 8
box_valid[:] = [7, 7]
check(lib.narwhal_screen_boxes_from_clip_f64(
    box_clip_raw, 2, (ctypes.c_uint64 * 3)(0, 2, 1), 2, box_flags_raw,
    800.0, 600.0, box_out, box_valid) == -2
      and tuple(box_valid) == (0, 0) and all(math.isnan(value) for value in box_out),
      "clip-box reduction rejects non-monotonic offsets and clears outputs")
check(lib.narwhal_screen_boxes_from_clip_f64(
    box_clip_raw, 2, box_offsets_raw, 2, (ctypes.c_ubyte * 2)(0, 2),
    800.0, 600.0, box_out, box_valid) == -2,
      "clip-box reduction rejects undocumented point flags")
box_oversized = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 4) + 1
check(lib.narwhal_screen_boxes_from_clip_f64(
    box_clip_raw, box_oversized, (ctypes.c_uint64 * 2)(0, 1), 1,
    (ctypes.c_ubyte * 1)(0), 800.0, 600.0, box_out, box_valid) == -3,
      "clip-box reduction rejects a slice whose byte span exceeds isize::MAX")
check(lib.narwhal_screen_boxes_from_clip_f64(
    None, 0, None, 0, None, 800.0, 600.0, None, None) == 0,
      "clip-box reduction safely accepts an empty null batch")
box_out[:] = [7.0] * 8
box_valid[:] = [7, 7]
check(lib.narwhal_screen_boxes_from_world_f64(
    None, darray((-1.0, -1.0, 0.0, 1.0, 1.0, 0.0)), 2,
    box_offsets_raw, 2, box_flags_raw, 800.0, 600.0, box_out, box_valid) == -1
      and tuple(box_valid) == (0, 0) and all(math.isnan(value) for value in box_out),
      "fused world-box query clears outputs before rejecting a null matrix")
box_out[:] = [7.0] * 8
box_valid[:] = [7, 7]
check(lib.narwhal_screen_boxes_from_world_f64(
    darray(IDENTITY), darray((math.nan, 0.0, 0.0, 1.0, 1.0, 0.0)), 2,
    box_offsets_raw, 2, box_flags_raw, 800.0, 600.0, box_out, box_valid) == -2
      and tuple(box_valid) == (0, 0) and all(math.isnan(value) for value in box_out),
      "fused world-box query rejects nonfinite points and clears outputs")
world_box_oversized = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 3) + 1
check(lib.narwhal_screen_boxes_from_world_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), world_box_oversized,
    (ctypes.c_uint64 * 2)(0, 1), 1, (ctypes.c_ubyte * 1)(0),
    800.0, 600.0, box_out, box_valid) == -3,
      "fused world-box query rejects a slice whose byte span exceeds isize::MAX")
check(lib.narwhal_screen_boxes_from_world_f64(
    darray(IDENTITY), None, 0, None, 0, None,
    800.0, 600.0, None, None) == 0,
      "fused world-box query safely accepts an empty null batch")

aabb_out = (ctypes.c_double * 4)(0.0, 0.0, 0.0, 0.0)
aabb_unbounded_out = (ctypes.c_ubyte * 1)(1)
aabb_behind_out = (ctypes.c_ubyte * 1)(1)
check(lib.narwhal_screen_rects_from_aabbs_f64(
    darray(IDENTITY), None, darray((1.0, 1.0, 1.0)), 1, 800.0, 600.0,
    aabb_out, aabb_unbounded_out, aabb_behind_out) == -1
      and tuple(aabb_unbounded_out) == (0,) and tuple(aabb_behind_out) == (0,)
      and all(math.isnan(value) for value in aabb_out),
      "AABB projection clears outputs before rejecting null bounds")
check(lib.narwhal_screen_rects_from_aabbs_f64(
    darray(IDENTITY), darray((math.nan, 0.0, 0.0)), darray((1.0, 1.0, 1.0)),
    1, 800.0, 600.0, aabb_out, aabb_unbounded_out, aabb_behind_out) == -2,
      "AABB projection rejects nonfinite bounds")
check(lib.narwhal_screen_rects_from_aabbs_f64(
    darray(IDENTITY), darray((2.0, 0.0, 0.0)), darray((1.0, 1.0, 1.0)),
    1, 800.0, 600.0, aabb_out, aabb_unbounded_out, aabb_behind_out) == -2,
      "AABB projection rejects inverted bounds")
aabb_oversized = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 3) + 1
check(lib.narwhal_screen_rects_from_aabbs_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), darray((1.0, 1.0, 1.0)),
    aabb_oversized, 800.0, 600.0, aabb_out, aabb_unbounded_out,
    aabb_behind_out) == -3,
      "AABB projection rejects a slice whose byte span exceeds isize::MAX")
check(lib.narwhal_screen_rects_from_aabbs_f64(
    darray(IDENTITY), None, None, 0, 800.0, 600.0, None, None, None) == 0,
      "AABB projection safely accepts an empty null batch")

near_offsets_raw = (ctypes.c_uint64 * 3)(0, 2, 3)
near_segments_out = (ctypes.c_uint64 * 2)(0, 0)
near_parameters_out = (ctypes.c_double * 2)(0.0, 0.0)
near_distances_out = (ctypes.c_double * 2)(0.0, 0.0)
near_valid_out = (ctypes.c_ubyte * 2)(1, 1)
check(lib.narwhal_nearest_projected_polylines_f64(
    None, darray((-1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0)), 3,
    near_offsets_raw, 2, 800.0, 600.0, darray((400.0, 300.0)),
    near_segments_out, near_parameters_out, near_distances_out, near_valid_out) == -1
      and tuple(near_valid_out) == (0, 0)
      and all(value == (1 << 64) - 1 for value in near_segments_out)
      and all(math.isnan(value) for value in near_parameters_out)
      and all(math.isinf(value) for value in near_distances_out),
      "projected Near query clears every output before rejecting a null matrix")
check(lib.narwhal_nearest_projected_polylines_f64(
    darray(IDENTITY), darray((math.nan, 0.0, 0.0, 1.0, 0.0, 0.0)), 2,
    (ctypes.c_uint64 * 2)(0, 2), 1, 800.0, 600.0, darray((400.0, 300.0)),
    near_segments_out, near_parameters_out, near_distances_out, near_valid_out) == -2,
      "projected Near query rejects nonfinite world points")
check(lib.narwhal_nearest_projected_polylines_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0, 1.0, 0.0, 0.0)), 2,
    (ctypes.c_uint64 * 3)(0, 2, 1), 2, 800.0, 600.0, darray((400.0, 300.0)),
    near_segments_out, near_parameters_out, near_distances_out, near_valid_out) == -2,
      "projected Near query rejects non-monotonic offsets")
near_oversized = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 3) + 1
check(lib.narwhal_nearest_projected_polylines_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), near_oversized,
    (ctypes.c_uint64 * 2)(0, 1), 1, 800.0, 600.0, darray((400.0, 300.0)),
    near_segments_out, near_parameters_out, near_distances_out, near_valid_out) == -3,
      "projected Near query rejects a slice whose byte span exceeds isize::MAX")
check(lib.narwhal_nearest_projected_polylines_f64(
    darray(IDENTITY), None, 0, None, 0, 800.0, 600.0, darray((400.0, 300.0)),
    None, None, None, None) == 0,
      "projected Near query safely accepts an empty null batch")

screen_mouse = darray((5.0, 1.0))
polyline_index = ctypes.c_uint64(7)
polyline_distance = ctypes.c_double(7.0)
polyline_pixels_raw = darray(value for point in screen_pixels for value in point)
polyline_visible_raw = (ctypes.c_ubyte * 4)(1, 1, 1, 1)
polyline_offsets_raw = (ctypes.c_uint64 * 3)(0, 2, 4)
for label, pixels_ptr, visible_ptr, offsets_ptr, mouse_ptr in (
    ("null mouse", polyline_pixels_raw, polyline_visible_raw, polyline_offsets_raw, None),
    ("null pixels", None, polyline_visible_raw, polyline_offsets_raw, darray((5.0, 1.0))),
    ("null visibility", polyline_pixels_raw, None, polyline_offsets_raw, darray((5.0, 1.0))),
    ("null offsets", polyline_pixels_raw, polyline_visible_raw, None, darray((5.0, 1.0))),
):
    polyline_index.value = 7
    polyline_distance.value = 7.0
    result = lib.narwhal_nearest_screen_polyline_f64(
        pixels_ptr, visible_ptr, 4, offsets_ptr, 2, mouse_ptr, 9.0,
        ctypes.byref(polyline_index), ctypes.byref(polyline_distance))
    check(result == -1 and polyline_index.value == (1 << 64) - 1
          and math.isinf(polyline_distance.value),
          f"screen polyline query rejects {label} and clears outputs")
check(lib.narwhal_nearest_screen_polyline_f64(
    polyline_pixels_raw, polyline_visible_raw, 4, polyline_offsets_raw, 2,
    darray((5.0, 1.0)), 9.0, None, ctypes.byref(polyline_distance)) == -1
      and lib.narwhal_nearest_screen_polyline_f64(
          polyline_pixels_raw, polyline_visible_raw, 4, polyline_offsets_raw, 2,
          darray((5.0, 1.0)), 9.0, ctypes.byref(polyline_index), None) == -1,
      "screen polyline query rejects each null output pointer")
check(lib.narwhal_nearest_screen_polyline_f64(
    None, None, 0, empty_offsets, 2, darray((5.0, 1.0)), 9.0,
    ctypes.byref(polyline_index), ctypes.byref(polyline_distance)) == 0
      and polyline_index.value == (1 << 64) - 1 and math.isinf(polyline_distance.value),
      "screen polyline query safely accepts multiple empty curves")
invalid_point_offsets = (ctypes.c_uint64 * 3)(0, 4, 3)
polyline_index.value = 7
polyline_distance.value = 7.0
check(lib.narwhal_nearest_screen_polyline_f64(
    darray(value for point in screen_pixels for value in point),
    (ctypes.c_ubyte * 4)(1, 1, 1, 1), 4, invalid_point_offsets, 2,
    darray((5.0, 1.0)), 9.0,
    ctypes.byref(polyline_index), ctypes.byref(polyline_distance)) == -2
      and polyline_index.value == (1 << 64) - 1 and math.isinf(polyline_distance.value),
      "screen polyline query rejects non-monotonic offsets and clears outputs")
polyline_index.value = 7
polyline_distance.value = 7.0
check(lib.narwhal_nearest_screen_polyline_f64(
    darray(value for point in screen_pixels for value in point),
    (ctypes.c_ubyte * 4)(1, 2, 1, 1), 4,
    (ctypes.c_uint64 * 3)(0, 2, 4), 2, darray((5.0, 1.0)), 9.0,
    ctypes.byref(polyline_index), ctypes.byref(polyline_distance)) == -2
      and polyline_index.value == (1 << 64) - 1 and math.isinf(polyline_distance.value),
      "screen polyline query rejects visibility bytes and clears outputs")

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
polyline_oversized = (sys.maxsize // ctypes.sizeof(ctypes.c_double) // 2) + 1
check(lib.narwhal_nearest_screen_polyline_f64(
    darray((0.0, 0.0)), (ctypes.c_ubyte * 1)(1), polyline_oversized,
    (ctypes.c_uint64 * 2)(0, 1), 1, darray((0.0, 0.0)), 1.0,
    ctypes.byref(polyline_index), ctypes.byref(polyline_distance)) == -3,
      "screen polyline query rejects a slice whose byte span exceeds isize::MAX")
point_inside = (ctypes.c_ubyte * 2)(9, 9)
display_pixels = (ctypes.c_double * 4)(1.0, 1.0, 1.0, 1.0)
display_visible = (ctypes.c_ubyte * 2)(9, 9)
check(lib.narwhal_project_display_points_f64(
    None, darray((0.0, 0.0, 0.0, 1.0, 1.0, 0.0)), 2,
    800.0, 600.0, display_pixels, display_visible) == -1
      and tuple(display_visible) == (0, 0)
      and all(math.isnan(value) for value in display_pixels),
      "display-point projection clears outputs before rejecting a null matrix")
check(lib.narwhal_project_display_points_f64(
    darray(IDENTITY), darray((0.0, 0.0, 0.0)), oversized_count,
    800.0, 600.0, display_pixels, display_visible) == -3,
      "display-point projection rejects a slice whose byte span exceeds isize::MAX")
check(lib.narwhal_projected_points_rect_query_f64(
    None, darray((0.0, 0.0, 0.0, 1.0, 1.0, 0.0)), 2,
    800.0, 600.0, darray(rect), point_inside) == -1
      and tuple(point_inside) == (0, 0),
      "point rectangle query clears output before rejecting a null matrix")

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} Rust selection-core checks passed")
