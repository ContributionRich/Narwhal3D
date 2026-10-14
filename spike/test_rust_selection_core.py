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
for _ in range(250):
    segments = [((rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)),
                 (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-6, 6)))
                for _ in range(rng.randrange(0, 20))]
    x0, x1 = sorted((rng.uniform(-400, 1200), rng.uniform(-400, 1200)))
    y0, y1 = sorted((rng.uniform(-300, 900), rng.uniform(-300, 900)))
    sample_rect = (x0, y0, x1, y1)
    projected = [(scalar_project(PERSPECTIVE, a, 800.0, 600.0),
                  scalar_project(PERSPECTIVE, b, 800.0, 600.0)) for a, b in segments]
    expected_any = any(a is not None and b is not None and scalar_crosses(a, b, sample_rect)
                       for a, b in projected)
    got = rust_projected_any(PERSPECTIVE, segments, sample_rect)
    if got not in (0, 1) or bool(got) != expected_any:
        world_equivalent = False
        break
check(world_equivalent, "250 deterministic randomized projected batches match current semantics")

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

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    raise SystemExit(1)
print(f"\n{checks}/{checks} Rust selection-core checks passed")
